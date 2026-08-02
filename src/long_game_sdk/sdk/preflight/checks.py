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
import subprocess

import yaml

from long_game_sdk.sdk.preflight import instrument_checks
from long_game_sdk.sdk.preflight.environment_checks import run_environment_checks
from long_game_sdk.sdk.preflight.instrument_checks import InstrumentAdapter, run_instrument_checks
from long_game_sdk.sdk.preflight.safety_checks import run_safety_checks

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


def _is_energy_source(spec: Mapping[str, Any]) -> bool:
    safety = spec.get("safety")
    return bool(
        spec.get("energy_source")
        or spec.get("is_energy_source")
        or (isinstance(safety, Mapping) and (safety.get("energy_source") or safety.get("is_energy_source")))
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate the complete inventory before any instrument resource is opened."""
    if not isinstance(config, Mapping):
        raise PreflightConfigError("preflight config must be a mapping")
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
        if _is_energy_source(raw_spec):
            for field_name in ("expected_manufacturer", "expected_model", "expected_serial"):
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
            if str(raw_spec.get("expected_model", "")).strip().casefold() == "dp832" and channel_names != {
                "ch1", "ch2", "ch3"
            }:
                raise PreflightConfigError(
                    f"DP832 energy source {name!r} requires explicit CH1, CH2, and CH3 evidence"
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
        results.extend(run_instrument_checks(config, instruments=active_instruments))
        results.extend(run_safety_checks(config, instruments=active_instruments))
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
