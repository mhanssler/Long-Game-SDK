from __future__ import annotations

import json
import multiprocessing
import socket
import subprocess
import time
from types import SimpleNamespace

from long_game_sdk.sdk.transport_diagnostics import (
    _run_spawn_worker,
    ProbeResult,
    arp_lookup,
    check_stale_socket,
    compare_to_last_known_good,
    ping_gateway,
    ping_host,
    tcp_port_probe,
    visa_list_resources,
)


def _blocking_worker(connection):
    time.sleep(5)
    connection.send((True, None))


class FakeSocket:
    def __init__(self, outcome: object = None):
        self.outcome = outcome
        self.timeout = None
        self.address = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        self.address = address
        if isinstance(self.outcome, BaseException):
            raise self.outcome

    def close(self) -> None:
        self.closed = True


def test_probe_result_is_structured_and_ping_uses_small_fixed_timeout():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 packets transmitted, 1 received", stderr="")

    result = ping_host("192.0.2.10", runner=runner, platform_name="Linux")

    assert isinstance(result, ProbeResult)
    assert result.ok is True
    assert result.status == "reachable"
    assert result.target == "192.0.2.10"
    assert calls[0][0] == ["ping", "-c", "1", "-W", "2", "192.0.2.10"]
    assert calls[0][1]["timeout"] <= 3


def test_ping_failure_and_timeout_are_returned_as_data():
    def unreachable(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no route")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    failed = ping_host("192.0.2.11", runner=unreachable, platform_name="Darwin")
    timeout = ping_host("192.0.2.12", runner=timed_out, platform_name="Darwin")

    assert (failed.ok, failed.status) == (False, "unreachable")
    assert failed.error == "no route"
    assert (timeout.ok, timeout.status) == (False, "timeout")


def test_ping_gateway_resolves_then_probes_without_session_state():
    seen = []

    def fake_ping(ip):
        seen.append(ip)
        return ProbeResult("ping", ip, True, "reachable")

    result = ping_gateway(gateway_resolver=lambda: "192.168.1.1", ping=fake_ping)
    missing = ping_gateway(gateway_resolver=lambda: None, ping=fake_ping)

    assert result.target == "192.168.1.1"
    assert result.details["gateway"] == "192.168.1.1"
    assert seen == ["192.168.1.1"]
    assert (missing.ok, missing.status) == (False, "not_found")


def test_arp_lookup_reports_answer_mac_and_optional_vendor_signal():
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="? (192.0.2.20) at aa:bb:cc:11:22:33 on en0 ifscope [ethernet]",
            stderr="",
        )

    result = arp_lookup(
        "192.0.2.20",
        runner=runner,
        platform_name="Darwin",
        vendor_lookup=lambda mac: "Acme Instruments" if mac.startswith("AA:BB:CC") else None,
    )

    assert result.ok
    assert result.status == "answered"
    assert result.details == {
        "answers": True,
        "mac": "AA:BB:CC:11:22:33",
        "vendor": "Acme Instruments",
        "vendor_prefix_match": None,
        "expectation_status": "not_requested",
    }


def test_arp_lookup_not_found_is_data():
    result = arp_lookup(
        "192.0.2.21",
        runner=lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="no entry"),
        platform_name="Linux",
    )
    assert (result.ok, result.status) == (False, "not_found")
    assert result.details["answers"] is False


def test_arp_lookup_reports_expected_vendor_or_oui_prefix_match():
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="192.0.2.20 at aa:bb:cc:11:22:33", stderr="")

    vendor_match = arp_lookup(
        "192.0.2.20",
        runner=runner,
        expected_vendor_prefix="Acme",
        vendor_lookup=lambda mac: "Acme Instruments",
    )
    oui_mismatch = arp_lookup("192.0.2.20", runner=runner, expected_oui_prefix="DE:AD:BE")
    unknown = arp_lookup("192.0.2.20", runner=runner, expected_vendor_prefix="Acme")

    assert vendor_match.details["vendor_prefix_match"] is True
    assert oui_mismatch.details["vendor_prefix_match"] is False
    assert unknown.details["vendor_prefix_match"] is None


def test_tcp_probe_distinguishes_connected_refused_and_timeout():
    sockets = [FakeSocket(), FakeSocket(ConnectionRefusedError("refused")), FakeSocket(socket.timeout("late"))]

    def factory(*args):
        return sockets.pop(0)

    connected = tcp_port_probe("192.0.2.30", 5025, timeout=0.5, socket_factory=factory)
    refused = tcp_port_probe("192.0.2.30", 5026, socket_factory=factory)
    timeout = tcp_port_probe("192.0.2.30", 5027, socket_factory=factory)

    assert (connected.ok, connected.status) == (True, "connected")
    assert (refused.ok, refused.status) == (False, "refused")
    assert (timeout.ok, timeout.status) == (False, "timeout")
    assert connected.details["timeout"] == 0.5


def test_visa_list_resources_only_lists_and_closes_manager():
    class Manager:
        def __init__(self):
            self.closed = False
            self.listed = False

        def list_resources(self):
            self.listed = True
            return ("TCPIP::192.0.2.40::INSTR", "USB::1234::5678::INSTR")

        def close(self):
            self.closed = True

    manager = Manager()
    result = visa_list_resources(resource_manager_factory=lambda: manager)

    assert result.ok
    assert result.details["resources"] == ["TCPIP::192.0.2.40::INSTR", "USB::1234::5678::INSTR"]
    assert manager.listed and manager.closed


def test_visa_backend_error_is_returned_as_data():
    result = visa_list_resources(resource_manager_factory=lambda: (_ for _ in ()).throw(RuntimeError("backend absent")))
    assert (result.ok, result.status) == (False, "unavailable")
    assert "backend absent" in (result.error or "")


def test_visa_list_resources_reports_expected_ip_shapes_and_match_signal():
    class Manager:
        def list_resources(self):
            return ("TCPIP::192.0.2.40::INSTR", "USB::1234::5678::INSTR")

        def close(self):
            pass

    result = visa_list_resources(
        expected_ip="192.0.2.40",
        expected_resource="USB::1234::5678::INSTR",
        resource_manager_factory=Manager,
    )

    assert result.details["expected_candidates"] == [
        "TCPIP::192.0.2.40::INSTR",
        "TCPIP0::192.0.2.40::inst0::INSTR",
        "TCPIP0::192.0.2.40::5025::SOCKET",
    ]
    assert result.details["expected_ip_present"] is True
    assert result.details["expected_resource_present"] is True
    assert result.details["matched_resource"] == "USB::1234::5678::INSTR"


def test_stale_socket_is_read_only_best_effort_local_inspection():
    connections = [
        SimpleNamespace(
            laddr=SimpleNamespace(ip="192.0.2.50", port=9000),
            raddr=(),
            status="LISTEN",
            pid=4321,
        )
    ]

    result = check_stale_socket(
        "192.0.2.50",
        9000,
        connection_provider=lambda: connections,
        process_lookup=lambda pid: {"pid": pid, "name": "old-driver"},
    )

    assert result.ok is False
    assert result.status == "occupied"
    assert result.details["matches"][0]["process"]["name"] == "old-driver"
    assert connections[0].status == "LISTEN"


def test_stale_socket_clear_and_permission_failure_are_data():
    clear = check_stale_socket("127.0.0.1", 9001, connection_provider=lambda: [])
    denied = check_stale_socket(
        "127.0.0.1",
        9001,
        connection_provider=lambda: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert (clear.ok, clear.status) == (True, "clear")
    assert (denied.ok, denied.status) == (False, "unknown")


def test_unbounded_collaborators_receive_timeout_or_return_timeout():
    seen = []

    def connections(*, timeout):
        seen.append(("connections", timeout))
        raise TimeoutError("slow provider")

    class Manager:
        def list_resources(self, *, timeout):
            seen.append(("visa", timeout))
            return ()

        def close(self):
            pass

    stale = check_stale_socket("127.0.0.1", 9001, timeout=0.25, connection_provider=connections)
    visa = visa_list_resources(timeout=0.5, resource_manager_factory=Manager)

    assert (stale.ok, stale.status) == (False, "timeout")
    assert visa.details["timeout"] == 0.5
    assert seen == [("connections", 0.25), ("visa", 0.5)]


def test_compare_to_last_known_good_uses_latest_matching_jsonl_record(tmp_path):
    history = tmp_path / "known-good.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps({"identity": "scope-a", "ip": "192.0.2.1", "mac": "AA:AA:AA:AA:AA:AA", "resource": "TCPIP::old"}),
                json.dumps({"identity": "scope-a", "ip": "192.0.2.2", "mac": "BB:BB:BB:BB:BB:BB", "resource": "TCPIP::current"}),
                json.dumps({"identity": "other", "ip": "203.0.113.5"}),
            ]
        ),
        encoding="utf-8",
    )

    result = compare_to_last_known_good(
        {
            "identity": "scope-a",
            "ip": "192.0.2.9",
            "mac": "bb:bb:bb:bb:bb:bb",
            "resource": "TCPIP::current",
        },
        jsonl_path=history,
    )

    assert result.ok is False
    assert result.status == "changed"
    assert result.details["changes"] == {"ip": {"expected": "192.0.2.2", "actual": "192.0.2.9"}}


def test_compare_to_last_known_good_accepts_injected_records_and_handles_missing():
    current = {"identity": "meter-a", "ip": "192.0.2.60", "mac": "00:11:22:33:44:55", "resource": "USB::1"}
    same = compare_to_last_known_good(current, records=[dict(current)])
    absent = compare_to_last_known_good(current, records=[])

    assert (same.ok, same.status) == (True, "match")
    assert same.details["changes"] == {}
    assert (absent.ok, absent.status) == (False, "not_found")


def test_compare_uses_most_recent_successful_envelope_and_diagnostic_session():
    current = {"identity": "meter-a", "ip": "192.0.2.2", "mac": "00:11:22:33:44:55", "resource": "USB::2"}
    records = [
        {
            "timestamp": "2026-01-03T00:00:00+00:00",
            "event_type": "connection",
            "data": {**current, "ip": "192.0.2.99", "status": "failed"},
        },
        {
            "timestamp": "2026-01-02T00:00:00+00:00",
            "event_type": "connection_success",
            "data": dict(current),
        },
        {"timestamp": "2026-01-01T00:00:00+00:00", **current, "ip": "192.0.2.1", "status": "success"},
    ]
    envelope = compare_to_last_known_good(current, records=records)
    diagnostic = compare_to_last_known_good(
        current,
        records=[
            {
                "timestamp": "2026-01-04T00:00:00+00:00",
                "event_type": "diagnostic_session",
                "data": {"instrument_identity": dict(current), "status": "resolved"},
            }
        ],
    )

    assert (envelope.ok, envelope.status) == (True, "match")
    assert envelope.details["last_known_good"]["timestamp"] == "2026-01-02T00:00:00+00:00"
    assert (diagnostic.ok, diagnostic.status) == (True, "match")


def test_compare_to_last_known_good_returns_invalid_input_as_data():
    result = compare_to_last_known_good(None, records=[])  # type: ignore[arg-type]

    assert (result.ok, result.status) == (False, "invalid")
    assert result.error


def test_compare_discovers_bounded_default_reports_log_and_structures_no_history(tmp_path, monkeypatch):
    current = {"identity": "meter-a", "ip": "192.0.2.60", "mac": "00:11:22:33:44:55", "resource": "USB::1"}
    reports = tmp_path / "reports"
    reports.mkdir()
    history = reports / "transport_diagnostics.jsonl"
    history.write_text(json.dumps({**current, "status": "success"}) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    found = compare_to_last_known_good(current, environ={})
    history.unlink()
    missing = compare_to_last_known_good(current, environ={})

    assert (found.ok, found.status) == (True, "match")
    assert found.details["history_path"] == str(history)
    assert (missing.ok, missing.status) == (False, "not_found")
    assert 1 <= len(missing.details["searched_paths"]) <= 4
    assert missing.details["changes"] == {}


def test_compare_history_reader_receives_bounded_timeout(tmp_path):
    current = {"identity": "meter-a", "ip": "192.0.2.60", "mac": "00:11:22:33:44:55", "resource": "USB::1"}
    history = tmp_path / "known-good.jsonl"
    history.touch()
    seen = []

    def reader(path, *, timeout):
        seen.append((path, timeout))
        return [{**current, "status": "success"}]

    result = compare_to_last_known_good(current, jsonl_path=history, timeout=0.75, history_reader=reader)

    assert (result.ok, result.status) == (True, "match")
    assert seen == [(history, 0.75)]


def test_arp_expectation_failures_do_not_hide_cache_answer():
    runner = lambda *args, **kwargs: SimpleNamespace(  # noqa: E731
        returncode=0, stdout="192.0.2.20 at aa:bb:cc:11:22:33", stderr=""
    )
    mismatch = arp_lookup("192.0.2.20", runner=runner, expected_oui_prefix="DE:AD:BE")
    unavailable = arp_lookup("192.0.2.20", runner=runner, expected_vendor_prefix="Acme")
    assert mismatch.details["answers"] is True
    assert (mismatch.ok, mismatch.status) == (False, "mismatch")
    assert (unavailable.ok, unavailable.status) == (False, "expectation_unavailable")


def test_visa_normalizes_board_instr_and_requires_socket_port():
    class Manager:
        def list_resources(self):
            return (
                "TCPIP0::192.0.2.40::inst0::INSTR",
                "TCPIP0::192.0.2.41::5025::SOCKET",
                "TCPIP0::192.0.2.42::4000::SOCKET",
            )

        def close(self):
            pass

    instr = visa_list_resources(expected_ip="192.0.2.40", resource_manager_factory=Manager)
    socket_match = visa_list_resources(expected_ip="192.0.2.41", expected_port=5025, resource_manager_factory=Manager)
    wrong_port = visa_list_resources(expected_ip="192.0.2.42", expected_port=5025, resource_manager_factory=Manager)
    exact_missing = visa_list_resources(
        expected_ip="192.0.2.40", expected_resource="TCPIP::missing::INSTR", resource_manager_factory=Manager
    )
    assert instr.details["expected_ip_present"] is True
    assert socket_match.details["expected_ip_present"] is True
    assert (wrong_port.ok, wrong_port.status) == (False, "mismatch")
    assert exact_missing.details["expected_ip_present"] is True
    assert exact_missing.details["expected_resource_present"] is False
    assert (exact_missing.ok, exact_missing.status) == (False, "mismatch")


def test_compare_orders_timezone_aware_timestamps_by_instant():
    current = {"identity": "meter-a", "ip": "new", "mac": None, "resource": None}
    records = [
        {"timestamp": "2026-01-01T12:30:00+02:00", **current, "ip": "old"},
        {"timestamp": "2026-01-01T11:00:00+00:00", **current},
    ]
    result = compare_to_last_known_good(current, records=records)
    assert (result.ok, result.status) == (True, "match")
    assert result.details["last_known_good"]["timestamp"] == "2026-01-01T11:00:00+00:00"


def test_compare_rejects_duplicate_keys_and_nonfinite_history(tmp_path):
    current = {"identity": "meter-a"}
    for line in ('{"identity":"meter-a","identity":"other"}\n', '{"identity":"meter-a","x":NaN}\n'):
        history = tmp_path / "bad.jsonl"
        history.write_text(line, encoding="utf-8")
        result = compare_to_last_known_good(current, jsonl_path=history)
        assert (result.ok, result.status) == (False, "unavailable")


def test_spawn_worker_enforces_deadline_and_reaps_blocking_child():
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()
    try:
        _run_spawn_worker(_blocking_worker, (), 0.1)
    except TimeoutError:
        pass
    else:
        raise AssertionError("blocking worker should time out")
    assert time.monotonic() - started < 0.8
    assert {child.pid for child in multiprocessing.active_children()} == before
