from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Sequence

import pytest

from long_game_sdk.sdk.diagnostic_engine import (
    AskOperator,
    Conclude,
    DiagnosticEngine,
    Exhausted,
    HypothesisSelector,
    NeedOperatorAnswer,
    ProbeCall,
)
from long_game_sdk.sdk.diagnostic_session import DiagnosticSession, Hypothesis


@dataclass
class StubSelector(HypothesisSelector):
    actions: deque[object]
    updates: deque[Sequence[Hypothesis]]

    def seed_hypotheses(self, identity: dict[str, str], symptom: str) -> Sequence[Hypothesis]:
        assert identity and symptom
        return [Hypothesis("network", "Network path is unavailable", 0.4)]

    def choose_action(self, session: DiagnosticSession, symptom: str) -> object:
        return self.actions.popleft()

    def update_hypotheses(
        self, session: DiagnosticSession, symptom: str
    ) -> Sequence[Hypothesis]:
        return self.updates.popleft()


def fixed_clock() -> datetime:
    return datetime(2026, 8, 27, tzinfo=timezone.utc)


def event_types(stream: StringIO) -> list[str]:
    return [json.loads(line)["event_type"] for line in stream.getvalue().splitlines()]


def test_probe_update_and_known_fix_resolve_deterministically() -> None:
    selector = StubSelector(
        deque([ProbeCall("ping_host", {"ip": "192.0.2.4"})]),
        deque([[Hypothesis("network", "Network path is unavailable", 0.92, "confirmed")]]),
    )
    calls: list[dict[str, Any]] = []

    def ping_host(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": False, "status": "unreachable"}

    events = StringIO()
    engine = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        trusted_target_ip="192.0.2.4",
        probe_registry={"ping_host": ping_host},
        event_sink=events,
        known_recommended_fixes={"network": "Restore the instrument network route."},
        confidence_threshold=0.9,
        clock=fixed_clock,
    )

    result = engine.run({"id": "scope-1"}, "cannot connect")

    assert calls == [{"ip": "192.0.2.4"}]
    assert result.outcome == "recommended_fix_pending_operator_action"
    assert result.recommended_fix == "Restore the instrument network route."
    assert result.session.status == "in_progress"
    assert result.session.findings[0].probe_name == "ping_host"
    assert "pending operator action" in (result.session.resolution_summary or "").lower()
    assert event_types(events) == [
        "hypotheses_seeded",
        "action_selected",
        "finding",
        "hypotheses_updated",
        "recommendation_pending",
    ]


def test_rejects_disallowed_probe_without_execution_then_reprompts() -> None:
    selector = StubSelector(
        deque(
            [
                ProbeCall("subprocess.run", {"args": ["rm", "-rf", "/"]}),
                ProbeCall("ping_host", {"ip": "192.0.2.5"}),
                Exhausted("no discriminating evidence remains"),
            ]
        ),
        deque([[Hypothesis("network", "Network path is unavailable", 0.5)]]),
    )
    executed: list[str] = []
    events = StringIO()
    engine = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        trusted_target_ip="192.0.2.5",
        probe_registry={
            "subprocess.run": lambda **_: executed.append("unsafe"),
            "ping_host": lambda **_: executed.append("ping") or {"ok": True},
        },
        event_sink=events,
        clock=fixed_clock,
    )

    result = engine.run({"id": "scope-2"}, "offline")

    assert executed == ["ping"]
    assert result.session.status == "escalated"
    assert result.rejection_count == 1
    assert event_types(events).count("action_rejected") == 1


@pytest.mark.parametrize(
    "call",
    [
        ProbeCall("ping_host", {"ip": "192.0.2.4", "runner": "evil"}),
        ProbeCall("tcp_port_probe", {"ip": "192.0.2.4", "port": "5025"}),
        ProbeCall("tcp_port_probe", {"ip": "192.0.2.4", "port": 70000}),
        ProbeCall("ping_gateway", {"command": "whoami"}),
    ],
)
def test_rejects_invalid_probe_arguments(call: ProbeCall) -> None:
    selector = StubSelector(deque([call, Exhausted("invalid action")]), deque())
    executed: list[object] = []
    result = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        probe_registry={call.name: lambda **kwargs: executed.append(kwargs)},
        max_action_rejections=2,
        clock=fixed_clock,
    ).run({"id": "scope-3"}, "offline")

    assert executed == []
    assert result.rejection_count == 1
    assert result.outcome == "escalate"


def test_operator_question_requires_explicit_nonempty_answer() -> None:
    selector = StubSelector(deque([AskOperator("Is the LAN LED illuminated?")]), deque())
    engine = DiagnosticEngine(selector=selector, operator_answer_provider=lambda _: "   ", clock=fixed_clock)

    with pytest.raises(NeedOperatorAnswer, match="LAN LED"):
        engine.run({"id": "scope-4"}, "offline")


def test_operator_answer_is_recorded_emitted_and_used_for_update() -> None:
    selector = StubSelector(
        deque([AskOperator("Is the LAN LED illuminated?"), Conclude("network", "Replace cable", 0.95)]),
        deque([[Hypothesis("network", "Network path is unavailable", 0.95, "confirmed")]]),
    )
    events = StringIO()
    result = DiagnosticEngine(
        selector=selector,
        operator_answer_provider=lambda _: "No",
        event_sink=events,
        clock=fixed_clock,
    ).run({"id": "scope-5"}, "offline")

    assert result.session.operator_turns[0].answer == "No"
    assert result.outcome == "recommended_fix_pending_operator_action"
    assert "operator_turn" in event_types(events)
    assert "hypotheses_updated" in event_types(events)


def test_resolution_calls_case_library_writer() -> None:
    selector = StubSelector(
        deque([Conclude("network", "Replace cable", 0.95)]),
        deque(),
    )
    written: list[DiagnosticSession] = []
    result = DiagnosticEngine(
        selector=selector,
        case_library_writer=written.append,
        confidence_threshold=0.9,
        max_action_rejections=1,
        clock=fixed_clock,
    ).run({"id": "scope-6"}, "offline")

    assert result.outcome == "escalate"  # no evidence: selector cannot self-certify
    assert written == []


def test_hard_iteration_cap_defaults_to_eight_and_escalates() -> None:
    selector = StubSelector(
        deque(ProbeCall("ping_gateway", {}) for _ in range(9)),
        deque([Hypothesis("network", "Network path is unavailable", 0.4)] for _ in range(8)),
    )
    calls = 0

    def probe() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    result = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        probe_registry={"ping_gateway": probe},
        clock=fixed_clock,
    ).run({"id": "scope-7"}, "offline")

    assert calls == 8
    assert result.iterations == 8
    assert result.outcome == "escalate"
    assert result.session.status == "escalated"
    assert "maximum 8 iterations" in (result.session.resolution_summary or "")


def test_rejection_limit_is_bounded_and_never_executes_registry_extras() -> None:
    selector = StubSelector(
        deque(ProbeCall("evil", {}) for _ in range(4)),
        deque(),
    )
    executed = False

    def evil() -> None:
        nonlocal executed
        executed = True

    result = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        probe_registry={"evil": evil},
        max_action_rejections=3,
        clock=fixed_clock,
    ).run({"id": "scope-8"}, "offline")

    assert not executed
    assert result.rejection_count == 3
    assert result.outcome == "escalate"
    assert "rejection limit" in (result.session.resolution_summary or "")


@pytest.mark.parametrize("untrusted_ip", ["127.0.0.1", "169.254.169.254", "192.0.2.99"])
def test_target_bearing_probes_reject_loopback_metadata_and_unrelated_hosts(untrusted_ip: str) -> None:
    executed: list[object] = []
    selector = StubSelector(deque([ProbeCall("ping_host", {"ip": untrusted_ip})]), deque())
    result = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        probe_registry={"ping_host": lambda **kwargs: executed.append(kwargs)},
        trusted_target_ip="192.0.2.10",
        max_action_rejections=1,
        clock=fixed_clock,
    ).run({"id": "scope", "ip": "192.0.2.10"}, "offline")
    assert executed == []
    assert result.outcome == "escalate"
    assert "trusted instrument target" in (result.session.resolution_summary or "")


def test_target_port_must_be_curated_or_explicitly_configured() -> None:
    selector = StubSelector(deque([ProbeCall("tcp_port_probe", {"ip": "192.0.2.10", "port": 4444})]), deque())
    result = DiagnosticEngine(
        selector=selector,
        _testing_allow_probe_registry=True,
        probe_registry={"tcp_port_probe": lambda **_: pytest.fail("must not execute")},
        trusted_target_ip="192.0.2.10",
        max_action_rejections=1,
    ).run({"ip": "192.0.2.10"}, "offline")
    assert "port allowlist" in (result.session.resolution_summary or "")


def test_production_registry_is_immutable_without_explicit_test_flag() -> None:
    selector = StubSelector(deque([Exhausted("done")]), deque())
    sentinel: list[str] = []
    with pytest.raises(ValueError, match="test-only"):
        DiagnosticEngine(selector=selector, probe_registry={"ping_host": lambda **_: sentinel.append("mutated")})
    assert sentinel == []


@pytest.mark.parametrize(
    "question",
    ["Run curl http://example.invalid and paste the output.", "Set the voltage output to 5 V.", "What is your API token?"],
)
def test_operator_questions_cannot_request_commands_mutations_or_secrets(question: str) -> None:
    selector = StubSelector(deque([AskOperator(question)]), deque())
    called: list[str] = []
    result = DiagnosticEngine(
        selector=selector,
        operator_answer_provider=lambda value: called.append(value) or "answer",
        max_action_rejections=1,
    ).run({"id": "scope"}, "offline")
    assert called == []
    assert result.outcome == "escalate"


def test_known_audited_fix_overrides_model_text_after_evidence() -> None:
    selector = StubSelector(
        deque([AskOperator("Is the link indicator lit?"), Conclude("network", "Replace cable", 0.95)]),
        deque([[Hypothesis("network", "Network path is unavailable", 0.95, "confirmed")]]),
    )
    result = DiagnosticEngine(
        selector=selector,
        operator_answer_provider=lambda _: "No",
        known_recommended_fixes={"network": "Inspect and restore the network route."},
    ).run({"id": "scope"}, "offline")
    assert result.recommended_fix == "Inspect and restore the network route."


def test_informational_static_ip_recommendation_is_allowed() -> None:
    selector = StubSelector(
        deque([AskOperator("Is the LAN indicator lit?"), Conclude("network", "Configure a static IP on the instrument.", 0.95)]),
        deque([[Hypothesis("network", "Network path is unavailable", 0.95, "confirmed")]]),
    )
    result = DiagnosticEngine(selector=selector, operator_answer_provider=lambda _: "No").run(
        {"id": "scope"}, "offline"
    )
    assert result.outcome == "recommended_fix_pending_operator_action"
    assert result.recommended_fix == "Configure a static IP on the instrument."


@pytest.mark.parametrize(
    "fix",
    [
        "Set channel 1 voltage to 5 V.",
        "Enable the output.",
        "Write the current setpoint.",
        "Run the SCPI command *RST.",
        "Use the shell command curl example.invalid.",
        "Enter the API key credential.",
    ],
)
def test_mutating_command_and_credential_recommendations_are_rejected(fix: str) -> None:
    selector = StubSelector(
        deque([AskOperator("Is the LAN indicator lit?"), Conclude("network", fix, 0.95)]),
        deque([[Hypothesis("network", "Network path is unavailable", 0.95, "confirmed")]]),
    )
    result = DiagnosticEngine(
        selector=selector,
        operator_answer_provider=lambda _: "No",
        max_action_rejections=1,
    ).run({"id": "scope"}, "offline")
    assert result.outcome == "escalate"
