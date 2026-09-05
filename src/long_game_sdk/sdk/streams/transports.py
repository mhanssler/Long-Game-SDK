"""Standard-library asyncio transports and blocking-stream adaptation."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import threading
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypeVar

from ._close import CloseState


_T = TypeVar("_T")


async def _run_daemon(call: Callable[[], _T], *, name: str) -> _T:
    """Run one blocking call without using an executor that waits at shutdown."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()

    def finish(result: _T | None, error: BaseException | None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)  # type: ignore[arg-type]

    def worker() -> None:
        try:
            result, error = call(), None
        except BaseException as caught:
            result, error = None, caught
        try:
            loop.call_soon_threadsafe(finish, result, error)
        except RuntimeError:
            # A noncooperative daemon may outlive its event loop.
            pass

    threading.Thread(target=worker, name=name, daemon=True).start()
    return await future


class TCPTransport:
    """An asyncio TCP byte-stream transport."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._close_state = CloseState()

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        *,
        ssl: Any = None,
        server_hostname: str | None = None,
    ) -> TCPTransport:
        """Open a TCP connection using :func:`asyncio.open_connection`."""
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=ssl,
            server_hostname=server_hostname,
        )
        return cls(reader, writer)

    async def read(self, size: int = -1) -> bytes:
        return await self._reader.read(size)

    async def write(self, data: bytes) -> None:
        if self._close_state.started:
            raise RuntimeError("transport is closing or closed")
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        await self._close_state.close(self._close_impl, name="tcp-close")

    async def _close_impl(self) -> None:
        self._writer.close()
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._writer.wait_closed()


class SubprocessTransport:
    """A subprocess connected through asyncio stdin/stdout pipes."""

    def __init__(self, process: asyncio.subprocess.Process, *, close_timeout: float = 2.0) -> None:
        self._validate_close_timeout(close_timeout)
        if process.stdout is None or process.stdin is None:
            raise ValueError("subprocess must have stdin and stdout pipes")
        self.process = process
        self._reader = process.stdout
        self._writer = process.stdin
        self._close_timeout = close_timeout
        self._close_state = CloseState()

    @staticmethod
    def _validate_close_timeout(close_timeout: float) -> None:
        if not math.isfinite(close_timeout) or close_timeout <= 0:
            raise ValueError("close_timeout must be a finite positive number")

    @classmethod
    async def spawn(
        cls,
        program: str | os.PathLike[str],
        *args: str | os.PathLike[str],
        close_timeout: float = 2.0,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SubprocessTransport:
        """Spawn a child with stderr merged into the expected output stream."""
        cls._validate_close_timeout(close_timeout)
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        return cls(process, close_timeout=close_timeout)

    async def read(self, size: int = -1) -> bytes:
        return await self._reader.read(size)

    async def write(self, data: bytes) -> None:
        if self._close_state.started:
            raise RuntimeError("transport is closing or closed")
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        await self._close_state.close(self._close_impl, name="subprocess-close")

    async def _close_impl(self) -> None:
        if not self._writer.is_closing():
            self._writer.close()
            with contextlib.suppress(ConnectionError, BrokenPipeError, TimeoutError):
                async with asyncio.timeout(self._close_timeout):
                    await self._writer.wait_closed()
        if self.process.returncode is None:
            try:
                async with asyncio.timeout(self._close_timeout):
                    await self.process.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    async with asyncio.timeout(self._close_timeout):
                        await self.process.wait()
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    async with asyncio.timeout(self._close_timeout):
                        await self.process.wait()


class BlockingStreamTransport:
    """Adapt a blocking byte-stream-shaped object without optional dependencies.

    Method names are configurable for APIs such as ``read_raw``/``write_raw``.
    Calls execute in dedicated daemon threads. ``close_timeout`` bounds how long
    the adapter waits for the client's close method.
    """

    def __init__(
        self,
        stream: object,
        *,
        read_method: str = "read",
        write_method: str = "write",
        close_method: str = "close",
        flush_method: str | None = None,
        read_accepts_size: bool = True,
        encoding: str = "utf-8",
        empty_read_policy: Literal["timeout", "eof"] = "timeout",
        empty_read_delay: float = 0.01,
        close_timeout: float = 2.0,
    ) -> None:
        if empty_read_policy not in ("timeout", "eof"):
            raise ValueError("empty_read_policy must be 'timeout' or 'eof'")
        if not math.isfinite(empty_read_delay) or empty_read_delay <= 0:
            raise ValueError("empty_read_delay must be a finite positive number")
        if not math.isfinite(close_timeout) or close_timeout <= 0:
            raise ValueError("close_timeout must be a finite positive number")
        self._stream = stream
        self._read_method = read_method
        self._write_method = write_method
        self._close_method = close_method
        self._flush_method = flush_method
        self._read_accepts_size = read_accepts_size
        self._encoding = encoding
        self._empty_read_policy = empty_read_policy
        self._empty_read_delay = empty_read_delay
        self._close_timeout = close_timeout
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()
        self._poisoned = False
        self._close_error: TimeoutError | None = None
        self._close_state = CloseState()
        for method_name in (read_method, write_method, close_method):
            if not callable(getattr(stream, method_name, None)):
                raise TypeError(f"stream has no callable {method_name!r} method")
        if flush_method is not None and not callable(getattr(stream, flush_method, None)):
            raise TypeError(f"stream has no callable {flush_method!r} method")

    def _blocking_read(self, size: int) -> bytes:
        method = getattr(self._stream, self._read_method)
        value = method(size) if self._read_accepts_size else method()
        if value is None:
            return b""
        if isinstance(value, str):
            return value.encode(self._encoding)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise TypeError(f"blocking read returned unsupported type {type(value).__name__}")

    async def read(self, size: int = -1) -> bytes:
        self._check_io()
        async with self._read_lock:
            self._check_io()
            while not self._close_state.closed and not self._close_state.closing:
                # Every retry is a new worker admission: another operation may
                # have failed while this reader slept after an empty result.
                self._check_io()
                try:
                    data = await _run_daemon(
                        lambda: self._blocking_read(size),
                        name="long-game-blocking-read",
                    )
                except BaseException:
                    self._poisoned = True
                    raise
                if self._close_state.closed or self._close_state.closing:
                    return b""
                if data or self._empty_read_policy == "eof":
                    return data
                await asyncio.sleep(self._empty_read_delay)
            return b""

    def _check_io(self) -> None:
        if self._poisoned:
            raise RuntimeError("transport is poisoned after indeterminate I/O or close")
        if self._close_state.started:
            raise RuntimeError("transport is closing or closed")

    def _blocking_write(self, data: bytes) -> None:
        method = getattr(self._stream, self._write_method)
        written = method(data)
        if isinstance(written, int) and written < len(data):
            raise OSError(f"short blocking write: {written} of {len(data)} bytes")
        if self._flush_method is not None:
            getattr(self._stream, self._flush_method)()

    async def write(self, data: bytes) -> None:
        self._check_io()
        async with self._write_lock:
            self._check_io()
            payload = bytes(data)
            try:
                await _run_daemon(lambda: self._blocking_write(payload), name="long-game-blocking-write")
            except BaseException:
                self._poisoned = True
                raise

    async def close(self) -> None:
        self._poisoned = True
        await self._close_state.close(self._close_impl, name="blocking-stream-close")

    async def _close_impl(self) -> None:
        if self._close_error is not None:
            raise self._close_error
        try:
            async with asyncio.timeout(self._close_timeout):
                await _run_daemon(
                    getattr(self._stream, self._close_method),
                    name="long-game-blocking-close",
                )
        except TimeoutError as error:
            self._poisoned = True
            self._close_error = TimeoutError(f"blocking stream close exceeded {self._close_timeout} seconds")
            raise self._close_error from error
