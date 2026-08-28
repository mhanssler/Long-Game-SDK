from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from long_game_sdk.sdk.diagnostic_engine import (
    AskOperator,
    DiagnosticEngine,
    Exhausted,
    NeedOperatorAnswer,
    ProbeCall,
)
from long_game_sdk.sdk.diagnostic_session import DiagnosticSession, Hypothesis
from long_game_sdk.sdk.transport_diagnostics import ping_host, visa_list_resources


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC_MODULES = (
    ROOT / "src" / "long_game_sdk" / "sdk" / "transport_diagnostics.py",
    ROOT / "src" / "long_game_sdk" / "sdk" / "diagnostic_engine.py",
)


def fixed_clock() -> datetime:
    return datetime(2026, 8, 27, tzinfo=timezone.utc)


@dataclass
class AdversarialSelector:
    actions: list[object]
    choose_calls: int = 0
    update_calls: int = 0

    def seed_hypotheses(self, identity: dict[str, str], symptom: str) -> Sequence[Hypothesis]:
        return [Hypothesis("transport", "The transport path is unavailable", 0.4)]

    def choose_action(self, session: DiagnosticSession, symptom: str) -> object:
        self.choose_calls += 1
        return self.actions.pop(0)

    def update_hypotheses(self, session: DiagnosticSession, symptom: str) -> Sequence[Hypothesis]:
        self.update_calls += 1
        return [Hypothesis("transport", "The transport path is unavailable", 0.4)]


def test_unsupported_and_off_list_actions_never_execute_then_reprompt_and_escalate() -> None:
    executions: list[str] = []

    def unsupported_callable() -> None:
        executions.append("unsupported")

    def registry_extra(**_: object) -> None:
        executions.append("off-list")

    selector = AdversarialSelector(
        [
            unsupported_callable,
            ProbeCall("registry_extra", {}),
            Exhausted("permitted diagnostics exhausted"),
        ]
    )
    events: list[dict[str, Any]] = []
    result = DiagnosticEngine(
        selector=selector,  # type: ignore[arg-type]
        _testing_allow_probe_registry=True,
        probe_registry={"registry_extra": registry_extra},
        event_sink=events.append,
        max_action_rejections=3,
        clock=fixed_clock,
    ).run({"id": "dp832-boundary"}, "cannot connect")

    assert executions == []
    assert selector.choose_calls == 3
    assert selector.update_calls == 0
    assert result.outcome == "escalate"
    assert result.iterations == 0
    assert result.rejection_count == 2
    assert result.session.resolution_summary == "permitted diagnostics exhausted"
    assert [event["event_type"] for event in events].count("action_rejected") == 2
    assert events[-1]["event_type"] == "diagnosis_escalated"


def test_never_converging_selector_hits_exact_hard_iteration_cap() -> None:
    cap = 5
    selector = AdversarialSelector([ProbeCall("ping_gateway", {}) for _ in range(cap + 2)])
    probe_calls = 0

    def fake_ping_gateway() -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        return {"ok": True, "status": "reachable"}

    result = DiagnosticEngine(
        selector=selector,  # type: ignore[arg-type]
        _testing_allow_probe_registry=True,
        probe_registry={"ping_gateway": fake_ping_gateway},
        max_iterations=cap,
        clock=fixed_clock,
    ).run({"id": "dp832-boundary"}, "intermittent connection")

    assert probe_calls == cap
    assert selector.choose_calls == cap
    assert selector.update_calls == cap
    assert result.iterations == cap
    assert result.outcome == "escalate"
    assert result.session.status == "escalated"
    assert result.session.resolution_summary == (
        f"maximum {cap} iterations reached without a known recommended fix"
    )


def test_diagnostic_modules_have_no_static_universal_driver_mutation_path() -> None:
    forbidden_imports: list[tuple[Path, int, str]] = []
    forbidden_calls: list[tuple[Path, int, str]] = []
    mutating_symbols = {
        "UniversalDriver",
        "_assert_mutation_allowed",
        "arm",
        "armed",
        "open_resource",
        "write",
    }

    for path in DIAGNOSTIC_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "universal_driver" or alias.name.endswith(".universal_driver"):
                        forbidden_imports.append((path, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "universal_driver" or module.endswith(".universal_driver"):
                    forbidden_imports.append((path, node.lineno, module))
            elif isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name) and function.id in {"__import__", "import_module"}:
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        imported = node.args[0].value
                        if imported == "universal_driver" or imported.endswith(".universal_driver"):
                            forbidden_imports.append((path, node.lineno, imported))
                elif (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "importlib"
                    and function.attr == "import_module"
                ):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        imported = node.args[0].value
                        if imported == "universal_driver" or imported.endswith(".universal_driver"):
                            forbidden_imports.append((path, node.lineno, imported))
                elif isinstance(function, ast.Name) and function.id in mutating_symbols:
                    forbidden_calls.append((path, node.lineno, function.id))
                elif isinstance(function, ast.Attribute) and function.attr in mutating_symbols:
                    # A generic stream ``write`` is not a UniversalDriver path. Tie
                    # attribute calls to explicit driver/module receiver names; the
                    # import checks above independently reject aliases at their source.
                    receiver = function.value
                    receiver_name = receiver.id if isinstance(receiver, ast.Name) else None
                    if receiver_name in {"driver", "instrument", "universal_driver"}:
                        forbidden_calls.append((path, node.lineno, function.attr))

    assert forbidden_imports == []
    assert forbidden_calls == []


def test_runtime_import_and_call_seams_are_poisoned_without_being_touched() -> None:
    script = r'''
import builtins
import json
import sys
import types

attempts = []
poison_calls = []

class Bomb:
    def __init__(self, name):
        self.name = name
    def __call__(self, *args, **kwargs):
        poison_calls.append(self.name)
        raise AssertionError(f"universal-driver seam called: {self.name}")

poison = types.ModuleType("long_game_sdk.sdk.universal_driver")
poison.UniversalDriver = Bomb("UniversalDriver")
poison.armed = Bomb("armed")
poison.arm = Bomb("arm")
poison.write = Bomb("write")
sys.modules[poison.__name__] = poison

real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "universal_driver" or name.endswith(".universal_driver"):
        attempts.append(name)
        return poison
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

from long_game_sdk.sdk.diagnostic_engine import DiagnosticEngine, Exhausted
from long_game_sdk.sdk.diagnostic_session import Hypothesis
from long_game_sdk.sdk.transport_diagnostics import ping_host, tcp_port_probe, visa_list_resources

class Selector:
    def seed_hypotheses(self, identity, symptom):
        return [Hypothesis("transport", "transport", 0.1)]
    def choose_action(self, session, symptom):
        return Exhausted("done")
    def update_hypotheses(self, session, symptom):
        raise AssertionError("no update expected")

class Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""

class Socket:
    def settimeout(self, timeout):
        self.timeout = timeout
    def connect(self, address):
        self.address = address
    def close(self):
        self.closed = True

class Manager:
    def list_resources(self):
        return ()
    def close(self):
        self.closed = True

assert ping_host("192.0.2.10", runner=lambda *a, **k: Completed(), platform_name="Linux").ok
assert tcp_port_probe("192.0.2.10", 5025, socket_factory=lambda *a: Socket()).ok
assert visa_list_resources(resource_manager_factory=Manager).ok
assert DiagnosticEngine(selector=Selector()).run({"id": "fake"}, "offline").outcome == "escalate"
print(json.dumps({"attempts": attempts, "poison_calls": poison_calls}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"attempts": [], "poison_calls": []}


def test_ask_operator_emits_question_and_blank_answer_cannot_advance_loop() -> None:
    selector = AdversarialSelector([AskOperator("Is the LAN indicator illuminated?"), Exhausted("must not skip")])
    events: list[dict[str, Any]] = []
    provider_calls: list[str] = []

    def blank_provider(question: str) -> str:
        provider_calls.append(question)
        return "   "

    engine = DiagnosticEngine(
        selector=selector,  # type: ignore[arg-type]
        operator_answer_provider=blank_provider,
        event_sink=events.append,
        clock=fixed_clock,
    )
    with pytest.raises(NeedOperatorAnswer, match="LAN indicator") as raised:
        engine.run({"id": "dp832-boundary"}, "offline")

    assert raised.value.question == "Is the LAN indicator illuminated?"
    assert raised.value.session.status == "in_progress"
    assert raised.value.iterations == 1
    assert raised.value.rejection_count == 0
    assert provider_calls == ["Is the LAN indicator illuminated?"]
    assert selector.choose_calls == 1
    assert selector.update_calls == 0
    assert [event["event_type"] for event in events] == [
        "hypotheses_seeded",
        "action_selected",
        "operator_question",
        "operator_answer_required",
    ]
    assert events[2]["data"]["question"] == "Is the LAN indicator illuminated?"


def test_ask_operator_rerun_with_explicit_answer_records_turn_before_proceeding() -> None:
    first = AdversarialSelector([AskOperator("Is the LAN indicator illuminated?")])
    with pytest.raises(NeedOperatorAnswer):
        DiagnosticEngine(
            selector=first,  # type: ignore[arg-type]
            operator_answer_provider=lambda _: None,
            clock=fixed_clock,
        ).run({"id": "dp832-boundary"}, "offline")
    assert first.update_calls == 0

    rerun = AdversarialSelector([AskOperator("Is the LAN indicator illuminated?"), Exhausted("answered")])
    result = DiagnosticEngine(
        selector=rerun,  # type: ignore[arg-type]
        operator_answer_provider=lambda _: "No",
        clock=fixed_clock,
    ).run({"id": "dp832-boundary"}, "offline")

    assert rerun.update_calls == 1
    assert result.session.operator_turns[0].question == "Is the LAN indicator illuminated?"
    assert result.session.operator_turns[0].answer == "No"
    assert rerun.choose_calls == 2
    assert result.outcome == "escalate"
    assert result.session.resolution_summary == "answered"


@pytest.mark.parametrize("injected_key", ["runner", "callable"])
def test_disallowed_arguments_cannot_inject_runner_or_callable(injected_key: str) -> None:
    injected_calls: list[str] = []

    def injected(*_: object, **__: object) -> None:
        injected_calls.append(injected_key)

    registry_calls: list[dict[str, object]] = []
    selector = AdversarialSelector(
        [
            ProbeCall("ping_host", {"ip": "192.0.2.10", injected_key: injected}),
            Exhausted("rejected injection"),
        ]
    )
    result = DiagnosticEngine(
        selector=selector,  # type: ignore[arg-type]
        _testing_allow_probe_registry=True,
        probe_registry={"ping_host": lambda **kwargs: registry_calls.append(kwargs)},
        clock=fixed_clock,
    ).run({"id": "dp832-boundary"}, "offline")

    assert injected_calls == []
    assert registry_calls == []
    assert result.rejection_count == 1
    assert result.outcome == "escalate"


def test_probe_registry_cannot_expand_fixed_allowlist() -> None:
    registry_calls: list[str] = []
    selector = AdversarialSelector([ProbeCall("custom_read_only_probe", {}), Exhausted("off-list")])
    result = DiagnosticEngine(
        selector=selector,  # type: ignore[arg-type]
        _testing_allow_probe_registry=True,
        probe_registry={"custom_read_only_probe": lambda: registry_calls.append("called")},
        clock=fixed_clock,
    ).run({"id": "dp832-boundary"}, "offline")

    assert registry_calls == []
    assert result.rejection_count == 1
    assert result.outcome == "escalate"


def _required_hardware_resource(name: str) -> str:
    if os.environ.get("LONG_GAME_RUN_RIGOL_DP832_TIER0") != "1":
        pytest.skip("set LONG_GAME_RUN_RIGOL_DP832_TIER0=1 for opt-in Tier0 hardware checks")
    resource = os.environ.get(name, "").strip()
    if not resource:
        pytest.skip(f"set {name} to explicitly select the Rigol DP832 resource")
    return resource


@pytest.mark.hardware
@pytest.mark.rigol_dp832
def test_hardware_tier0_dp832_ping_only() -> None:
    ip = _required_hardware_resource("RIGOL_DP832_IP")
    result = ping_host(ip)
    assert result.ok, result


@pytest.mark.hardware
@pytest.mark.rigol_dp832
def test_hardware_tier0_dp832_visa_listing_only() -> None:
    resource = _required_hardware_resource("RIGOL_DP832_VISA_RESOURCE")
    result = visa_list_resources(expected_resource=resource)
    assert result.ok, result
    assert result.details["expected_resource_present"] is True
