"""Read-only, session-independent transport diagnostic primitives.

The functions in this module deliberately return :class:`ProbeResult` for both
success and failure.  They do not open instrument sessions, change networking
state, or terminate processes.  Optional keyword-only collaborators are test
seams and also make platform-specific behavior replaceable by applications.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import inspect
import json
import multiprocessing
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
from time import monotonic
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """A stable structured result shared by all transport probes."""

    probe: str
    target: str | None
    ok: bool
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0


Runner = Callable[..., Any]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _worker_visa(connection: Any) -> None:
    manager: Any = None
    try:
        import pyvisa

        manager = pyvisa.ResourceManager()
        connection.send((True, [str(item) for item in manager.list_resources()]))
    except BaseException as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
        connection.close()


def _worker_connections(connection: Any) -> None:
    try:
        import psutil

        rows = []
        for item in psutil.net_connections(kind="tcp"):
            local_ip, local_port = _address_parts(item.laddr)
            remote_ip, remote_port = _address_parts(item.raddr)
            process = None
            if item.pid is not None:
                try:
                    process = {"pid": item.pid, "name": psutil.Process(item.pid).name()}
                except Exception:
                    process = {"pid": item.pid, "name": None}
            rows.append({"local": (local_ip, local_port), "remote": (remote_ip, remote_port),
                         "status": str(item.status), "pid": item.pid, "process": process})
        connection.send((True, rows))
    except BaseException as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _worker_history(connection: Any, path: str) -> None:
    try:
        connection.send((True, _read_jsonl(Path(path))))
    except BaseException as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _run_spawn_worker(worker: Callable[..., None], args: tuple[Any, ...], timeout: float) -> Any:
    """Run a top-level default collaborator in a killable spawn child."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(sender, *args), daemon=False)
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            process.terminate()
            process.join()
            raise TimeoutError("diagnostic collaborator timed out")
        ok, payload = receiver.recv()
        process.join(timeout=0.2)
        if process.is_alive():
            process.terminate()
            process.join()
        if not ok:
            raise RuntimeError(payload)
        return payload
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join()


def _bounded_default_visa_listing(timeout: float) -> list[str]:
    return list(_run_spawn_worker(_worker_visa, (), timeout))


def _invoke_with_timeout(callback: Callable[..., Any], timeout: float, *args: Any) -> Any:
    """Pass a timeout to collaborators that advertise timeout support."""
    try:
        parameters = inspect.signature(callback).parameters.values()
        supports_timeout = any(
            (parameter.name == "timeout" and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY)
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_timeout = False
    if supports_timeout:
        return callback(*args, timeout=timeout)
    return callback(*args)


def _result(
    probe: str,
    target: str | None,
    ok: bool,
    status: str,
    started: float,
    *,
    details: dict[str, Any] | None = None,
    error: object | None = None,
) -> ProbeResult:
    return ProbeResult(
        probe=probe,
        target=target,
        ok=ok,
        status=status,
        details=details or {},
        error=None if error is None else str(error),
        duration_ms=round((monotonic() - started) * 1000, 3),
    )


def _completed_error(completed: Any) -> str | None:
    message = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "")).strip()
    return message or None


def ping_host(
    ip: str,
    *,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> ProbeResult:
    """Send one ICMP echo with a two-second OS and process timeout."""
    started = monotonic()
    system = platform_name or platform.system()
    if system == "Windows":
        command = ["ping", "-n", "1", "-w", "2000", ip]
    elif system == "Darwin":
        command = ["ping", "-c", "1", "-W", "2000", ip]
    else:
        command = ["ping", "-c", "1", "-W", "2", ip]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=2.5, check=False)
        if completed.returncode == 0:
            return _result("ping", ip, True, "reachable", started)
        return _result("ping", ip, False, "unreachable", started, error=_completed_error(completed))
    except subprocess.TimeoutExpired as exc:
        return _result("ping", ip, False, "timeout", started, error=exc)
    except Exception as exc:
        return _result("ping", ip, False, "unavailable", started, error=exc)


def _default_gateway_resolver(
    *, runner: Runner = subprocess.run, platform_name: str | None = None
) -> str | None:
    system = platform_name or platform.system()
    if system == "Windows":
        command = ["route", "print", "0.0.0.0"]
    elif system == "Darwin":
        command = ["route", "-n", "get", "default"]
    else:
        command = ["ip", "route", "show", "default"]
    completed = runner(command, capture_output=True, text=True, timeout=2.0, check=False)
    if completed.returncode != 0:
        return None
    output = str(completed.stdout)
    if system == "Darwin":
        match = re.search(r"^\s*gateway:\s*(\S+)", output, re.MULTILINE)
    elif system == "Windows":
        match = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d{1,3}(?:\.\d{1,3}){3})", output, re.MULTILINE)
    else:
        match = re.search(r"\bdefault\s+via\s+(\S+)", output)
    return match.group(1) if match else None


def ping_gateway(
    *,
    gateway_resolver: Callable[[], str | None] | None = None,
    ping: Callable[[str], ProbeResult] = ping_host,
) -> ProbeResult:
    """Resolve the default gateway and perform the same bounded ping probe."""
    started = monotonic()
    try:
        gateway = (gateway_resolver or _default_gateway_resolver)()
    except Exception as exc:
        return _result("ping_gateway", None, False, "unavailable", started, error=exc)
    if not gateway:
        return _result("ping_gateway", None, False, "not_found", started, error="default gateway not found")
    try:
        ping_result = ping(gateway)
    except Exception as exc:
        return _result("ping_gateway", gateway, False, "unavailable", started, error=exc)
    return replace(
        ping_result,
        probe="ping_gateway",
        target=gateway,
        details={**ping_result.details, "gateway": gateway},
        duration_ms=round((monotonic() - started) * 1000, 3),
    )


_MAC_PATTERN = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})(?![0-9A-Fa-f])")


def arp_lookup(
    ip: str,
    *,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
    vendor_lookup: Callable[[str], str | None] | None = None,
    expected_vendor_prefix: str | None = None,
    expected_oui_prefix: str | None = None,
) -> ProbeResult:
    """Read the host ARP/neighbor cache and report MAC/vendor evidence.

    No discovery packet is generated here.  ``answers`` means the operating
    system currently has a complete neighbor-cache answer for the address.
    """
    started = monotonic()
    system = platform_name or platform.system()
    command = ["arp", "-a", ip] if system == "Windows" else ["arp", "-n", ip]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=2.0, check=False)
        output = f"{getattr(completed, 'stdout', '')}\n{getattr(completed, 'stderr', '')}"
        match = _MAC_PATTERN.search(output)
        mac = match.group(1).replace("-", ":").upper() if match else None
        incomplete = bool(re.search(r"\b(?:incomplete|failed|no entry)\b", output, re.IGNORECASE))
        answers = completed.returncode == 0 and mac is not None and not incomplete
        vendor = None
        if answers and mac is not None and vendor_lookup is not None:
            try:
                vendor = vendor_lookup(mac)
            except Exception:
                vendor = None
        prefix_results: list[bool | None] = []
        if expected_vendor_prefix is not None:
            prefix_results.append(None if vendor is None else vendor.casefold().startswith(expected_vendor_prefix.casefold()))
        if expected_oui_prefix is not None:
            normalized_mac = None if mac is None else re.sub(r"[^0-9A-Fa-f]", "", mac).casefold()
            normalized_prefix = re.sub(r"[^0-9A-Fa-f]", "", expected_oui_prefix).casefold()
            prefix_results.append(None if normalized_mac is None else normalized_mac.startswith(normalized_prefix))
        vendor_prefix_match: bool | None
        if not prefix_results or any(value is None for value in prefix_results):
            vendor_prefix_match = False if False in prefix_results else None
        else:
            vendor_prefix_match = all(prefix_results)
        expectation_status = (
            "not_requested" if not prefix_results else
            "unavailable" if vendor_prefix_match is None else
            "match" if vendor_prefix_match else "mismatch"
        )
        details = {
            "answers": answers,
            "mac": mac,
            "vendor": vendor,
            "vendor_prefix_match": vendor_prefix_match,
            "expectation_status": expectation_status,
        }
        if answers:
            if vendor_prefix_match is False:
                return _result("arp", ip, False, "mismatch", started, details=details)
            if prefix_results and vendor_prefix_match is None:
                return _result("arp", ip, False, "expectation_unavailable", started, details=details)
            return _result("arp", ip, True, "answered", started, details=details)
        return _result("arp", ip, False, "not_found", started, details=details, error=_completed_error(completed))
    except subprocess.TimeoutExpired as exc:
        return _result(
            "arp",
            ip,
            False,
            "timeout",
            started,
            details={"answers": False, "mac": None, "vendor": None, "vendor_prefix_match": None,
                     "expectation_status": "unavailable" if expected_vendor_prefix or expected_oui_prefix else "not_requested"},
            error=exc,
        )
    except Exception as exc:
        return _result(
            "arp",
            ip,
            False,
            "unavailable",
            started,
            details={"answers": False, "mac": None, "vendor": None, "vendor_prefix_match": None,
                     "expectation_status": "unavailable" if expected_vendor_prefix or expected_oui_prefix else "not_requested"},
            error=exc,
        )


def tcp_port_probe(
    ip: str,
    port: int,
    timeout: float = 2.0,
    *,
    socket_factory: Callable[..., Any] = socket.socket,
) -> ProbeResult:
    """Attempt a bounded TCP connect and distinguish refusal from timeout."""
    started = monotonic()
    target = f"{ip}:{port}"
    if timeout <= 0 or timeout > 10:
        return _result("tcp_port", target, False, "invalid", started, error="timeout must be greater than 0 and at most 10 seconds")
    sock: Any = None
    try:
        sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        return _result("tcp_port", target, True, "connected", started, details={"ip": ip, "port": port, "timeout": timeout})
    except ConnectionRefusedError as exc:
        return _result("tcp_port", target, False, "refused", started, details={"ip": ip, "port": port, "timeout": timeout}, error=exc)
    except (TimeoutError, socket.timeout) as exc:
        return _result("tcp_port", target, False, "timeout", started, details={"ip": ip, "port": port, "timeout": timeout}, error=exc)
    except Exception as exc:
        return _result("tcp_port", target, False, "unreachable", started, details={"ip": ip, "port": port, "timeout": timeout}, error=exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


_VISA_TCPIP = re.compile(r"^TCPIP(?:\d*)::([^:]+)::(.+)$", re.IGNORECASE)


def _visa_resource_matches(resource: str, expected_ip: str | None, expected_port: int) -> bool:
    if expected_ip is None:
        return False
    match = _VISA_TCPIP.fullmatch(resource.strip())
    if match is None or match.group(1).casefold() != expected_ip.casefold():
        return False
    components = match.group(2).split("::")
    if components[-1].casefold() == "instr":
        return len(components) in {1, 2}
    if components[-1].casefold() == "socket":
        return len(components) == 2 and components[0].isdigit() and int(components[0]) == expected_port
    return False


def visa_list_resources(
    expected_ip: str | None = None,
    expected_resource: str | None = None,
    timeout: float = 2.0,
    *,
    expected_port: int = 5025,
    resource_manager_factory: Callable[[], Any] | None = None,
) -> ProbeResult:
    """List VISA resources and independently evaluate exact-resource and IP expectations.

    Injected factories are called directly: they must honor the advertised timeout
    when their ``list_resources`` method accepts it. The default backend is run in
    an isolated, killable worker (see ``_bounded_default_visa_listing``).
    """
    started = monotonic()
    manager: Any = None
    candidates = [] if expected_ip is None else [
        f"TCPIP::{expected_ip}::INSTR",
        f"TCPIP0::{expected_ip}::inst0::INSTR",
        f"TCPIP0::{expected_ip}::{expected_port}::SOCKET",
    ]
    empty_details = {
        "resources": [],
        "expected_candidates": candidates,
        "expected_resource_present": None if expected_resource is None else False,
        "expected_ip_present": None if expected_ip is None else False,
        "matched_resource": None,
        "timeout": timeout,
    }
    if timeout <= 0 or timeout > 10 or isinstance(expected_port, bool) or not isinstance(expected_port, int) or not 1 <= expected_port <= 65535:
        return _result("visa_resources", "VISA", False, "invalid", started, details=empty_details,
                       error="timeout/expected_port is outside the permitted range")
    try:
        if resource_manager_factory is None:
            resources = _bounded_default_visa_listing(timeout)
        else:
            manager = resource_manager_factory()
            resources = list(_invoke_with_timeout(manager.list_resources, timeout))
        resources = [str(resource) for resource in resources]
        exact_present = None if expected_resource is None else expected_resource in resources
        ip_matches = [resource for resource in resources if _visa_resource_matches(resource, expected_ip, expected_port)]
        ip_present = None if expected_ip is None else bool(ip_matches)
        matched_resource = expected_resource if exact_present else (ip_matches[0] if ip_matches else None)
        details = {**empty_details, "resources": resources, "expected_resource_present": exact_present,
                   "expected_ip_present": ip_present, "matched_resource": matched_resource}
        expectations = [value for value in (exact_present, ip_present) if value is not None]
        matched = all(expectations)
        return _result("visa_resources", "VISA", matched, "listed" if matched else "mismatch", started, details=details)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        return _result("visa_resources", "VISA", False, "timeout", started, details=empty_details, error=exc)
    except Exception as exc:
        return _result("visa_resources", "VISA", False, "unavailable", started, details=empty_details, error=exc)
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass


def _default_connections() -> Sequence[Any]:
    import psutil

    return psutil.net_connections(kind="tcp")


def _default_process_lookup(pid: int) -> dict[str, Any]:
    import psutil

    process = psutil.Process(pid)
    return {"pid": pid, "name": process.name()}


def _address_parts(address: Any) -> tuple[str | None, int | None]:
    if not address:
        return None, None
    if hasattr(address, "ip"):
        return str(address.ip), int(address.port)
    try:
        return str(address[0]), int(address[1])
    except (IndexError, TypeError, ValueError):
        return None, None


def check_stale_socket(
    ip: str,
    port: int,
    timeout: float = 2.0,
    *,
    connection_provider: Callable[..., Iterable[Any]] | None = None,
    process_lookup: Callable[[int], Mapping[str, Any]] = _default_process_lookup,
) -> ProbeResult:
    """Inspect local TCP tables for users of an endpoint; never alter them.

    Default psutil enumeration runs in a spawned, killable worker. Injected
    providers are called directly for testability and must honor ``timeout``
    themselves when they advertise that keyword; arbitrary closures cannot be
    safely isolated across platforms.
    """
    started = monotonic()
    target = f"{ip}:{port}"
    if timeout <= 0 or timeout > 10:
        return _result("stale_socket", target, False, "invalid", started, error="timeout must be greater than 0 and at most 10 seconds")
    try:
        default_rows = connection_provider is None
        if default_rows:
            connections = _run_spawn_worker(_worker_connections, (), timeout)
        else:
            # Arbitrary injected closures cannot be safely killed cross-platform;
            # injected providers therefore own their advertised timeout contract.
            assert connection_provider is not None
            connections = _invoke_with_timeout(connection_provider, timeout)
        matches: list[dict[str, Any]] = []
        for connection in connections:
            if default_rows:
                local_ip, local_port = connection["local"]
                remote_ip, remote_port = connection["remote"]
                pid = connection["pid"]
                process = connection["process"]
                connection_status = connection["status"]
            else:
                local_ip, local_port = _address_parts(getattr(connection, "laddr", None))
                remote_ip, remote_port = _address_parts(getattr(connection, "raddr", None))
                pid = getattr(connection, "pid", None)
                process = None
                connection_status = str(getattr(connection, "status", "UNKNOWN"))
                if pid is not None:
                    try:
                        process = process_lookup(pid)
                    except Exception:
                        process = {"pid": pid, "name": None}
            if (local_ip, local_port) != (ip, port) and (remote_ip, remote_port) != (ip, port):
                continue
            matches.append(
                {
                    "local": {"ip": local_ip, "port": local_port},
                    "remote": {"ip": remote_ip, "port": remote_port},
                    "status": connection_status,
                    "pid": pid,
                    "process": dict(process) if process is not None else None,
                }
            )
        if matches:
            return _result("stale_socket", target, False, "occupied", started, details={"matches": matches, "timeout": timeout})
        return _result("stale_socket", target, True, "clear", started, details={"matches": [], "timeout": timeout})
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        return _result("stale_socket", target, False, "timeout", started, details={"matches": [], "timeout": timeout}, error=exc)
    except Exception as exc:
        return _result("stale_socket", target, False, "unknown", started, details={"matches": [], "timeout": timeout}, error=exc)


def _identity_key(record: Mapping[str, Any]) -> Any:
    for key in ("identity", "id", "name", "serial"):
        if key in record:
            return record[key]
    return None


def _history_payload(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any]]:
    """Return comparable identity data and a useful flattened history view."""
    data = record.get("data")
    if not isinstance(data, Mapping):
        return record, dict(record)
    identity = data.get("instrument_identity")
    payload = identity if isinstance(identity, Mapping) else data
    flattened = dict(payload)
    for key in ("timestamp", "event_type"):
        if key in record:
            flattened[key] = record[key]
    return payload, flattened


def _is_successful_history_record(record: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    """Reject explicit failures while retaining unmarked legacy known-good rows."""
    event_type = str(record.get("event_type", "")).casefold()
    if event_type in {"connection_success", "connection_succeeded", "connected"}:
        return True
    for source in (payload, record.get("data"), record):
        if not isinstance(source, Mapping):
            continue
        for marker in ("success", "ok"):
            if marker in source:
                return source[marker] is True
        if "status" in source:
            status = str(source["status"]).casefold()
            return status in {"success", "successful", "succeeded", "connected", "resolved", "passed", "ok"}
    return True


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(
                line,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(value, Mapping):
                raise ValueError("each history line must contain a JSON object")
            records.append(value)
    return records


def _timestamp_key(value: Any, index: int) -> tuple[bool, float, int]:
    if not isinstance(value, str):
        return False, float("-inf"), index
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False, float("-inf"), index
        return True, parsed.astimezone(timezone.utc).timestamp(), index
    except (ValueError, OverflowError, OSError):
        return False, float("-inf"), index


def compare_to_last_known_good(
    identity: Mapping[str, Any],
    *,
    records: Iterable[Mapping[str, Any]] | None = None,
    jsonl_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 2.0,
    history_reader: Callable[..., list[Mapping[str, Any]]] | None = None,
) -> ProbeResult:
    """Compare current values with the most recent successful matching record.

    ``records`` is the injection seam.  Otherwise JSONL is read from
    ``jsonl_path`` or ``LONG_GAME_LAST_KNOWN_GOOD``. If neither is set, a small
    deterministic set of JSONL names under ``reports/`` is checked. This
    function never creates or updates the history file.
    """
    started = monotonic()
    try:
        current = dict(identity)
    except (TypeError, ValueError) as exc:
        return _result("last_known_good", None, False, "invalid", started, error=exc)
    key = _identity_key(current)
    target = None if key is None else str(key)
    if key is None:
        return _result("last_known_good", target, False, "invalid", started, error="identity key is missing")
    if timeout <= 0 or timeout > 10:
        return _result(
            "last_known_good",
            target,
            False,
            "invalid",
            started,
            error="timeout must be greater than 0 and at most 10 seconds",
        )
    history_path: Path | None = None
    searched_paths: list[str] = []
    try:
        if records is None:
            environment = os.environ if environ is None else environ
            configured_path = jsonl_path or environment.get("LONG_GAME_LAST_KNOWN_GOOD") or environment.get("LG_LAST_KNOWN_GOOD")
            if configured_path:
                path_candidates = [Path(configured_path)]
            else:
                reports = Path.cwd() / "reports"
                path_candidates = [
                    reports / "transport_diagnostics.jsonl",
                    reports / "diagnostics.jsonl",
                    reports / "diagnostic_sessions.jsonl",
                ]
            searched_paths = [str(path) for path in path_candidates]
            history_path = next((path for path in path_candidates if path.is_file()), None)
            if history_path is None:
                return _result(
                    "last_known_good",
                    target,
                    False,
                    "not_found",
                    started,
                    details={"changes": {}, "searched_paths": searched_paths},
                    error="history was not found",
                )
            if history_reader is None:
                history = _run_spawn_worker(_worker_history, (str(history_path),), timeout)
            else:
                # Injected readers must implement their own timeout contract.
                history = _invoke_with_timeout(history_reader, timeout, history_path)
        else:
            history = list(records)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        timeout_details: dict[str, Any] = {"changes": {}}
        if history_path is not None:
            timeout_details["history_path"] = str(history_path)
        return _result("last_known_good", target, False, "timeout", started, details=timeout_details, error=exc)
    except FileNotFoundError as exc:
        return _result(
            "last_known_good",
            target,
            False,
            "not_found",
            started,
            details={"changes": {}, "searched_paths": searched_paths},
            error=exc,
        )
    except Exception as exc:
        return _result("last_known_good", target, False, "unavailable", started, error=exc)

    candidates: list[tuple[tuple[bool, float, int], Mapping[str, Any], dict[str, Any]]] = []
    for index, record in enumerate(history):
        payload, flattened = _history_payload(record)
        if _identity_key(payload) != key or not _is_successful_history_record(record, payload):
            continue
        timestamp = record.get("timestamp", payload.get("timestamp"))
        candidates.append((_timestamp_key(timestamp, index), payload, flattened))
    if not candidates:
        missing_details: dict[str, Any] = {"changes": {}}
        if history_path is not None:
            missing_details["history_path"] = str(history_path)
        return _result("last_known_good", target, False, "not_found", started, details=missing_details)
    _, known, known_details = max(candidates, key=lambda candidate: candidate[0])

    changes: dict[str, dict[str, Any]] = {}
    for field_name in ("ip", "mac", "resource"):
        expected = known.get(field_name)
        actual = current.get(field_name)
        if field_name == "mac" and isinstance(expected, str) and isinstance(actual, str):
            equal = expected.replace("-", ":").casefold() == actual.replace("-", ":").casefold()
        else:
            equal = expected == actual
        if not equal:
            changes[field_name] = {"expected": expected, "actual": actual}
    details: dict[str, Any] = {"changes": changes, "last_known_good": known_details}
    if history_path is not None:
        details["history_path"] = str(history_path)
    return _result("last_known_good", target, not changes, "match" if not changes else "changed", started, details=details)


__all__ = [
    "ProbeResult",
    "arp_lookup",
    "check_stale_socket",
    "compare_to_last_known_good",
    "ping_gateway",
    "ping_host",
    "tcp_port_probe",
    "visa_list_resources",
]
