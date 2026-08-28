from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.request import Request

import pytest

from long_game_sdk.sdk.diagnostic_engine import AskOperator, Conclude, Exhausted, ProbeCall
from long_game_sdk.sdk.diagnostic_session import DiagnosticSession, Finding, Hypothesis, OperatorTurn
from long_game_sdk.sdk.llm_hypothesis_selector import (
    DEFAULT_MODEL,
    HTTPJSONBackend,
    LLMHypothesisSelector,
    SelectorOutputError,
)


REQUIRED_POLICY = (
    "You may only call one of the listed probes, or ask the operator one question, or conclude. "
    "Do not invent probes. Do not propose changing instrument output or setpoints — that is out of scope for this tool."
)


class FakeBackend:
    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Sequence[Mapping[str, str]], Mapping[str, Any]]] = []

    def generate_json(
        self,
        messages: Sequence[Mapping[str, str]],
        json_schema: Mapping[str, Any],
    ) -> str:
        self.calls.append((messages, json_schema))
        return self.responses.pop(0)


def make_selector(backend: FakeBackend, *, max_hypotheses: int = 4) -> LLMHypothesisSelector:
    return LLMHypothesisSelector(
        backend=backend,
        instrument_schema={"manufacturer": "string", "ip": "IPv4 address", "port": "integer"},
        allowed_probes={
            "ping_host": "Check whether the configured instrument IP responds to ICMP.",
            "tcp_port_probe": "Try a TCP connection to an instrument host and port.",
        },
        similar_cases=[{"case_id": "case-17", "summary": "Wrong static IP", "resolution": "Correct inventory"}],
        max_hypotheses=max_hypotheses,
    )


def session() -> DiagnosticSession:
    return DiagnosticSession(
        instrument_identity={"manufacturer": "Acme", "ip": "192.0.2.10", "port": "5025"},
        hypotheses=[Hypothesis("network", "Network path unavailable", 0.6)],
        findings=[
            Finding(
                "ping_host",
                {"ip": "192.0.2.10"},
                {"reachable": False, "stderr": "timeout"},
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        ],
        operator_turns=[
            OperatorTurn("Is the link LED lit?", "yes", datetime(2026, 1, 2, tzinfo=timezone.utc))
        ],
    )


def test_seed_hypotheses_uses_bounded_strict_json_and_complete_context() -> None:
    backend = FakeBackend(
        [
            json.dumps(
                {
                    "hypotheses": [
                        {
                            "id": "network",
                            "description": "Network path unavailable",
                            "score": 0.65,
                            "status": "open",
                        }
                    ]
                }
            )
        ]
    )
    selector = make_selector(backend)

    hypotheses = selector.seed_hypotheses(
        {"manufacturer": "Acme", "ip": "192.0.2.10", "port": "5025"},
        "Instrument does not answer queries",
    )

    assert hypotheses == [Hypothesis("network", "Network path unavailable", 0.65, "open")]
    messages, schema = backend.calls[0]
    prompt = "\n".join(message["content"] for message in messages)
    assert REQUIRED_POLICY in prompt
    assert '"ping_host": "Check whether the configured instrument IP responds to ICMP."' in prompt
    assert '"manufacturer": "string"' in prompt
    assert '"case_id": "case-17"' in prompt
    assert "Instrument does not answer queries" in prompt
    assert schema["properties"]["hypotheses"]["maxItems"] == 4
    assert schema["additionalProperties"] is False


def test_choose_action_parses_each_typed_action() -> None:
    backend = FakeBackend(
        [
            '{"action":"probe","name":"ping_host","args":{"ip":"192.0.2.10"}}',
            '{"action":"ask_operator","question":"Is the Ethernet link LED lit?"}',
            '{"action":"conclude","hypothesis_id":"network","recommended_fix":"Repair the network path.","confidence":0.94}',
            '{"action":"exhausted","reason":"No discriminating read-only probe remains."}',
        ]
    )
    selector = make_selector(backend)
    current = session()

    assert selector.choose_action(current, "No response") == ProbeCall("ping_host", {"ip": "192.0.2.10"})
    assert selector.choose_action(current, "No response") == AskOperator("Is the Ethernet link LED lit?")
    assert selector.choose_action(current, "No response") == Conclude(
        "network", "Repair the network path.", 0.94
    )
    assert selector.choose_action(current, "No response") == Exhausted(
        "No discriminating read-only probe remains."
    )

    prompt = "\n".join(message["content"] for message in backend.calls[0][0])
    assert REQUIRED_POLICY in prompt
    assert '"probe_name": "ping_host"' in prompt  # finding evidence
    assert '"score": 0.6' in prompt
    assert '"answer": "yes"' in prompt
    action_schema = backend.calls[0][1]
    assert {branch["properties"]["action"]["const"] for branch in action_schema["oneOf"]} == {
        "probe",
        "ask_operator",
        "conclude",
        "exhausted",
    }


def test_choose_action_safely_exhausts_on_malformed_or_off_contract_output() -> None:
    backend = FakeBackend(
        [
            "```json\n{\"action\":\"probe\",\"name\":\"ping_host\",\"args\":{}}\n```",
            '{"action":"probe","name":"factory_reset","args":{}}',
            '{"action":"ask_operator","question":"ok?","extra":"not allowed"}',
            '{"action":"conclude","hypothesis_id":"network","recommended_fix":"x","confidence":1.2}',
        ]
    )
    selector = make_selector(backend)

    for _ in range(4):
        action = selector.choose_action(session(), "No response")
        assert isinstance(action, Exhausted)
        assert "selector output rejected" in action.reason


def test_update_hypotheses_includes_evidence_and_rejects_invalid_lists() -> None:
    valid = {
        "hypotheses": [
            {
                "id": "network",
                "description": "Network path unavailable",
                "score": 0.9,
                "status": "confirmed",
            }
        ]
    }
    backend = FakeBackend(
        [
            json.dumps(valid),
            json.dumps({"hypotheses": []}),
            json.dumps({"hypotheses": [valid["hypotheses"][0]] * 5}),
            '{"hypotheses":[{"id":"x","description":"x","score":0.2,"status":"open","extra":1}]}',
        ]
    )
    selector = make_selector(backend)

    assert selector.update_hypotheses(session(), "No response") == [
        Hypothesis("network", "Network path unavailable", 0.9, "confirmed")
    ]
    update_prompt = "\n".join(message["content"] for message in backend.calls[0][0])
    assert '"reachable": false' in update_prompt
    assert '"case_id": "case-17"' in update_prompt

    with pytest.raises(SelectorOutputError, match="at least one"):
        selector.update_hypotheses(session(), "No response")
    with pytest.raises(SelectorOutputError, match="at most 4"):
        selector.update_hypotheses(session(), "No response")
    with pytest.raises(SelectorOutputError, match="exactly"):
        selector.update_hypotheses(session(), "No response")


def test_similar_cases_can_be_supplied_later_without_mutating_caller_data() -> None:
    backend = FakeBackend(['{"action":"exhausted","reason":"done"}'])
    selector = make_selector(backend)
    cases = [{"case_id": "later", "secret_note": "historical evidence"}]
    selector.set_similar_cases(cases)
    cases[0]["secret_note"] = "mutated"

    selector.choose_action(session(), "No response")

    prompt = "\n".join(message["content"] for message in backend.calls[0][0])
    assert "historical evidence" in prompt
    assert '"case_id": "later"' in prompt
    assert "mutated" not in prompt


def test_http_backend_defaults_and_ollama_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"message":{"content":"{\\"answer\\":1}"}}'

    def fake_urlopen(request: Request, timeout: float) -> Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("long_game_sdk.sdk.llm_hypothesis_selector.urlopen", fake_urlopen)
    backend = HTTPJSONBackend()
    result = backend.generate_json([{"role": "user", "content": "hello"}], {"type": "object"})

    assert result == '{"answer":1}'
    assert backend.model == DEFAULT_MODEL == "qwen3:8b"
    assert backend.endpoint.endswith("/api/chat")
    payload = json.loads(captured["request"].data)
    assert payload == {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "think": False,
        "format": {"type": "object"},
        "options": {"temperature": 0},
    }
    assert captured["timeout"] == backend.timeout


def test_http_backend_openai_wire_format_and_controlled_bad_response(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(
        [
            b'{"choices":[{"message":{"content":"{\\"action\\":\\"exhausted\\",\\"reason\\":\\"done\\"}"}}]}',
            b'{"choices":[]}',
        ]
    )
    payloads: list[dict[str, Any]] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return next(replies)

    def fake_urlopen(request: Request, timeout: float) -> Response:
        del timeout
        assert isinstance(request.data, bytes)
        payloads.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("long_game_sdk.sdk.llm_hypothesis_selector.urlopen", fake_urlopen)
    backend = HTTPJSONBackend(
        endpoint="http://127.0.0.1:1234/v1/chat/completions", model="local-model", timeout=3.0
    )
    content = backend.generate_json([{"role": "user", "content": "hello"}], {"type": "object"})

    assert json.loads(content)["action"] == "exhausted"
    assert payloads[0]["temperature"] == 0
    assert payloads[0]["response_format"]["type"] == "json_schema"
    assert payloads[0]["response_format"]["json_schema"]["strict"] is True
    with pytest.raises(SelectorOutputError, match="content"):
        backend.generate_json([{"role": "user", "content": "hello"}], {"type": "object"})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.0.2.10:11434/api/chat",
        "https://127.0.0.1/api/chat",
        "http://user:password@127.0.0.1/api/chat",
        "http://127.0.0.1/api/chat#fragment",
    ],
)
def test_http_backend_rejects_nonlocal_or_credentialed_endpoints_by_default(endpoint: str) -> None:
    with pytest.raises(ValueError):
        HTTPJSONBackend(endpoint=endpoint)


def test_remote_endpoint_requires_explicit_opt_in_and_timeout_is_bounded() -> None:
    backend = HTTPJSONBackend(endpoint="https://192.0.2.10/v1/chat/completions", allow_remote=True)
    assert backend.allow_remote is True
    with pytest.raises(ValueError, match="at most"):
        HTTPJSONBackend(timeout=121)


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"exhausted","action":"probe","reason":"done"}',
        '{"action":"conclude","hypothesis_id":"network","recommended_fix":"inspect","confidence":NaN}',
    ],
)
def test_selector_rejects_duplicate_keys_and_nonfinite_json(raw: str) -> None:
    selector = make_selector(FakeBackend([raw]))
    action = selector.choose_action(session(), "No response")
    assert isinstance(action, Exhausted)
    assert "selector output rejected" in action.reason
