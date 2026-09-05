"""Synchronous facade over the asyncio expect implementation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Any, Literal

from .core import AsyncTransport, ExpectMatch, ExpectSession, PatternLike, StreamEvent
from .transports import BlockingStreamTransport, SubprocessTransport, TCPTransport


def _require_no_running_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("SyncExpectSession cannot be used inside a running event loop; use ExpectSession")


class SyncExpectSession:
    """Blocking expect facade backed by one persistent asyncio event loop.

    Use it as a context manager, or call :meth:`close` explicitly. Each blocking
    operation runs the loop until completion; no polling or busy loop is used.
    """

    def __init__(self, runner: asyncio.Runner, session: ExpectSession) -> None:
        self._runner = runner
        self._session = session
        self._closed = False

    @classmethod
    def _open(
        cls,
        factory: Callable[[], Awaitable[AsyncTransport]],
        *,
        source: str,
        encoding: str,
        read_size: int,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
    ) -> SyncExpectSession:
        _require_no_running_loop()
        ExpectSession.validate_options(
            encoding=encoding,
            read_size=read_size,
            max_buffer_size=max_buffer_size,
            max_events=max_events,
        )
        runner = asyncio.Runner()

        async def create() -> ExpectSession:
            transport = await factory()
            try:
                session = ExpectSession(
                    transport,
                    source=source,
                    encoding=encoding,
                    read_size=read_size,
                    max_buffer_size=max_buffer_size,
                    max_events=max_events,
                )
                await session.__aenter__()
                return session
            except BaseException:
                await transport.close()
                raise

        try:
            session = runner.run(create())
        except BaseException:
            runner.close()
            raise
        return cls(runner, session)

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int,
        *,
        source: str = "tcp",
        encoding: str = "utf-8",
        read_size: int = 4096,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
        ssl: Any = None,
        server_hostname: str | None = None,
    ) -> SyncExpectSession:
        """Open a synchronous facade around a TCP transport."""

        async def connect() -> AsyncTransport:
            return await TCPTransport.connect(host, port, ssl=ssl, server_hostname=server_hostname)

        return cls._open(
            connect,
            source=source,
            encoding=encoding,
            read_size=read_size,
            max_buffer_size=max_buffer_size,
            max_events=max_events,
        )

    @classmethod
    def subprocess(
        cls,
        program: str | os.PathLike[str],
        *args: str | os.PathLike[str],
        close_timeout: float = 2.0,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        source: str = "process",
        encoding: str = "utf-8",
        read_size: int = 4096,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
    ) -> SyncExpectSession:
        """Spawn a subprocess and expose blocking expect operations."""

        async def spawn() -> AsyncTransport:
            return await SubprocessTransport.spawn(
                program,
                *args,
                close_timeout=close_timeout,
                cwd=cwd,
                env=env,
            )

        return cls._open(
            spawn,
            source=source,
            encoding=encoding,
            read_size=read_size,
            max_buffer_size=max_buffer_size,
            max_events=max_events,
        )

    @classmethod
    def blocking(
        cls,
        stream: object,
        *,
        read_method: str = "read",
        write_method: str = "write",
        close_method: str = "close",
        flush_method: str | None = None,
        read_accepts_size: bool = True,
        empty_read_policy: Literal["timeout", "eof"] = "timeout",
        empty_read_delay: float = 0.01,
        close_timeout: float = 2.0,
        source: str = "blocking",
        encoding: str = "utf-8",
        read_size: int = 4096,
        max_buffer_size: int = 1024 * 1024,
        max_events: int = 1000,
    ) -> SyncExpectSession:
        """Wrap a serial/SSH/VISA-like blocking object."""

        async def adapt() -> AsyncTransport:
            return BlockingStreamTransport(
                stream,
                read_method=read_method,
                write_method=write_method,
                close_method=close_method,
                flush_method=flush_method,
                read_accepts_size=read_accepts_size,
                encoding=encoding,
                empty_read_policy=empty_read_policy,
                empty_read_delay=empty_read_delay,
                close_timeout=close_timeout,
            )

        return cls._open(
            adapt,
            source=source,
            encoding=encoding,
            read_size=read_size,
            max_buffer_size=max_buffer_size,
            max_events=max_events,
        )

    @property
    def transport(self) -> AsyncTransport:
        return self._session.transport

    @property
    def events(self) -> tuple[StreamEvent, ...]:
        return self._session.events

    @property
    def buffer(self) -> bytes:
        return self._session.buffer

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> SyncExpectSession:
        if self._closed:
            raise RuntimeError("session is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _check_open(self) -> None:
        _require_no_running_loop()
        if self._closed:
            raise RuntimeError("session is closed")

    def expect(
        self,
        pattern: PatternLike,
        *,
        timeout: float | None = None,
        failures: tuple[PatternLike, ...] | list[PatternLike] = (),
    ) -> ExpectMatch:
        self._check_open()
        return self._runner.run(self._session.expect(pattern, timeout=timeout, failures=failures))

    def send(self, data: bytes) -> None:
        self._check_open()
        self._runner.run(self._session.send(data))

    def send_text(self, text: str, *, newline: bool = False) -> None:
        self._check_open()
        self._runner.run(self._session.send_text(text, newline=newline))

    def send_json(self, value: Any, *, newline: bool = True) -> None:
        self._check_open()
        self._runner.run(self._session.send_json(value, newline=newline))

    def close(self) -> None:
        """Close the transport, then finalize the owned event loop."""
        if self._closed:
            return
        _require_no_running_loop()
        try:
            self._runner.run(self._session.close())
        finally:
            self._runner.close()
            self._closed = True
