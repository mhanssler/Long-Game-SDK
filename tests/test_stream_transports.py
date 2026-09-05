from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import sys
import threading

import pytest

from long_game_sdk.sdk.streams import (
    AsyncTransport,
    BlockingStreamTransport,
    ExpectSession,
    SubprocessTransport,
    TCPTransport,
)


def test_tcp_transport_uses_real_asyncio_streams() -> None:
    async def scenario() -> None:
        received = bytearray()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            received.extend(await reader.readexactly(5))
            writer.write(b"REA")
            await writer.drain()
            writer.write(b"DY\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            transport = await TCPTransport.connect("127.0.0.1", port)
            assert isinstance(transport, AsyncTransport)
            async with ExpectSession(transport, source="tcp") as session:
                await session.send(b"hello")
                result = await session.expect("READY", timeout=1)
            assert result.match == b"READY"
        assert received == b"hello"

    asyncio.run(scenario())


def test_subprocess_transport_uses_asyncio_subprocess_pipes() -> None:
    async def scenario() -> None:
        program = (
            "import sys,time; "
            "sys.stdout.buffer.write(b'prom'); sys.stdout.buffer.flush(); time.sleep(.02); "
            "sys.stdout.buffer.write(b'pt> '); sys.stdout.buffer.flush(); "
            "line=sys.stdin.buffer.readline(); sys.stdout.buffer.write(b'got:'+line); sys.stdout.buffer.flush()"
        )
        transport = await SubprocessTransport.spawn(sys.executable, "-u", "-c", program)
        async with ExpectSession(transport, source="process") as session:
            await session.expect(b"prompt> ", timeout=1)
            await session.send_text("ping", newline=True)
            result = await session.expect(b"got:ping\n", timeout=1)
        assert result.match == b"got:ping\n"
        assert transport.process.returncode is not None

    asyncio.run(scenario())


def test_subprocess_close_terminates_a_running_child() -> None:
    async def scenario() -> None:
        transport = await SubprocessTransport.spawn(
            sys.executable, "-c", "import time; time.sleep(60)", close_timeout=0.2
        )
        await transport.close()
        assert transport.process.returncode is not None

    asyncio.run(scenario())


def test_blocking_adapter_runs_configurable_operations_off_loop() -> None:
    class BlockingDevice:
        def __init__(self) -> None:
            self.input: queue.Queue[bytes] = queue.Queue()
            self.input.put(b"instrument RE")
            self.input.put(b"ADY")
            self.writes: list[bytes] = []
            self.thread_ids: list[int] = []
            self.closed = False

        def read_raw(self) -> bytes:
            self.thread_ids.append(threading.get_ident())
            try:
                return self.input.get(timeout=0.2)
            except queue.Empty:
                return b""

        def write_raw(self, data: bytes) -> None:
            self.thread_ids.append(threading.get_ident())
            self.writes.append(data)

        def close(self) -> None:
            self.thread_ids.append(threading.get_ident())
            self.closed = True

    async def scenario() -> None:
        loop_thread = threading.get_ident()
        device = BlockingDevice()
        transport = BlockingStreamTransport(
            device,
            read_method="read_raw",
            write_method="write_raw",
            read_accepts_size=False,
        )
        async with ExpectSession(transport, source="visa") as session:
            await session.expect("READY", timeout=1)
            await session.send(b"MEAS?")
        assert device.writes == [b"MEAS?"]
        assert device.closed
        assert device.thread_ids
        assert all(thread_id != loop_thread for thread_id in device.thread_ids)

    asyncio.run(scenario())


def test_blocking_empty_reads_default_to_timeouts_without_busy_loop() -> None:
    class TimeoutThenData:
        def __init__(self) -> None:
            self.reads = 0

        def read(self, size: int) -> bytes:
            self.reads += 1
            return b"" if self.reads == 1 else b"ready"

        def write(self, data: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    async def scenario() -> None:
        stream = TimeoutThenData()
        transport = BlockingStreamTransport(stream, empty_read_delay=0.01)
        assert await transport.read(10) == b"ready"
        assert stream.reads == 2

    asyncio.run(scenario())


def test_blocking_empty_read_can_explicitly_mean_eof() -> None:
    class EmptyStream:
        def read(self, size: int) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    async def scenario() -> None:
        transport = BlockingStreamTransport(EmptyStream(), empty_read_policy="eof")
        assert await transport.read(10) == b""

    asyncio.run(scenario())


def test_blocking_empty_read_configuration_is_validated() -> None:
    class Stream:
        read = write = close = lambda *args: None

    with pytest.raises(ValueError, match="empty_read_policy"):
        BlockingStreamTransport(Stream(), empty_read_policy="invalid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty_read_delay"):
        BlockingStreamTransport(Stream(), empty_read_delay=0)
    with pytest.raises(ValueError, match="close_timeout"):
        BlockingStreamTransport(Stream(), close_timeout=float("inf"))


def test_subprocess_close_timeout_is_validated_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def unexpected_spawn(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)

    async def scenario() -> None:
        for invalid in (0, -1, float("inf"), float("nan")):
            with pytest.raises(ValueError, match="close_timeout"):
                await SubprocessTransport.spawn(sys.executable, close_timeout=invalid)
        assert not called

    asyncio.run(scenario())


def test_subprocess_close_tolerates_exit_during_terminate_signal() -> None:
    class Reader:
        async def read(self, size: int = -1) -> bytes:
            return b""

    class Writer:
        def is_closing(self) -> bool:
            return True

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

        def write(self, data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

    class Process:
        def __init__(self) -> None:
            self.stdout = Reader()
            self.stdin = Writer()
            self.returncode: int | None = None
            self.waits = 0

        async def wait(self) -> int:
            self.waits += 1
            if self.waits == 1:
                await asyncio.sleep(1)
            return 0

        def terminate(self) -> None:
            self.returncode = 0
            raise ProcessLookupError

        def kill(self) -> None:
            raise AssertionError("kill should not be needed")

    async def scenario() -> None:
        process = Process()
        transport = SubprocessTransport(process, close_timeout=0.01)  # type: ignore[arg-type]
        await transport.close()
        assert process.waits == 2

    asyncio.run(scenario())


def test_blocking_close_is_not_starved_by_a_stuck_read() -> None:
    class CloseUnblocksRead:
        def __init__(self) -> None:
            self.read_started = threading.Event()
            self.release_read = threading.Event()
            self.read_thread_daemon: bool | None = None
            self.closed = False

        def read(self, size: int) -> bytes:
            self.read_thread_daemon = threading.current_thread().daemon
            self.read_started.set()
            self.release_read.wait()
            return b""

        def write(self, data: bytes) -> None:
            pass

        def close(self) -> None:
            self.closed = True
            self.release_read.set()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        device = CloseUnblocksRead()
        transport = BlockingStreamTransport(device, empty_read_policy="eof", close_timeout=0.1)
        read_task = asyncio.create_task(transport.read(1))
        try:
            while not device.read_started.is_set():
                await asyncio.sleep(0)
            await asyncio.wait_for(transport.close(), 0.2)
            assert device.closed
            assert device.read_thread_daemon is True
            assert await read_task == b""
        finally:
            device.release_read.set()
            if not read_task.done():
                await read_task
            executor.shutdown(wait=True)

    asyncio.run(scenario())


def test_blocking_close_has_a_finite_deadline_for_noncooperative_clients() -> None:
    class StuckClose:
        def __init__(self) -> None:
            self.close_started = threading.Event()
            self.release_close = threading.Event()

        def read(self, size: int) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            pass

        def close(self) -> None:
            self.close_started.set()
            self.release_close.wait()

    async def scenario() -> None:
        device = StuckClose()
        transport = BlockingStreamTransport(device, close_timeout=0.02)
        try:
            with pytest.raises(TimeoutError, match="blocking stream close exceeded"):
                await transport.close()
            assert device.close_started.is_set()
        finally:
            device.release_close.set()

    asyncio.run(scenario())
