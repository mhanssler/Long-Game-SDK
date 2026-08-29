from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
import yaml

from long_game_sdk.sdk import diagnostic_case_library
from long_game_sdk.sdk.diagnostic_case_library import DiagnosticCaseLibrary
from long_game_sdk.sdk.diagnostic_engine import AskOperator, Conclude, DiagnosticEngine
from long_game_sdk.sdk.diagnostic_session import DiagnosticSession, Finding, Hypothesis
from long_game_sdk.sdk.transport_diagnostics import ProbeResult


def _resolved_session(
    *,
    instrument_class: str = "oscilloscope",
    symptom: str = "Intermittent noisy waveform",
    root_cause: str = "Loose probe ground",
    fix: str = "Reseat the probe ground lead",
    outcome: str = "resolved",
) -> DiagnosticSession:
    session = DiagnosticSession(
        instrument_identity={"serial": "SCOPE-1", "instrument_class": instrument_class},
        findings=[
            Finding(
                "check_stale_socket",
                {"port": 5025, "ip": "192.0.2.1"},
                {"ok": False, "reason": "reset"},
                datetime(2026, 8, 27, tzinfo=timezone.utc),
            )
        ],
        status="resolved",
        resolution_summary="Confirmed after inspection",
        symptom=symptom,
        symptom_tags=["Waveform", "noisy", "waveform"],
        confirmed_root_cause=root_cause,
        fix_applied=fix,
        outcome=outcome,
    )
    return session


def test_save_resolved_session_is_deterministic_unique_safe_yaml(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    session = _resolved_session()

    first = library.save(session)
    first_bytes = first.read_bytes()
    second = library.save(session)

    assert first == second
    assert first_bytes == second.read_bytes()
    assert first.parent == tmp_path.resolve()
    assert first.name.startswith("case-") and first.suffix == ".yaml"
    case = yaml.safe_load(first_bytes)
    assert case == {
        "schema_version": 1,
        "case_id": first.stem.removeprefix("case-"),
        "instrument_class": "oscilloscope",
        "instrument_identity": {"instrument_class": "oscilloscope", "serial": "SCOPE-1"},
        "symptom": "Intermittent noisy waveform",
        "symptom_tags": ["intermittent", "noisy", "waveform"],
        "findings_summary": [
            {
                "probe_name": "check_stale_socket",
                "args": {"ip": "192.0.2.1", "port": 5025},
                "result": {"ok": False, "reason": "reset"},
            }
        ],
        "confirmed_root_cause": "Loose probe ground",
        "fix_applied": "Reseat the probe ground lead",
        "recommended_fix": None,
        "outcome": "resolved",
    }
    assert "!!python" not in first.read_text(encoding="utf-8")

    changed = _resolved_session(root_cause="Damaged probe")
    assert library.save(changed) != first


def test_save_rejects_unresolved_cases_and_never_accepts_a_filename(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    session = _resolved_session()
    session.status = "in_progress"

    with pytest.raises(ValueError, match="resolved"):
        library.save(session)
    with pytest.raises(TypeError):
        library.save(session, filename="../escape.yaml")  # type: ignore[call-arg]
    assert list(tmp_path.glob("*.yaml")) == []
    assert not (tmp_path.parent / "escape.yaml").exists()


def test_retrieve_matches_class_and_normalized_keywords_with_stable_ties(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    most_relevant = library.save(_resolved_session(symptom="Ethernet connection drops intermittently"))
    tie_a = library.save(_resolved_session(symptom="Ethernet timeout", root_cause="A"))
    tie_b = library.save(_resolved_session(symptom="Ethernet refused", root_cause="B"))
    library.save(_resolved_session(instrument_class="power_supply", symptom="Ethernet connection drops"))

    cases = library.retrieve(
        {"instrument_class": " OSCILLOSCOPE ", "serial": "new"},
        "Intermittent ethernet CONNECTION drop",
        limit=3,
    )

    assert [case["case_id"] for case in cases][0] == most_relevant.stem.removeprefix("case-")
    tied_ids = [tie_a.stem.removeprefix("case-"), tie_b.stem.removeprefix("case-")]
    assert [case["case_id"] for case in cases][1:] == sorted(tied_ids)
    assert all(case["instrument_class"] == "oscilloscope" for case in cases)
    assert library.retrieve({"instrument_class": "oscilloscope"}, "ethernet", limit=0) == []


def test_retrieve_skips_malformed_unsafe_and_wrong_shape_yaml(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    expected = library.save(_resolved_session(symptom="display flickers"))
    (tmp_path / "broken.yaml").write_text("[not: valid", encoding="utf-8")
    (tmp_path / "unsafe.yaml").write_text("!!python/object:builtins.object {}", encoding="utf-8")
    (tmp_path / "list.yaml").write_text("- not\n- a\n- case\n", encoding="utf-8")

    cases = library.retrieve("oscilloscope", "flickering display", limit=5)

    assert [case["case_id"] for case in cases] == [expected.stem.removeprefix("case-")]


class RecordingSelector:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cases: list[Mapping[str, Any]] = []

    def set_similar_cases(self, cases: Sequence[Mapping[str, Any]]) -> None:
        self.events.append("set_cases")
        self.cases = list(cases)

    def seed_hypotheses(self, identity: dict[str, str], symptom: str) -> Sequence[Hypothesis]:
        self.events.append("seed")
        return [Hypothesis("ground", "Loose probe ground", 0.95, "confirmed")]

    def choose_action(self, session: DiagnosticSession, symptom: str) -> AskOperator | Conclude:
        self.events.append("choose")
        if not session.operator_turns:
            return AskOperator("Is the waveform clean after inspecting the probe ground?")
        return Conclude("ground", "Reseat ground", 0.95)

    def update_hypotheses(self, session: DiagnosticSession, symptom: str) -> Sequence[Hypothesis]:
        self.events.append("update")
        return [Hypothesis("ground", "Loose probe ground", 0.95, "confirmed")]


class RecordingLibrary:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.saved: list[DiagnosticSession] = []

    def retrieve(
        self, instrument_identity: Mapping[str, str] | str, symptom: str, *, limit: int
    ) -> Sequence[Mapping[str, Any]]:
        self.events.append(f"retrieve:{limit}")
        assert instrument_identity == {"instrument_class": "oscilloscope", "serial": "new"}
        assert symptom == "Noisy waveform"
        return [{"case_id": "old-case", "confirmed_root_cause": "Loose ground"}]

    def save(self, session: DiagnosticSession) -> Path:
        self.events.append("save")
        self.saved.append(session)
        return Path("unused.yaml")


def test_engine_retrieves_before_seed_but_does_not_persist_pending_recommendation() -> None:
    events: list[str] = []
    selector = RecordingSelector(events)
    library = RecordingLibrary(events)

    result = DiagnosticEngine(
        selector=selector,
        case_library=library,
        similar_case_limit=4,
        operator_answer_provider=lambda _: "No",
    ).run({"instrument_class": "oscilloscope", "serial": "new"}, "Noisy waveform")

    assert events == ["retrieve:4", "set_cases", "seed", "choose", "update", "choose"]
    assert selector.cases[0]["case_id"] == "old-case"
    assert library.saved == []
    assert result.session.symptom == "Noisy waveform"
    assert result.session.confirmed_root_cause == "Loose probe ground"
    assert result.session.recommended_fix == "Reseat ground"
    assert result.session.outcome == "recommended_fix_pending_operator_action"
    assert result.session.status == "in_progress"


def test_injected_writer_callback_is_not_called_for_pending_recommendation() -> None:
    events: list[str] = []
    selector = RecordingSelector(events)
    written: deque[DiagnosticSession] = deque()

    DiagnosticEngine(
        selector=selector,
        case_library_writer=written.append,
        operator_answer_provider=lambda _: "No",
    ).run(
        {"instrument_class": "oscilloscope"}, "noise"
    )

    assert list(written) == []


def test_save_accepts_genuinely_operator_confirmed_resolved_session(tmp_path: Path) -> None:
    session = _resolved_session()
    session.fix_applied = "Operator reseated the ground lead and verified a clean waveform"
    session.recommended_fix = None

    saved = DiagnosticCaseLibrary(tmp_path).save(session)

    assert yaml.safe_load(saved.read_text(encoding="utf-8"))["fix_applied"] == session.fix_applied


@pytest.mark.parametrize(
    ("outcome", "fix_applied"),
    [
        ("recommended_fix_pending_operator_action", "Operator may apply this later"),
        ("resolved", None),
        ("resolved", "   "),
    ],
)
def test_save_rejects_pending_or_missing_applied_fix(
    tmp_path: Path, outcome: str, fix_applied: str | None
) -> None:
    session = _resolved_session(outcome=outcome)
    session.fix_applied = fix_applied
    with pytest.raises(ValueError, match="resolved|fix applied"):
        DiagnosticCaseLibrary(tmp_path).save(session)


def test_retrieve_excludes_zero_overlap_and_rejects_semantically_invalid_case(tmp_path: Path) -> None:
    import hashlib

    library = DiagnosticCaseLibrary(tmp_path)
    saved = library.save(_resolved_session(symptom="display flickers"))
    assert library.retrieve("oscilloscope", "ethernet timeout", limit=5) == []
    case = yaml.safe_load(saved.read_text(encoding="utf-8"))
    case["confirmed_root_cause"] = ""
    body = {key: value for key, value in case.items() if key not in {"schema_version", "case_id"}}
    case["case_id"] = hashlib.sha256(yaml.safe_dump(body, allow_unicode=True, sort_keys=True).encode()).hexdigest()
    invalid = tmp_path / f"case-{case['case_id']}.yaml"
    invalid.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    cases = library.retrieve("oscilloscope", "display flickers", limit=5)
    assert [item["case_id"] for item in cases] == [saved.stem.removeprefix("case-")]


def test_real_probe_result_is_saved_structurally(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    session = _resolved_session()
    session.findings[0].result = ProbeResult("tcp_port", "192.0.2.1:5025", False, "refused", {"port": 5025})
    case = yaml.safe_load(library.save(session).read_text(encoding="utf-8"))
    assert case["findings_summary"][0]["result"] == {
        "probe": "tcp_port",
        "target": "192.0.2.1:5025",
        "ok": False,
        "status": "refused",
        "details": {"port": 5025},
        "error": None,
        "duration_ms": 0.0,
    }


def test_case_io_rejects_symlink_and_tampered_existing_destination(tmp_path: Path) -> None:
    library = DiagnosticCaseLibrary(tmp_path)
    session = _resolved_session()
    path = library.save(session)
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(FileExistsError, match="does not contain"):
        library.save(session)
    path.unlink()
    outside = tmp_path.parent / "outside-case.yaml"
    outside.write_text("outside", encoding="utf-8")
    path.symlink_to(outside)
    with pytest.raises(FileExistsError):
        library.save(session)
    assert outside.read_text(encoding="utf-8") == "outside"


def test_directory_fsync_is_skipped_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(diagnostic_case_library.os, "name", "nt")

    def unexpected_open(*args: object, **kwargs: object) -> int:
        raise AssertionError("Windows directory handles must not be opened with os.open")

    monkeypatch.setattr(diagnostic_case_library.os, "open", unexpected_open)

    diagnostic_case_library._fsync_directory(tmp_path)
