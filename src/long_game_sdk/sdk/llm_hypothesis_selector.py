"""Pluggable, non-executing local-LLM policy for diagnostic hypotheses.

The selector translates strictly validated JSON into the diagnostic engine's
closed set of data-only actions.  The HTTP backend supports common local Ollama
and OpenAI-compatible servers using only the standard library.
"""

from __future__ import annotations

from copy import deepcopy
import ipaddress
import json
import math
from typing import Any, Mapping, Protocol, Sequence, cast, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .diagnostic_engine import AskOperator, Conclude, DiagnosticAction, Exhausted, ProbeCall
from .diagnostic_session import DiagnosticSession, Hypothesis

DEFAULT_ENDPOINT = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0
MAX_RESPONSE_BYTES = 1_048_576
MAX_REQUEST_BYTES = 524_288
MAX_MODEL_OUTPUT_CHARS = 262_144
_ORIGINAL_URLOPEN = urlopen

_POLICY = (
    "You may only call one of the listed probes, or ask the operator one question, or conclude. "
    "Do not invent probes. Do not propose changing instrument output or setpoints — that is out of scope for this tool."
)
_HYPOTHESIS_KEYS = frozenset({"id", "description", "score", "status"})
_STATUS_VALUES = frozenset({"open", "confirmed", "ruled_out"})


class SelectorError(RuntimeError):
    """Base class for controlled local-selector failures."""


class SelectorOutputError(SelectorError):
    """The backend returned malformed or contract-violating JSON."""


class SelectorBackendError(SelectorError):
    """The configured generation backend failed without exposing its payload."""


@runtime_checkable
class JSONGenerationBackend(Protocol):
    """Minimal interface implemented by local or test JSON generators."""

    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        json_schema: Mapping[str, Any],
    ) -> str: ...


class HTTPJSONBackend:
    """JSON chat backend for Ollama or OpenAI-compatible local endpoints."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        allow_remote: bool = False,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be numeric")
        if not math.isfinite(float(timeout)) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(f"timeout must be finite, greater than zero, and at most {MAX_TIMEOUT:g} seconds")
        endpoint = endpoint.strip()
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ({"http", "https"} if allow_remote else {"http"}):
            raise ValueError("endpoint scheme is not allowed")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("endpoint must not contain credentials or a fragment")
        if not parsed.hostname:
            raise ValueError("endpoint must contain a host")
        if not allow_remote and not _is_loopback(parsed.hostname):
            raise ValueError("remote model endpoint requires allow_remote=True")
        self.endpoint = endpoint
        self.model = model.strip()
        self.timeout = float(timeout)
        self.allow_remote = allow_remote

    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        json_schema: Mapping[str, Any],
    ) -> str:
        normalized_messages = [dict(message) for message in messages]
        schema = dict(json_schema)
        if self._is_openai_endpoint:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": normalized_messages,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "diagnostic_selector_output", "strict": True, "schema": schema},
                },
            }
        else:
            payload = {
                "model": self.model,
                "messages": normalized_messages,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {"temperature": 0},
            }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise SelectorOutputError("model request exceeds the prompt/context size limit")
        request = Request(
            self.endpoint,
            data=encoded,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            # ``urlopen`` identity check preserves the long-standing private
            # unit-test seam; production always uses the no-redirect opener.
            response_context = (
                urlopen(request, timeout=self.timeout)
                if urlopen is not _ORIGINAL_URLOPEN
                else build_opener(_NoRedirectHandler()).open(request, timeout=self.timeout)
            )
            with response_context as response:
                try:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                except TypeError:  # legacy fake responses without a size parameter
                    raw = response.read()
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise SelectorBackendError("model response exceeded the size limit")
                decoded = _strict_json_loads(raw.decode("utf-8"))
        except SelectorBackendError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SelectorBackendError("local JSON generation request failed") from exc

        try:
            if self._is_openai_endpoint:
                content = decoded["choices"][0]["message"]["content"]
            else:
                content = decoded["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SelectorOutputError("backend response did not contain textual message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise SelectorOutputError("backend response did not contain textual message content")
        if len(content) > MAX_MODEL_OUTPUT_CHARS:
            raise SelectorOutputError("backend message content exceeded the size limit")
        return content

    @property
    def _is_openai_endpoint(self) -> bool:
        endpoint = self.endpoint.rstrip("/").lower()
        return "/v1/" in endpoint or endpoint.endswith("/chat/completions")


class LLMHypothesisSelector:
    """Strict JSON adapter from a pluggable LLM backend to engine policy data."""

    def __init__(
        self,
        *,
        backend: JSONGenerationBackend,
        instrument_schema: Mapping[str, Any],
        allowed_probes: Mapping[str, str],
        similar_cases: Sequence[Mapping[str, Any]] = (),
        max_hypotheses: int = 8,
    ) -> None:
        if not isinstance(backend, JSONGenerationBackend):
            raise TypeError("backend must implement generate_json")
        if not isinstance(max_hypotheses, int) or isinstance(max_hypotheses, bool) or max_hypotheses < 1:
            raise ValueError("max_hypotheses must be a positive integer")
        if not allowed_probes:
            raise ValueError("allowed_probes must not be empty")
        if not all(
            isinstance(name, str)
            and name.strip()
            and isinstance(description, str)
            and description.strip()
            for name, description in allowed_probes.items()
        ):
            raise ValueError("probe names and descriptions must be non-empty strings")
        self.backend = backend
        self.instrument_schema = deepcopy(dict(instrument_schema))
        self.allowed_probes = {name: description for name, description in allowed_probes.items()}
        self.max_hypotheses = max_hypotheses
        self._similar_cases: list[Mapping[str, Any]] = []
        self.set_similar_cases(similar_cases)

    def set_similar_cases(self, similar_cases: Sequence[Mapping[str, Any]]) -> None:
        """Replace retrieval context; copying lets callers safely reuse their list."""

        if isinstance(similar_cases, (str, bytes)) or not all(isinstance(case, Mapping) for case in similar_cases):
            raise TypeError("similar_cases must contain only mappings")
        self._similar_cases = deepcopy([dict(case) for case in similar_cases])

    def seed_hypotheses(self, identity: dict[str, str], symptom: str) -> Sequence[Hypothesis]:
        schema = self._hypothesis_schema()
        context = {
            "task": "Seed a bounded differential diagnosis.",
            "symptom": symptom,
            "instrument_identity": identity,
            "instrument_schema": self.instrument_schema,
            "allowed_probes": self.allowed_probes,
            "evidence": {"findings": [], "operator_turns": []},
            "current_hypothesis_scores": [],
            "similar_cases": self._similar_cases,
        }
        return self._generate_hypotheses(context, schema)

    def choose_action(self, session: DiagnosticSession, symptom: str) -> DiagnosticAction:
        schema = self._action_schema()
        context = self._session_context(session, symptom)
        context["task"] = "Choose exactly one next diagnostic action."
        try:
            value = self._generate(context, schema)
            return self._parse_action(value, session)
        except SelectorOutputError as exc:
            # A typed inert action lets the engine escalate without ever treating
            # untrusted model text as a probe, callable, command, or instruction.
            return Exhausted(f"selector output rejected: {exc}")

    def update_hypotheses(self, session: DiagnosticSession, symptom: str) -> Sequence[Hypothesis]:
        schema = self._hypothesis_schema()
        context = self._session_context(session, symptom)
        context["task"] = "Update and rescore the bounded differential diagnosis from the evidence."
        return self._generate_hypotheses(context, schema)

    def _session_context(self, session: DiagnosticSession, symptom: str) -> dict[str, Any]:
        serialized = session.to_dict()
        return {
            "symptom": symptom,
            "instrument_identity": session.instrument_identity,
            "instrument_schema": self.instrument_schema,
            "allowed_probes": self.allowed_probes,
            "evidence": {
                "findings": serialized["findings"],
                "operator_turns": serialized["operator_turns"],
            },
            "current_hypothesis_scores": serialized["hypotheses"],
            "similar_cases": self._similar_cases,
        }

    def _generate_hypotheses(
        self, context: Mapping[str, Any], schema: Mapping[str, Any]
    ) -> list[Hypothesis]:
        value = self._generate(context, schema)
        if set(value) != {"hypotheses"}:
            raise SelectorOutputError("hypothesis output must contain exactly the hypotheses field")
        items = value["hypotheses"]
        if not isinstance(items, list):
            raise SelectorOutputError("hypotheses must be a JSON list")
        if not items:
            raise SelectorOutputError("hypotheses must contain at least one item")
        if len(items) > self.max_hypotheses:
            raise SelectorOutputError(f"hypotheses must contain at most {self.max_hypotheses} items")
        parsed = [self._parse_hypothesis(item) for item in items]
        identifiers = [item.id for item in parsed]
        if len(identifiers) != len(set(identifiers)):
            raise SelectorOutputError("hypothesis ids must be unique")
        return parsed

    def _generate(self, context: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
        prompt = (
            f"{_POLICY}\n"
            "Return one JSON value only, with no Markdown or commentary. It must exactly match this JSON schema:\n"
            f"{_dump(schema)}\n"
            "Diagnostic context:\n"
            f"{_dump(context)}"
        )
        try:
            raw = self.backend.generate_json(
                [
                    {"role": "system", "content": _POLICY},
                    {"role": "user", "content": prompt},
                ],
                schema,
            )
        except SelectorError:
            raise
        except Exception as exc:
            # Pluggable backends must not leak arbitrary transport exceptions
            # across the selector boundary or be miscategorized as probes.
            raise SelectorBackendError("JSON generation backend failed") from exc
        if not isinstance(raw, str):
            raise SelectorOutputError("backend output must be text containing one JSON object")
        try:
            if len(raw) > MAX_MODEL_OUTPUT_CHARS:
                raise SelectorOutputError("backend output exceeded the size limit")
            value = _strict_json_loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SelectorOutputError("backend output is not one strict JSON object") from exc
        if not isinstance(value, dict):
            raise SelectorOutputError("backend output must be one JSON object")
        return value

    @staticmethod
    def _parse_hypothesis(value: object) -> Hypothesis:
        if not isinstance(value, dict) or set(value) != _HYPOTHESIS_KEYS:
            raise SelectorOutputError("each hypothesis must contain exactly id, description, score, and status")
        identifier = value["id"]
        description = value["description"]
        score = value["score"]
        status = value["status"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise SelectorOutputError("hypothesis id must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise SelectorOutputError("hypothesis description must be a non-empty string")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise SelectorOutputError("hypothesis score must be numeric")
        if not math.isfinite(float(score)) or not 0.0 <= score <= 1.0:
            raise SelectorOutputError("hypothesis score must be between 0 and 1")
        if not isinstance(status, str) or status not in _STATUS_VALUES:
            raise SelectorOutputError("hypothesis status is invalid")
        return Hypothesis(identifier.strip(), description.strip(), float(score), cast(Any, status))

    def _parse_action(self, value: Mapping[str, Any], session: DiagnosticSession) -> DiagnosticAction:
        action = value.get("action")
        expected_keys: dict[str, frozenset[str]] = {
            "probe": frozenset({"action", "name", "args"}),
            "ask_operator": frozenset({"action", "question"}),
            "conclude": frozenset(
                {"action", "hypothesis_id", "recommended_fix", "confidence"}
            ),
            "exhausted": frozenset({"action", "reason"}),
        }
        if not isinstance(action, str) or action not in expected_keys:
            raise SelectorOutputError("action discriminator is unsupported")
        if set(value) != expected_keys[action]:
            raise SelectorOutputError(f"{action} action must contain exactly its schema fields")
        if action == "probe":
            name = value["name"]
            args = value["args"]
            if not isinstance(name, str) or name not in self.allowed_probes:
                raise SelectorOutputError("probe is not in the listed probe set")
            if not isinstance(args, dict) or not all(isinstance(key, str) for key in args):
                raise SelectorOutputError("probe args must be a JSON object with string keys")
            return ProbeCall(name, args)
        if action == "ask_operator":
            question = value["question"]
            if not isinstance(question, str) or not question.strip():
                raise SelectorOutputError("operator question must be a non-empty string")
            return AskOperator(question.strip())
        if action == "conclude":
            hypothesis_id = value["hypothesis_id"]
            fix = value["recommended_fix"]
            confidence = value["confidence"]
            if not isinstance(hypothesis_id, str) or hypothesis_id not in {item.id for item in session.hypotheses}:
                raise SelectorOutputError("conclusion refers to an unknown hypothesis")
            if not isinstance(fix, str) or not fix.strip():
                raise SelectorOutputError("recommended fix must be a non-empty string")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise SelectorOutputError("conclusion confidence must be numeric")
            if not math.isfinite(float(confidence)) or not 0.0 <= confidence <= 1.0:
                raise SelectorOutputError("conclusion confidence must be between 0 and 1")
            return Conclude(hypothesis_id, fix.strip(), float(confidence))
        reason = value["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise SelectorOutputError("exhaustion reason must be a non-empty string")
        return Exhausted(reason.strip())

    def _hypothesis_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["hypotheses"],
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": self.max_hypotheses,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "description", "score", "status"],
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "description": {"type": "string", "minLength": 1},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                            "status": {"type": "string", "enum": sorted(_STATUS_VALUES)},
                        },
                    },
                }
            },
        }

    def _action_schema(self) -> dict[str, Any]:
        def branch(action: str, properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", *required],
                "properties": {"action": {"const": action}, **properties},
            }

        return {
            "oneOf": [
                branch(
                    "probe",
                    {
                        "name": {"type": "string", "enum": list(self.allowed_probes)},
                        "args": {"type": "object"},
                    },
                    ["name", "args"],
                ),
                branch(
                    "ask_operator",
                    {"question": {"type": "string", "minLength": 1}},
                    ["question"],
                ),
                branch(
                    "conclude",
                    {
                        "hypothesis_id": {"type": "string", "minLength": 1},
                        "recommended_fix": {"type": "string", "minLength": 1},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    ["hypothesis_id", "recommended_fix", "confidence"],
                ),
                branch(
                    "exhausted",
                    {"reason": {"type": "string", "minLength": 1}},
                    ["reason"],
                ),
            ]
        }


def _dump(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError) as exc:
        raise SelectorOutputError("selector context must be JSON serializable") from exc


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_loads(value: str) -> Any:
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "HTTPJSONBackend",
    "JSONGenerationBackend",
    "LLMHypothesisSelector",
    "SelectorBackendError",
    "SelectorError",
    "SelectorOutputError",
]
