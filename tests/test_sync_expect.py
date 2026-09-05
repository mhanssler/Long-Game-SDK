from __future__ import annotations

import sys

import pytest

from long_game_sdk.sdk.streams import AsyncTransport, ExpectEOF, ExpectSession, SubprocessTransport, SyncExpectSession


def test_sync_subprocess_session_round_trip_and_cleanup() -> None:
    program = (
        "import sys; print('ready>', flush=True); "
        "line=sys.stdin.readline(); print('echo:'+line, end='', flush=True)"
    )
    with SyncExpectSession.subprocess(sys.executable, "-u", "-c", program) as session:
        transport = session.transport
        assert isinstance(transport, SubprocessTransport)
        session.expect("ready>", timeout=1)
        session.send_text("hello", newline=True)
        assert session.expect(b"echo:hello\n", timeout=1).match == b"echo:hello\n"

    assert transport.process.returncode is not None
    assert session.closed


def test_sync_context_closes_after_expect_error() -> None:
    program = "import sys; sys.stdout.write('partial'); sys.stdout.flush()"
    with pytest.raises(ExpectEOF), SyncExpectSession.subprocess(
        sys.executable, "-u", "-c", program
    ) as session:
        session.expect("missing", timeout=1)
    assert session.closed


def test_sync_wrapper_rejects_use_inside_running_event_loop() -> None:
    import asyncio

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            SyncExpectSession.subprocess(sys.executable, "-c", "pass")

    asyncio.run(scenario())


def test_sync_open_validates_before_creating_transport() -> None:
    called = False

    async def factory() -> AsyncTransport:
        nonlocal called
        called = True
        raise AssertionError("must not connect")

    with pytest.raises(ValueError, match="read_size"):
        SyncExpectSession._open(factory, source="test", encoding="utf-8", read_size=0)
    assert not called


def test_sync_open_closes_factory_transport_if_session_entry_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Transport:
        def __init__(self) -> None:
            self.closed = False

        async def read(self, size: int = -1) -> bytes:
            return b""

        async def write(self, data: bytes) -> None:
            pass

        async def close(self) -> None:
            self.closed = True

    transport = Transport()

    async def factory() -> AsyncTransport:
        return transport

    async def broken_enter(self: ExpectSession) -> ExpectSession:
        raise RuntimeError("entry failed")

    monkeypatch.setattr(ExpectSession, "__aenter__", broken_enter)
    with pytest.raises(RuntimeError, match="entry failed"):
        SyncExpectSession._open(factory, source="test", encoding="utf-8", read_size=1)
    assert transport.closed
