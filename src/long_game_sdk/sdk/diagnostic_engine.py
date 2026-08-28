"""Deterministic, sandboxed differential-diagnosis loop.

The selector is deliberately a policy object, not an executor.  It can propose
only the typed actions below; this module validates every proposal against a
closed transport-probe schema before looking it up in the injected registry.
Consequently, putting an unsafe callable in a registry does not make it
selectable.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import html
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Iterable, Literal, Mapping, NoReturn, Protocol, Sequence, TextIO, cast, runtime_checkable

import yaml

from .diagnostic_case_library import DiagnosticCaseLibrary, DiagnosticCaseStore
from .diagnostic_session import DiagnosticSession, Finding, Hypothesis, OperatorTurn
from .transport_diagnostics import (
    arp_lookup,
    check_stale_socket,
    compare_to_last_known_good,
    ping_gateway,
    ping_host,
    tcp_port_probe,
    visa_list_resources,
)


@dataclass(frozen=True, slots=True)
class ProbeCall:
    """Request one named, read-only transport probe."""

    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AskOperator:
    """Request explicit evidence that cannot safely be gathered automatically."""

    question: str


@dataclass(frozen=True, slots=True)
class Conclude:
    """Recommend a known fix for a sufficiently likely hypothesis."""

    hypothesis_id: str
    recommended_fix: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Exhausted:
    """Signal that no useful permitted action remains."""

    reason: str


DiagnosticAction = ProbeCall | AskOperator | Conclude | Exhausted


@runtime_checkable
class HypothesisSelector(Protocol):
    """Policy interface used by :class:`DiagnosticEngine`.

    Implementations may use rules, a replay fixture, or an LLM.  They receive
    state and return data only; they never receive the probe registry.
    """

    def seed_hypotheses(self, identity: dict[str, str], symptom: str) -> Sequence[Hypothesis]: ...

    def choose_action(self, session: DiagnosticSession, symptom: str) -> DiagnosticAction: ...

    def update_hypotheses(self, session: DiagnosticSession, symptom: str) -> Sequence[Hypothesis]: ...


@runtime_checkable
class SimilarCaseAwareSelector(Protocol):
    """Optional selector extension for retrieved diagnostic context."""

    def set_similar_cases(self, cases: Sequence[Mapping[str, Any]]) -> None: ...


class NeedOperatorAnswer(RuntimeError):
    """Controlled pause raised when a question has no explicit non-empty answer."""

    def __init__(
        self,
        question: str,
        *,
        session: DiagnosticSession,
        iterations: int,
        rejection_count: int,
    ) -> None:
        self.question = question
        self.session = session
        self.iterations = iterations
        self.rejection_count = rejection_count
        super().__init__(f"operator answer required: {question}")


ResolutionOutcome = Literal["resolved", "recommended_fix_pending_operator_action", "escalate"]


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """Final engine result, including loop and sandbox metrics."""

    session: DiagnosticSession
    outcome: ResolutionOutcome
    iterations: int
    rejection_count: int
    hypothesis_id: str | None = None
    recommended_fix: str | None = None
    confidence: float | None = None


Probe = Callable[..., Any]
OperatorAnswerProvider = Callable[[str], str | None]
CaseLibraryWriter = Callable[[DiagnosticSession], None]
Clock = Callable[[], datetime]

MAX_OPERATOR_QUESTION_LENGTH = 500
MAX_OPERATOR_ANSWER_LENGTH = 2_000
MAX_SCHEMA_BYTES = 1_048_576
_IDENTITY_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_UNSAFE_TEXT = re.compile(
    r"(?is)(?:\b(?:set|change|adjust|enable|disable|turn\s+on|turn\s+off|write|program|configure)\b.{0,50}"
    r"\b(?:output|setpoint|voltage|current|channel)\b|\b(?:scpi|shell|terminal|command|password|passwd|token|api[_ -]?key|credential|secret)\b|"
    r"(?:^|\s)(?:sudo|ssh|curl|wget|bash|sh|powershell|cmd(?:\.exe)?|python)\s)",
)

class StructuredEventEmitter(Protocol):
    """Object sink accepted in addition to callbacks and JSONL streams."""

    def emit(self, event: dict[str, Any]) -> None: ...


EventSink = Callable[[dict[str, Any]], None] | TextIO | StructuredEventEmitter


# This is intentionally closed.  Registry keys cannot expand it.
_ALLOWED_ARGUMENTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "ping_host": (frozenset({"ip"}), frozenset()),
    "ping_gateway": (frozenset(), frozenset()),
    "arp_lookup": (
        frozenset({"ip"}),
        frozenset({"expected_vendor_prefix", "expected_oui_prefix"}),
    ),
    "tcp_port_probe": (frozenset({"ip", "port"}), frozenset({"timeout"})),
    "visa_list_resources": (
        frozenset(),
        frozenset({"expected_ip", "expected_resource", "timeout"}),
    ),
    "check_stale_socket": (frozenset({"ip", "port"}), frozenset({"timeout"})),
    "compare_to_last_known_good": (frozenset({"identity"}), frozenset({"timeout"})),
}

_DEFAULT_REGISTRY: dict[str, Probe] = {
    "ping_host": ping_host,
    "ping_gateway": ping_gateway,
    "arp_lookup": arp_lookup,
    "tcp_port_probe": tcp_port_probe,
    "visa_list_resources": visa_list_resources,
    "check_stale_socket": check_stale_socket,
    "compare_to_last_known_good": compare_to_last_known_good,
}


class DiagnosticEngine:
    """Run a bounded differential diagnosis using injected policy and I/O."""

    def __init__(
        self,
        *,
        selector: HypothesisSelector,
        probe_registry: Mapping[str, Probe] | None = None,
        _testing_allow_probe_registry: bool = False,
        trusted_target_ip: str | None = None,
        allowed_target_ports: Iterable[int] = (111, 5025),
        operator_answer_provider: OperatorAnswerProvider | None = None,
        event_sink: EventSink | None = None,
        case_library_writer: CaseLibraryWriter | None = None,
        case_library: DiagnosticCaseStore | None = None,
        similar_case_limit: int = 3,
        known_recommended_fixes: Mapping[str, str] | None = None,
        confidence_threshold: float = 0.9,
        max_iterations: int = 8,
        max_action_rejections: int = 3,
        clock: Clock | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_action_rejections < 1:
            raise ValueError("max_action_rejections must be at least 1")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if isinstance(similar_case_limit, bool) or not isinstance(similar_case_limit, int) or similar_case_limit < 0:
            raise ValueError("similar_case_limit must be a non-negative integer")
        if probe_registry is not None and not _testing_allow_probe_registry:
            raise ValueError("probe_registry replacement is test-only")
        self.selector = selector
        self.probe_registry = dict(_DEFAULT_REGISTRY if probe_registry is None else probe_registry)
        self.trusted_target_ip = _normalized_ip(trusted_target_ip) if trusted_target_ip is not None else None
        self.allowed_target_ports = _validated_ports(allowed_target_ports)
        self.operator_answer_provider = operator_answer_provider
        self.event_sink = event_sink
        self.case_library_writer = case_library_writer
        self.case_library = case_library
        self.similar_case_limit = similar_case_limit
        self.known_recommended_fixes = dict(known_recommended_fixes or {})
        self.confidence_threshold = confidence_threshold
        self.max_iterations = max_iterations
        self.max_action_rejections = max_action_rejections
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, instrument_identity: Mapping[str, str], symptom: str) -> DiagnosticResult:
        """Run until resolution, explicit exhaustion, or a configured bound."""

        identity = dict(instrument_identity)
        if not identity:
            raise ValueError("instrument_identity must not be empty")
        if not isinstance(symptom, str) or not symptom.strip():
            raise ValueError("symptom must be a non-empty string")
        target_ip = self.trusted_target_ip or _target_ip_from_identity(identity)

        if self.case_library is not None:
            similar_cases = self.case_library.retrieve(identity, symptom, limit=self.similar_case_limit)
            if isinstance(self.selector, SimilarCaseAwareSelector):
                self.selector.set_similar_cases(similar_cases)

        session = DiagnosticSession(instrument_identity=identity, symptom=symptom.strip())
        session.hypotheses = self._validated_hypotheses(
            self.selector.seed_hypotheses(identity, symptom), "initial hypotheses"
        )
        self._emit("hypotheses_seeded", hypotheses=session.hypotheses, symptom=symptom)

        iterations = 0
        rejections = 0
        while iterations < self.max_iterations:
            action = self.selector.choose_action(session, symptom)
            self._emit("action_selected", action=action, iteration=iterations + 1)
            rejection = self._action_rejection(action, session, target_ip)
            if rejection is not None:
                rejections += 1
                self._emit(
                    "action_rejected",
                    action=action,
                    reason=rejection,
                    rejection_count=rejections,
                )
                if rejections >= self.max_action_rejections:
                    return self._escalate(
                        session,
                        iterations,
                        rejections,
                        f"Action rejection limit ({self.max_action_rejections}) reached: {rejection}",
                    )
                continue

            if isinstance(action, Exhausted):
                return self._escalate(session, iterations, rejections, action.reason.strip())
            if isinstance(action, Conclude):
                return self._resolve(session, action, iterations, rejections)

            iterations += 1
            if isinstance(action, ProbeCall):
                # Validation happened before registry lookup.  No dynamic import,
                # command, path, or callable supplied by the selector is used.
                probe = self.probe_registry[action.name]
                args = dict(action.args)
                result = probe(**args)
                finding = Finding(action.name, args, result, self.clock())
                session.findings.append(finding)
                self._emit("finding", finding=finding, iteration=iterations)
            else:
                self._emit("operator_question", question=action.question.strip(), iteration=iterations)
                answer = None
                if self.operator_answer_provider is not None:
                    answer = self.operator_answer_provider(action.question)
                if not isinstance(answer, str) or not answer.strip():
                    self._emit("operator_answer_required", question=action.question, iteration=iterations)
                    raise NeedOperatorAnswer(
                        action.question,
                        session=session,
                        iterations=iterations,
                        rejection_count=rejections,
                    )
                if len(answer) > MAX_OPERATOR_ANSWER_LENGTH:
                    return self._escalate(session, iterations, rejections, "operator answer exceeded the safety length limit")
                turn = OperatorTurn(action.question.strip(), answer.strip(), self.clock())
                session.operator_turns.append(turn)
                self._emit("operator_turn", operator_turn=turn, iteration=iterations)

            session.hypotheses = self._validated_hypotheses(
                self.selector.update_hypotheses(session, symptom), "hypothesis update"
            )
            self._emit("hypotheses_updated", hypotheses=session.hypotheses, iteration=iterations)
            automatic = self._known_fix_conclusion(session)
            if automatic is not None:
                return self._resolve(session, automatic, iterations, rejections)

        return self._escalate(
            session,
            iterations,
            rejections,
            f"maximum {self.max_iterations} iterations reached without a known recommended fix",
        )

    def _action_rejection(self, action: object, session: DiagnosticSession, target_ip: str | None) -> str | None:
        if isinstance(action, ProbeCall):
            return self._probe_rejection(action, target_ip, session.instrument_identity)
        if isinstance(action, AskOperator):
            if not isinstance(action.question, str) or not action.question.strip():
                return "operator question is empty"
            if len(action.question) > MAX_OPERATOR_QUESTION_LENGTH:
                return "operator question exceeds the safety length limit"
            if _UNSAFE_TEXT.search(action.question):
                return "operator question requests an unsafe action or secret"
            return None
        if isinstance(action, Exhausted):
            return None if isinstance(action.reason, str) and action.reason.strip() else "exhaustion reason is empty"
        if isinstance(action, Conclude):
            hypothesis = next((item for item in session.hypotheses if item.id == action.hypothesis_id), None)
            if hypothesis is None:
                return "conclusion refers to an unknown hypothesis"
            if hypothesis.status == "ruled_out" or hypothesis.score < self.confidence_threshold:
                return "conclusion hypothesis is not eligible at the configured threshold"
            if not session.findings and not session.operator_turns:
                return "conclusion requires collected evidence"
            if not isinstance(action.recommended_fix, str) or not action.recommended_fix.strip():
                return "conclusion has no known recommended fix"
            if isinstance(action.confidence, bool) or not isinstance(action.confidence, (int, float)):
                return "conclusion confidence is not numeric"
            if not math.isfinite(float(action.confidence)) or not 0.0 <= action.confidence <= 1.0:
                return "conclusion confidence must be between 0 and 1"
            if action.confidence < self.confidence_threshold:
                return "conclusion confidence is below threshold"
            if action.confidence > hypothesis.score + 0.01:
                return "conclusion confidence materially exceeds the hypothesis score"
            fix = self.known_recommended_fixes.get(action.hypothesis_id, action.recommended_fix)
            if _unsafe_recommendation(fix):
                return "recommended fix is outside the read-only observation/network/transport boundary"
            return None
        return "selector returned an unsupported action type"

    def _probe_rejection(
        self, action: ProbeCall, target_ip: str | None, instrument_identity: Mapping[str, str]
    ) -> str | None:
        if action.name not in _ALLOWED_ARGUMENTS:
            return f"probe {action.name!r} is not in the fixed allowlist"
        if action.name not in self.probe_registry or not callable(self.probe_registry[action.name]):
            return f"probe {action.name!r} is unavailable in the injected registry"
        if not isinstance(action.args, Mapping):
            return "probe arguments must be a mapping"
        required, optional = _ALLOWED_ARGUMENTS[action.name]
        keys = set(action.args)
        if not all(isinstance(key, str) for key in keys):
            return "probe argument names must be strings"
        missing = required - keys
        unexpected = keys - required - optional
        if missing:
            return f"missing required probe arguments: {sorted(missing)}"
        if unexpected:
            return f"probe arguments are not allowed: {sorted(unexpected)}"
        invalid = _validate_argument_values(action.name, action.args)
        if invalid is not None:
            return invalid
        for key in ("ip", "expected_ip"):
            if key in action.args:
                if target_ip is None:
                    return "target-bearing probe requires a trusted instrument target"
                if _normalized_ip(cast(str, action.args[key])) != target_ip:
                    return f"{key} must exactly match the trusted instrument target"
        if "port" in action.args and action.args["port"] not in self.allowed_target_ports:
            return "port is outside the curated instrument port allowlist"
        if action.name == "compare_to_last_known_good" and dict(cast(Mapping[str, Any], action.args["identity"])) != dict(instrument_identity):
            return "identity must exactly match the trusted instrument identity"
        return None

    def _known_fix_conclusion(self, session: DiagnosticSession) -> Conclude | None:
        eligible = [
            hypothesis
            for hypothesis in session.hypotheses
            if hypothesis.score >= self.confidence_threshold
            and hypothesis.status != "ruled_out"
            and bool(session.findings or session.operator_turns)
            and self.known_recommended_fixes.get(hypothesis.id, "").strip()
        ]
        if not eligible:
            return None
        selected = sorted(eligible, key=lambda item: (-item.score, item.id))[0]
        return Conclude(selected.id, self.known_recommended_fixes[selected.id], selected.score)

    def _resolve(
        self,
        session: DiagnosticSession,
        conclusion: Conclude,
        iterations: int,
        rejections: int,
    ) -> DiagnosticResult:
        # A model conclusion is only a recommendation. It does not establish
        # that an operator applied the fix or verified recovery.
        session.status = "in_progress"
        fix = self.known_recommended_fixes.get(conclusion.hypothesis_id, conclusion.recommended_fix).strip()
        session.confirmed_root_cause = next(
            (item.description for item in session.hypotheses if item.id == conclusion.hypothesis_id),
            conclusion.hypothesis_id,
        )
        session.recommended_fix = fix
        session.outcome = "recommended_fix_pending_operator_action"
        session.resolution_summary = (
            f"Hypothesis {conclusion.hypothesis_id!r} reached confidence "
            f"{conclusion.confidence:.3f}; recommended fix pending operator action: {fix}"
        )
        result = DiagnosticResult(
            session=session,
            outcome="recommended_fix_pending_operator_action",
            iterations=iterations,
            rejection_count=rejections,
            hypothesis_id=conclusion.hypothesis_id,
            recommended_fix=fix,
            confidence=float(conclusion.confidence),
        )
        self._emit("recommendation_pending", result=result)
        # Pending recommendations are deliberately excluded from resolved-case
        # memory until an operator confirms a genuinely applied fix.
        return result

    def _escalate(
        self,
        session: DiagnosticSession,
        iterations: int,
        rejections: int,
        reason: str,
    ) -> DiagnosticResult:
        session.status = "escalated"
        session.resolution_summary = reason
        result = DiagnosticResult(session, "escalate", iterations, rejections)
        self._emit("diagnosis_escalated", result=result, reason=reason)
        return result

    @staticmethod
    def _validated_hypotheses(values: Sequence[Hypothesis], context: str) -> list[Hypothesis]:
        hypotheses = list(values)
        if not hypotheses:
            raise ValueError(f"{context} must not be empty")
        if not all(isinstance(value, Hypothesis) for value in hypotheses):
            raise TypeError(f"{context} must contain only Hypothesis values")
        identifiers = [hypothesis.id for hypothesis in hypotheses]
        if any(not identifier.strip() for identifier in identifiers):
            raise ValueError(f"{context} contains an empty hypothesis id")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"{context} contains duplicate hypothesis ids")
        return hypotheses

    def _emit(self, event_type: str, **data: Any) -> None:
        if self.event_sink is None:
            return
        event = {
            "timestamp": self.clock().isoformat(),
            "event_type": event_type,
            "data": _json_safe(data),
        }
        sink = self.event_sink
        if callable(sink):
            sink(event)
        elif callable(getattr(sink, "emit", None)):
            cast(StructuredEventEmitter, sink).emit(event)
        elif callable(getattr(sink, "write", None)):
            stream = cast(TextIO, sink)
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")
            stream.flush()
        else:
            raise TypeError("event_sink must be callable or provide emit()/write()")


def _normalized_ip(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("trusted_target_ip must be an IP address")
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise ValueError("trusted_target_ip must be an IP address") from exc


def _validated_ports(values: Iterable[int]) -> frozenset[int]:
    ports: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError("allowed_target_ports must contain valid integer ports")
        ports.add(value)
    return frozenset(ports | {111, 5025})


def _target_ip_from_identity(identity: Mapping[str, str]) -> str | None:
    direct = identity.get("ip") or identity.get("target_ip")
    if direct:
        return _normalized_ip(direct)
    resource = identity.get("resource", "")
    # VISA TCPIP resources delimit their host with ``::``.  Only an IP literal
    # is trusted; DNS names are intentionally not resolved here.
    match = re.match(r"(?i)^TCPIP\d*::([^:]+)::", resource.strip())
    if match:
        try:
            return _normalized_ip(match.group(1))
        except ValueError:
            return None
    return None


def _unsafe_recommendation(value: object) -> bool:
    return not isinstance(value, str) or not value.strip() or bool(_UNSAFE_TEXT.search(value))


def _validate_argument_values(name: str, args: Mapping[str, Any]) -> str | None:
    for key in ("ip", "expected_ip"):
        if key in args:
            value = args[key]
            if not isinstance(value, str) or not value.strip():
                return f"{key} must be a non-empty string"
            try:
                ipaddress.ip_address(value)
            except ValueError:
                return f"{key} must be an IP address"
    for key in ("expected_resource", "expected_vendor_prefix", "expected_oui_prefix"):
        if key in args and (not isinstance(args[key], str) or not args[key].strip()):
            return f"{key} must be a non-empty string"
    if "port" in args:
        port = args["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            return "port must be an integer between 1 and 65535"
    if "timeout" in args:
        timeout = args["timeout"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            return "timeout must be numeric"
        if not math.isfinite(float(timeout)) or not 0 < timeout <= 10:
            return "timeout must be greater than 0 and at most 10 seconds"
    if name == "compare_to_last_known_good":
        identity = args.get("identity")
        if not isinstance(identity, Mapping) or not identity:
            return "identity must be a non-empty mapping"
        if not all(isinstance(key, str) and isinstance(value, (str, int, float, bool, type(None))) for key, value in identity.items()):
            return "identity must contain only string keys and scalar values"
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


# Lazy factories avoid a module cycle when callers import llm_hypothesis_selector
# (which imports the engine's action types) before importing this module directly.
DEFAULT_ENDPOINT = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 30.0


def HTTPJSONBackend(**kwargs: Any) -> Any:
    from .llm_hypothesis_selector import HTTPJSONBackend as backend_type

    return backend_type(**kwargs)


def LLMHypothesisSelector(**kwargs: Any) -> HypothesisSelector:
    from .llm_hypothesis_selector import LLMHypothesisSelector as selector_type

    return selector_type(**kwargs)


_PROBE_DESCRIPTIONS: dict[str, str] = {
    "ping_host": "Check whether an IP host responds to ICMP without changing it.",
    "ping_gateway": "Check whether the local default gateway responds.",
    "arp_lookup": "Inspect the local ARP/neighbor entry for an IP address.",
    "tcp_port_probe": "Attempt a read-only TCP connection to an IP address and port.",
    "visa_list_resources": "List VISA resources without opening or configuring an instrument.",
    "check_stale_socket": "Check for a stale TCP instrument socket.",
    "compare_to_last_known_good": "Compare identity metadata with the last known-good observation.",
}


class _CLIInputError(ValueError):
    """An argparse validation error that can be returned without a traceback."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CLIInputError(message)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than 0")
    return parsed


def _confidence(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the read-only ``lg-diagnose`` command-line parser."""

    parser = _ArgumentParser(
        prog="lg-diagnose",
        description="Run a bounded, read-only instrument diagnosis and write a report bundle.",
    )
    parser.add_argument("--identity", required=True, help="Instrument identity/schema name (for example rigol_dp832)")
    parser.add_argument("--resource", required=True, help="Instrument resource identifier (recorded as context only)")
    parser.add_argument("--symptom", required=True, help="Observed symptom to diagnose")
    parser.add_argument("--output", "-o", required=True, help="Output directory for report.md and events.jsonl")
    parser.add_argument("--model-endpoint", default=DEFAULT_ENDPOINT, help="Local Ollama or OpenAI-compatible chat endpoint")
    parser.add_argument("--allow-remote-model", action="store_true", help="Explicitly permit a non-loopback HTTP(S) model endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local model name")
    parser.add_argument("--model-timeout", type=_positive_float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument("--max-iterations", type=_positive_int, default=8, help="Maximum evidence-gathering turns")
    parser.add_argument("--confidence", type=_confidence, default=0.9, help="Conclusion confidence threshold (0 through 1)")
    parser.add_argument(
        "--case-library",
        default="diagnostics_cases",
        help="Local resolved-case memory directory (default: diagnostics_cases)",
    )
    return parser


def _load_instrument_schema(identity: str) -> dict[str, Any]:
    """Load a matching repository schema when present; absence is supported."""

    slug = identity.strip()
    if not _IDENTITY_SLUG.fullmatch(slug):
        raise ValueError("identity must be a lowercase schema slug")
    filename = f"{slug}.yaml"
    roots = [(Path.cwd() / "schemas").resolve(), (Path(__file__).resolve().parents[3] / "schemas").resolve()]
    for root in roots:
        candidate = (root / filename).resolve()
        if candidate.parent != root or not candidate.is_file():
            continue
        if candidate.stat().st_size > MAX_SCHEMA_BYTES:
            raise ValueError(f"instrument schema {candidate} exceeds {MAX_SCHEMA_BYTES} bytes")
        loaded = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            return dict(loaded)
        raise ValueError(f"instrument schema {candidate} must contain a YAML mapping")
    return {}


def _schema_ports(schema: Mapping[str, Any]) -> frozenset[int]:
    ports: set[int] = {111, 5025}

    def visit(value: object, parent_key: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, str(key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)
        elif "port" in parent_key and isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
            ports.add(value)

    visit(schema)
    return frozenset(ports)


def _input_answer(question: str) -> str | None:
    """Ask explicitly on stdin; EOF is handled as a controlled escalation."""

    try:
        warning = "WARNING: Do not run commands, change instrument state, or disclose passwords, tokens, or keys."
        return input(f"{warning}\n{question.strip()} ")
    except (EOFError, KeyboardInterrupt):
        return None


def _markdown_value(value: object) -> str:
    # Values can contain model, probe, schema, or operator-controlled text.
    collapsed = " ".join(str(value).split())
    escaped = html.escape(collapsed, quote=True)
    # Intraword underscores and punctuation are not structural in this
    # single-line list context, so preserve them for readable identifiers.
    return re.sub(r"([\\`*{}\[\]])", r"\\\1", escaped)


def _fallback_recommendation(session: DiagnosticSession) -> str:
    summary = (session.resolution_summary or "").casefold()
    if "operator input required" in summary or "operator answer" in summary:
        return "Rerun interactively and provide the requested observational operator evidence."
    if "model backend" in summary or "endpoint and model" in summary:
        return "Verify the configured local model endpoint and model availability, then retry."
    if "probe" in summary or "case-library" in summary or "network access" in summary:
        return "Review the read-only probe and case-library evidence, then escalate with the report if access cannot be restored."
    if "rejection" in summary or "runtime data" in summary or "diagnostic engine" in summary:
        return "Review the rejected diagnostic inputs and safety constraints, then retry or escalate with this report."
    return "Escalate with this report for operator review; no verified fix is available."


def render_diagnostic_report(
    result: DiagnosticResult,
    *,
    identity: str,
    resource: str,
    symptom: str,
) -> str:
    """Render a human-readable, evidence-oriented diagnostic report."""

    session = result.session
    lines = [
        "# Long Game Diagnostic Report",
        "",
        f"- Identity: {_markdown_value(identity)}",
        f"- Resource: {_markdown_value(resource)}",
        f"- Symptom: {_markdown_value(symptom)}",
        f"- Status: {session.status}",
        f"- Outcome: {result.outcome}",
        f"- Iterations: {result.iterations}",
        "- Safety mode: read-only diagnosis; no instrument settings or outputs were changed.",
        "",
        "## Hypotheses Considered",
        "",
    ]
    if session.hypotheses:
        lines.extend(
            f"- **{_markdown_value(item.id)}** ({item.status}, confidence {item.score:.3f}): {_markdown_value(item.description)}"
            for item in session.hypotheses
        )
    else:
        lines.append("- No hypotheses were produced before escalation.")
    lines.extend(["", "## Evidence", ""])
    if session.findings:
        for finding in session.findings:
            rendered = json.dumps(_json_safe(finding.result), ensure_ascii=False, sort_keys=True)
            lines.append(f"- **{_markdown_value(finding.probe_name)}**: {_markdown_value(rendered)}")
    else:
        lines.append("- No automatic probe findings were collected.")
    lines.extend(["", "## Operator Turns", ""])
    if session.operator_turns:
        for turn in session.operator_turns:
            lines.append(f"- **Question:** {_markdown_value(turn.question)}")
            lines.append(f"  **Answer:** {_markdown_value(turn.answer)}")
    else:
        lines.append("- No operator questions were completed.")
    lines.extend(
        [
            "",
            "## Root Cause / Status",
            "",
            f"- Hypothesis: {_markdown_value(result.hypothesis_id or 'Not confirmed; escalation required')}",
            f"- Summary: {_markdown_value(session.resolution_summary or 'No resolution summary available.')}",
            "",
            "## Recommended Fix",
            "",
            f"- {_markdown_value(result.recommended_fix or _fallback_recommendation(session))}",
            "",
        ]
    )
    return "\n".join(lines)


def _escalated_result(identity: dict[str, str], message: str) -> DiagnosticResult:
    session = DiagnosticSession(instrument_identity=identity)
    session.status = "escalated"
    session.resolution_summary = message
    return DiagnosticResult(session=session, outcome="escalate", iterations=0, rejection_count=0)


def _write_json_event(stream: TextIO, event_type: str, data: Mapping[str, Any]) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "data": _json_safe(data),
    }
    stream.write(json.dumps(event, separators=(",", ":")) + "\n")
    stream.flush()


class _SafeBundleWriter:
    """No-follow report bundle writer with atomic final artifact replacement."""

    def __init__(self, directory: Path) -> None:
        if directory.is_symlink():
            raise OSError("output directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise OSError("output path is not a regular directory")
        self.directory = directory.resolve(strict=True)

    def _destination(self, name: str) -> Path:
        destination = self.directory / name
        if destination.exists() or destination.is_symlink():
            info = destination.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise OSError(f"refusing unsafe artifact destination: {name}")
        return destination

    def open_events(self) -> TextIO:
        destination = self._destination("events.jsonl")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OSError("events artifact must be a regular file")
        return os.fdopen(descriptor, "w", encoding="utf-8")

    def write_atomic(self, name: str, content: str) -> None:
        destination = self._destination(name)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._destination(name)
            os.replace(temporary, destination)
            directory_fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``lg-diagnose`` and return a process exit code.

    Diagnostic escalation is a successful, bounded outcome. Only invalid input
    or inability to create/write the requested report directory returns nonzero.
    """

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        for name in ("identity", "resource", "symptom", "output", "model_endpoint", "model", "case_library"):
            if not getattr(args, name).strip():
                raise _CLIInputError(f"--{name.replace('_', '-')} must not be empty")
    except _CLIInputError as exc:
        parser.print_usage(sys.stderr)
        print(f"lg-diagnose: error: {exc}", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser()
    try:
        bundle = _SafeBundleWriter(output)
        events_stream = bundle.open_events()
    except OSError as exc:
        print(f"lg-diagnose: cannot create output directory: {exc}", file=sys.stderr)
        return 2

    identity = {"identity": args.identity.strip(), "resource": args.resource.strip()}
    result: DiagnosticResult
    try:
        with events_stream:
            try:
                schema = _load_instrument_schema(args.identity)
                backend = HTTPJSONBackend(
                    endpoint=args.model_endpoint,
                    model=args.model,
                    timeout=args.model_timeout,
                    allow_remote=args.allow_remote_model,
                )
                selector = LLMHypothesisSelector(
                    backend=backend,
                    instrument_schema=schema,
                    allowed_probes=_PROBE_DESCRIPTIONS,
                )
                engine = DiagnosticEngine(
                    selector=selector,
                    trusted_target_ip=_target_ip_from_identity(identity),
                    allowed_target_ports=_schema_ports(schema),
                    operator_answer_provider=_input_answer,
                    event_sink=events_stream,
                    case_library=DiagnosticCaseLibrary(args.case_library),
                    confidence_threshold=args.confidence,
                    max_iterations=args.max_iterations,
                )
                result = engine.run(identity, args.symptom.strip())
            except NeedOperatorAnswer as exc:
                partial = getattr(exc, "session", DiagnosticSession(instrument_identity=identity))
                partial.status = "escalated"
                partial.resolution_summary = "Operator input required; rerun interactively to continue this read-only diagnosis."
                result = DiagnosticResult(
                    partial,
                    "escalate",
                    getattr(exc, "iterations", 0),
                    getattr(exc, "rejection_count", 0),
                )
                _write_json_event(
                    events_stream,
                    "operator_input_required",
                    {"question": exc.question, "message": partial.resolution_summary},
                )
            except Exception as exc:
                # Runtime/backend/model/probe failures are diagnostic evidence,
                # not CLI crashes. Do not expose backend response details.
                module = type(exc).__module__
                if module.endswith("llm_hypothesis_selector"):
                    category = "model_backend_or_output"
                    message = "Diagnosis escalated because the model backend failed or returned invalid output. Verify the endpoint and model, then retry."
                elif isinstance(exc, (OSError, TimeoutError)):
                    category = "probe_or_case_io"
                    message = "Diagnosis escalated because a read-only probe or case-library operation failed. Review events and network access."
                else:
                    category = "diagnostic_engine"
                    message = "Diagnosis escalated because the diagnostic engine rejected or could not process runtime data. Review events and inputs."
                result = _escalated_result(identity, message)
                _write_json_event(
                    events_stream,
                    "diagnostic_error",
                    {"category": category, "error_type": type(exc).__name__, "message": message},
                )
            _write_json_event(
                events_stream,
                "diagnosis_final",
                {
                    "outcome": result.outcome,
                    "status": result.session.status,
                    "iterations": result.iterations,
                    "hypothesis_id": result.hypothesis_id,
                    "recommended_fix": result.recommended_fix,
                },
            )

        report = render_diagnostic_report(
            result,
            identity=args.identity,
            resource=args.resource,
            symptom=args.symptom,
        )
        bundle.write_atomic("report.md", report)
        bundle.write_atomic("session.yaml", result.session.to_yaml())
    except OSError as exc:
        print(f"lg-diagnose: cannot write diagnostic output: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote diagnostic report bundle: {output}")
    return 0


__all__ = [
    "AskOperator",
    "Conclude",
    "DiagnosticAction",
    "DiagnosticEngine",
    "DiagnosticResult",
    "Exhausted",
    "HypothesisSelector",
    "NeedOperatorAnswer",
    "ProbeCall",
    "ResolutionOutcome",
    "SimilarCaseAwareSelector",
    "build_parser",
    "main",
    "render_diagnostic_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
