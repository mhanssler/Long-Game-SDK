"""Core lab preflight orchestration.

The preflight layer is intentionally hardware-adapter friendly: production runs can
open PyVISA/serial/TCP instruments, while tests and sales demos can inject fake
instrument adapters and still generate realistic readiness reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol
import os
import subprocess

import yaml

from long_game_sdk.sdk.preflight.environment_checks import run_environment_checks
from long_game_sdk.sdk.preflight.instrument_checks import InstrumentAdapter, run_instrument_checks
from long_game_sdk.sdk.preflight.safety_checks import run_safety_checks


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

    rig = dict(config.get("rig") or {})
    runtime = dict(config.get("runtime") or {})
    environment = env if env is not None else os.environ
    operator = runtime.get("operator") or environment.get("LG_OPERATOR")
    dut_serial = runtime.get("dut_serial") or environment.get("LG_DUT_SERIAL")
    git_commit = runtime.get("git_commit") or capture_git_commit(repo)

    results: list[CheckResult] = []
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
    results.extend(run_instrument_checks(config, instruments=instruments))
    results.extend(run_safety_checks(config, instruments=instruments))

    return PreflightReport(
        rig_name=str(rig.get("name", "unknown-rig")),
        dut_type=str(rig.get("dut_type", "unknown-dut")),
        generated_at=datetime.now(UTC).isoformat(),
        operator=operator,
        dut_serial=dut_serial,
        git_commit=git_commit,
        results=tuple(results),
    )
