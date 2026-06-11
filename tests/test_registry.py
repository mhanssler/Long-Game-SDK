from __future__ import annotations

import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity
from long_game_sdk.sdk.registry import ensure_schema, infer_capability_profile, match_driver


def test_unknown_scope_gets_generated_scope_schema(tmp_path):
    identity = InstrumentIdentity(
        transport="visa",
        resource="USB0::1::2::SCOPE::INSTR",
        manufacturer="ExampleCo",
        model="WaveRunner 9000",
        serial="123",
        idn="ExampleCo,WaveRunner 9000,123,1.0",
    )

    match = match_driver(identity)
    assert match.instrument_class == "oscilloscope"
    assert match.confidence == "generated-profile"

    schema_path = ensure_schema(identity, schemas_dir=tmp_path)
    assert schema_path is not None
    schema = yaml.safe_load(schema_path.read_text())
    assert schema["device"]["instrument_class"] == "oscilloscope"
    assert "identify" in schema["capabilities"]["oscilloscope"]["commands"]
    assert schema["safety"]["safe_state"] == []


def test_unknown_power_supply_gets_output_off_safe_state(tmp_path):
    identity = InstrumentIdentity(
        transport="visa",
        resource="USB0::1::2::PSU::INSTR",
        manufacturer="ExampleCo",
        model="DC Power Supply 42",
        serial="123",
        idn="ExampleCo,DC Power Supply 42,123,1.0",
    )

    profile = infer_capability_profile(identity)
    assert profile.instrument_class == "power_supply"

    schema_path = ensure_schema(identity, schemas_dir=tmp_path)
    assert schema_path is not None
    schema = yaml.safe_load(schema_path.read_text())
    assert ":OUTPut OFF" in schema["safety"]["safe_state"]
    assert "measure_voltage" in schema["capabilities"]["power_supply"]["commands"]


def test_unknown_raw_usb_gets_placeholder_schema(tmp_path):
    identity = InstrumentIdentity(
        transport="usb",
        resource="USB::aaaa::bbbb::bus1-addr2",
        manufacturer="Mystery",
        model="Widget",
        serial="123",
        idn="Mystery,Widget,123",
        vendor_id="aaaa",
        product_id="bbbb",
    )

    match = match_driver(identity)
    assert match.driver_kind == "usb-placeholder"

    schema_path = ensure_schema(identity, schemas_dir=tmp_path)
    assert schema_path is not None
    schema = yaml.safe_load(schema_path.read_text())
    assert schema["device"]["protocol"] == "USB"
    assert schema["safety"]["safe_state"] == []
