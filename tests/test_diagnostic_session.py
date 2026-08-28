from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest
import yaml

from long_game_sdk.sdk.diagnostic_session import (
    DiagnosticSession,
    Finding,
    Hypothesis,
    OperatorTurn,
)


NOW = datetime(2026, 8, 27, 18, 30, tzinfo=timezone.utc)


def make_session() -> DiagnosticSession:
    return DiagnosticSession(
        instrument_identity={
            "manufacturer": "RIGOL TECHNOLOGIES",
            "model": "DP832",
            "serial": "DP8A000001",
        },
        hypotheses=[
            Hypothesis(
                id="h-1",
                description="The output is disabled",
                score=0.75,
                status="confirmed",
            )
        ],
        findings=[
            Finding(
                probe_name="query_output",
                args={"channel": 1},
                result={"enabled": False},
                timestamp=NOW,
            )
        ],
        operator_turns=[
            OperatorTurn(
                question="Is the load connected?",
                answer="No",
                timestamp=NOW,
            )
        ],
        status="resolved",
        resolution_summary="Enable channel one after connecting the load.",
    )


def test_session_serializes_nested_dataclasses_to_plain_data() -> None:
    session = make_session()

    data = session.to_dict()

    assert data == {
        "instrument_identity": {
            "manufacturer": "RIGOL TECHNOLOGIES",
            "model": "DP832",
            "serial": "DP8A000001",
        },
        "hypotheses": [
            {
                "id": "h-1",
                "description": "The output is disabled",
                "score": 0.75,
                "status": "confirmed",
            }
        ],
        "findings": [
            {
                "probe_name": "query_output",
                "args": {"channel": 1},
                "result": {"enabled": False},
                "timestamp": "2026-08-27T18:30:00+00:00",
            }
        ],
        "operator_turns": [
            {
                "question": "Is the load connected?",
                "answer": "No",
                "timestamp": "2026-08-27T18:30:00+00:00",
            }
        ],
        "status": "resolved",
        "resolution_summary": "Enable channel one after connecting the load.",
    }
    assert yaml.safe_load(session.to_yaml()) == data


def test_session_emits_one_sdk_style_jsonl_event() -> None:
    session = make_session()
    output = io.StringIO()

    event = session.emit_event(output, timestamp=NOW)

    assert output.getvalue().endswith("\n")
    assert output.getvalue().count("\n") == 1
    assert json.loads(output.getvalue()) == event
    assert event == {
        "timestamp": "2026-08-27T18:30:00+00:00",
        "event_type": "diagnostic_session",
        "data": session.to_dict(),
    }


@pytest.mark.parametrize("status", ["pending", "closed", "unknown"])
def test_invalid_hypothesis_status_is_rejected(status: str) -> None:
    with pytest.raises(ValueError, match="hypothesis status"):
        Hypothesis(id="h-1", description="Bad status", score=0.5, status=status)  # type: ignore[arg-type]


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_hypothesis_score_must_be_a_probability(score: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        Hypothesis(id="h-1", description="Bad score", score=score)


@pytest.mark.parametrize("status", ["pending", "closed", "unknown"])
def test_invalid_session_status_is_rejected(status: str) -> None:
    with pytest.raises(ValueError, match="session status"):
        DiagnosticSession(instrument_identity={}, status=status)  # type: ignore[arg-type]


def test_mutable_session_collections_are_not_shared() -> None:
    first = DiagnosticSession(instrument_identity={})
    second = DiagnosticSession(instrument_identity={})

    first.hypotheses.append(Hypothesis(id="h-1", description="First", score=0.5))

    assert second.hypotheses == []
