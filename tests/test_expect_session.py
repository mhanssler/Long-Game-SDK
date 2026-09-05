from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from long_game_sdk.sdk.streams._close import CloseState
from long_game_sdk.sdk.streams import (
    ExpectBufferOverflow,
    ExpectDecodeError,
    ExpectEOF,
    ExpectFailure,
    ExpectSession,
    ExpectTimeout,
    StreamEvent,
)


class ScriptedTransport:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = deque(chunks)
        self.writes: list[bytes] = []
        self.closed = False
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        if chunks:
            self.release_read.set()

    async def read(self, size: int = -1) -> bytes:
        self.read_started.set()
        await self.release_read.wait()
        await asyncio.sleep(0)
        return self.chunks.popleft() if self.chunks else b""

    async def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def close(self) -> None:
        self.closed = True
        self.release_read.set()


def test_stream_event_is_timestamped_and_immutable() -> None:
    event = StreamEvent(source="console", data=b"ready", timestamp=datetime.now(UTC))
    assert event.source == "console"
    assert event.data == b"ready"
    assert event.timestamp.tzinfo is not None
    with pytest.raises(FrozenInstanceError):
        event.data = b"changed"  # type: ignore[misc]


def test_expect_matches_literals_and_regex_across_chunks_and_sends_payloads() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([b"booting\nRE", b"ADY 42\nprompt> "])
        async with ExpectSession(transport, source="dut", read_size=3) as session:
            literal = await session.expect(b"READY")
            regex = await session.expect(re.compile(rb"prompt>\s"))
            await session.send(b"raw")
            await session.send_text(" text")
            await session.send_json({"enabled": True})

        assert literal.match == b"READY"
        assert literal.before == b"booting\n"
        assert regex.match == b"prompt> "
        assert regex.before == b" 42\n"
        assert [event.data for event in session.events] == [b"booting\nRE", b"ADY 42\nprompt> "]
        assert all(event.source == "dut" for event in session.events)
        assert transport.writes == [b"raw", b" text", b'{"enabled":true}\n']
        assert transport.closed

    asyncio.run(scenario())


def test_failure_timeout_and_eof_are_distinct() -> None:
    async def failure_scenario() -> None:
        transport = ScriptedTransport([b"starting FAI", b"LED now"])
        async with ExpectSession(transport) as session:
            with pytest.raises(ExpectFailure) as error:
                await session.expect("READY", failures=[re.compile("FAILED")])
            assert error.value.match == b"FAILED"

    async def timeout_scenario() -> None:
        transport = ScriptedTransport([])
        transport.release_read.clear()
        async with ExpectSession(transport) as session:
            await transport.read_started.wait()
            with pytest.raises(ExpectTimeout):
                await session.expect("never", timeout=0.01)

    async def eof_scenario() -> None:
        transport = ScriptedTransport([b"partial"])
        async with ExpectSession(transport) as session:
            with pytest.raises(ExpectEOF) as error:
                await session.expect("complete")
            assert error.value.buffer == b"partial"

    asyncio.run(failure_scenario())
    asyncio.run(timeout_scenario())
    asyncio.run(eof_scenario())


def test_context_cleanup_survives_cancellation() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([])
        transport.release_read.clear()
        session = ExpectSession(transport)
        await session.__aenter__()
        task = asyncio.create_task(session.expect("never"))
        await transport.read_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await session.close()
        assert transport.closed
        assert session.closed

    asyncio.run(scenario())


def test_buffer_overflow_is_bounded_and_reported() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([b"1234", b"5"])
        async with ExpectSession(transport, max_buffer_size=4) as session:
            with pytest.raises(ExpectBufferOverflow) as error:
                await session.expect(b"never")
            assert error.value.limit == 4
            assert error.value.buffer == b"1234"
            assert session.buffer == b"1234"

    asyncio.run(scenario())


def test_event_history_retains_only_configured_number_of_chunks() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([b"one", b"two", b"three"])
        async with ExpectSession(transport, max_events=2) as session:
            await session.expect(b"three")
            assert [event.data for event in session.events] == [b"two", b"three"]

    asyncio.run(scenario())


def test_session_limits_are_validated() -> None:
    transport = ScriptedTransport([])
    with pytest.raises(ValueError, match="max_buffer_size"):
        ExpectSession(transport, max_buffer_size=0)
    with pytest.raises(ValueError, match="max_events"):
        ExpectSession(transport, max_events=-1)


def test_only_utf8_text_mode_is_accepted_and_multibyte_regex_spans_reads() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        ExpectSession(ScriptedTransport([]), encoding="utf-16")

    async def scenario() -> None:
        transport = ScriptedTransport([b"price: \xe2", b"\x82", b"\xac10"])
        async with ExpectSession(transport, encoding="utf_8") as session:
            result = await session.expect(re.compile(r"€\d+"))
        assert result.before == b"price: "
        assert result.match == "€10".encode()

    asyncio.run(scenario())


def test_overlapping_expect_calls_are_rejected() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([])
        transport.release_read.clear()
        async with ExpectSession(transport) as session:
            first = asyncio.create_task(session.expect(b"first"))
            await transport.read_started.wait()
            with pytest.raises(RuntimeError, match="already active"):
                await session.expect(b"second")
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

    asyncio.run(scenario())


def test_close_unblocks_cancellation_resistant_read_and_is_shared() -> None:
    class CloseUnblocksTransport:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.unblocked = asyncio.Event()
            self.close_calls = 0

        async def read(self, size: int = -1) -> bytes:
            self.started.set()
            while not self.unblocked.is_set():
                try:
                    await self.unblocked.wait()
                except asyncio.CancelledError:
                    continue
            return b""

        async def write(self, data: bytes) -> None:
            pass

        async def close(self) -> None:
            self.close_calls += 1
            self.unblocked.set()
            await asyncio.sleep(0)

    async def scenario() -> None:
        transport = CloseUnblocksTransport()
        session = ExpectSession(transport)
        await session.__aenter__()
        await transport.started.wait()
        await asyncio.gather(session.close(), session.close())
        assert session.closed
        assert transport.close_calls == 1

    asyncio.run(scenario())


def test_failed_close_can_be_retried_and_does_not_mark_session_closed() -> None:
    class RetryTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__([])
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("close failed")
            await super().close()

    async def scenario() -> None:
        transport = RetryTransport()
        session = ExpectSession(transport)
        with pytest.raises(OSError, match="close failed"):
            await session.close()
        assert not session.closed
        await session.close()
        assert session.closed
        assert transport.close_calls == 2

    asyncio.run(scenario())


def test_text_regex_waits_for_complete_utf8_and_malformed_input_fails_closed() -> None:
    async def split_sequence() -> None:
        transport = ScriptedTransport([b"\xe2", b"\x82\xac"])
        async with ExpectSession(transport) as session:
            result = await session.expect(re.compile(r"."))
        assert result.match == "€".encode()

    async def malformed_text() -> None:
        transport = ScriptedTransport([b"\xff"])
        async with ExpectSession(transport) as session:
            with pytest.raises(ExpectDecodeError, match="malformed UTF-8"):
                await session.expect(re.compile(r"."))

    async def binary_safe() -> None:
        transport = ScriptedTransport([b"\xff"])
        async with ExpectSession(transport) as session:
            result = await session.expect(re.compile(rb"."))
        assert result.match == b"\xff"

    asyncio.run(split_sequence())
    asyncio.run(malformed_text())
    asyncio.run(binary_safe())


def test_buffer_overflow_is_terminal_before_matching_or_sending() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport([b"OK", b"overflow"])
        session = ExpectSession(transport, max_buffer_size=2)
        await session.__aenter__()
        assert session._reader_task is not None
        await session._reader_task

        with pytest.raises(ExpectBufferOverflow):
            await session.expect(b"OK")
        with pytest.raises(ExpectBufferOverflow):
            await session.send(b"unsafe-after-overflow")
        with pytest.raises(ExpectBufferOverflow):
            await session.expect(re.compile(rb"OK"))
        assert transport.writes == []
        await session.close()

    asyncio.run(scenario())


def test_multi_waiter_failed_close_cannot_clear_an_immediate_retry() -> None:
    class RacingCloseTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__([])
            self.close_calls = 0
            self.first_started = asyncio.Event()
            self.fail_first = asyncio.Event()
            self.retry_started = asyncio.Event()
            self.finish_retry = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                self.first_started.set()
                await self.fail_first.wait()
                raise OSError("first close failed")
            self.retry_started.set()
            await self.finish_retry.wait()
            self.closed = True

    async def scenario() -> None:
        transport = RacingCloseTransport()
        session = ExpectSession(transport)

        async def fail_then_retry() -> None:
            with pytest.raises(OSError, match="first close failed"):
                await session.close()
            await session.close()

        stale_waiter = asyncio.create_task(session.close())
        await transport.first_started.wait()
        retrying_waiter = asyncio.create_task(fail_then_retry())
        await asyncio.sleep(0)
        transport.fail_first.set()
        await transport.retry_started.wait()
        third_waiter = asyncio.create_task(session.close())
        await asyncio.sleep(0)
        assert transport.close_calls == 2
        transport.finish_retry.set()
        await retrying_waiter
        with pytest.raises(OSError, match="first close failed"):
            await stale_waiter
        await third_waiter
        assert session.closed

    asyncio.run(scenario())


def test_cancelled_close_waiter_does_not_leave_delayed_failure_stale() -> None:
    class DelayedFailureTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__([])
            self.close_calls = 0
            self.started = asyncio.Event()
            self.fail = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                self.started.set()
                await self.fail.wait()
                raise OSError("delayed close failure")
            self.closed = True

    async def scenario() -> None:
        transport = DelayedFailureTransport()
        session = ExpectSession(transport)
        waiter = asyncio.create_task(session.close())
        await transport.started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        transport.fail.set()
        while session._close_state.closing:
            await asyncio.sleep(0)

        await session.close()
        assert transport.close_calls == 2
        assert session.closed

    asyncio.run(scenario())


def test_old_failed_close_completion_cannot_clear_a_new_task() -> None:
    async def scenario() -> None:
        state = CloseState()

        async def fail() -> None:
            raise OSError("old failure")

        old_task = asyncio.create_task(fail())
        with pytest.raises(OSError, match="old failure"):
            await old_task

        new_task = asyncio.create_task(asyncio.sleep(60))
        state._task = new_task
        state._completed(old_task)
        assert state._task is new_task
        new_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await new_task

    asyncio.run(scenario())
