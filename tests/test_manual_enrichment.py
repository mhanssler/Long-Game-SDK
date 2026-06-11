from __future__ import annotations

import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity
from long_game_sdk.sdk.manual_enrichment import extract_scpi_commands, merge_commands_into_schema
from long_game_sdk.sdk.registry import ensure_schema


def test_extract_scpi_commands_from_manual_text():
    text = """
    Remote command reference:
    *IDN? returns identification.
    :MEASure:VOLTage? reads voltage.
    :OUTPut CH1,OFF disables output.
    SOURce1:VOLTage:LEVel:IMMediate:AMPLitude 1.000 sets voltage.
    """
    commands = extract_scpi_commands(text)
    assert "*IDN?" in commands
    assert ":MEASURE:VOLTAGE?" in commands
    assert ":OUTPUT CH1,OFF" in commands
    assert "SOURCE1:VOLTAGE:LEVEL:IMMEDIATE:AMPLITUDE 1.000" in commands


def test_merge_manual_commands_into_generated_schema(tmp_path):
    identity = InstrumentIdentity(
        transport="visa",
        resource="USB0::1::2::PSU::INSTR",
        manufacturer="ExampleCo",
        model="DC Power Supply 42",
        serial="123",
        idn="ExampleCo,DC Power Supply 42,123,1.0",
    )
    schema_path = ensure_schema(identity, schemas_dir=tmp_path)
    assert schema_path is not None
    schema = yaml.safe_load(schema_path.read_text())

    merged, added, total = merge_commands_into_schema(
        schema,
        ["*IDN?", ":MEASURE:POWER?", ":OUTPUT OFF"],
        identity,
        "https://example.com/manual.pdf",
    )

    assert total == 3
    assert added >= 1  # *IDN? and :OUTPUT OFF were already present in the generic PSU schema.
    commands = merged["capabilities"]["power_supply"]["commands"]
    assert ":MEASURE:POWER?" in commands.values()
    assert merged["generated"]["manual_enriched"] is True
    assert merged["generated"]["manual_url"] == "https://example.com/manual.pdf"
    assert merged["safety"]["safe_state"]  # existing safe-state retained, not replaced
