"""Fail-closed hardware smoke tests for expected instruments.

`lg-smoke CONFIG` accepts an exact expected-equipment inventory, positively
verifies the whole discovered bench is safe, performs read-only probes, and
positively verifies safe state again. A missing inventory or any unsafe or
unverifiable result prevents probes.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyvisa
import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity, discover_all
from long_game_sdk.sdk.drivers.labjack_u3 import LabJackDependencyError, LabJackU3Driver
from long_game_sdk.sdk.registry import ensure_schema, match_driver
from long_game_sdk.sdk.safety import SafeStateResult, apply_safe_state, apply_usb_safe_state


@dataclass(frozen=True)
class SmokeResult:
    resource: str
    model: str
    instrument_class: str
    driver_kind: str
    schema: str
    checks: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]


class SmokeSafetyError(RuntimeError):
    """Raised when a smoke run cannot prove its initial or final safe state."""

    probe_phase_entered: bool | None = None


_READ_ONLY_SCPI_QUERY = re.compile(
    r"^(?:\*[A-Za-z][A-Za-z0-9]*|:[A-Za-z][A-Za-z0-9]*(?::[A-Za-z][A-Za-z0-9]*)*)\?"
    r"(?:\s+[A-Za-z0-9_.+\-]+(?:\s*,\s*[A-Za-z0-9_.+\-]+)*)?$"
)


def _is_read_only_scpi_query(value: Any) -> bool:
    """Accept one syntactically read-only SCPI query and no compound commands."""

    if not isinstance(value, str):
        return False
    query = value.strip()
    return (
        query.count("?") == 1
        and not any(separator in query for separator in (";", "\n", "\r"))
        and _READ_ONLY_SCPI_QUERY.fullmatch(query) is not None
    )


def _verify_safe_state(config: Mapping[str, Any] | None, phase: str) -> None:
    """Attempt both safety transports and reject anything not positively safe."""

    results: list[SafeStateResult] = []
    failures: list[str] = []
    for label, operation in (("VISA", apply_safe_state), ("USB", apply_usb_safe_state)):
        try:
            results.extend(operation(config=config))
        except Exception as exc:  # noqa: BLE001 - attempt the other transport too
            failures.append(f"{label}: {exc}")

    if config is None:
        failures.append("an expected-equipment config is required")
    failures.extend(
        f"{item.resource}: {item.state}"
        + (f" ({'; '.join(item.errors)})" if item.errors else "")
        for item in results
        if not item.safe
    )
    if failures:
        raise SmokeSafetyError(f"{phase} safe-state verification failed: " + "; ".join(failures))


def _load_schema(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _safe_queries(schema: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    safety = schema.get("safety", {}) if isinstance(schema, dict) else {}
    for query in safety.get("verification", []) or []:
        if _is_read_only_scpi_query(query):
            queries.append(query.strip())
    for capability in (schema.get("capabilities", {}) or {}).values():
        commands = capability.get("commands", {}) if isinstance(capability, dict) else {}
        for name, command in commands.items():
            if not _is_read_only_scpi_query(command):
                continue
            # Skip parameterized queries in smoke; they need user/test context.
            if "{" in command or "}" in command:
                continue
            if name in {"identify", "get_event_status", "get_operation_complete", "get_output", "get_input", "read"}:
                queries.append(command)
    deduped: list[str] = []
    for query in ["*IDN?", *queries]:
        if query not in deduped:
            deduped.append(query)
    return deduped[:8]


def _smoke_visa(identity: InstrumentIdentity, schema_path: Path | None) -> SmokeResult:
    match = match_driver(identity)
    schema = _load_schema(schema_path)
    checks: list[tuple[str, str]] = []
    errors: list[str] = []
    rm = pyvisa.ResourceManager("@py")
    instrument = None
    try:
        instrument = cast(Any, rm.open_resource(identity.resource))
        instrument.timeout = 3000
        for query in _safe_queries(schema):
            try:
                checks.append((query, str(instrument.query(query)).strip().replace("\x00", "")))
            except Exception as exc:  # noqa: BLE001 - report and continue
                errors.append(f"{query}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    finally:
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass
        try:
            rm.close()
        except Exception:
            pass
    return SmokeResult(identity.resource, identity.model, match.instrument_class, match.driver_kind, str(schema_path or ""), tuple(checks), tuple(errors))


def _smoke_usb(identity: InstrumentIdentity, schema_path: Path | None) -> SmokeResult:
    match = match_driver(identity)
    checks: list[tuple[str, str]] = []
    errors: list[str] = []
    if (identity.vendor_id, identity.product_id) == ("0cd5", "0003"):
        driver = None
        try:
            if not identity.serial or identity.serial.upper() == "UNKNOWN":
                raise RuntimeError("LabJack serial is unavailable; refusing to open an unbound device")
            driver = LabJackU3Driver(serial=identity.serial)
            checks.append(("AIN0", f"{driver.read_ain(0):.6f}"))
            checks.append(("AIN1", f"{driver.read_ain(1):.6f}"))
        except LabJackDependencyError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LabJack smoke failed: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass
    else:
        checks.append(("usb_identity", identity.idn))
    return SmokeResult(identity.resource, identity.model, match.instrument_class, match.driver_kind, str(schema_path or ""), tuple(checks), tuple(errors))


def run_smoke(config: Mapping[str, Any] | None = None) -> list[SmokeResult]:
    """Probe only between two positively verified safe-state operations.

    ``config`` is optional at the Python-call level for compatibility, but a
    no-config invocation always fails closed after read-only safety discovery.
    """

    results: list[SmokeResult] = []
    probe_phase_entered = False
    try:
        # This gate prevents discovery/probes unless every result is positively
        # safe or explicitly validated as non-energizing.
        try:
            _verify_safe_state(config, "initial")
        except SmokeSafetyError as exc:
            exc.probe_phase_entered = False
            raise
        probe_phase_entered = True
        for identity in discover_all():
            schema_path = ensure_schema(identity)
            if identity.transport == "visa":
                results.append(_smoke_visa(identity, schema_path))
            else:
                results.append(_smoke_usb(identity, schema_path))
    finally:
        # Outermost cleanup covers gate, discovery, and probe failures. The
        # helper attempts both transports before it can fail.
        try:
            _verify_safe_state(config, "final")
        except SmokeSafetyError as exc:
            exc.probe_phase_entered = probe_phase_entered
            raise
    return results


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lg-smoke",
        description=(
            "Safely verify communication with the exact instruments declared in a bench YAML file."
        ),
        epilog="""What BENCH_CONFIG is:
  An input YAML file containing rig.instruments with each expected instrument's
  exact connection and identity. Use the same bench YAML accepted by lg-preflight;
  it is not a report file or an instrument address by itself.

What the command does:
  1. Requires an initial verified safe state for the complete declared bench.
  2. Runs identity and other allow-listed read-only probes.
  3. Requires a final verified safe state, even after a probe failure.

Example (PowerShell):
  lg-discover
  lg-preflight .\\bench.yaml
  lg-smoke .\\bench.yaml

Result meaning:
  PASS     All read-only probes passed, and both safety gates passed.
  FAIL     Safety gates passed, but one or more read-only probes failed.
  BLOCKED  Initial or final safety verification failed; the run is not a PASS.
           An initial gate failure prevents probes. A final gate failure can
           occur after probes have run.

Exit status: 0 = PASS, 1 = probe failure, 2 = invalid config or safety BLOCKED.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        metavar="BENCH_CONFIG",
        help="path to bench/preflight YAML containing the exact rig.instruments inventory",
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        loaded = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping):
            raise ValueError("config must be a mapping")
        config = cast(Mapping[str, Any], loaded)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"smoke config error: {exc}")
        return 2

    print("--- Long Game SDK Hardware Smoke ---")
    print(f"Bench config: {Path(args.config)}")
    print("Sequence: initial safety gate -> read-only probes -> final safety gate")
    try:
        results = run_smoke(config)
    except SmokeSafetyError as exc:
        print(
            "BLOCKED: Exact instrument identity and safe output state "
            "could not be positively verified at one or more safety gates."
        )
        if exc.probe_phase_entered is False:
            print("Read-only smoke probes were not allowed: the initial safety gate failed.")
        elif exc.probe_phase_entered is True:
            print("Read-only probe phase was entered; final safety verification failed.")
        else:
            print("Probe execution status is unavailable; do not assume that no probes ran.")
        print("This is an unverified condition, not proof that the bench is dangerous.")
        print("Safe-state commands may have been attempted; confirm the physical bench state before continuing.")
        print(f"Details: {exc}")
        return 2
    for result in results:
        print(f"\n{result.resource}")
        print(f"  model:  {result.model}")
        print(f"  class:  {result.instrument_class}")
        print(f"  driver: {result.driver_kind}")
        print(f"  schema: {result.schema}")
        if result.checks:
            print("  checks:")
            for query, response in result.checks:
                print(f"    {query} -> {response}")
        if result.errors:
            print("  errors:")
            for error in result.errors:
                print(f"    {error}")

    passed_checks = sum(len(result.checks) for result in results)
    failed_checks = sum(len(result.errors) for result in results)
    instrument_count = len(results)
    instrument_label = "instrument" if instrument_count == 1 else "instruments"
    if failed_checks:
        print(
            f"\nFAIL: {instrument_count} {instrument_label} probed; "
            "initial and final safe-state verification passed, but read-only probe errors occurred."
        )
    else:
        print(
            f"\nPASS: {instrument_count} {instrument_label} probed; "
            "initial and final safe-state verification passed."
        )
    print(f"Read-only checks: {passed_checks} passed, {failed_checks} failed")
    return 1 if failed_checks else 0


if __name__ == "__main__":
    raise SystemExit(main())
