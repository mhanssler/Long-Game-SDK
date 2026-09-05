"""Cancellation-safe shared close-task state for stream implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class CloseState:
    """Share close work while making failed operations immediately retryable."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.started = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closing(self) -> bool:
        return self._task is not None

    async def close(self, operation: Callable[[], Awaitable[None]], *, name: str | None = None) -> None:
        self.started = True
        if self._closed:
            return
        task = self._task
        if task is None:
            task = asyncio.create_task(self._run(operation), name=name)
            self._task = task
            task.add_done_callback(self._completed)
        await asyncio.shield(task)

    async def _run(self, operation: Callable[[], Awaitable[None]]) -> None:
        await operation()
        self._closed = True

    def _completed(self, task: asyncio.Task[None]) -> None:
        failed = task.cancelled() or task.exception() is not None
        if failed and self._task is task:
            self._task = None
