"""Core expect session primitives."""

from __future__ import annotations

import asyncio
import codecs
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Pattern, Protocol, TypeAlias, cast, runtime_checkable

from ._close import CloseState


@runtime_checkable
class AsyncTransport(Protocol):
    """Minimal byte-stream transport consumed by :class:`ExpectSession`."""

    async def read(self, size: int = -1) -> bytes:
        """Read up to *size* bytes; return ``b\"\"`` at EOF."""
        ...

    async def write(self, data: bytes) -> None:
        """Write all bytes, including any required flush/drain."""
        ...

    async def close(self) -> None:
        """Close the stream and release its resources."""
        ...


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """An immutable chunk observed on a stream."""

    source: str
    data: bytes
    timestamp: datetime


PatternLike: TypeAlias = bytes | str | Pattern[bytes] | Pattern[str]


@dataclass(frozen=True, slots=True)
class ExpectMatch:
    """A successful match and the bytes surrounding it at match time."""

    before: bytes
    match: bytes
    after: bytes
    pattern: PatternLike


class ExpectError(Exception):
    """Base class for expect operation errors."""


class ExpectTimeout(ExpectError, TimeoutError):
    """Raised when an expected pattern is not seen before the timeout."""

    def __init__(self, timeout: float | None, buffer: bytes) -> None:
        self.timeout = timeout
        self.buffer = buffer
        super().__init__(f"pattern not observed within {timeout!r} seconds")


class ExpectEOF(ExpectError, EOFError):
    """Raised when EOF arrives before a requested pattern."""

    def __init__(self, buffer: bytes) -> None:
        self.buffer = buffer
        super().__init__("stream reached EOF before pattern was observed")


class ExpectFailure(ExpectError):
    """Raised when an explicit failure pattern is observed."""

    def __init__(self, pattern: PatternLike, match: bytes, buffer: bytes) -> None:
        self.pattern = pattern
        self.match = match
        self.buffer = buffer
        super().__init__(f"failure pattern observed: {match!r}")


class ExpectReadError(ExpectError):
    """Raised when transport I/O has failed or become indeterminate."""


class ExpectDecodeError(ExpectError, UnicodeError):
    """Raised when text matching encounters malformed UTF-8 input."""

    def __init__(self, buffer: bytes) -> None:
        self.buffer = buffer
        super().__init__("text regex cannot match malformed UTF-8 input")


class ExpectBufferOverflow(ExpectError):
    """Raised when unconsumed input reaches the configured safety bound."""

    def __init__(self, limit: int, buffer: bytes) -> None:
        self.limit = limit
        self.buffer = buffer
        super().__init__(f"unconsumed stream buffer exceeded {limit} bytes")


@dataclass(frozen=True, slots=True)
class _Found:
    start: int
    end: int
    data: bytes


def _search(pattern: PatternLike, data: bytes, encoding: str, *, eof: bool = False) -> _Found | None:
    if isinstance(pattern, bytes):
        start = data.find(pattern)
        return None if start < 0 else _Found(start, start + len(pattern), pattern)
    if isinstance(pattern, str):
        needle = pattern.encode(encoding)
        start = data.find(needle)
        return None if start < 0 else _Found(start, start + len(needle), needle)

    regex_pattern = pattern.pattern
    if isinstance(regex_pattern, bytes):
        byte_match = cast(Pattern[bytes], pattern).search(data)
        if byte_match is None:
            return None
        start, end = byte_match.span()
        return _Found(start, end, data[start:end])

    try:
        text = data.decode(encoding)
        complete_data = data
    except UnicodeDecodeError as error:
        if eof or error.reason != "unexpected end of data" or error.end != len(data):
            raise ExpectDecodeError(data) from error
        complete_data = data[: error.start]
        text = complete_data.decode(encoding)
    text_match = cast(Pattern[str], pattern).search(text)
    if text_match is None:
        return None
    char_start, char_end = text_match.span()
    start = len(text[:char_start].encode(encoding))
    end = len(text[:char_end].encode(encoding))
    return _Found(start, end, complete_data[start:end])


class ExpectSession:
    """Continuously collect transport bytes and provide expect-style matching."""

    def __init__(
        self,
        transport: AsyncTransport,
        *,
        source: str = "stream",
        encoding: str = "utf-8",
        read_size: int = 4096,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
        close_timeout: float = 10.0,
    ) -> None:
        self.validate_options(
            encoding=encoding,
            read_size=read_size,
            max_buffer_size=max_buffer_size,
            max_events=max_events,
        )
        self.transport = transport
        if not math.isfinite(close_timeout) or close_timeout <= 0:
            raise ValueError("close_timeout must be a finite positive number")
        self.close_timeout = close_timeout
        self.source = source
        self.encoding = encoding
        self.read_size = read_size
        self.max_buffer_size = max_buffer_size
        self.max_events = max_events
        self._buffer = bytearray()
        self._events: deque[StreamEvent] = deque()
        self._event_bytes = 0
        self._stopping = False
        self._condition = asyncio.Condition()
        self._reader_task: asyncio.Task[None] | None = None
        self._eof = False
        self._read_error: BaseException | None = None
        self._close_state = CloseState()
        self._expect_lock = asyncio.Lock()

    @staticmethod
    def validate_options(
        *,
        encoding: str,
        read_size: int,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
    ) -> None:
        """Validate options without acquiring or creating a transport."""
        try:
            codec_name = codecs.lookup(encoding).name
        except LookupError as error:
            raise ValueError(f"unknown encoding: {encoding!r}") from error
        if codec_name != "utf-8":
            raise ValueError("only UTF-8 encoding is supported for byte-safe text matching")
        if type(read_size) is not int or read_size <= 0:
            raise ValueError("read_size must be positive")
        if type(max_buffer_size) is not int or max_buffer_size <= 0:
            raise ValueError("max_buffer_size must be positive")
        if type(max_events) is not int or max_events < 0:
            raise ValueError("max_events must be non-negative")

    @property
    def events(self) -> tuple[StreamEvent, ...]:
        """A stable snapshot of the bounded chunk history."""
        return tuple(self._events)

    @property
    def buffer(self) -> bytes:
        """Currently unconsumed bytes."""
        return bytes(self._buffer)

    @property
    def closed(self) -> bool:
        return self._close_state.closed

    async def __aenter__(self) -> ExpectSession:
        self._ensure_reader()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    def _ensure_reader(self) -> None:
        if self._stopping or self._close_state.closed or self._close_state.closing:
            raise RuntimeError("session is closing or closed")
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_forever(), name=f"expect-reader:{self.source}")
            self._reader_task.add_done_callback(self._reader_completed)

    def _reader_completed(self, task: asyncio.Task[None]) -> None:
        # A task cancelled before its first step never enters the coroutine's
        # exception handler, but must still publish terminal state to waiters.
        if task.cancelled() and not self._stopping and self._read_error is None:
            self._read_error = asyncio.CancelledError("reader cancelled before starting")
            asyncio.create_task(self._notify_reader_stopped())

    async def _notify_reader_stopped(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def _read_forever(self) -> None:
        try:
            while True:
                data = await self.transport.read(self.read_size)
                async with self._condition:
                    if not data:
                        self._eof = True
                        self._condition.notify_all()
                        return
                    chunk = bytes(data)
                    if len(self._buffer) + len(chunk) > self.max_buffer_size:
                        self._read_error = ExpectBufferOverflow(self.max_buffer_size, bytes(self._buffer))
                        self._condition.notify_all()
                        return
                    self._buffer.extend(chunk)
                    if self.max_events:
                        while self._events and (
                            len(self._events) >= self.max_events
                            or self._event_bytes + len(chunk) > self.max_buffer_size
                        ):
                            self._event_bytes -= len(self._events.popleft().data)
                        self._events.append(StreamEvent(self.source, chunk, datetime.now(UTC)))
                        self._event_bytes += len(chunk)
                    self._condition.notify_all()
        except asyncio.CancelledError as error:
            async with self._condition:
                if not self._stopping and self._read_error is None:
                    self._read_error = error
                self._condition.notify_all()
            raise
        except BaseException as error:
            async with self._condition:
                if not self._close_state.closing:
                    self._read_error = error
                self._condition.notify_all()

    async def expect(
        self,
        pattern: PatternLike,
        *,
        timeout: float | None = None,
        failures: tuple[PatternLike, ...] | list[PatternLike] = (),
    ) -> ExpectMatch:
        """Wait for *pattern*, consuming bytes through the match.

        One expect operation may be active at a time; overlapping calls are
        rejected. Failure patterns win when they start no later than success.
        """
        if self._expect_lock.locked():
            raise RuntimeError("another expect operation is already active")
        async with self._expect_lock:
            self._ensure_reader()
            try:
                async with asyncio.timeout(timeout):
                    async with self._condition:
                        while True:
                            current = bytes(self._buffer)
                            if isinstance(self._read_error, ExpectBufferOverflow):
                                raise self._read_error
                            if self._read_error is not None:
                                raise ExpectReadError("transport reader failed") from self._read_error
                            if self._stopping:
                                raise ExpectEOF(current)
                            wanted = _search(pattern, current, self.encoding, eof=self._eof)
                            failed: tuple[PatternLike, _Found] | None = None
                            for failure_pattern in failures:
                                found = _search(failure_pattern, current, self.encoding, eof=self._eof)
                                if found is not None and (failed is None or found.start < failed[1].start):
                                    failed = (failure_pattern, found)
                            if failed is not None and (wanted is None or failed[1].start <= wanted.start):
                                raise ExpectFailure(failed[0], failed[1].data, current)
                            if wanted is not None:
                                result = ExpectMatch(
                                    before=current[: wanted.start],
                                    match=wanted.data,
                                    after=current[wanted.end :],
                                    pattern=pattern,
                                )
                                del self._buffer[: wanted.end]
                                return result
                            if self._read_error is not None:
                                raise ExpectReadError("transport reader failed") from self._read_error
                            if self._eof:
                                raise ExpectEOF(current)
                            await self._condition.wait()
            except TimeoutError as error:
                raise ExpectTimeout(timeout, bytes(self._buffer)) from error

    async def send(self, data: bytes) -> None:
        """Send bytes unchanged."""
        self._ensure_reader()
        if isinstance(self._read_error, ExpectBufferOverflow):
            raise self._read_error
        if self._read_error is not None:
            raise ExpectReadError("transport reader failed") from self._read_error
        try:
            await self.transport.write(bytes(data))
        except BaseException as error:
            async with self._condition:
                if self._read_error is None:
                    self._read_error = error
                self._condition.notify_all()
            raise

    async def send_text(self, text: str, *, newline: bool = False) -> None:
        """Encode and send UTF-8 text, optionally followed by a newline."""
        await self.send((text + ("\n" if newline else "")).encode(self.encoding))

    async def send_json(self, value: Any, *, newline: bool = True) -> None:
        """Serialize compact JSON and send it as text."""
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        await self.send_text(payload, newline=newline)

    async def close(self) -> None:
        """Close the transport and reader through one cancellation-safe task."""
        self._stopping = True
        # Timeout cancels only this waiter; CloseState retains shielded cleanup.
        async with asyncio.timeout(self.close_timeout):
            await self._close_state.close(self._close_impl, name=f"expect-close:{self.source}")

    async def _close_impl(self) -> None:
        async with self._condition:
            self._eof = True
            self._condition.notify_all()
        try:
            # Closing first lets cooperative clients unblock their reads.
            await self.transport.close()
        finally:
            reader = self._reader_task
            if reader is not None and not reader.done():
                reader.cancel()
            if reader is not None:
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
