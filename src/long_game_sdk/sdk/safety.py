"""Safe-state helpers for lab instruments.

The safe-state policy is intentionally conservative: de-energize sources/loads,
perform read-only checks, and avoid changing setpoints unless explicitly asked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, cast

import pyvisa

from long_game_sdk.sdk.discovery import discover_usb
from long_game_sdk.sdk.drivers.labjack_u3 import LabJackDependencyError, LabJackU3Driver


@dataclass(frozen=True)
class SafeStateResult:
    resource: str
    idn: str
    model: str
    actions: tuple[str, ...]
    checks: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]


SAFE_STATE_COMMANDS: dict[str, tuple[str, ...]] = {
    # Power supply: turn outputs off, but do not alter configured setpoints.
    "DP832": (":OUTPut CH1,OFF", ":OUTPut CH2,OFF", ":OUTPut CH3,OFF"),
    # DC electronic load: input off is the critical safe state.
    "DL3021": (":INPut OFF",),
}

SAFE_STATE_CHECKS: dict[str, tuple[str, ...]] = {
    "DP832": (
        ":OUTPut? CH1",
        ":OUTPut? CH2",
        ":OUTPut? CH3",
        ":MEASure:VOLTage? CH1",
        ":MEASure:VOLTage? CH2",
        ":MEASure:VOLTage? CH3",
        ":MEASure:CURRent? CH1",
        ":MEASure:CURRent? CH2",
        ":MEASure:CURRent? CH3",
    ),
    "DL3021": (":INPut?", ":MEASure:VOLTage?", ":MEASure:CURRent?", ":MEASure:POWer?"),
    # Scope has no hazardous source/load output in this bench setup; keep checks read-only.
    "DS1102E": (":CHANnel1:DISPlay?", ":CHANnel2:DISPlay?", ":TRIGger:STATus?"),
}


def _model_from_idn(idn: str) -> str:
    normalized = idn.upper()
    for model in (*SAFE_STATE_COMMANDS.keys(), *SAFE_STATE_CHECKS.keys()):
        if model.upper() in normalized:
            return model
    return "UNKNOWN"


def apply_safe_state(resource_names: Iterable[str] | None = None) -> list[SafeStateResult]:
    """Apply safe-state commands to all known PyVISA instruments.

    Unknown instruments are identified but not written to.
    """

    rm = pyvisa.ResourceManager("@py")
    resources = tuple(resource_names) if resource_names is not None else rm.list_resources()
    results: list[SafeStateResult] = []

    for resource in resources:
        actions: list[str] = []
        checks: list[tuple[str, str]] = []
        errors: list[str] = []
        idn = "UNKNOWN"
        model = "UNKNOWN"
        instrument = None

        try:
            instrument = cast(Any, rm.open_resource(resource))
            instrument.timeout = 3000
            idn = instrument.query("*IDN?").strip().replace("\x00", "")
            model = _model_from_idn(idn)

            for command in SAFE_STATE_COMMANDS.get(model, ()):  # writes only for known safe devices
                try:
                    instrument.write(command)
                    actions.append(command)
                    time.sleep(0.1)
                except Exception as exc:  # noqa: BLE001 - command failure is diagnostic output
                    errors.append(f"{command}: {exc}")

            for query in SAFE_STATE_CHECKS.get(model, ()):  # read-only verification
                try:
                    checks.append((query, instrument.query(query).strip()))
                except Exception as exc:  # noqa: BLE001 - query failure is diagnostic output
                    errors.append(f"{query}: {exc}")

        except Exception as exc:  # noqa: BLE001 - keep scanning other instruments
            errors.append(str(exc))
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass

        results.append(
            SafeStateResult(
                resource=resource,
                idn=idn,
                model=model,
                actions=tuple(actions),
                checks=tuple(checks),
                errors=tuple(errors),
            )
        )

    return results


def apply_usb_safe_state() -> list[SafeStateResult]:
    """Apply safe-state for known non-VISA USB instruments."""

    results: list[SafeStateResult] = []
    for identity in discover_usb():
        if (identity.vendor_id, identity.product_id) != ("0cd5", "0003"):
            continue
        actions: list[str] = []
        checks: list[tuple[str, str]] = []
        errors: list[str] = []
        driver = None
        try:
            driver = LabJackU3Driver()
            driver.safe_state()
            actions.extend(["DAC0=0.0 V", "DAC1=0.0 V"])
            # Read a few analog inputs as a non-invasive smoke check.
            for channel in (0, 1):
                checks.append((f"AIN{channel}", f"{driver.read_ain(channel):.6f}"))
        except LabJackDependencyError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep safe-state report complete
            errors.append(f"LabJack U3 safe-state failed: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass
        results.append(
            SafeStateResult(
                resource=identity.resource,
                idn=identity.idn,
                model="U3",
                actions=tuple(actions),
                checks=tuple(checks),
                errors=tuple(errors),
            )
        )
    return results


def main() -> None:
    print("--- Long Game SDK Safe-State ---")
    for result in [*apply_safe_state(), *apply_usb_safe_state()]:
        print(f"\n{result.resource}")
        print(f"  IDN: {result.idn}")
        print(f"  Model: {result.model}")
        if result.actions:
            print("  Safe actions:")
            for action in result.actions:
                print(f"    wrote {action}")
        else:
            print("  Safe actions: none required/known")
        if result.checks:
            print("  Verification:")
            for query, response in result.checks:
                print(f"    {query} -> {response}")
        if result.errors:
            print("  Errors:")
            for error in result.errors:
                print(f"    {error}")


if __name__ == "__main__":
    main()
