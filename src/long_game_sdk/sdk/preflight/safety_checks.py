"""Fail-closed safety-oriented preflight checks."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Mapping

from long_game_sdk.sdk.preflight.instrument_checks import InstrumentAdapter
from long_game_sdk.sdk.preflight.results import result

_SOURCE_UNITS = {"voltage_limit": "V", "current_limit": "A"}
_LOAD_UNITS = {**_SOURCE_UNITS, "power_limit": "W"}
_NUMBER = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*([A-Za-z]+)?\s*$")
_QUERY_SCHEMAS = {
    "output_query": re.compile(
        r"^:?(?:OUTP(?:UT)?)(?:\s*:\s*STAT(?:E)?)?\?\s*(?:CH(?:AN(?:NEL)?)?\s*\d+)?$",
        re.IGNORECASE,
    ),
    "voltage_query": re.compile(
        r"^:?(?:(?:(?:SOUR(?:CE)?)\s*\d*\s*:)?VOLT(?:AGE)?(?:\s*:\s*LEV(?:EL)?)?|MEAS(?:URE)?\s*:\s*VOLT(?:AGE)?)\?$",
        re.IGNORECASE,
    ),
    "current_query": re.compile(
        r"^:?(?:(?:(?:SOUR(?:CE)?)\s*\d*\s*:)?CURR(?:ENT)?(?:\s*:\s*LEV(?:EL)?)?|MEAS(?:URE)?\s*:\s*CURR(?:ENT)?)\?$",
        re.IGNORECASE,
    ),
    "input_query": re.compile(
        r"^:?(?:INP(?:UT)?)(?:\s*:\s*STAT(?:E)?)?\?$",
        re.IGNORECASE,
    ),
    "power_query": re.compile(
        r"^:?(?:MEAS(?:URE)?\s*:\s*POW(?:ER)?)\?$",
        re.IGNORECASE,
    ),
}


def _instrument_configs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in ((config.get("rig") or {}).get("instruments") or [])]


def _adapter_for(name: str, instruments: Mapping[str, InstrumentAdapter] | None) -> InstrumentAdapter | None:
    return instruments.get(name) if instruments else None


def _has_exact_identity(spec: Mapping[str, Any]) -> bool:
    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            spec.get("expected_manufacturer"),
            spec.get("expected_model"),
            spec.get("expected_serial"),
        )
    )


def energy_control_kind(spec: Mapping[str, Any]) -> str | None:
    """Return ``source`` or ``load`` for explicitly or intrinsically controlled energy."""
    manufacturer = str(spec.get("expected_manufacturer", "")).strip().casefold()
    model = str(spec.get("expected_model", "")).strip().casefold()
    if _has_exact_identity(spec) and model == "dp832":
        return "source"
    if _has_exact_identity(spec) and manufacturer == "rigol" and model == "dl3021":
        return "load"
    safety = spec.get("safety")
    if (
        spec.get("energy_source")
        or spec.get("is_energy_source")
        or (isinstance(safety, Mapping) and (safety.get("energy_source") or safety.get("is_energy_source")))
    ):
        return "source"
    return None


def is_energy_source(spec: Mapping[str, Any]) -> bool:
    """Return whether the instrument sources energy (electronic loads do not)."""
    return energy_control_kind(spec) == "source"


def is_energy_controlling(spec: Mapping[str, Any]) -> bool:
    """Return whether the instrument can source or actively sink controlled energy."""
    return energy_control_kind(spec) is not None


def _quantity(value: Any, unit: str, *, require_mapping_unit: bool = True) -> float:
    """Parse a finite nonnegative quantity; bare numbers use the requested canonical unit."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric safety limit")
    supplied_unit = None
    if isinstance(value, Mapping):
        if "value" not in value or (require_mapping_unit and "unit" not in value):
            raise ValueError("quantity mappings require value and unit")
        supplied_unit = str(value.get("unit", "")).upper()
        value = value["value"]
    if not isinstance(value, (int, float)):
        raise ValueError("must be a typed numeric value, not free-form text")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("must be finite and nonnegative")
    if supplied_unit is not None and supplied_unit != unit:
        raise ValueError(f"unit must be {unit}")
    return number


def _read_quantity(response: str, unit: str) -> float:
    match = _NUMBER.fullmatch(response)
    if not match:
        raise ValueError(f"unparseable numeric readback {response!r}")
    suffix = (match.group(2) or unit).upper()
    if suffix != unit:
        raise ValueError(f"readback unit {suffix} does not match {unit}")
    number = float(match.group(1))
    if not math.isfinite(number) or number < 0:
        raise ValueError("readback must be finite and nonnegative")
    return number


def _off(response: str) -> bool:
    tokens = response.strip().upper().replace(",", " ").split()
    return bool(tokens) and tokens[-1] in {"0", "OFF", "FALSE"}


def _validated_query(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    query = value.strip()
    if any(separator in query for separator in (";", "\n", "\r")) or query.count("?") != 1:
        raise ValueError(f"{field} must be one read-only query without command separators")
    schema = _QUERY_SCHEMAS[field]
    if schema.fullmatch(query) is None:
        raise ValueError(f"{field} is not in the trusted read-only query schema")
    return query


def _calibration_due(value: Any) -> date:
    if isinstance(value, datetime):
        raise ValueError("calibration_due must be a date without a time")
    if isinstance(value, date):
        due = value
    elif isinstance(value, str):
        try:
            due = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("calibration_due must be a valid ISO YYYY-MM-DD date") from exc
    else:
        raise ValueError("calibration_due must be a valid ISO YYYY-MM-DD date")
    if due < date.today():
        raise ValueError(f"calibration expired on {due.isoformat()}")
    return due


def run_safety_checks(config: Mapping[str, Any], *, instruments: Mapping[str, InstrumentAdapter] | None = None):
    checks = []
    for spec in _instrument_configs(config):
        name = str(spec.get("name", "unnamed_instrument"))
        configured = set(spec.get("checks") or [])
        safety = dict(spec.get("safety") or {})
        adapter = _adapter_for(name, instruments)
        control_kind = energy_control_kind(spec)
        source = control_kind == "source"
        load = control_kind == "load"

        if "calibration_date" in configured or safety.get("calibration_due"):
            if safety.get("calibration_due"):
                try:
                    due = _calibration_due(safety["calibration_due"])
                    checks.append(result("calibration_date", "safety", "pass", f"{name}: calibration due {due.isoformat()}."))
                except ValueError as exc:
                    checks.append(result("calibration_date", "safety", "fail", f"{name}: invalid calibration_due: {exc}."))
            else:
                checks.append(result("calibration_date", "safety", "fail", f"{name}: calibration due date not documented."))

        channel_specs = safety.get("channels") if source else None
        if not isinstance(channel_specs, list):
            channel_specs = [safety]
        for channel_spec in channel_specs:
            if not isinstance(channel_spec, Mapping):
                continue  # validate_config reports malformed energy-controller records.
            channel = str(channel_spec.get("channel", "")).strip()
            evidence_prefix = f"{name} {channel}".strip()
            must_verify_state = "output_disabled_on_start" in configured or source or load
            if must_verify_state:
                state_field = "input_query" if load else "output_query"
                result_name = "input_disabled_on_start" if load else "output_disabled_on_start"
                state_label = "load input" if load else "energy-source output"
                query = channel_spec.get(state_field) or (
                    None if control_kind else spec.get(state_field)
                )
                if adapter is None or not query:
                    checks.append(result(
                        result_name, "safety", "fail",
                        f"{evidence_prefix}: {state_label} state is missing or unverifiable.",
                        severity="high", evidence={"channel": channel},
                    ))
                else:
                    try:
                        trusted_query = _validated_query(query, state_field)
                        response = adapter.query(trusted_query).strip()
                        status = "pass" if _off(response) else "fail"
                        checks.append(result(
                            result_name, "safety", status,
                            f"{evidence_prefix}: {trusted_query} returned {response}.",
                            severity="high",
                            evidence={"channel": channel, "query": trusted_query, "response": response},
                        ))
                    except Exception as exc:  # noqa: BLE001
                        checks.append(result(
                            result_name, "safety", "fail",
                            f"{evidence_prefix}: {state_label}-state readback failed: {exc}", severity="high",
                            evidence={"channel": channel, "query": query},
                        ))

            units = _LOAD_UNITS if load else _SOURCE_UNITS
            for key, unit in units.items():
                if key not in configured and control_kind is None:
                    continue
                if key not in channel_spec:
                    checks.append(result(
                        key, "safety", "fail",
                        f"{evidence_prefix}: {key} missing from safety config.",
                        severity="high", evidence={"channel": channel},
                    ))
                    continue
                try:
                    maximum = _quantity(channel_spec[key], unit)
                    query_key = key.removesuffix("_limit") + "_query"
                    setpoint_key = key.removesuffix("_limit") + "_setpoint"
                    if not channel_spec.get(query_key):
                        if control_kind is not None:
                            raise ValueError(f"trusted live {query_key} readback evidence is required")
                        if setpoint_key in channel_spec:
                            actual = _quantity(channel_spec[setpoint_key], unit, require_mapping_unit=False)
                            response = "configured setpoint"
                            trusted_query = ""
                        else:
                            actual = 0.0
                            response = "no configured actual"
                            trusted_query = ""
                    else:
                        if adapter is None:
                            raise ValueError(f"{query_key} configured but no instrument was injected")
                        trusted_query = _validated_query(channel_spec[query_key], query_key)
                        response = adapter.query(trusted_query).strip()
                        actual = _read_quantity(response, unit)
                    exceeded = actual > maximum
                    checks.append(result(
                        key, "safety", "fail" if exceeded else "pass",
                        f"{evidence_prefix}: {key} maximum {maximum} {unit}; live readback {actual} {unit}.",
                        severity="high" if exceeded else "medium",
                        evidence={
                            "channel": channel, "maximum": maximum, "unit": unit,
                            "query": trusted_query, "response": response, "actual": actual,
                        },
                    ))
                except Exception as exc:  # noqa: BLE001
                    checks.append(result(
                        key, "safety", "fail",
                        f"{evidence_prefix}: invalid or unverifiable {key}: {exc}",
                        severity="high", evidence={"channel": channel},
                    ))
    return checks
