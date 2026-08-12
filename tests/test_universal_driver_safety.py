from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from long_game_sdk.sdk.universal_driver import (
    InstrumentCommandError,
    MutationSafetyError,
    SchemaSafetyError,
    UniversalDriver,
)


class FakeInstrument:
    def __init__(self, idn: str = "ACME,SAFE-1,SN1,1.0") -> None:
        self.idn = idn
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.closed = False
        self.fail_on: str | None = None

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command == self.fail_on:
            raise OSError("transport failed")
        if command == "*IDN?":
            return self.idn
        if command == ":SYSTem:ERRor?":
            return '-222,"Data out of range"'
        return "42"

    def write(self, command: str) -> int:
        self.writes.append(command)
        if command == self.fail_on:
            raise OSError("transport failed")
        return len(command)

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self, instrument: FakeInstrument) -> None:
        self.instrument = instrument
        self.opened: list[str] = []

    def open_resource(self, resource_name: str) -> FakeInstrument:
        self.opened.append(resource_name)
        return self.instrument

    def close(self) -> None:
        pass


def write_schema(tmp_path: Path, schema: dict) -> Path:
    path = tmp_path / "device.yaml"
    path.write_text(yaml.safe_dump(schema), encoding="utf-8")
    return path


def schema_with(commands: dict) -> dict:
    return {
        "device": {"manufacturer": "ACME", "model": "SAFE-1"},
        "identification": {"idn_pattern": r"ACME,SAFE-1,"},
        "capabilities": {"source": {"commands": commands}},
    }


def bound_identity() -> dict[str, str]:
    return {
        "manufacturer": "ACME",
        "model": "SAFE-1",
        "serial": "SN1",
    }


def bounded_write() -> dict:
    return {
        "template": ":SOURce{channel}:VOLTage {value:.3f}",
        "operation": "write",
        "parameters": {
            "channel": {"minimum": 1, "maximum": 2},
            "value": {"minimum": 0.0, "maximum": 5.0},
        },
    }


def test_legacy_schema_is_read_only_and_uses_template_query_marker(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(
        tmp_path,
        schema_with({"measure": ":MEASure? CH{channel}", "set_value": ":SET {value}"}),
    )
    driver = UniversalDriver("mock", str(path), instrument=instrument)

    assert driver.measure(channel=1) == "42"
    with pytest.raises(SchemaSafetyError, match="legacy command.*read-only"):
        driver.set_value(value="1?")

    assert instrument.queries == [":MEASure? CH1"]
    assert instrument.writes == []


def test_explicit_read_metadata_dispatches_query_like_command(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(
        tmp_path,
        schema_with(
            {"read_register": {"template": ":READ {register}?  ", "operation": "read"}}
        ),
    )
    driver = UniversalDriver("mock", str(path), instrument=instrument)

    assert driver.read_register(register="STATUS") == "42"
    assert instrument.queries == [":READ STATUS?  "]


def test_explicit_read_metadata_cannot_disguise_mutating_command(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(
        tmp_path,
        schema_with(
            {"enable_output": {"template": ":OUTPut ON", "operation": "read"}}
        ),
    )

    with pytest.raises(SchemaSafetyError, match="query"):
        UniversalDriver("mock", str(path), instrument=instrument)

    assert instrument.queries == []
    assert instrument.writes == []


@pytest.mark.parametrize(
    "command_name",
    ["schema_trusted", "armed", "instrument", "identity_verified", "_armed_context"],
)
def test_dynamic_command_names_cannot_collide_with_driver_or_security_attributes(
    tmp_path: Path, command_name: str
) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({command_name: "*IDN?"}))

    with pytest.raises(SchemaSafetyError, match="collides|reserved"):
        UniversalDriver("mock", str(path), instrument=instrument)

    assert instrument.queries == []


def test_duplicate_dynamic_command_names_across_capabilities_are_rejected(tmp_path: Path) -> None:
    schema = schema_with({"identify": "*IDN?"})
    schema["capabilities"]["diagnostics"] = {"commands": {"identify": ":SYSTem:ERRor?"}}
    path = write_schema(tmp_path, schema)

    with pytest.raises(SchemaSafetyError, match="duplicate"):
        UniversalDriver("mock", str(path), instrument=FakeInstrument())


@pytest.mark.parametrize("trusted", [False, True])
def test_write_rejected_without_all_safety_boundaries(tmp_path: Path, trusted: bool) -> None:
    instrument = FakeInstrument(idn="OTHER,MODEL,SN1,1.0" if trusted else "ACME,SAFE-1,SN1,1.0")
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver("mock", str(path), instrument=instrument, trusted_schema=trusted)

    with driver.armed():
        with pytest.raises(MutationSafetyError):
            driver.set_voltage(channel=1, value=3.3)
    assert instrument.writes == []


def test_bounded_write_allowed_only_inside_armed_context(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with pytest.raises(MutationSafetyError, match="armed"):
        driver.set_voltage(channel=1, value=3.3)
    with driver.armed():
        driver.set_voltage(channel=1, value=3.3)
    with pytest.raises(MutationSafetyError, match="armed"):
        driver.set_voltage(channel=1, value=3.3)

    assert instrument.writes == [":SOURce1:VOLTage 3.300"]


def test_armed_write_rechecks_identity_instead_of_using_cached_verification(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed():
        instrument.idn = "ACME,SAFE-1,REPLACED,1.0"
        with pytest.raises(MutationSafetyError, match="identity"):
            driver.set_voltage(channel=1, value=3.3)

    assert instrument.queries == ["*IDN?", "*IDN?"]
    assert instrument.writes == []


@pytest.mark.parametrize(
    "live_idn",
    [
        "ACME,SAFE-1,sn1,1.0",
        "\tACME,SAFE-1,SN1,1.0",
        "ACME,SAFE\x00-1,SN1,1.0",
        "ACME,SAFE-1,SN\x001,1.0",
    ],
)
def test_live_identity_controls_and_serial_case_mismatch_never_authorize_write(
    tmp_path: Path, live_idn: str
) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", path, instrument=instrument, trusted_schema=True,
        expected_identity=bound_identity(),
    )
    instrument.idn = live_idn

    with driver.armed(), pytest.raises(MutationSafetyError, match="identity"):
        driver.set_voltage(channel=1, value=1.0)

    assert instrument.writes == []


@pytest.mark.parametrize("serial", ["\tSN1", "SN\x001", "SN1\x7f"])
def test_configured_identity_controls_are_rejected_without_writes(
    tmp_path: Path, serial: str
) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))

    with pytest.raises(ValueError, match="control"):
        UniversalDriver(
            "mock", path, instrument=instrument, trusted_schema=True,
            expected_identity={"manufacturer": "ACME", "model": "SAFE-1", "serial": serial},
        )

    assert instrument.writes == []


def test_write_identity_allows_spaces_and_vendor_model_case_with_exact_serial(tmp_path: Path) -> None:
    instrument = FakeInstrument(idn="  acme  ,  safe-1  ,  SN1  ,1.0")
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", path, instrument=instrument, trusted_schema=True,
        expected_identity={"manufacturer": " ACME ", "model": " SAFE-1 ", "serial": " SN1 "},
    )

    with driver.armed():
        driver.set_voltage(channel=1, value=1.0)

    assert instrument.writes == [":SOURce1:VOLTage 1.000"]


def test_armed_write_rejects_replaced_instrument_object(tmp_path: Path) -> None:
    original = FakeInstrument()
    replacement = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=original, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed():
        driver.instrument = replacement
        with pytest.raises(MutationSafetyError, match="instrument.*changed|identity"):
            driver.set_voltage(channel=1, value=3.3)

    assert replacement.writes == []


def test_armed_write_rejects_instrument_swap_during_identity_recheck(tmp_path: Path) -> None:
    original = FakeInstrument()
    replacement = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=original, trusted_schema=True, expected_identity=bound_identity()
    )
    original_query = original.query

    def swapping_query(command: str) -> str:
        response = original_query(command)
        if command == "*IDN?":
            driver.instrument = replacement
        return response

    original.query = swapping_query  # type: ignore[method-assign]

    with driver.armed(), pytest.raises(MutationSafetyError, match="instrument.*changed|identity"):
        driver.set_voltage(channel=1, value=3.3)

    assert original.writes == []
    assert replacement.writes == []


def test_bounded_write_accepts_compact_bounds_metadata(tmp_path: Path) -> None:
    command = {
        "command": ":SOURce{channel}:VOLTage {value}",
        "operation": "mutating",
        "bounds": {"channel": [1, 2], "value": [0.0, 5.0]},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed():
        driver.set_voltage(channel=2, value=5.0)

    assert instrument.writes == [":SOURce2:VOLTage 5.0"]


@pytest.mark.parametrize("channel,value", [(0, 3.3), (1, -0.1), (1, 5.1), (1, "not-a-number")])
def test_write_rejects_out_of_bounds_or_non_numeric_parameters(
    tmp_path: Path, channel: int, value: object
) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="bounds|numeric"):
        driver.set_voltage(channel=channel, value=value)
    assert instrument.writes == []


def test_write_rejects_numeric_parameter_without_declared_bounds(tmp_path: Path) -> None:
    command = bounded_write()
    del command["parameters"]["channel"]
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="bounds"):
        driver.set_voltage(channel=1, value=3.3)
    assert instrument.writes == []


@pytest.mark.parametrize("value", ["999999", "-0.25", "1e6", "NaN", "Infinity"])
def test_write_rejects_numeric_lexical_string_without_declared_bounds(
    tmp_path: Path, value: str
) -> None:
    command = {
        "template": ":SOURce:VOLTage {value}",
        "operation": "write",
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="bounds"):
        driver.set_voltage(value=value)

    assert instrument.writes == []


def test_bounded_numeric_lexical_string_is_checked_as_numeric(tmp_path: Path) -> None:
    command = {
        "template": ":SOURce:VOLTage {value:.3f}",
        "operation": "write",
        "parameters": {"value": {"minimum": 0.0, "maximum": 5.0}},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed():
        driver.set_voltage(value="3.3")
        with pytest.raises(MutationSafetyError, match="outside bounds"):
            driver.set_voltage(value="999999")

    assert instrument.writes == [":SOURce:VOLTage 3.300"]


def test_numeric_lexical_enum_cannot_replace_finite_bounds(tmp_path: Path) -> None:
    command = {
        "template": ":SOURce:VOLTage {value}",
        "operation": "write",
        "parameters": {"value": {"enum": ["999999"]}},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="bounds"):
        driver.set_voltage(value="999999")

    assert instrument.writes == []


def test_enumerated_non_numeric_string_parameter_remains_allowed(tmp_path: Path) -> None:
    command = {
        "template": ":SOURce:FUNCtion {mode}",
        "operation": "write",
        "parameters": {"mode": {"enum": ["VOLTage", "CURRent"]}},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_mode": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed():
        driver.set_mode(mode="VOLTage")

    assert instrument.writes == [":SOURce:FUNCtion VOLTage"]


def test_numeric_enum_requires_exact_type_and_bool_cannot_match_one(tmp_path: Path) -> None:
    command = {
        "template": ":ROUTe {channel}",
        "operation": "write",
        "parameters": {"channel": {"enum": [1, 2], "minimum": 1, "maximum": 2}},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"route": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True,
        expected_identity=bound_identity(),
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="enum"):
        getattr(driver, "route")(channel=True)
    assert instrument.writes == []


def test_write_rejects_value_outside_declared_non_numeric_enum(tmp_path: Path) -> None:
    command = {
        "template": ":SOURce:FUNCtion {mode}",
        "operation": "write",
        "parameters": {"mode": {"enum": ["VOLTage", "CURRent"]}},
    }
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_mode": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="enum|allowed"):
        driver.set_mode(mode="VOLTage;*RST")

    assert instrument.writes == []


def test_write_rejects_unconstrained_non_numeric_field(tmp_path: Path) -> None:
    command = {"template": ":ROUTe {route}", "operation": "write"}
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_route": command}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="constraint|enum"):
        driver.set_route(route="SAFE")

    assert instrument.writes == []


def test_mutation_requires_exact_out_of_band_manufacturer_model_serial_binding(
    tmp_path: Path,
) -> None:
    instrument = FakeInstrument(idn="ACME,SAFE-1-EVIL,SN1,1.0")
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="identity"):
        driver.set_voltage(channel=1, value=1.0)
    assert instrument.writes == []


def test_schema_identity_pattern_is_not_an_out_of_band_mutation_binding(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver("mock", str(path), instrument=instrument, trusted_schema=True)

    with driver.armed(), pytest.raises(MutationSafetyError, match="out-of-band|binding|identity"):
        driver.set_voltage(channel=1, value=1.0)
    assert instrument.writes == []


def test_malformed_idn_cannot_authorize_mutation(tmp_path: Path) -> None:
    instrument = FakeInstrument(idn="ACME,SAFE-1")
    path = write_schema(tmp_path, schema_with({"set_voltage": bounded_write()}))
    driver = UniversalDriver(
        "mock", str(path), instrument=instrument, trusted_schema=True, expected_identity=bound_identity()
    )

    with driver.armed(), pytest.raises(MutationSafetyError, match="identity"):
        driver.set_voltage(channel=1, value=1.0)
    assert instrument.writes == []


@pytest.mark.parametrize("separator", [";", "\n", "\r"])
def test_rejects_command_separators_from_templates_or_parameters(
    tmp_path: Path, separator: str
) -> None:
    instrument = FakeInstrument()
    path = write_schema(
        tmp_path,
        schema_with(
            {
                "read": {
                    "template": ":READ {register}?",
                    "operation": "read",
                }
            }
        ),
    )
    driver = UniversalDriver("mock", str(path), instrument=instrument)

    with pytest.raises(SchemaSafetyError, match="multiple commands|separator"):
        driver.read(register=f"STATUS{separator}*RST")
    assert instrument.queries == []


def test_resource_manager_can_be_injected(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    manager = FakeResourceManager(instrument)
    path = write_schema(tmp_path, schema_with({"identify": "*IDN?"}))

    driver = UniversalDriver("mock-resource", str(path), resource_manager=manager)

    assert manager.opened == ["mock-resource"]
    assert driver.identify() == instrument.idn


def test_owned_manager_closes_even_when_instrument_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Instrument(FakeInstrument):
        def close(self) -> None:
            events.append("instrument-close")
            raise OSError("close failed")

    class Manager:
        def open_resource(self, resource_name: str) -> Instrument:
            return Instrument()

        def close(self) -> None:
            events.append("manager-close")

    monkeypatch.setattr("long_game_sdk.sdk.universal_driver.pyvisa.ResourceManager", Manager)
    path = write_schema(tmp_path, schema_with({"identify": "*IDN?"}))
    driver = UniversalDriver("mock", str(path))

    with pytest.raises(OSError, match="close failed"):
        driver.close()
    assert events == ["instrument-close", "manager-close"]


def test_initialization_failure_closes_opened_instrument_and_owned_manager_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Instrument(FakeInstrument):
        def close(self) -> None:
            events.append("instrument-close")
            raise OSError("instrument close failed")

    class Manager:
        def open_resource(self, resource_name: str) -> Instrument:
            return Instrument()

        def close(self) -> None:
            events.append("manager-close")

    monkeypatch.setattr("long_game_sdk.sdk.universal_driver.pyvisa.ResourceManager", Manager)
    bad_schema = write_schema(tmp_path, {"capabilities": []})

    with pytest.raises(SchemaSafetyError):
        UniversalDriver("mock", bad_schema)

    assert events == ["instrument-close", "manager-close"]


def test_initialization_failure_closes_instrument_opened_by_injected_manager_only(
    tmp_path: Path,
) -> None:
    instrument = FakeInstrument()
    manager = FakeResourceManager(instrument)
    bad_schema = write_schema(tmp_path, {"capabilities": []})

    with pytest.raises(SchemaSafetyError):
        UniversalDriver("mock", bad_schema, resource_manager=manager)

    assert instrument.closed


def test_transport_error_is_raised_structurally_without_online_lookup(tmp_path: Path) -> None:
    instrument = FakeInstrument()
    instrument.fail_on = ":READ?"
    path = write_schema(tmp_path, schema_with({"read": ":READ?"}))
    driver = UniversalDriver("mock", str(path), instrument=instrument)
    driver.agent = object()  # A configured agent must never be consulted by error handling.

    with pytest.raises(InstrumentCommandError) as caught:
        driver.read()

    error = caught.value
    assert isinstance(error.original_error, OSError)
    assert error.command == ":READ?"
    assert error.operation == "read"
    assert error.instrument_error == '-222,"Data out of range"'
    assert instrument.queries == [":READ?", ":SYSTem:ERRor?"]
