"""Serializable data model for instrument diagnostic sessions.

The model deliberately keeps transport concerns small: a session can be rendered
as plain Python data or YAML, and emitted as one structured JSON Lines event.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TextIO

import yaml

HypothesisStatus = Literal["open", "confirmed", "ruled_out"]
SessionStatus = Literal["in_progress", "resolved", "escalated"]

_HYPOTHESIS_STATUSES = frozenset({"open", "confirmed", "ruled_out"})
_SESSION_STATUSES = frozenset({"in_progress", "resolved", "escalated"})


@dataclass
class Hypothesis:
    """A possible explanation and its current probability-like score."""

    id: str
    description: str
    score: float
    status: HypothesisStatus = "open"

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("hypothesis score must be between 0 and 1")
        if self.status not in _HYPOTHESIS_STATUSES:
            raise ValueError(f"invalid hypothesis status: {self.status}")

    @property
    def prior(self) -> float:
        """Bayesian terminology alias for the hypothesis score."""

        return self.score

    @property
    def likelihood_score(self) -> float:
        """Explicit terminology alias for the hypothesis score."""

        return self.score


@dataclass
class Finding:
    """The result of running a diagnostic probe."""

    probe_name: str
    args: dict[str, Any]
    result: Any
    timestamp: datetime


@dataclass
class OperatorTurn:
    """A question asked during diagnosis and the operator's answer."""

    question: str
    answer: str
    timestamp: datetime


@dataclass
class DiagnosticSession:
    """Complete, incrementally mutable state of an instrument diagnosis."""

    instrument_identity: dict[str, str]
    hypotheses: list[Hypothesis] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    operator_turns: list[OperatorTurn] = field(default_factory=list)
    status: SessionStatus = "in_progress"
    resolution_summary: str | None = None
    # Optional case-memory metadata. Defaults preserve construction and
    # serialization compatibility for callers created before case memory.
    symptom: str | None = None
    symptom_tags: list[str] = field(default_factory=list)
    confirmed_root_cause: str | None = None
    recommended_fix: str | None = None
    fix_applied: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _SESSION_STATUSES:
            raise ValueError(f"invalid diagnostic session status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON/YAML-safe representation of this session."""

        serialized = _serialize(asdict(self))
        # ``asdict`` always returns a dictionary for this dataclass. Keeping the
        # check explicit protects this public method if serialization changes.
        if not isinstance(serialized, dict):  # pragma: no cover - defensive
            raise TypeError("diagnostic session did not serialize to a mapping")
        # Keep the historical serialized shape when case metadata is unused.
        for key in (
            "symptom",
            "symptom_tags",
            "confirmed_root_cause",
            "recommended_fix",
            "fix_applied",
            "outcome",
        ):
            if serialized.get(key) in (None, []):
                serialized.pop(key, None)
        return serialized

    def to_yaml(self) -> str:
        """Render the session as safe, stable-order YAML."""

        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    def to_event(self, *, timestamp: datetime | None = None) -> dict[str, Any]:
        """Build an SDK-style structured logging event."""

        emitted_at = timestamp or datetime.now(timezone.utc)
        return {
            "timestamp": emitted_at.isoformat(),
            "event_type": "diagnostic_session",
            "data": self.to_dict(),
        }

    def to_jsonl(self, *, timestamp: datetime | None = None) -> str:
        """Render one compact JSON Lines record, including its trailing newline."""

        return json.dumps(self.to_event(timestamp=timestamp), separators=(",", ":")) + "\n"

    def emit_event(
        self,
        destination: TextIO | str | Path,
        *,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Write one JSON-L event to a stream or append it to a path.

        The returned dictionary is the exact event that was written, which is
        useful to callers that also forward structured events elsewhere.
        """

        event = self.to_event(timestamp=timestamp)
        line = json.dumps(event, separators=(",", ":")) + "\n"
        if isinstance(destination, (str, Path)):
            with Path(destination).open("a", encoding="utf-8") as stream:
                stream.write(line)
        else:
            destination.write(line)
        return event


def _serialize(value: Any) -> Any:
    """Recursively convert supported model values into serialization primitives."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value
