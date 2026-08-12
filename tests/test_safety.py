from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from long_game_sdk.sdk import safety
from long_game_sdk.sdk.discovery import InstrumentIdentity


class FakeInstrument:
    def __init__(self, responses, *, write_error=False):
        self.responses = responses
        self.write_error = write_error
        self.writes = []

    def query(self, command):
        value = self.responses[command]
        if isinstance(value, Exception):
            raise value
        return value

    def write(self, command):
        if self.write_error:
            raise OSError("write failed")
        self.writes.append(command)

    def close(self): pass


class SequencedIdentityInstrument(FakeInstrument):
    """VISA fake whose identity can change between safe-state mutations."""

    def __init__(self, identities, responses=None):
        super().__init__(responses or {})
        self.identities = iter(identities)
        self.events = []

    def query(self, command):
        self.events.append(("query", command))
        if command == "*IDN?":
            value = next(self.identities)
            if isinstance(value, Exception):
                raise value
            return value
        return super().query(command)

    def write(self, command):
        self.events.append(("write", command))
        super().write(command)


class FakeRM:
    def __init__(self, instruments): self.instruments = instruments
    def list_resources(self): return tuple(self.instruments)
    def open_resource(self, resource): return self.instruments[resource]
    def close(self): pass


def _patch_rm(monkeypatch, instruments):
    monkeypatch.setattr(safety.pyvisa, "ResourceManager", lambda *_: FakeRM(instruments))
    monkeypatch.setattr(safety.time, "sleep", lambda *_: None)


def test_dp832_is_verified_only_when_every_output_readback_is_off(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({
        "*IDN?": "RIGOL TECHNOLOGIES,DP832,DP8-123,1.0",
        ":OUTPut? CH1": "CH1,OFF", ":OUTPut? CH2": "0", ":OUTPut? CH3": "OFF",
        ":MEASure:VOLTage? CH1": "0", ":MEASure:VOLTage? CH2": "0", ":MEASure:VOLTage? CH3": "0",
        ":MEASure:CURRent? CH1": "0", ":MEASure:CURRent? CH2": "0", ":MEASure:CURRent? CH3": "0",
    })
    _patch_rm(monkeypatch, {resource: instrument})
    result = safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": "RIGOL TECHNOLOGIES",
        "expected_model": "DP832",
        "expected_serial": "DP8-123",
    }})[0]
    assert result.state == "verified_safe"
    assert result.safe


def test_no_config_known_source_discovery_is_strictly_read_only(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state()[0]

    assert result.state == "unverifiable"
    assert result.checks == ()
    assert instrument.writes == []


def test_safe_state_closes_manager_when_resource_close_fails(monkeypatch):
    events = []

    class ClosingInstrument(FakeInstrument):
        def close(self):
            events.append("instrument-close")
            raise OSError("close failed")

    class ClosingManager(FakeRM):
        def close(self):
            events.append("manager-close")

    resource = "USB::SCOPE"
    instrument = ClosingInstrument({"*IDN?": "RIGOL,DS1102E,SN1,1"})
    monkeypatch.setattr(
        safety.pyvisa, "ResourceManager", lambda *_: ClosingManager({resource: instrument})
    )

    safety.apply_safe_state()

    assert events == ["instrument-close", "manager-close"]


def test_safe_state_rejects_top_level_instruments_consistently(monkeypatch):
    _patch_rm(monkeypatch, {})

    with pytest.raises(safety.SafeStateConfigError, match="top-level instruments"):
        safety.apply_safe_state(config={"instruments": [{"name": "psu"}]})


def test_dl3021_on_readback_is_unsafe_and_errors_are_unverifiable(monkeypatch):
    resource = "USB::DL3021"
    base = {"*IDN?": "RIGOL,DL3021,DL3-123,1", ":INPut?": "INPUT ON",
            ":MEASure:VOLTage?": "0", ":MEASure:CURRent?": "0", ":MEASure:POWer?": "0"}
    _patch_rm(monkeypatch, {resource: FakeInstrument(base)})
    expected = {resource: {
        "expected_manufacturer": "RIGOL",
        "expected_model": "DL3021",
        "expected_serial": "DL3-123",
    }}
    assert safety.apply_safe_state(expected_devices=expected)[0].state == "unsafe"
    base[":INPut?"] = OSError("readback failed")
    _patch_rm(monkeypatch, {resource: FakeInstrument(base)})
    assert safety.apply_safe_state(expected_devices=expected)[0].state == "unverifiable"


def test_write_failure_blocks_even_when_readback_says_off(monkeypatch):
    resource = "USB::DL3021"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DL3021,SN,1", ":INPut?": "OFF",
                                 ":MEASure:VOLTage?": "0", ":MEASure:CURRent?": "0", ":MEASure:POWer?": "0"},
                                write_error=True)
    _patch_rm(monkeypatch, {resource: instrument})
    assert safety.apply_safe_state()[0].state == "unverifiable"


def test_unknown_instrument_is_read_only_unless_expected_as_energy_source(monkeypatch):
    resource = "USB::UNKNOWN"
    instrument = FakeInstrument({"*IDN?": "ACME,MYSTERY,SN-1,1"})
    _patch_rm(monkeypatch, {resource: instrument})
    assert safety.apply_safe_state()[0].state == "unverifiable"
    assert instrument.writes == []
    result = safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": "ACME", "expected_model": "MYSTERY",
        "expected_serial": "SN-1", "energy_source": True,
    }})[0]
    assert result.state == "unverifiable"
    assert instrument.writes == []


def test_no_config_unknown_device_is_never_reported_safe(monkeypatch):
    resource = "USB::UNKNOWN"
    _patch_rm(monkeypatch, {resource: FakeInstrument({"*IDN?": "ACME,MYSTERY,SN-1,1"})})

    result = safety.apply_safe_state()[0]

    assert result.state == "unverifiable"
    assert not result.safe
    assert any("read-only discovery" in error for error in result.errors)


def test_safe_state_closes_manager_when_list_resources_fails(monkeypatch):
    events = []

    class Manager:
        def list_resources(self):
            raise OSError("discovery failed")

        def close(self):
            events.append("manager-close")

    monkeypatch.setattr(safety.pyvisa, "ResourceManager", lambda *_: Manager())

    with pytest.raises(OSError, match="discovery failed"):
        safety.apply_safe_state()

    assert events == ["manager-close"]


def test_configured_expected_resource_that_is_not_discovered_is_unverifiable(monkeypatch):
    _patch_rm(monkeypatch, {})
    config = {"rig": {"instruments": [{
        "name": "psu", "connection": "USB::MISSING", "expected_manufacturer": "RIGOL",
        "expected_model": "DP832", "expected_serial": "SN1",
    }]}}
    results = safety.apply_safe_state(config=config)
    assert len(results) == 1
    assert results[0].resource == "USB::MISSING"
    assert results[0].state == "unverifiable"


def test_expected_identity_can_come_from_preflight_config(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DP832,WRONG,1",
        ":OUTPut? CH1": "OFF", ":OUTPut? CH2": "OFF", ":OUTPut? CH3": "OFF",
        ":MEASure:VOLTage? CH1": "0", ":MEASure:VOLTage? CH2": "0", ":MEASure:VOLTage? CH3": "0",
        ":MEASure:CURRent? CH1": "0", ":MEASure:CURRent? CH2": "0", ":MEASure:CURRent? CH3": "0"})
    _patch_rm(monkeypatch, {resource: instrument})
    config = {"rig": {"instruments": [{
        "name": "psu", "connection": resource, "expected_manufacturer": "RIGOL",
        "expected_model": "DP832", "expected_serial": "RIGHT",
    }]}}
    assert safety.apply_safe_state(config=config)[0].state == "unverifiable"


def test_model_selection_uses_exact_parsed_idn_model_field(monkeypatch):
    resource = "USB::SPOOF"
    instrument = FakeInstrument({"*IDN?": "ACME,NOT-DP832,DP832-SERIAL,1"})
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state()[0]

    assert result.model == "UNKNOWN"
    assert instrument.writes == []


def test_configured_identity_mismatch_performs_zero_model_specific_writes(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DP832,WRONG,1"})
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": "RIGOL", "expected_model": "DP832", "expected_serial": "RIGHT"
    }})[0]

    assert result.state == "unverifiable"
    assert instrument.writes == []


def _expected_device(model, serial):
    return {
        "expected_manufacturer": "RIGOL",
        "expected_model": model,
        "expected_serial": serial,
    }


def test_dp832_identity_change_immediately_before_first_write_blocks_all_writes(monkeypatch):
    resource = "USB::DP832"
    good = "RIGOL,DP832,SN1,1"
    instrument = SequencedIdentityInstrument([good, "RIGOL,DP832,ATTACKER,1"])
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DP832", "SN1")}
    )[0]

    assert instrument.events == [("query", "*IDN?"), ("query", "*IDN?")]
    assert instrument.writes == []
    assert result.actions == ()
    assert result.state == "unverifiable"
    assert any("identity mismatch" in error for error in result.errors)


def test_dp832_identity_change_between_writes_blocks_current_and_subsequent_writes(monkeypatch):
    resource = "USB::DP832"
    good = "RIGOL,DP832,SN1,1"
    instrument = SequencedIdentityInstrument([good, good, "RIGOL,DP832,sn1,1"])
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DP832", "SN1")}
    )[0]

    assert instrument.events == [
        ("query", "*IDN?"),
        ("query", "*IDN?"),
        ("write", ":OUTPut CH1,OFF"),
        ("query", "*IDN?"),
    ]
    assert instrument.writes == [":OUTPut CH1,OFF"]
    assert result.actions == (":OUTPut CH1,OFF",)
    assert result.state == "unverifiable"


def test_dl3021_identity_change_immediately_before_write_blocks_write(monkeypatch):
    resource = "USB::DL3021"
    good = "RIGOL,DL3021,SN1,1"
    instrument = SequencedIdentityInstrument([good, "RIGOL,DL3021,ATTACKER,1"])
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DL3021", "SN1")}
    )[0]

    assert instrument.events == [("query", "*IDN?"), ("query", "*IDN?")]
    assert instrument.writes == []
    assert result.actions == ()
    assert result.state == "unverifiable"


@pytest.mark.parametrize(
    "fresh_identity",
    [OSError("fresh identity query failed"), "RIGOL,DP832\x00,SN1,1", "RIGOL,DP832"],
    ids=["query-failure", "control-character", "malformed"],
)
def test_dp832_invalid_fresh_identity_immediately_before_write_blocks_all_writes(
    monkeypatch, fresh_identity
):
    resource = "USB::DP832"
    instrument = SequencedIdentityInstrument(["RIGOL,DP832,SN1,1", fresh_identity])
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DP832", "SN1")}
    )[0]

    assert instrument.events == [("query", "*IDN?"), ("query", "*IDN?")]
    assert instrument.writes == []
    assert result.actions == ()
    assert result.state == "unverifiable"


@pytest.mark.parametrize(
    "fresh_identity",
    [OSError("fresh identity query failed"), "RIGOL,DL3021\x00,SN1,1", "RIGOL,DL3021"],
    ids=["query-failure", "control-character", "malformed"],
)
def test_dl3021_invalid_fresh_identity_immediately_before_write_blocks_write(
    monkeypatch, fresh_identity
):
    resource = "USB::DL3021"
    instrument = SequencedIdentityInstrument(["RIGOL,DL3021,SN1,1", fresh_identity])
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DL3021", "SN1")}
    )[0]

    assert instrument.events == [("query", "*IDN?"), ("query", "*IDN?")]
    assert instrument.writes == []
    assert result.actions == ()
    assert result.state == "unverifiable"


def test_pre_write_authorization_and_write_remain_bound_to_opened_transport(monkeypatch):
    resource = "USB::DP832"
    good = "RIGOL,DP832,SN1,1"
    readbacks = {
        ":OUTPut? CH1": "OFF", ":OUTPut? CH2": "OFF", ":OUTPut? CH3": "OFF",
        ":MEASure:VOLTage? CH1": "0", ":MEASure:VOLTage? CH2": "0",
        ":MEASure:VOLTage? CH3": "0", ":MEASure:CURRent? CH1": "0",
        ":MEASure:CURRent? CH2": "0", ":MEASure:CURRent? CH3": "0",
    }
    attacker = FakeInstrument({"*IDN?": "RIGOL,DP832,ATTACKER,1"})

    class SubstitutionAttemptInstrument(SequencedIdentityInstrument):
        def query(self, command):
            response = super().query(command)
            if command == "*IDN?":
                manager.instruments[resource] = attacker
            return response

    opened = SubstitutionAttemptInstrument([good, good, good, good], readbacks)
    instruments: dict[str, FakeInstrument] = {resource: opened}
    manager = FakeRM(instruments)
    monkeypatch.setattr(safety.pyvisa, "ResourceManager", lambda *_: manager)
    monkeypatch.setattr(safety.time, "sleep", lambda *_: None)

    result = safety.apply_safe_state(
        expected_devices={resource: _expected_device("DP832", "SN1")}
    )[0]

    assert opened.writes == list(safety.SAFE_STATE_COMMANDS["DP832"])
    assert attacker.writes == []
    assert result.state == "verified_safe"


@pytest.mark.parametrize(
    ("expected", "live"),
    [
        ({"expected_serial": "sn1"}, "RIGOL,DP832,SN1,1"),
        ({"expected_serial": "\tSN1"}, "RIGOL,DP832,SN1,1"),
        ({"expected_serial": "SN\x00X"}, "RIGOL,DP832,SN\x00X,1"),
        ({"expected_serial": "SN1"}, "\tRIGOL,DP832,SN1,1"),
        ({"expected_serial": "SN1"}, "RIGOL,DP\x00832,SN1,1"),
        ({"expected_serial": "SN1"}, "RIGOL,DP832,SN\x001,1"),
    ],
)
def test_identity_controls_and_serial_case_mismatch_never_authorize_safe_state_writes(
    monkeypatch, expected, live
):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": live})
    _patch_rm(monkeypatch, {resource: instrument})
    binding = {
        "expected_manufacturer": "RIGOL",
        "expected_model": "DP832",
        "expected_serial": "SN1",
        **expected,
    }

    try:
        result = safety.apply_safe_state(expected_devices={resource: binding})[0]
        assert result.state == "unverifiable"
    except safety.SafeStateConfigError:
        pass

    assert instrument.writes == []


def test_safe_state_identity_allows_spaces_and_vendor_model_case_with_exact_serial(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({
        "*IDN?": "  rigol  ,  dp832  ,  SN1  ,1",
        ":OUTPut? CH1": "OFF", ":OUTPut? CH2": "OFF", ":OUTPut? CH3": "OFF",
        ":MEASure:VOLTage? CH1": "0", ":MEASure:VOLTage? CH2": "0",
        ":MEASure:VOLTage? CH3": "0", ":MEASure:CURRent? CH1": "0",
        ":MEASure:CURRent? CH2": "0", ":MEASure:CURRent? CH3": "0",
    })
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": " RIGOL ", "expected_model": " DP832 ",
        "expected_serial": " SN1 ",
    }})[0]

    assert result.state == "verified_safe"
    assert instrument.writes == list(safety.SAFE_STATE_COMMANDS["DP832"])


def test_configured_malformed_idn_performs_zero_model_specific_writes(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DP832"})
    _patch_rm(monkeypatch, {resource: instrument})

    result = safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": "RIGOL", "expected_model": "DP832",
        "expected_serial": "RIGHT",
    }})[0]

    assert result.state == "unverifiable"
    assert instrument.writes == []


def test_off_status_without_safe_measured_voltage_is_not_verified_safe(monkeypatch):
    resource = "USB::DL3021"
    instrument = FakeInstrument({
        "*IDN?": "RIGOL,DL3021,SN,1", ":INPut?": "OFF",
        ":MEASure:VOLTage?": "12.0", ":MEASure:CURRent?": "0", ":MEASure:POWer?": "0",
    })
    _patch_rm(monkeypatch, {resource: instrument})

    assert safety.apply_safe_state(expected_devices={resource: {
        "expected_manufacturer": "RIGOL",
        "expected_model": "DL3021",
        "expected_serial": "SN",
    }})[0].state == "unsafe"


def test_non_numeric_measurement_evidence_is_unverifiable(monkeypatch):
    resource = "USB::DL3021"
    instrument = FakeInstrument({
        "*IDN?": "RIGOL,DL3021,SN,1", ":INPut?": "OFF",
        ":MEASure:VOLTage?": "OVLD", ":MEASure:CURRent?": "0", ":MEASure:POWer?": "0",
    })
    _patch_rm(monkeypatch, {resource: instrument})

    assert safety.apply_safe_state()[0].state == "unverifiable"


def test_unknown_expected_instrument_requires_explicit_non_energy_validation(monkeypatch):
    resource = "USB::UNKNOWN"
    instrument = FakeInstrument({"*IDN?": "ACME,MYSTERY,SN-1,1"})
    _patch_rm(monkeypatch, {resource: instrument})

    expected = {"expected_model": "MYSTERY", "expected_serial": "SN-1"}
    assert safety.apply_safe_state(expected_devices={resource: expected})[0].state == "unverifiable"
    expected["validated_non_energy"] = True
    assert safety.apply_safe_state(expected_devices={resource: expected})[0].state == "no_action_required"


@pytest.mark.parametrize(
    "classification",
    [
        {"energy_source": True, "is_energy_source": False},
        {"energy_source": False, "is_energy_source": True},
        {"energy_source": True, "validated_non_energy": True},
        {"is_energy_source": True, "validated_non_energy": True},
    ],
)
def test_contradictory_energy_classification_is_rejected_before_resource_access(
    monkeypatch, classification
):
    resource_manager_calls = []
    monkeypatch.setattr(
        safety.pyvisa,
        "ResourceManager",
        lambda *_: resource_manager_calls.append(True) or FakeRM({}),
    )
    expected = {
        "expected_manufacturer": "ACME",
        "expected_model": "MYSTERY",
        "expected_serial": "SN-1",
        **classification,
    }

    with pytest.raises(safety.SafeStateConfigError, match="contradictory energy classification"):
        safety.apply_safe_state(expected_devices={"USB::UNKNOWN": expected})

    assert resource_manager_calls == []


def test_nested_inventory_energy_classification_conflict_is_rejected_before_resource_access(
    monkeypatch,
):
    resource_manager_calls = []
    monkeypatch.setattr(
        safety.pyvisa,
        "ResourceManager",
        lambda *_: resource_manager_calls.append(True) or FakeRM({}),
    )
    config = {"rig": {"instruments": [{
        "name": "mystery",
        "connection": "USB::UNKNOWN",
        "expected_manufacturer": "ACME",
        "expected_model": "MYSTERY",
        "expected_serial": "SN-1",
        "energy_source": True,
        "safety": {"energy_source": False},
    }]}}

    with pytest.raises(safety.SafeStateConfigError, match="contradictory energy classification"):
        safety.apply_safe_state(config=config)

    assert resource_manager_calls == []


def test_cross_input_energy_classification_conflict_is_rejected_before_resource_access(
    monkeypatch,
):
    resource = "USB::UNKNOWN"
    resource_manager_calls = []
    monkeypatch.setattr(
        safety.pyvisa,
        "ResourceManager",
        lambda *_: resource_manager_calls.append(True) or FakeRM({}),
    )
    config = {"rig": {"instruments": [{
        "name": "mystery",
        "connection": resource,
        "expected_manufacturer": "ACME",
        "expected_model": "MYSTERY",
        "expected_serial": "SN-1",
        "energy_source": True,
    }]}}
    expected_devices = {resource: {
        "expected_manufacturer": "ACME",
        "expected_model": "MYSTERY",
        "expected_serial": "SN-1",
        "energy_source": False,
    }}

    with pytest.raises(safety.SafeStateConfigError, match="contradictory energy classification"):
        safety.apply_safe_state(config=config, expected_devices=expected_devices)

    assert resource_manager_calls == []


@pytest.mark.parametrize(
    ("field", "conflicting_alias", "conflicting_value"),
    [
        ("manufacturer", "manufacturer", "OTHER"),
        ("model", "model", "DL3021"),
        ("serial", "serial", "WRONG"),
    ],
)
def test_nested_safety_exact_identity_conflict_is_rejected_before_resource_access(
    monkeypatch, field, conflicting_alias, conflicting_value
):
    resource_manager_calls = []
    monkeypatch.setattr(
        safety.pyvisa,
        "ResourceManager",
        lambda *_: resource_manager_calls.append(True) or FakeRM({}),
    )
    config = {"rig": {"instruments": [{
        "name": "psu",
        "connection": "USB::DP832",
        "expected_manufacturer": "RIGOL",
        "expected_model": "DP832",
        "expected_serial": "RIGHT",
        "safety": {conflicting_alias: conflicting_value},
    }]}}

    with pytest.raises(safety.SafeStateConfigError, match=field):
        safety.apply_safe_state(config=config)

    assert resource_manager_calls == []


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("manufacturer", "OTHER"),
        ("model", "DL3021"),
        ("serial", "WRONG"),
    ],
)
def test_cross_input_exact_identity_conflict_is_rejected_before_resource_access(
    monkeypatch, field, conflicting_value
):
    resource_manager_calls = []
    monkeypatch.setattr(
        safety.pyvisa,
        "ResourceManager",
        lambda *_: resource_manager_calls.append(True) or FakeRM({}),
    )
    resource = "USB::DP832"
    config = {"rig": {"instruments": [{
        "name": "psu",
        "connection": resource,
        "expected_manufacturer": "RIGOL",
        "expected_model": "DP832",
        "expected_serial": "RIGHT",
    }]}}
    expected = {
        "expected_manufacturer": "rigol",
        "expected_model": "dp832",
        "expected_serial": "right",
    }
    expected[f"expected_{field}"] = conflicting_value
    expected_devices = {f"  {resource.lower()}  ": expected}

    with pytest.raises(safety.SafeStateConfigError, match=field):
        safety.apply_safe_state(config=config, expected_devices=expected_devices)

    assert resource_manager_calls == []


def test_documented_bench_example_explicitly_reviews_scope_as_non_energy():
    repository = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (repository / "examples/lab_preflight_bench_a.yaml").read_text(encoding="utf-8")
    )
    scope = next(
        spec for spec in config["rig"]["instruments"] if spec["expected_model"] == "DS1102E"
    )

    assert scope["expected_manufacturer"] == "RIGOL TECHNOLOGIES"
    assert scope["expected_serial"] == "DS1ZA000000000"
    assert scope["safety"]["validated_non_energy"] is True


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"rig": {}},
        {"rig": {"instruments": []}},
        {"rig": {"instruments": ["not-a-mapping"]}},
        {"rig": {"instruments": [{"name": "psu", "connection": "USB::PSU"}]}},
    ],
)
def test_configured_safe_state_rejects_malformed_or_empty_inventory_before_opening(
    monkeypatch, config
):
    opened = []

    class TrackingRM(FakeRM):
        def open_resource(self, resource):
            opened.append(resource)
            return super().open_resource(resource)

    monkeypatch.setattr(safety.pyvisa, "ResourceManager", lambda *_: TrackingRM({}))

    with pytest.raises(safety.SafeStateConfigError):
        safety.apply_safe_state(config=config)

    assert opened == []


@pytest.mark.parametrize("duplicate", ["name", "connection"])
def test_configured_safe_state_rejects_duplicate_names_and_resource_bindings(
    monkeypatch, duplicate
):
    _patch_rm(monkeypatch, {})
    first = {
        "name": "psu-a", "connection": "USB::A", "expected_manufacturer": "RIGOL",
        "expected_model": "DP832", "expected_serial": "SN-A",
    }
    second = {
        "name": "psu-b", "connection": "USB::B", "expected_manufacturer": "RIGOL",
        "expected_model": "DP832", "expected_serial": "SN-B",
    }
    second[duplicate] = first[duplicate]

    with pytest.raises(safety.SafeStateConfigError, match="duplicate"):
        safety.apply_safe_state(config={"rig": {"instruments": [first, second]}})


def test_configured_supported_source_requires_complete_identity_before_any_write(monkeypatch):
    resource = "USB::DP832"
    instrument = FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})
    _patch_rm(monkeypatch, {resource: instrument})
    config = {"rig": {"instruments": [{
        "name": "psu", "connection": resource, "expected_model": "DP832",
    }]}}

    with pytest.raises(safety.SafeStateConfigError, match="manufacturer.*model.*serial"):
        safety.apply_safe_state(config=config)

    assert instrument.writes == []


def test_unbound_supported_source_is_not_written_when_inventory_is_configured(monkeypatch):
    configured = "USB::SCOPE"
    discovered = "USB::DP832"
    source = FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})
    scope = FakeInstrument({
        "*IDN?": "RIGOL,DS1102E,SCOPE1,1",
        ":CHANnel1:DISPlay?": "OFF",
        ":CHANnel2:DISPlay?": "OFF",
        ":TRIGger:STATus?": "STOP",
    })
    _patch_rm(monkeypatch, {configured: scope, discovered: source})
    config = {"rig": {"instruments": [{
        "name": "scope", "connection": configured, "expected_manufacturer": "RIGOL",
        "expected_model": "DS1102E", "expected_serial": "SCOPE1",
        "validated_non_energy": True,
    }]}}

    results = safety.apply_safe_state(config=config)

    source_result = next(item for item in results if item.resource == discovered)
    assert source_result.state == "unverifiable"
    assert source.writes == []


def test_labjack_acknowledged_dac_writes_are_unverifiable_without_dac_readback(monkeypatch):
    identity = InstrumentIdentity(transport="usb", resource="USB::u3", model="U3", serial="470012345",
                                  idn="LabJack,U3,470012345", vendor_id="0cd5", product_id="0003")
    monkeypatch.setattr(safety, "discover_usb", lambda: [identity])
    seen = []
    ain_reads = []
    class Driver:
        def __init__(self, *, serial): seen.append(serial)
        def safe_state(self): pass
        def read_ain(self, channel):
            ain_reads.append(channel)
            return 0.0
        def close(self): pass
    monkeypatch.setattr(safety, "LabJackU3Driver", Driver)
    config = {"rig": {"instruments": [{
        "name": "daq", "connection": identity.resource,
        "expected_manufacturer": "LabJack", "expected_model": "U3",
        "expected_serial": "470012345",
    }]}}
    result = safety.apply_usb_safe_state(config=config)[0]
    assert seen == ["470012345"]
    assert ain_reads == []
    assert result.actions == ("DAC0=0.0 V", "DAC1=0.0 V")
    assert result.checks == ()
    assert result.state == "unverifiable"
    assert not result.safe
    assert any("DAC readback" in error for error in result.errors)


def test_no_config_labjack_discovery_is_strictly_read_only(monkeypatch):
    identity = InstrumentIdentity(
        transport="usb", resource="USB::u3", manufacturer="LabJack", model="U3",
        serial="470012345", idn="LabJack,U3,470012345",
        vendor_id="0cd5", product_id="0003",
    )
    monkeypatch.setattr(safety, "discover_usb", lambda: [identity])
    opened = []
    monkeypatch.setattr(safety, "LabJackU3Driver", lambda **kwargs: opened.append(kwargs))

    result = safety.apply_usb_safe_state()[0]

    assert opened == []
    assert result.actions == ()
    assert result.state == "unverifiable"
    assert any("read-only discovery" in error for error in result.errors)


def test_configured_labjack_identity_mismatch_performs_zero_dac_writes(monkeypatch):
    identity = InstrumentIdentity(
        transport="usb", resource="USB::u3", manufacturer="LabJack", model="U3",
        serial="WRONG", idn="LabJack,U3,WRONG", vendor_id="0cd5", product_id="0003",
    )
    monkeypatch.setattr(safety, "discover_usb", lambda: [identity])
    opened = []
    monkeypatch.setattr(safety, "LabJackU3Driver", lambda **kwargs: opened.append(kwargs))
    config = {"rig": {"instruments": [{
        "name": "daq", "connection": identity.resource,
        "expected_manufacturer": "LabJack", "expected_model": "U3",
        "expected_serial": "RIGHT",
    }]}}

    result = safety.apply_usb_safe_state(config=config)[0]

    assert opened == []
    assert result.actions == ()
    assert result.state == "unverifiable"
    assert any("identity mismatch" in error for error in result.errors)


def test_configured_labjack_missing_from_discovery_is_unverifiable(monkeypatch):
    monkeypatch.setattr(safety, "discover_usb", lambda: [])
    resource = "USB::0cd5::0003::serial470012345"
    config = {"rig": {"instruments": [{
        "name": "daq", "connection": resource,
        "expected_manufacturer": "LabJack", "expected_model": "U3",
        "expected_serial": "470012345",
    }]}}

    result = safety.apply_usb_safe_state(config=config)

    assert len(result) == 1
    assert result[0].resource == resource
    assert result[0].state == "unverifiable"
    assert any("not discovered" in error for error in result[0].errors)


def test_labjack_unknown_serial_is_unverifiable_without_opening_driver(monkeypatch):
    identity = InstrumentIdentity(transport="usb", resource="USB::u3", model="U3", serial="UNKNOWN",
                                  idn="LabJack,U3,UNKNOWN", vendor_id="0cd5", product_id="0003")
    monkeypatch.setattr(safety, "discover_usb", lambda: [identity])
    monkeypatch.setattr(safety, "LabJackU3Driver", lambda **_: (_ for _ in ()).throw(AssertionError("must not open unbound")))
    assert safety.apply_usb_safe_state()[0].state == "unverifiable"


def test_safe_cli_returns_nonzero_for_blocking_result(monkeypatch):
    blocked = safety.SafeStateResult("r", "id", "DP832", (), (), ("failure",), "unverifiable")
    monkeypatch.setattr(safety, "apply_safe_state", lambda: [blocked])
    monkeypatch.setattr(safety, "apply_usb_safe_state", lambda: [])
    assert safety.main([]) != 0


def test_safe_cli_passes_config_to_labjack_binding(monkeypatch, tmp_path):
    config = {"rig": {"instruments": [{
        "name": "daq", "connection": "USB::0cd5::0003::serial470012345",
        "expected_manufacturer": "LabJack", "expected_model": "U3",
        "expected_serial": "470012345",
    }]}}
    config_path = tmp_path / "bench.yaml"
    config_path.write_text(yaml.safe_dump(config))
    seen = []
    monkeypatch.setattr(safety, "apply_safe_state", lambda **kwargs: [])
    monkeypatch.setattr(
        safety, "apply_usb_safe_state", lambda **kwargs: seen.append(kwargs) or []
    )

    assert safety.main([str(config_path)]) == 0
    assert seen == [{"config": config}]


def test_safe_cli_loads_expected_equipment_config_and_fails_when_discovery_is_empty(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "bench.yaml"
    config_path.write_text(yaml.safe_dump({
        "rig": {"instruments": [{
            "name": "main_psu",
            "connection": "USB::MISSING",
            "expected_model": "Rigol DP832",
            "safety": {"energy_source": True},
        }]}
    }))
    _patch_rm(monkeypatch, {})
    monkeypatch.setattr(safety, "discover_usb", lambda: [])

    assert safety.main([str(config_path)]) == 2
