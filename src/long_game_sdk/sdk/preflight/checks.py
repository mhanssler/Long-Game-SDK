"""Core lab preflight orchestration.

The preflight layer is intentionally hardware-adapter friendly: production runs can
open PyVISA/serial/TCP instruments, while tests and sales demos can inject fake
instrument adapters and still generate realistic readiness reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
import os
import re
import subprocess

import yaml

from long_game_sdk.sdk.preflight import instrument_checks
from long_game_sdk.sdk.preflight.environment_checks import run_environment_checks
from long_game_sdk.sdk.preflight.instrument_checks import (
    InstrumentAdapter,
    ParsedLiveIdentity,
    identities_equal,
    identity_field_equal,
    normalized_identity_value,
    run_instrument_checks,
)
from long_game_sdk.sdk.preflight.safety_checks import energy_control_kind, run_safety_checks

SUPPORTED_CHECKS = frozenset({
    "identity",
    "instrument_reachable",
    "output_disabled_on_start",
    "voltage_limit",
    "current_limit",
    "calibration_date",
})


class PreflightConfigError(ValueError):
    """Raised before resource access when a preflight inventory is unsafe or malformed."""


_IDENTITY_FIELDS = ("expected_manufacturer", "expected_model", "expected_serial")


def _canonicalize_identity(raw_spec: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a copy with either full expected identity alias canonicalized and expanded."""
    spec = dict(raw_spec)
    for field_name in ("expected_identity", "expected_idn", *_IDENTITY_FIELDS):
        value = spec.get(field_name)
        if isinstance(value, str):
            try:
                spec[field_name] = normalized_identity_value(value)
            except ValueError as exc:
                raise PreflightConfigError(
                    f"instrument {name!r} {field_name} contains a control character"
                ) from exc
    aliases: list[tuple[str, str, tuple[str, ...]]] = []
    for alias_name in ("expected_identity", "expected_idn"):
        alias = spec.get(alias_name)
        if alias is None:
            continue
        if not isinstance(alias, str):
            raise PreflightConfigError(
                f"instrument {name!r} {alias_name} must be a full IDN string"
            )
        parts = tuple(part.strip() for part in alias.split(","))
        if len(parts) < 3 or any(not part for part in parts[:3]):
            raise PreflightConfigError(
                f"instrument {name!r} {alias_name} must contain manufacturer, model, and serial"
            )
        aliases.append((alias_name, ",".join(parts), parts))

    if aliases:
        canonical_alias = aliases[0][1]
        try:
            canonical_identity = ParsedLiveIdentity.parse(canonical_alias)
            aliases_conflict = any(
                not identities_equal(canonical_identity, ParsedLiveIdentity.parse(alias))
                for _, alias, _ in aliases[1:]
            )
        except ValueError as exc:
            raise PreflightConfigError(f"instrument {name!r} has malformed expected identity") from exc
        if aliases_conflict:
            raise PreflightConfigError(
                f"instrument {name!r} has conflicting expected identity declarations"
            )
        for field_name, alias_value in zip(_IDENTITY_FIELDS, aliases[0][2][:3], strict=True):
            explicit = spec.get(field_name)
            comparison_field = field_name.removeprefix("expected_")
            if explicit is not None and (
                not isinstance(explicit, str)
                or not identity_field_equal(comparison_field, explicit, alias_value)
            ):
                raise PreflightConfigError(
                    f"instrument {name!r} has conflicting expected identity declarations"
                )
            spec[field_name] = alias_value
        spec["expected_identity"] = canonical_alias
        spec.pop("expected_idn", None)
    return spec


def _normalized_config(config: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    rig = config.get("rig")
    if not isinstance(rig, Mapping):
        return normalized
    normalized_rig = dict(rig)
    inventory = rig.get("instruments")
    if isinstance(inventory, list):
        normalized_rig["instruments"] = [
            _canonicalize_identity(item, str(item.get("name", "unnamed_instrument")))
            if isinstance(item, Mapping)
            else item
            for item in inventory
        ]
    normalized["rig"] = normalized_rig
    return normalized


def _query_addresses_channel(query: str, channel: str, query_field: str) -> bool:
    """Return whether a trusted DP832 query explicitly selects ``channel``.

    Malformed/non-read-only commands are left to the safety check layer so they
    remain structured report failures rather than inventory-shape errors.
    """
    trusted_patterns = {
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
    }
    if trusted_patterns[query_field].fullmatch(query.strip()) is None:
        return True
    match = re.fullmatch(r"CH(\d+)", channel.strip(), re.IGNORECASE)
    if match is None:
        return False
    number = re.escape(match.group(1))
    # DP832 output state queries select a ``CHn`` argument, while source
    # voltage/current queries encode the same channel in ``SOURce<n>``.
    return bool(
        re.search(rf"(?<![A-Z0-9])CH(?:AN(?:NEL)?)?\s*{number}(?!\d)", query, re.IGNORECASE)
        or re.search(rf"(?<![A-Z0-9])SOUR(?:CE)?\s*{number}(?!\d)", query, re.IGNORECASE)
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate the complete inventory before any instrument resource is opened."""
    if not isinstance(config, Mapping):
        raise PreflightConfigError("preflight config must be a mapping")
    config = _normalized_config(config)
    rig = config.get("rig")
    if not isinstance(rig, Mapping):
        raise PreflightConfigError("preflight config requires a rig mapping")
    inventory = rig.get("instruments")
    if not isinstance(inventory, list) or not inventory:
        raise PreflightConfigError("rig.instruments must be a non-empty list")
    names: set[str] = set()
    connections: set[str] = set()
    for index, raw_spec in enumerate(inventory, start=1):
        if not isinstance(raw_spec, Mapping):
            raise PreflightConfigError(f"rig.instruments[{index}] must be a mapping")
        name_value = raw_spec.get("name")
        if not isinstance(name_value, str) or not name_value.strip():
            raise PreflightConfigError(f"rig.instruments[{index}].name must be a non-empty string")
        name = name_value.strip()
        name_key = name.casefold()
        if name_key in names:
            raise PreflightConfigError(f"duplicate instrument name: {name!r}")
        names.add(name_key)
        if "connection" in raw_spec:
            connection_value = raw_spec.get("connection")
            if not isinstance(connection_value, str) or not connection_value.strip():
                raise PreflightConfigError(
                    f"instrument {name!r} connection must be a non-empty string"
                )
            connection = connection_value.strip()
            connection_key = connection.casefold()
            if connection_key in connections:
                raise PreflightConfigError(f"duplicate instrument connection: {connection!r}")
            connections.add(connection_key)
        configured_checks = raw_spec.get("checks")
        if not isinstance(configured_checks, list) or not configured_checks:
            raise PreflightConfigError(f"instrument {name!r} checks must be a non-empty list")
        if any(not isinstance(check, str) or not check.strip() for check in configured_checks):
            raise PreflightConfigError(f"instrument {name!r} checks must contain non-empty strings")
        if len(configured_checks) != len(set(configured_checks)):
            raise PreflightConfigError(f"instrument {name!r} has duplicate checks")
        unknown = set(configured_checks) - SUPPORTED_CHECKS
        if unknown:
            raise PreflightConfigError(f"instrument {name!r} has unsupported checks: {sorted(unknown)}")
        safety = raw_spec.get("safety", {})
        if not isinstance(safety, Mapping):
            raise PreflightConfigError(f"instrument {name!r} safety must be a mapping")
        control_kind = energy_control_kind(raw_spec)
        identity_values = [raw_spec.get(field_name) for field_name in _IDENTITY_FIELDS]
        if control_kind is not None and any(identity_values) and not all(
            isinstance(value, str) and value.strip() for value in identity_values
        ):
            missing_identity = [
                field_name
                for field_name, value in zip(_IDENTITY_FIELDS, identity_values, strict=True)
                if not isinstance(value, str) or not value.strip()
            ]
            raise PreflightConfigError(
                f"energy controller {name!r} has partial expected identity; "
                f"missing {', '.join(missing_identity)}"
            )
        if control_kind == "source":
            for field_name in _IDENTITY_FIELDS:
                value = raw_spec.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise PreflightConfigError(f"energy source {name!r} requires exact {field_name}")
            channels = safety.get("channels")
            if not isinstance(channels, list) or not channels:
                raise PreflightConfigError(
                    f"energy source {name!r} requires explicit safety.channels readback evidence"
                )
            channel_names: set[str] = set()
            for channel_index, channel in enumerate(channels, start=1):
                if not isinstance(channel, Mapping):
                    raise PreflightConfigError(
                        f"energy source {name!r} safety.channels[{channel_index}] must be a mapping"
                    )
                channel_name = channel.get("channel")
                if not isinstance(channel_name, str) or not channel_name.strip():
                    raise PreflightConfigError(
                        f"energy source {name!r} safety.channels[{channel_index}] requires channel"
                    )
                channel_key = channel_name.strip().casefold()
                if channel_key in channel_names:
                    raise PreflightConfigError(
                        f"energy source {name!r} has duplicate safety channel {channel_name!r}"
                    )
                channel_names.add(channel_key)
                for field_name in (
                    "output_query", "voltage_limit", "voltage_query",
                    "current_limit", "current_query",
                ):
                    if field_name not in channel:
                        raise PreflightConfigError(
                            f"energy source {name!r} channel {channel_name!r} requires {field_name}"
                        )
                for query_field in ("output_query", "voltage_query", "current_query"):
                    query = channel.get(query_field)
                    if isinstance(query, str) and query.strip() and not _query_addresses_channel(
                        query, channel_name, query_field
                    ):
                        raise PreflightConfigError(
                            f"energy source {name!r} channel {channel_name!r} {query_field} "
                            f"must explicitly address {channel_name.strip()}"
                        )
            if str(raw_spec.get("expected_model", "")).strip().casefold() == "dp832" and channel_names != {
                "ch1", "ch2", "ch3"
            }:
                raise PreflightConfigError(
                    f"DP832 energy source {name!r} requires explicit CH1, CH2, and CH3 evidence"
                )
        elif control_kind == "load":
            required = (
                "input_query", "voltage_limit", "voltage_query", "current_limit",
                "current_query", "power_limit", "power_query",
            )
            missing = [field_name for field_name in required if field_name not in safety]
            if missing:
                raise PreflightConfigError(
                    f"DL3021 energy-controlling load {name!r} requires explicit input-state, voltage, "
                    f"current, and power live evidence; missing {', '.join(missing)}"
                )
    runtime = config.get("runtime", {})
    if not isinstance(runtime, Mapping):
        raise PreflightConfigError("runtime must be a mapping")


@dataclass(frozen=True)
class CheckResult:
    """Single readiness check result."""

    name: str
    status: str
    message: str
    category: str
    severity: str = "medium"
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True)
class PreflightReport:
    """Aggregated preflight outcome."""

    rig_name: str
    dut_type: str
    generated_at: str
    operator: str | None
    dut_serial: str | None
    git_commit: str | None
    results: tuple[CheckResult, ...]

    @property
    def ready(self) -> bool:
        return not any(result.status == "fail" for result in self.results)

    @property
    def summary_counts(self) -> dict[str, int]:
        counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a lab preflight YAML config."""

    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Preflight config must be a mapping: {config_path}")
    return data


def capture_git_commit(repo: str | Path | None = None) -> str | None:
    """Return the current git commit for traceability, if available."""

    workdir = Path(repo) if repo else Path.cwd()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=workdir,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
    except Exception:
        return None


def run_preflight(
    config: Mapping[str, Any],
    *,
    instruments: Mapping[str, InstrumentAdapter] | None = None,
    env: Mapping[str, str] | None = None,
    repo: str | Path | None = None,
) -> PreflightReport:
    """Run all configured preflight checks and return a structured report."""

    config = _normalized_config(config)
    validate_config(config)
    rig = dict(config.get("rig") or {})
    runtime = dict(config.get("runtime") or {})
    environment = env if env is not None else os.environ
    operator = runtime.get("operator") or environment.get("LG_OPERATOR")
    dut_serial = runtime.get("dut_serial") or environment.get("LG_DUT_SERIAL")
    git_commit = runtime.get("git_commit") or capture_git_commit(repo)

    active_instruments: dict[str, InstrumentAdapter] = dict(instruments or {})
    owned_adapters: list[InstrumentAdapter] = []
    if instruments is None:
        for spec in (rig.get("instruments") or []):
            name = str(spec.get("name", "unnamed_instrument"))
            connection = spec.get("connection")
            if not connection:
                continue
            try:
                adapter = instrument_checks.VisaInstrumentAdapter(str(connection))
            except Exception as exc:  # noqa: BLE001 - checks report reachability instead of aborting
                class UnavailableAdapter:
                    def query(self, command: str, *, _exc: Exception = exc) -> str:
                        raise _exc

                    def write(self, command: str, *, _exc: Exception = exc) -> Any:
                        raise _exc

                    def close(self) -> None:
                        return None

                active_instruments[name] = UnavailableAdapter()
            else:
                active_instruments[name] = adapter
                owned_adapters.append(adapter)

    results: list[CheckResult] = []
    try:
        if not operator:
            results.append(
                CheckResult(
                    name="operator_captured",
                    category="environment",
                    status="warn",
                    severity="low",
                    message="Operator name not supplied; set runtime.operator or LG_OPERATOR.",
                )
            )
        if not dut_serial:
            results.append(
                CheckResult(
                    name="dut_serial_captured",
                    category="environment",
                    status="warn",
                    severity="medium",
                    message="DUT serial not supplied; set runtime.dut_serial or LG_DUT_SERIAL.",
                )
            )

        results.extend(run_environment_checks(config, env=environment, repo=repo, git_commit=git_commit))
        instrument_outcome = run_instrument_checks(config, instruments=active_instruments)
        results.extend(instrument_outcome.results)
        results.extend(run_safety_checks(
            config,
            instruments=active_instruments,
            live_identities=instrument_outcome.live_identities,
        ))
    finally:
        for owned_adapter in reversed(owned_adapters):
            try:
                owned_adapter.close()
            except Exception:
                pass

    return PreflightReport(
        rig_name=str(rig.get("name", "unknown-rig")),
        dut_type=str(rig.get("dut_type", "unknown-dut")),
        generated_at=datetime.now(UTC).isoformat(),
        operator=operator,
        dut_serial=dut_serial,
        git_commit=git_commit,
        results=tuple(results),
    )
