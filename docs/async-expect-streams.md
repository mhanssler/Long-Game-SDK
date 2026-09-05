# Async expect streams

`long_game_sdk.sdk.streams` provides a small, standard-library-only byte-stream
API for consoles, network services, subprocesses, and optional hardware clients.

> **Physical-side-effect warning:** sending console or instrument commands can
> energize outputs, move actuators, erase/flash targets, or otherwise change
> hardware state. An `expect()` match confirms text only; it is not a safety
> interlock. Apply the SDK's normal preflight, limits, and operator controls
> before connecting this API to physical equipment.

## Async subprocess example

```python
import asyncio
import re
import sys

from long_game_sdk.sdk.streams import ExpectSession, SubprocessTransport


async def main() -> None:
    # A local echo child only: no instruments, OpenOCD, or hardware commands.
    transport = await SubprocessTransport.spawn(
        sys.executable, "-u", "-c",
        "import sys; print('READY 42', flush=True); "
        "print('echo:' + sys.stdin.readline(), end='', flush=True)",
    )
    async with ExpectSession(
        transport,
        source="local-echo",
        max_buffer_size=1024 * 1024,
        max_events=1000,
    ) as console:
        await console.expect(
            re.compile(rb"READY \d+"),
            timeout=10,
            failures=[b"Error:", re.compile(rb"FAILED", re.IGNORECASE)],
        )
        await console.send_text("hello", newline=True)
        assert (await console.expect(b"echo:hello\n", timeout=2)).match == b"echo:hello\n"


asyncio.run(main())
```

`expect()` accepts literal `bytes`/`str` or compiled byte/text regexes. Matching
uses the unconsumed aggregate buffer, so patterns can span read boundaries. A
successful match consumes through the end of that match. It returns an immutable
`ExpectMatch` with `before`, `match`, and `after` snapshots. Explicit failures,
timeouts, premature EOF, and buffer overflow raise `ExpectFailure`,
`ExpectTimeout`, `ExpectEOF`, and `ExpectBufferOverflow`, respectively. Transport
read failures or unexpected reader cancellation are terminal and take precedence
over buffered matches. Failed/cancelled sends are also terminal because partial
transmission is indeterminate. Subsequent expects/sends raise `ExpectReadError`;
the original send still raises its original exception or cancellation.

Only UTF-8 (including aliases such as `utf8`) is accepted for text matching and
sending. This deliberate restriction keeps text-regex character spans aligned
with byte offsets, including when a multibyte character is split across reads.
Text regexes inspect only the complete, decodable UTF-8 prefix, so they cannot
consume a partial trailing character. Malformed UTF-8 raises
`ExpectDecodeError` instead of matching replacement or surrogate characters.
An incomplete trailing code point at EOF is malformed, not a matchable prefix.
Literal bytes and byte regexes remain binary-safe; use them for arbitrary binary
protocols.

One `expect()` call may be active per session. An overlapping call is rejected
with `RuntimeError` rather than racing to consume shared bytes.

The unconsumed buffer is explicitly bounded by `max_buffer_size` (default 1 MiB).
Input that would exceed it is not appended and produces deterministic
`ExpectBufferOverflow`. `session.events` is a bounded immutable snapshot of the
newest `max_events` chunks (default 1000; use `0` to disable history), with an
additional byte budget equal to `max_buffer_size`. Oldest whole events are evicted
to satisfy both limits, even after matched input is consumed. These
bounds cap internally retained input and scan size (not regex CPU time); choose lower values for
untrusted or high-rate streams and ensure valid expected responses fit within
the buffer bound. Overflow is terminal for the session and takes precedence over
an earlier buffered match; subsequent expects and sends fail with the same
overflow instead of using a potentially truncated stream view.

Use an async context manager (or call `await session.close()`) so reader tasks
and transports are released. Concurrent close callers share one cleanup task,
and cancellation of a caller does not cancel cleanup. Close immediately stops new
session I/O and wakes an active expect with `ExpectEOF`, even if cleanup fails.
A failed close does not mark the async session closed and may be retried for
cleanup only; it never re-enables session I/O. `ExpectSession.close_timeout`
(default 10 seconds, finite and positive) bounds each caller's wait, including
custom transports whose close/read suppress cancellation. A timeout leaves the
single shielded cleanup task in flight; later close calls wait on the same task.
It is not proof that resources were released. Custom async transports must
eventually unblock reads and honor cancellation for event-loop shutdown to finish;
Python cannot forcibly stop an uncooperative coroutine or blocking thread.

## TCP and synchronous scripts

Use `await TCPTransport.connect(host, port)` with `ExpectSession` for async TCP.
Simple non-async scripts can use the dedicated synchronous facade:

```python
from long_game_sdk.sdk.streams import SyncExpectSession

with SyncExpectSession.tcp("127.0.0.1", 9000) as console:
    console.expect("login:", timeout=5)
    console.send_text("operator", newline=True)
```

The sync facade owns one persistent event loop and blocks on loop completion; it
does not poll. It must not be called from a thread already running an asyncio
event loop. The facade is single-threaded and must not be shared across threads.
It finalizes its owned event loop even when transport close raises; `closed` then
means the facade is unusable, not that client resource release was verified.
Session options are
validated before a TCP connection, subprocess, or blocking adapter is created;
if session entry later fails, the newly created transport is closed.

`SubprocessTransport.close_timeout` must be finite and positive. Close first
bounds stdin-close waiting, then waits for normal child exit, then terminates and
finally kills the direct child. The deadline is per phase, not a total deadline.
Signal races with an already-exited child are tolerated. Descendant process-tree
or process-group management is intentionally outside this API.

## Optional blocking clients

No serial, SSH, or VISA package is required. Wrap an installed client's blocking
object by naming its byte-oriented methods:

```python
from long_game_sdk.sdk.streams import BlockingStreamTransport, ExpectSession

# `instrument` might come from an optional VISA library.
transport = BlockingStreamTransport(
    instrument,
    read_method="read_raw",
    write_method="write_raw",
    read_accepts_size=False,
    empty_read_policy="timeout",  # safe default for serial/VISA timeout reads
)
async with ExpectSession(transport, source="instrument") as console:
    await console.send(b"*IDN?\n")
    identity = await console.expect(b"\n", timeout=2)
```

Blocking serial/VISA APIs commonly return `b""` or `None` for a read timeout,
not EOF. Therefore `empty_read_policy="timeout"` is the default: the adapter
retries after `empty_read_delay` (default 10 ms), avoiding a busy loop. Set
`empty_read_policy="eof"` only when an empty result definitively means end of
stream. Use an `expect()` timeout to bound the overall wait.

Blocking operations run in dedicated daemon threads; the event loop itself is
never blocked. Independent threads mean stuck reads
cannot exhaust asyncio's shared executor, starve the unblocking close call, or
make `asyncio.Runner.close()` wait for executor shutdown. `close_timeout`
(default 2 seconds, finite and positive) bounds how long the adapter waits for
the client's close method. Closing may run concurrently with a read, so the
wrapped client must permit that when close is expected to unblock read.

Python cannot forcibly stop a thread. A noncooperative read or close may continue
running in its daemon thread after the adapter reports a close timeout or the
event loop shuts down. Daemon status prevents that thread alone from holding
interpreter shutdown hostage; it does **not** kill the operation, release its
resources, undo physical side effects, or make an unsafe client thread-safe.
Configure finite device-level timeouts whenever possible. If the client cannot
safely close during a read, arrange application-level quiescence before close.

The adapter validates read values, detects reported short writes, serializes
writes, and performs close in its own daemon thread. Read and write admission each
use an asyncio lock **before** starting workers, so queued callers do not create
waiting OS threads. Cancellation or failure of an admitted blocking I/O call
permanently poisons the adapter; a cancelled worker may still be running, so no
replacement reads or writes are admitted. Empty-read retries recheck this state
before starting each worker, including after a concurrent write or close failure.
Close can run concurrently with reads
and writes and must be safe for the wrapped client. Any close attempt stops I/O;
a close timeout is terminal and repeat closes re-raise the timeout without
launching another close worker. Ordinary completed close errors may be retried
for cleanup only. It does not make an unknown third-party object thread-safe
beyond those boundaries.

These are byte transports, not hardware-command authorization. They do not
override the SDK's dry-run-only OpenOCD policy or its strict identity and
same-transport, per-write instrument authorization requirements. Use trusted
regexes: Python's regex engine has no hard CPU deadline, and a pathological regex
can block the event loop despite an `expect()` timeout.
