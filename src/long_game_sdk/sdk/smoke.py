"""Safe hardware smoke tests for discovered instruments.

`lg-smoke` proves that newly discovered equipment is reachable and classed
without performing hazardous output-enabling actions. It wraps execution in
safe-state calls so live bench tests start and end de-energized.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyvisa
import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity, discover_all
from long_game_sdk.sdk.drivers.labjack_u3 import LabJackDependencyError, LabJackU3Driver
from long_game_sdk.sdk.registry import ensure_schema, match_driver
from long_game_sdk.sdk.safety import apply_safe_state, apply_usb_safe_state


@dataclass(frozen=True)
class SmokeResult:
    resource: str
    model: str
    instrument_class: str
    driver_kind: str
    schema: str
    checks: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]


def _load_schema(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _safe_queries(schema: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    safety = schema.get("safety", {}) if isinstance(schema, dict) else {}
    for query in safety.get("verification", []) or []:
        if isinstance(query, str) and "?" in query:
            queries.append(query)
    for capability in (schema.get("capabilities", {}) or {}).values():
        commands = capability.get("commands", {}) if isinstance(capability, dict) else {}
        for name, command in commands.items():
            if not isinstance(command, str) or "?" not in command:
                continue
            # Skip parameterized queries in smoke; they need user/test context.
            if "{" in command or "}" in command:
                continue
            if name in {"identify", "get_event_status", "get_operation_complete", "get_system_error", "get_output", "get_input", "read"}:
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
    return SmokeResult(identity.resource, identity.model, match.instrument_class, match.driver_kind, str(schema_path or ""), tuple(checks), tuple(errors))


def _smoke_usb(identity: InstrumentIdentity, schema_path: Path | None) -> SmokeResult:
    match = match_driver(identity)
    checks: list[tuple[str, str]] = []
    errors: list[str] = []
    if (identity.vendor_id, identity.product_id) == ("0cd5", "0003"):
        driver = None
        try:
            driver = LabJackU3Driver()
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


def run_smoke() -> list[SmokeResult]:
    """Run safe-state, safe probes, then safe-state again."""

    # Start safe before touching hardware. Unknown gear only gets read-only probes.
    apply_safe_state()
    apply_usb_safe_state()
    results: list[SmokeResult] = []
    try:
        for identity in discover_all():
            schema_path = ensure_schema(identity)
            if identity.transport == "visa":
                results.append(_smoke_visa(identity, schema_path))
            else:
                results.append(_smoke_usb(identity, schema_path))
    finally:
        # End safe even if a probe fails.
        apply_safe_state()
        apply_usb_safe_state()
    return results


def main() -> None:
    print("--- Long Game SDK Hardware Smoke ---")
    for result in run_smoke():
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


if __name__ == "__main__":
    main()
