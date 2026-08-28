from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from long_game_sdk.sdk import diagnostic_engine


class ScriptedBackend:
    responses: list[str] = []
    instances: list["ScriptedBackend"] = []

    def __init__(self, *, endpoint: str, model: str, timeout: float, allow_remote: bool = False) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.allow_remote = allow_remote
        self.calls: list[tuple[Sequence[Mapping[str, str]], Mapping[str, Any]]] = []
        type(self).instances.append(self)

    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        json_schema: Mapping[str, Any],
    ) -> str:
        self.calls.append((messages, json_schema))
        return type(self).responses.pop(0)


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_cli_runs_interactively_and_writes_complete_report_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    ScriptedBackend.responses = [
        json.dumps(
            {
                "hypotheses": [
                    {
                        "id": "cable",
                        "description": "Ethernet cable is disconnected",
                        "score": 0.6,
                        "status": "open",
                    }
                ]
            }
        ),
        json.dumps({"action": "ask_operator", "question": "Is the LAN LED illuminated?"}),
        json.dumps(
            {
                "hypotheses": [
                    {
                        "id": "cable",
                        "description": "Ethernet cable is disconnected",
                        "score": 0.96,
                        "status": "confirmed",
                    }
                ]
            }
        ),
        json.dumps(
            {
                "action": "conclude",
                "hypothesis_id": "cable",
                "recommended_fix": "Replace or reseat the Ethernet cable.",
                "confidence": 0.96,
            }
        ),
    ]
    ScriptedBackend.instances = []
    answers: list[str] = []
    monkeypatch.setattr(diagnostic_engine, "HTTPJSONBackend", ScriptedBackend)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: answers.append(prompt) or "No, it is dark",
    )
    output = tmp_path / "dp832-incident"

    exit_code = diagnostic_engine.main(
        [
            "--identity",
            "rigol_dp832",
            "--resource",
            "TCPIP::192.168.1.50::INSTR",
            "--symptom",
            "Cannot connect over Ethernet",
            "-o",
            str(output),
            "--model-endpoint",
            "http://local.invalid/api/chat",
            "--model",
            "test-model",
            "--model-timeout",
            "0.25",
            "--max-iterations",
            "3",
            "--confidence",
            "0.9",
            "--case-library",
            str(tmp_path / "cases"),
        ]
    )

    assert exit_code == 0
    assert len(answers) == 1
    assert "Do not run commands" in answers[0]
    assert answers[0].endswith("Is the LAN LED illuminated? ")
    assert {path.name for path in output.iterdir()} >= {
        "report.md",
        "events.jsonl",
        "session.yaml",
    }
    report = (output / "report.md").read_text()
    assert "# Long Game Diagnostic Report" in report
    assert "rigol_dp832" in report
    assert "TCPIP::192.168.1.50::INSTR" in report
    assert "Ethernet cable is disconnected" in report
    assert "No, it is dark" in report
    assert "Replace or reseat the Ethernet cable." in report
    assert "recommended_fix_pending_operator_action" in report
    event_types = [event["event_type"] for event in _events(output / "events.jsonl")]
    assert "operator_turn" in event_types
    assert event_types[-1] == "diagnosis_final"
    session = yaml.safe_load((output / "session.yaml").read_text())
    assert session["status"] == "in_progress"
    case_paths = list((tmp_path / "cases").glob("case-*.yaml"))
    assert case_paths == []  # pending recommendations are not resolved memory
    backend = ScriptedBackend.instances[0]
    assert backend.timeout == 0.25
    seed_prompt = backend.calls[0][0][-1]["content"]
    assert "RIGOL.*DP832.*" in seed_prompt  # matching schemas/rigol_dp832.yaml was loaded


def test_unavailable_backend_is_valid_escalation_with_actionable_artifacts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    class OfflineBackend(ScriptedBackend):
        def generate_json(self, messages, json_schema):  # type: ignore[no-untyped-def]
            raise OSError("connection refused secret detail")

    monkeypatch.setattr(diagnostic_engine, "HTTPJSONBackend", OfflineBackend)
    output = tmp_path / "offline"

    exit_code = diagnostic_engine.main(
        [
            "--identity",
            "unknown_meter",
            "--resource",
            "TCPIP::192.0.2.10::INSTR",
            "--symptom",
            "unreachable",
            "-o",
            str(output),
            "--model-timeout",
            "0.01",
        ]
    )

    assert exit_code == 0
    report = (output / "report.md").read_text()
    assert "escalated" in report.lower()
    assert "local model endpoint" in report.lower()
    assert "model availability" in report.lower()
    assert "Traceback" not in report
    events = _events(output / "events.jsonl")
    assert events[-2]["event_type"] == "diagnostic_error"
    assert events[-1]["event_type"] == "diagnosis_final"
    assert events[-1]["data"]["outcome"] == "escalate"
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


def test_invalid_numeric_cli_input_is_nonzero_and_creates_no_output(
    tmp_path: Path, capsys
) -> None:
    output = tmp_path / "invalid"

    exit_code = diagnostic_engine.main(
        [
            "--identity",
            "rigol_dp832",
            "--resource",
            "TCPIP::192.0.2.10::INSTR",
            "--symptom",
            "offline",
            "-o",
            str(output),
            "--confidence",
            "1.5",
        ]
    )

    assert exit_code == 2
    assert not output.exists()
    assert "confidence" in capsys.readouterr().err.lower()


def test_parser_has_no_execute_or_mutating_driver_surface() -> None:
    parser = diagnostic_engine.build_parser()
    assert parser.get_default("case_library") == "diagnostics_cases"
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--execute" not in option_strings
    source = Path(diagnostic_engine.__file__).read_text()
    assert "universal_driver" not in source
    assert "sdk.drivers" not in source
