"""Instrument registry and schema onboarding.

The registry turns discovery results into executable driver strategies. Known
instruments get curated schemas. Unknown SCPI/VISA instruments get a generated
schema based on conservative instrument-class inference so the SDK can still
identify, classify, safely probe, and expose common commands immediately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity, discover_all

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = PROJECT_ROOT / "schemas"


@dataclass(frozen=True)
class DriverMatch:
    key: str
    schema_filename: str | None
    driver_kind: str
    confidence: str
    instrument_class: str = "unknown"


@dataclass(frozen=True)
class CapabilityProfile:
    instrument_class: str
    aliases: tuple[str, ...]
    commands: dict[str, str]
    safe_state: tuple[str, ...]
    verification: tuple[str, ...]
    notes: str


COMMON_SCPI_COMMANDS: dict[str, str] = {
    "identify": "*IDN?",
    "clear_status": "*CLS",
    "get_event_status": "*ESR?",
    "get_operation_complete": "*OPC?",
    "get_system_error": ":SYSTem:ERRor?",
}

CAPABILITY_PROFILES: tuple[CapabilityProfile, ...] = (
    CapabilityProfile(
        instrument_class="oscilloscope",
        aliases=("OSCILLOSCOPE", "SCOPE", "DSO", "MSO", "WAVERUNNER", "WAVESURFER", "INFINIIVISION", "TEKTRONIX TDS", "TEKTRONIX MSO", "TEKTRONIX DPO"),
        commands={
            "run": ":RUN",
            "stop": ":STOP",
            "single": ":SINGle",
            "get_trigger_status": ":TRIGger:STATus?",
            "measure_vpp": ":MEASure:VPP? CHANnel{channel}",
            "measure_frequency": ":MEASure:FREQuency? CHANnel{channel}",
        },
        safe_state=(),
        verification=(":TRIGger:STATus?",),
        notes="Read-only by default for safe-state. Acquisition control commands are exposed but not run by smoke tests.",
    ),
    CapabilityProfile(
        instrument_class="power_supply",
        aliases=("POWER SUPPLY", "POWER-SUPPLY", "DC POWER", "PSU", "SUPPLY", "E363", "E36", "DP8", "N67", "N57"),
        commands={
            "output_on": ":OUTPut ON",
            "output_off": ":OUTPut OFF",
            "get_output": ":OUTPut?",
            "set_voltage": ":VOLTage {value:.6f}",
            "get_voltage": ":VOLTage?",
            "set_current": ":CURRent {value:.6f}",
            "get_current": ":CURRent?",
            "measure_voltage": ":MEASure:VOLTage?",
            "measure_current": ":MEASure:CURRent?",
        },
        safe_state=(":OUTPut OFF", "OUTPut OFF", "OUTP OFF"),
        verification=(":OUTPut?", ":MEASure:VOLTage?", ":MEASure:CURRent?"),
        notes="Generated source profile. Safe-state attempts common output-off forms only.",
    ),
    CapabilityProfile(
        instrument_class="electronic_load",
        aliases=("ELECTRONIC LOAD", "DC LOAD", "LOAD", "DL3", "N330", "63600"),
        commands={
            "input_on": ":INPut ON",
            "input_off": ":INPut OFF",
            "get_input": ":INPut?",
            "set_current": ":CURRent {value:.6f}",
            "get_current_setpoint": ":CURRent?",
            "measure_voltage": ":MEASure:VOLTage?",
            "measure_current": ":MEASure:CURRent?",
            "measure_power": ":MEASure:POWer?",
        },
        safe_state=(":INPut OFF", "INPut OFF", "LOAD OFF"),
        verification=(":INPut?", ":MEASure:CURRent?", ":MEASure:POWer?"),
        notes="Generated sink/load profile. Safe-state attempts common input-off forms only.",
    ),
    CapabilityProfile(
        instrument_class="dmm",
        aliases=("MULTIMETER", "DMM", "DIGITAL MULTIMETER", "344", "DM30", "DM40", "DMM6500"),
        commands={
            "read": ":READ?",
            "measure_voltage_dc": ":MEASure:VOLTage:DC?",
            "measure_voltage_ac": ":MEASure:VOLTage:AC?",
            "measure_current_dc": ":MEASure:CURRent:DC?",
            "measure_resistance": ":MEASure:RESistance?",
        },
        safe_state=(),
        verification=(":READ?",),
        notes="Measurement-only profile; no output-affecting safe-state commands.",
    ),
    CapabilityProfile(
        instrument_class="signal_generator",
        aliases=("SIGNAL GENERATOR", "FUNCTION GENERATOR", "WAVEFORM GENERATOR", "AWG", "AFG", "DG8", "336", "335"),
        commands={
            "output_on": ":OUTPut ON",
            "output_off": ":OUTPut OFF",
            "get_output": ":OUTPut?",
            "set_frequency": ":FREQuency {value:.6f}",
            "get_frequency": ":FREQuency?",
            "set_amplitude": ":VOLTage {value:.6f}",
            "get_amplitude": ":VOLTage?",
        },
        safe_state=(":OUTPut OFF", "OUTPut OFF", "OUTP OFF"),
        verification=(":OUTPut?", ":FREQuency?", ":VOLTage?"),
        notes="Generated source profile. Safe-state attempts output-off only.",
    ),
    CapabilityProfile(
        instrument_class="spectrum_analyzer",
        aliases=("SPECTRUM", "SIGNAL ANALYZER", "N90", "FSV", "FSEA", "RSA"),
        commands={
            "get_center_frequency": ":FREQuency:CENTer?",
            "set_center_frequency": ":FREQuency:CENTer {value:.6f}",
            "get_span": ":FREQuency:SPAN?",
            "set_span": ":FREQuency:SPAN {value:.6f}",
            "marker_y": ":CALCulate:MARKer:Y?",
        },
        safe_state=(),
        verification=(":FREQuency:CENTer?", ":FREQuency:SPAN?"),
        notes="Measurement profile; no output-affecting safe-state commands.",
    ),
    CapabilityProfile(
        instrument_class="daq",
        aliases=("DAQ", "DATA ACQUISITION", "SWITCH", "349", "DAQ6510"),
        commands={
            "read": ":READ?",
            "scan": ":ROUTe:SCAN?",
            "measure_voltage_dc": ":MEASure:VOLTage:DC? (@{channel})",
        },
        safe_state=(),
        verification=(":READ?",),
        notes="DAQ/readback profile; no output-affecting safe-state commands.",
    ),
)

GENERIC_PROFILE = CapabilityProfile(
    instrument_class="generic_scpi",
    aliases=(),
    commands={},
    safe_state=(),
    verification=("*ESR?", ":SYSTem:ERRor?"),
    notes="Generic SCPI profile. Only identity/status/error commands are trusted until a richer profile is generated.",
)

KNOWN_MODELS: dict[str, DriverMatch] = {
    "DP832": DriverMatch("rigol_dp832", "rigol_dp832.yaml", "scpi-universal", "built-in", "power_supply"),
    "DL3021": DriverMatch("rigol_dl3021", "rigol_dl3021.yaml", "scpi-universal", "built-in", "electronic_load"),
    "DS1102E": DriverMatch("rigol_ds1102e", "rigol_ds1102e.yaml", "scpi-universal", "built-in", "oscilloscope"),
    "U3": DriverMatch("labjack_u3", "labjack_u3.yaml", "labjack-u3", "built-in", "daq"),
}

USB_MODEL_HINTS: dict[tuple[str, str], str] = {
    ("0cd5", "0003"): "U3",
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "unknown"


def infer_capability_profile(identity: InstrumentIdentity) -> CapabilityProfile:
    text = f"{identity.manufacturer} {identity.model} {identity.idn}".upper()
    for profile in CAPABILITY_PROFILES:
        if any(alias in text for alias in profile.aliases):
            return profile
    return GENERIC_PROFILE


def match_driver(identity: InstrumentIdentity) -> DriverMatch:
    model_text = f"{identity.manufacturer} {identity.model} {identity.idn}".upper()
    for model, match in KNOWN_MODELS.items():
        if model.upper() in model_text:
            return match
    if identity.vendor_id and identity.product_id:
        hinted = USB_MODEL_HINTS.get((identity.vendor_id.lower(), identity.product_id.lower()))
        if hinted:
            return KNOWN_MODELS[hinted]
    if identity.transport != "visa":
        key = f"{_slug(identity.manufacturer)}_{_slug(identity.model)}"
        return DriverMatch(key=key, schema_filename=f"generated_usb_{key}.yaml", driver_kind="usb-placeholder", confidence="generated-placeholder", instrument_class="usb")
    profile = infer_capability_profile(identity)
    key = f"{_slug(identity.manufacturer)}_{_slug(identity.model)}"
    return DriverMatch(
        key=key,
        schema_filename=f"generated_{profile.instrument_class}_{key}.yaml",
        driver_kind="scpi-universal",
        confidence="generated-profile" if profile is not GENERIC_PROFILE else "generated-generic",
        instrument_class=profile.instrument_class,
    )


def _generic_scpi_schema(identity: InstrumentIdentity) -> dict[str, Any]:
    profile = infer_capability_profile(identity)
    commands = {**COMMON_SCPI_COMMANDS, **profile.commands}
    return {
        "device": {
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "serial": identity.serial,
            "protocol": "SCPI",
            "version": "generated-0.2.0",
            "instrument_class": profile.instrument_class,
        },
        "generated": {
            "source": "long-game-sdk auto-onboarding",
            "confidence": "profile" if profile is not GENERIC_PROFILE else "generic",
            "idn": identity.idn,
            "resource": identity.resource,
        },
        "capabilities": {
            profile.instrument_class: {
                "commands": commands,
            }
        },
        "safety": {
            "safe_state": list(profile.safe_state),
            "verification": list(profile.verification),
            "notes": profile.notes,
        },
        "identification": {"idn_pattern": re.escape(identity.model) if identity.model != "UNKNOWN" else ".*"},
    }


def _generic_usb_schema(identity: InstrumentIdentity) -> dict[str, Any]:
    return {
        "device": {
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "serial": identity.serial,
            "protocol": "USB",
            "version": "generated-0.2.0",
            "instrument_class": "usb_unknown",
        },
        "generated": {
            "source": "long-game-sdk auto-onboarding",
            "confidence": "placeholder",
            "idn": identity.idn,
            "resource": identity.resource,
            "vendor_id": identity.vendor_id,
            "product_id": identity.product_id,
        },
        "capabilities": {},
        "safety": {
            "safe_state": [],
            "verification": [],
            "notes": "Raw USB device placeholder. No control attempted until a protocol driver is available.",
        },
        "identification": {
            "vendor_id": identity.vendor_id,
            "product_id": identity.product_id,
        },
    }


def ensure_schema(identity: InstrumentIdentity, schemas_dir: Path = SCHEMAS_DIR) -> Path | None:
    """Ensure an executable schema exists for a discovered instrument."""

    match = match_driver(identity)
    if match.schema_filename is None:
        return None
    schemas_dir.mkdir(parents=True, exist_ok=True)
    path = schemas_dir / match.schema_filename
    if path.exists():
        return path
    if match.confidence in {"generated-generic", "generated-profile"}:
        path.write_text(yaml.safe_dump(_generic_scpi_schema(identity), sort_keys=False), encoding="utf-8")
        return path
    if match.confidence == "generated-placeholder":
        path.write_text(yaml.safe_dump(_generic_usb_schema(identity), sort_keys=False), encoding="utf-8")
        return path
    return path


def onboard_all() -> list[dict[str, str]]:
    """Discover all equipment and ensure each item has a driver strategy."""

    inventory = []
    for identity in discover_all():
        match = match_driver(identity)
        schema = ensure_schema(identity)
        inventory.append(
            {
                "transport": identity.transport,
                "manufacturer": identity.manufacturer,
                "model": identity.model,
                "serial": identity.serial,
                "resource": identity.resource,
                "driver_kind": match.driver_kind,
                "confidence": match.confidence,
                "instrument_class": match.instrument_class,
                "schema": str(schema) if schema else "",
            }
        )
    return inventory


def print_onboarding_report() -> None:
    print("--- Long Game SDK Auto-Onboarding ---")
    for item in onboard_all():
        print(
            dedent(
                f"""
                {item['manufacturer']} {item['model']} ({item['serial']})
                  class:      {item['instrument_class']}
                  transport:  {item['transport']}
                  resource:   {item['resource']}
                  driver:     {item['driver_kind']} ({item['confidence']})
                  schema:     {item['schema']}
                """
            ).strip()
        )


if __name__ == "__main__":
    print_onboarding_report()
