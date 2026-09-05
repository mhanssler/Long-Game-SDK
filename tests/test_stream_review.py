"""Adversarial lifecycle regressions; fake clients and local processes only."""
from __future__ import annotations

import asyncio
import threading

import pytest

from long_game_sdk.sdk.streams import BlockingStreamTransport, ExpectEOF, ExpectSession, SyncExpectSession


class IdleTransport:
    async def read(self, size: int = -1) -> bytes:
        await asyncio.Event().wait()
        return b""

    async def write(self, data: bytes) -> None:
        pass

    async def close(self) -> None:
        pass


def test_close_wakes_active_expect_even_when_reader_is_cancelled() -> None:
    async def scenario() -> None:
        session = ExpectSession(IdleTransport())
        waiter = asyncio.create_task(session.expect(b"missing"))
        await asyncio.sleep(0)
        await session.close()
        with pytest.raises(ExpectEOF):
            await asyncio.wait_for(waiter, 0.1)
    asyncio.run(scenario())


def test_event_history_has_byte_budget_even_when_matches_consume_buffer() -> None:
    async def scenario() -> None:
        class Transport(IdleTransport):
            def __init__(self) -> None:
                self.queue: asyncio.Queue[bytes] = asyncio.Queue()

            async def read(self, size: int = -1) -> bytes:
                return await self.queue.get()

        transport = Transport()
        async with ExpectSession(transport, max_buffer_size=4) as session:
            for _ in range(10):
                transport.queue.put_nowait(b"1234")
                await session.expect(b"1234")
            assert sum(len(event.data) for event in session.events) <= 4
    asyncio.run(scenario())


class BlockingClient:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.close_calls = 0

    def read(self, size: int) -> bytes:
        self.calls += 1
        self.started.set()
        self.release.wait()
        return b"data"

    def write(self, data: bytes) -> None:
        self.calls += 1
        self.started.set()
        self.release.wait()

    def close(self) -> None:
        self.close_calls += 1


@pytest.mark.parametrize("operation", ["read", "write"])
def test_cancelled_blocking_io_cannot_admit_replacement_workers(operation: str) -> None:
    async def scenario() -> None:
        client = BlockingClient()
        transport = BlockingStreamTransport(client)
        async def call() -> bytes | None:
            if operation == "read":
                return await transport.read(1)
            await transport.write(b"x")
            return None

        first = asyncio.create_task(call())
        try:
            async with asyncio.timeout(1):
                while not client.started.is_set():
                    await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(RuntimeError, match="indeterminate|poison"):
                await asyncio.wait_for(call(), 0.1)
            assert client.calls == 1
        finally:
            client.release.set()
            await transport.close()
    asyncio.run(scenario())


def test_blocking_write_admission_precedes_worker_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        client = BlockingClient()
        transport = BlockingStreamTransport(client)
        original = threading.Thread.start
        workers = 0

        def start(thread: threading.Thread) -> None:
            nonlocal workers
            if thread.name == "long-game-blocking-write":
                workers += 1
            original(thread)

        monkeypatch.setattr(threading.Thread, "start", start)
        tasks = [asyncio.create_task(transport.write(b"x")) for _ in range(20)]
        try:
            await asyncio.sleep(0.02)
            assert workers == 1
        finally:
            client.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await transport.close()
    asyncio.run(scenario())


def test_close_timeout_is_terminal_and_does_not_spawn_duplicate_close() -> None:
    async def scenario() -> None:
        class StuckClose(BlockingClient):
            def close(self) -> None:
                self.close_calls += 1
                self.release.wait()

        client = StuckClose()
        transport = BlockingStreamTransport(client, close_timeout=0.01)
        try:
            with pytest.raises(TimeoutError):
                await transport.close()
            with pytest.raises(RuntimeError, match="closing|poison|indeterminate"):
                await asyncio.wait_for(transport.write(b"forbidden"), 0.1)
            with pytest.raises(RuntimeError, match="closing|poison|indeterminate"):
                await transport.read(1)
            with pytest.raises(TimeoutError):
                await transport.close()
            assert client.close_calls == 1
        finally:
            client.release.set()
    asyncio.run(scenario())


def test_sync_close_failure_still_finalizes_loop() -> None:
    class Failure(IdleTransport):
        async def close(self) -> None:
            raise OSError("close failure")

    async def factory() -> Failure:
        return Failure()

    session = SyncExpectSession._open(factory, source="fake", encoding="utf-8", read_size=1)
    loop = session._runner.get_loop()
    try:
        with pytest.raises(OSError, match="close failure"):
            session.close()
        assert loop.is_closed()
        assert session.closed
    finally:
        if not loop.is_closed():
            session._runner.close()


@pytest.mark.parametrize("option", ["read_size", "max_buffer_size", "max_events"])
@pytest.mark.parametrize("value", [1.5, float("nan"), float("inf"), True])
def test_size_options_reject_nonintegers_before_acquisition(option: str, value: object) -> None:
    called = False

    async def factory() -> IdleTransport:
        nonlocal called
        called = True
        return IdleTransport()

    options = {"read_size": 1, option: value}
    with pytest.raises(ValueError, match=option):
        SyncExpectSession._open(factory, source="fake", encoding="utf-8", **options)
    assert not called


def test_reader_failure_precedes_buffered_success_and_blocks_send() -> None:
    from long_game_sdk.sdk.streams import ExpectReadError

    async def scenario() -> None:
        class Failure(IdleTransport):
            def __init__(self) -> None:
                self.reads = 0

            async def read(self, size: int = -1) -> bytes:
                self.reads += 1
                if self.reads == 1:
                    return b"OK"
                raise OSError("lost transport")

        async with ExpectSession(Failure()) as session:
            await session._reader_task
            with pytest.raises(ExpectReadError):
                await session.expect(b"OK")
            with pytest.raises(ExpectReadError):
                await session.send(b"forbidden")
    asyncio.run(scenario())


def test_truncated_utf8_at_eof_is_decode_error_not_success() -> None:
    import re
    from long_game_sdk.sdk.streams import ExpectDecodeError

    async def scenario() -> None:
        class Truncated(IdleTransport):
            def __init__(self) -> None:
                self.reads = 0

            async def read(self, size: int = -1) -> bytes:
                self.reads += 1
                return b"OK\xe2" if self.reads == 1 else b""

        async with ExpectSession(Truncated()) as session:
            await session._reader_task
            with pytest.raises(ExpectDecodeError):
                await session.expect(re.compile("OK"))
    asyncio.run(scenario())


def test_subprocess_close_deadline_includes_stalled_stdin() -> None:
    from long_game_sdk.sdk.streams import SubprocessTransport

    async def scenario() -> None:
        class Writer:
            def is_closing(self) -> bool:
                return False

            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                await asyncio.Event().wait()

        class Process:
            def __init__(self) -> None:
                self.stdout = object()
                self.stdin = Writer()
                self.returncode = None
                self.terminated = False

            async def wait(self) -> int:
                if not self.terminated:
                    await asyncio.Event().wait()
                self.returncode = -15
                return -15

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.terminated = True

        process = Process()
        transport = SubprocessTransport(process, close_timeout=0.01)
        try:
            await asyncio.wait_for(transport.close(), 0.1)
            assert process.terminated
        finally:
            # Avoid leaking the shielded cleanup task on the RED run.
            task = transport._close_state._task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_failed_blocking_close_does_not_reenable_io() -> None:
    async def scenario() -> None:
        class Failure(BlockingClient):
            def close(self) -> None:
                raise OSError("close failed")

        client = Failure()
        client.release.set()
        transport = BlockingStreamTransport(client)
        with pytest.raises(OSError):
            await transport.close()
        with pytest.raises(RuntimeError, match="closing|poison"):
            await transport.write(b"forbidden")
        assert client.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("stuck_operation", ["read", "close"])
def test_session_close_deadline_retains_single_cleanup(stuck_operation: str) -> None:
    async def scenario() -> None:
        class Resistant(IdleTransport):
            def __init__(self) -> None:
                self.release = asyncio.Event()
                self.started = asyncio.Event()
                self.close_calls = 0

            async def wait(self) -> None:
                self.started.set()
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        continue

            async def read(self, size: int = -1) -> bytes:
                if stuck_operation == "read":
                    await self.wait()
                else:
                    await super().read(size)
                return b""

            async def close(self) -> None:
                self.close_calls += 1
                if stuck_operation == "close":
                    await self.wait()

        transport = Resistant()
        session = ExpectSession(transport, close_timeout=0.01)
        await session.__aenter__()
        await asyncio.sleep(0)
        try:
            with pytest.raises(TimeoutError):
                await session.close()
            with pytest.raises(TimeoutError):
                await session.close()
            with pytest.raises(RuntimeError, match="closing"):
                await session.send(b"forbidden")
            assert transport.close_calls == 1
            assert not session.closed
        finally:
            transport.release.set()
            await session.close()
        assert session.closed
    asyncio.run(scenario())


def test_reader_cancelled_before_first_execution_is_terminal() -> None:
    from long_game_sdk.sdk.streams import ExpectReadError

    async def scenario() -> None:
        async with ExpectSession(IdleTransport()) as session:
            assert session._reader_task is not None
            session._reader_task.cancel()
            with pytest.raises(ExpectReadError):
                await asyncio.wait_for(session.expect(b"missing"), 0.1)
            with pytest.raises(ExpectReadError):
                await session.send(b"forbidden")

    asyncio.run(scenario())


def test_cancelled_reader_wakes_expect_with_terminal_error() -> None:
    from long_game_sdk.sdk.streams import ExpectReadError

    async def scenario() -> None:
        async with ExpectSession(IdleTransport()) as session:
            waiter = asyncio.create_task(session.expect(b"missing"))
            await asyncio.sleep(0)
            assert session._reader_task is not None
            session._reader_task.cancel()
            with pytest.raises(ExpectReadError):
                await asyncio.wait_for(waiter, 0.1)
            with pytest.raises(ExpectReadError):
                await session.send(b"forbidden")
    asyncio.run(scenario())


def test_subprocess_kill_wait_is_bounded() -> None:
    from long_game_sdk.sdk.streams import SubprocessTransport

    async def scenario() -> None:
        class Writer:
            def is_closing(self) -> bool:
                return True

        class Process:
            stdin = Writer()
            stdout = object()
            returncode = None
            killed = False

            async def wait(self) -> int:
                await asyncio.Event().wait()
                return 0

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                self.killed = True

        process = Process()
        transport = SubprocessTransport(process, close_timeout=0.01)
        waiter = asyncio.create_task(transport.close())
        try:
            done, _ = await asyncio.wait({waiter}, timeout=0.1)
            assert waiter in done, "post-kill wait exceeded its deadline"
            with pytest.raises(TimeoutError):
                await waiter
            assert process.killed
            with pytest.raises(RuntimeError, match="closing|closed"):
                await transport.write(b"forbidden")
        finally:
            task = transport._close_state._task
            if task is not None and not task.done():
                task.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["write", "close"])
def test_empty_read_retry_stops_after_other_operation_poisons_adapter(failure: str) -> None:
    async def scenario() -> None:
        class Client:
            def __init__(self) -> None:
                self.reads = 0

            def read(self, size: int) -> bytes:
                self.reads += 1
                return b"" if self.reads == 1 else b"unexpected retry"

            def write(self, data: bytes) -> None:
                raise OSError("write failed")

            def close(self) -> None:
                raise OSError("close failed")

        client = Client()
        transport = BlockingStreamTransport(client, empty_read_delay=0.05)
        reader = asyncio.create_task(transport.read(1))
        try:
            async with asyncio.timeout(1):
                while client.reads == 0:
                    await asyncio.sleep(0)
            # Let the empty-read result reach its retry delay.
            await asyncio.sleep(0.01)
            with pytest.raises(OSError):
                if failure == "write":
                    await transport.write(b"x")
                else:
                    await transport.close()
            with pytest.raises(RuntimeError, match="poison|closing"):
                await asyncio.wait_for(reader, 0.2)
            assert client.reads == 1
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_send_is_terminal_and_wakes_expect() -> None:
    from long_game_sdk.sdk.streams import ExpectReadError

    async def scenario() -> None:
        class StuckWrite(IdleTransport):
            async def write(self, data: bytes) -> None:
                await asyncio.Event().wait()

        async with ExpectSession(StuckWrite()) as session:
            waiter = asyncio.create_task(session.expect(b"missing"))
            send = asyncio.create_task(session.send(b"partial"))
            await asyncio.sleep(0)
            send.cancel()
            with pytest.raises(asyncio.CancelledError):
                await send
            with pytest.raises(ExpectReadError):
                await asyncio.wait_for(waiter, 0.1)
            with pytest.raises(ExpectReadError):
                await session.send(b"forbidden")
    asyncio.run(scenario())
