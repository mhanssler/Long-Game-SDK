"""Async expect-style byte streams.

The package intentionally depends only on Python's standard library. Optional
hardware clients can be integrated through :class:`BlockingStreamTransport`.
"""

from .core import (
    AsyncTransport,
    ExpectBufferOverflow,
    ExpectDecodeError,
    ExpectEOF,
    ExpectError,
    ExpectFailure,
    ExpectMatch,
    ExpectReadError,
    ExpectSession,
    ExpectTimeout,
    PatternLike,
    StreamEvent,
)
from .sync import SyncExpectSession
from .transports import BlockingStreamTransport, SubprocessTransport, TCPTransport

__all__ = [
    "AsyncTransport",
    "BlockingStreamTransport",
    "ExpectBufferOverflow",
    "ExpectDecodeError",
    "ExpectEOF",
    "ExpectError",
    "ExpectFailure",
    "ExpectMatch",
    "ExpectReadError",
    "ExpectSession",
    "ExpectTimeout",
    "PatternLike",
    "StreamEvent",
    "SubprocessTransport",
    "SyncExpectSession",
    "TCPTransport",
]
