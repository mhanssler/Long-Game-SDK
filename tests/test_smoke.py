from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

from long_game_sdk.sdk import safety, smoke
from long_game_sdk.sdk.discovery import InstrumentIdentity
from long_game_sdk.sdk.safety import SafeStateResult


def _safe(resource: str = "USB::PSU") -> SafeStateResult:
    return SafeStateResult(resource, "RIGOL,DP832,SN1,1", "DP832", (), (), (), "verified_safe")


def _blocked(resource: str = "USB::PSU") -> SafeStateResult:
    return SafeStateResult(resource, "RIGOL,DP832,SN1,1", "DP832", (), (), ("not verified",), "unverifiable")


def test_no_config_refuses_probes_and_still_attempts_final_safe_state(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(smoke, "apply_safe_state", lambda **_: events.append("visa-safe") or [])
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: events.append("usb-safe") or [])
    monkeypatch.setattr(
        smoke, "discover_all", lambda: (_ for _ in ()).throw(AssertionError("probe discovery must not run"))
    )

    with pytest.raises(smoke.SmokeSafetyError, match="expected-equipment config is required"):
        smoke.run_smoke()

    assert events == ["visa-safe", "usb-safe", "visa-safe", "usb-safe"]


def test_unverified_initial_state_refuses_probes_and_still_attempts_final_safe_state(monkeypatch):
    events: list[str] = []
    visa_results = iter([[_blocked()], [_safe()]])
    monkeypatch.setattr(
        smoke, "apply_safe_state", lambda **_: events.append("visa-safe") or next(visa_results)
    )
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: events.append("usb-safe") or [])
    monkeypatch.setattr(
        smoke, "discover_all", lambda: (_ for _ in ()).throw(AssertionError("probe discovery must not run"))
    )

    with pytest.raises(smoke.SmokeSafetyError, match="initial safe-state verification failed"):
        smoke.run_smoke({"rig": {"instruments": [{"name": "psu"}]}})

    assert events == ["visa-safe", "usb-safe", "visa-safe", "usb-safe"]


def test_verified_state_allows_read_only_probes_and_is_reverified_afterward(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(smoke, "apply_safe_state", lambda **_: events.append("visa-safe") or [_safe()])
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: events.append("usb-safe") or [])
    monkeypatch.setattr(smoke, "discover_all", lambda: events.append("discover") or [])

    assert smoke.run_smoke({"rig": {"instruments": [{"name": "psu"}]}}) == []
    assert events == ["visa-safe", "usb-safe", "discover", "visa-safe", "usb-safe"]


def test_probe_failure_does_not_prevent_both_final_safe_state_attempts(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(smoke, "apply_safe_state", lambda **_: events.append("visa-safe") or [_safe()])
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: events.append("usb-safe") or [])
    monkeypatch.setattr(
        smoke, "discover_all", lambda: (_ for _ in ()).throw(RuntimeError("probe failed"))
    )

    with pytest.raises(RuntimeError, match="probe failed"):
        smoke.run_smoke({"rig": {"instruments": [{"name": "psu"}]}})

    assert events == ["visa-safe", "usb-safe", "visa-safe", "usb-safe"]


def test_final_unverified_state_fails_closed(monkeypatch):
    visa_results = iter([[_safe()], [_blocked()]])
    monkeypatch.setattr(smoke, "apply_safe_state", lambda **_: next(visa_results))
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: [])
    monkeypatch.setattr(smoke, "discover_all", lambda: [])

    with pytest.raises(smoke.SmokeSafetyError, match="final safe-state verification failed"):
        smoke.run_smoke({"rig": {"instruments": [{"name": "psu"}]}})


def test_smoke_cli_requires_and_loads_expected_equipment_config(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(smoke, "run_smoke", lambda config: seen.append(config) or [])

    assert smoke.main([]) == 2

    config = {"rig": {"instruments": [{"name": "psu"}]}}
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(config))
    assert smoke.main([str(path)]) == 0
    assert seen == [config]


def test_smoke_cli_returns_nonzero_on_safety_failure(monkeypatch, tmp_path):
    path = tmp_path / "bench.yaml"
    path.write_text("rig:\n  instruments: []\n")
    monkeypatch.setattr(
        smoke,
        "run_smoke",
        lambda config: (_ for _ in ()).throw(smoke.SmokeSafetyError("blocked")),
    )

    assert smoke.main([str(path)]) == 2


def test_smoke_visa_rejects_compound_verification_before_query(monkeypatch, tmp_path):
    queried: list[str] = []

    class Instrument:
        def query(self, command):
            queried.append(command)
            return "response"

        def close(self):
            pass

    class ResourceManager:
        def open_resource(self, resource):
            return Instrument()

        def close(self):
            pass

    schema_path = tmp_path / "hostile.yaml"
    schema_path.write_text(yaml.safe_dump({
        "safety": {
            "verification": [":OUTPut ON;*IDN?", ":OUTPut? CH1"],
        },
    }))
    monkeypatch.setattr(smoke.pyvisa, "ResourceManager", lambda *_: ResourceManager())
    monkeypatch.setattr(
        smoke,
        "match_driver",
        lambda _: SimpleNamespace(instrument_class="test", driver_kind="fake"),
    )
    identity = InstrumentIdentity(transport="visa", resource="USB::TEST", model="TEST")

    result = smoke._smoke_visa(identity, schema_path)

    assert queried == ["*IDN?", ":OUTPut? CH1"]
    assert result.errors == ()


@pytest.mark.parametrize("failure", [None, "open", "query"])
def test_smoke_visa_closes_owned_resource_manager_on_every_path(monkeypatch, failure):
    class Instrument:
        def query(self, command):
            if failure == "query":
                raise RuntimeError("query failed")
            return "response"

        def close(self):
            pass

    class ResourceManager:
        closed = False

        def open_resource(self, resource):
            if failure == "open":
                raise RuntimeError("open failed")
            return Instrument()

        def close(self):
            self.closed = True

    manager = ResourceManager()
    monkeypatch.setattr(smoke.pyvisa, "ResourceManager", lambda *_: manager)
    monkeypatch.setattr(
        smoke,
        "match_driver",
        lambda _: SimpleNamespace(instrument_class="test", driver_kind="fake"),
    )
    identity = InstrumentIdentity(transport="visa", resource="USB::TEST", model="TEST")

    smoke._smoke_visa(identity, None)

    assert manager.closed


def test_smoke_labjack_opens_exact_discovered_serial(monkeypatch):
    opened: list[str] = []

    class Driver:
        def __init__(self, *, serial):
            opened.append(serial)

        def read_ain(self, channel):
            return 0.0

        def close(self):
            pass

    monkeypatch.setattr(smoke, "LabJackU3Driver", Driver)
    monkeypatch.setattr(
        smoke,
        "match_driver",
        lambda _: SimpleNamespace(instrument_class="daq", driver_kind="labjack"),
    )
    identity = InstrumentIdentity(
        transport="usb",
        resource="USB::0cd5::0003::bus1-addr2",
        manufacturer="LabJack",
        model="U3",
        serial="470012345",
        idn="LabJack,U3,470012345",
        vendor_id="0cd5",
        product_id="0003",
    )

    result = smoke._smoke_usb(identity, None)

    assert opened == ["470012345"]
    assert result.errors == ()


def test_documented_example_passes_safe_and_smoke_on_exact_fake_bench(monkeypatch):
    repository = Path(__file__).resolve().parents[1]
    config_path = repository / "examples/lab_preflight_bench_a.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    psu_spec, scope_spec = config["rig"]["instruments"]

    class Instrument:
        def __init__(self, responses):
            self.responses = responses
            self.writes = []

        def query(self, command):
            return self.responses[command]

        def write(self, command):
            self.writes.append(command)

        def close(self):
            pass

    class ResourceManager:
        def __init__(self, instruments):
            self.instruments = instruments

        def list_resources(self):
            return tuple(self.instruments)

        def open_resource(self, resource):
            return self.instruments[resource]

        def close(self):
            pass

    psu_idn = "RIGOL TECHNOLOGIES,DP832,DP8C000000000,1.0"
    scope_idn = "RIGOL TECHNOLOGIES,DS1102E,DS1ZA000000000,1.0"
    psu = Instrument({
        "*IDN?": psu_idn,
        ":OUTPut? CH1": "OFF",
        ":OUTPut? CH2": "OFF",
        ":OUTPut? CH3": "OFF",
        ":MEASure:VOLTage? CH1": "0",
        ":MEASure:VOLTage? CH2": "0",
        ":MEASure:VOLTage? CH3": "0",
        ":MEASure:CURRent? CH1": "0",
        ":MEASure:CURRent? CH2": "0",
        ":MEASure:CURRent? CH3": "0",
    })
    scope_instrument = Instrument({
        "*IDN?": scope_idn,
        ":CHANnel1:DISPlay?": "1",
        ":CHANnel2:DISPlay?": "1",
        ":TRIGger:STATus?": "STOP",
    })
    instruments = {
        psu_spec["connection"]: psu,
        scope_spec["connection"]: scope_instrument,
    }
    monkeypatch.setattr(safety.pyvisa, "ResourceManager", lambda *_: ResourceManager(instruments))
    monkeypatch.setattr(safety.time, "sleep", lambda *_: None)
    monkeypatch.setattr(smoke, "apply_usb_safe_state", lambda **_: [])
    monkeypatch.setattr(smoke, "ensure_schema", lambda _: None)
    monkeypatch.setattr(
        smoke,
        "match_driver",
        lambda _: SimpleNamespace(instrument_class="test", driver_kind="fake"),
    )
    monkeypatch.setattr(
        smoke,
        "discover_all",
        lambda: [
            InstrumentIdentity(
                transport="visa", resource=psu_spec["connection"],
                manufacturer="RIGOL TECHNOLOGIES", model="DP832",
                serial="DP8C000000000", idn=psu_idn,
            ),
            InstrumentIdentity(
                transport="visa", resource=scope_spec["connection"],
                manufacturer="RIGOL TECHNOLOGIES", model="DS1102E",
                serial="DS1ZA000000000", idn=scope_idn,
            ),
        ],
    )

    safe_results = safety.apply_safe_state(config=config)
    assert {result.model: result.state for result in safe_results} == {
        "DP832": "verified_safe",
        "DS1102E": "no_action_required",
    }
    assert smoke.main([str(config_path)]) == 0
    assert psu.writes == list(safety.SAFE_STATE_COMMANDS["DP832"]) * 3
