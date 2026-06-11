"""Instrument registry and schema onboarding.

This module is the bridge between discovered hardware and executable drivers.
Known equipment gets a high-confidence built-in schema. Unknown SCPI equipment
gets a conservative generic schema that still exposes identity/error/status
commands and records enough metadata for later expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

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


KNOWN_MODELS: dict[str, DriverMatch] = {
    "DP832": DriverMatch("rigol_dp832", "rigol_dp832.yaml", "scpi-universal", "built-in"),
    "DL3021": DriverMatch("rigol_dl3021", "rigol_dl3021.yaml", "scpi-universal", "built-in"),
    "DS1102E": DriverMatch("rigol_ds1102e", "rigol_ds1102e.yaml", "scpi-universal", "built-in"),
    "U3": DriverMatch("labjack_u3", "labjack_u3.yaml", "labjack-u3", "built-in"),
}

USB_MODEL_HINTS: dict[tuple[str, str], str] = {
    ("0cd5", "0003"): "U3",
}


def match_driver(identity: InstrumentIdentity) -> DriverMatch:
    model_text = f"{identity.manufacturer} {identity.model} {identity.idn}".upper()
    for model, match in KNOWN_MODELS.items():
        if model.upper() in model_text:
            return match
    if identity.vendor_id and identity.product_id:
        hinted = USB_MODEL_HINTS.get((identity.vendor_id.lower(), identity.product_id.lower()))
        if hinted:
            return KNOWN_MODELS[hinted]
    safe_model = identity.model.lower().replace(" ", "_").replace("/", "_")
    safe_mfg = identity.manufacturer.lower().replace(" ", "_").replace("/", "_")
    key = f"{safe_mfg}_{safe_model}".strip("_") or "unknown_instrument"
    return DriverMatch(key=key, schema_filename=f"generated_{key}.yaml", driver_kind="scpi-universal", confidence="generated-generic")


def _generic_scpi_schema(identity: InstrumentIdentity) -> dict:
    return {
        "device": {
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "serial": identity.serial,
            "protocol": "SCPI",
            "version": "generated-0.1.0",
        },
        "capabilities": {
            "identity": {
                "commands": {
                    "identify": "*IDN?",
                    "reset": "*RST",
                    "clear_status": "*CLS",
                    "get_event_status": "*ESR?",
                    "get_operation_complete": "*OPC?",
                    "get_system_error": ":SYSTem:ERRor?",
                }
            }
        },
        "safety": {
            "safe_state": [],
            "notes": "Generated generic SCPI schema. No output-affecting safe-state commands inferred.",
        },
        "identification": {"idn_pattern": f".*{identity.model}.*"},
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
    if match.confidence == "generated-generic":
        path.write_text(yaml.safe_dump(_generic_scpi_schema(identity), sort_keys=False), encoding="utf-8")
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
                  transport: {item['transport']}
                  resource:   {item['resource']}
                  driver:     {item['driver_kind']} ({item['confidence']})
                  schema:     {item['schema']}
                """
            ).strip()
        )


if __name__ == "__main__":
    print_onboarding_report()
