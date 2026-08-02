"""Fail-closed safe-state helpers for lab instruments."""
from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, cast

import pyvisa
import yaml

from long_game_sdk.sdk.discovery import discover_usb
from long_game_sdk.sdk.drivers.labjack_u3 import LabJackDependencyError, LabJackU3Driver

SafeState = Literal["verified_safe", "unsafe", "unverifiable", "no_action_required"]


class SafeStateConfigError(ValueError):
    """Raised before resource access when configured safe-state bindings are unsafe."""


@dataclass(frozen=True)
class SafeStateResult:
    resource: str
    idn: str
    model: str
    actions: tuple[str, ...]
    checks: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]
    state: SafeState = "unverifiable"

    @property
    def safe(self) -> bool:
        return self.state in {"verified_safe", "no_action_required"}


SAFE_STATE_COMMANDS = {
    "DP832": (":OUTPut CH1,OFF", ":OUTPut CH2,OFF", ":OUTPut CH3,OFF"),
    "DL3021": (":INPut OFF",),
}
SAFE_STATE_CHECKS = {
    "DP832": (":OUTPut? CH1", ":OUTPut? CH2", ":OUTPut? CH3", ":MEASure:VOLTage? CH1",
              ":MEASure:VOLTage? CH2", ":MEASure:VOLTage? CH3", ":MEASure:CURRent? CH1",
              ":MEASure:CURRent? CH2", ":MEASure:CURRent? CH3"),
    "DL3021": (":INPut?", ":MEASure:VOLTage?", ":MEASure:CURRent?", ":MEASure:POWer?"),
    "DS1102E": (":CHANnel1:DISPlay?", ":CHANnel2:DISPlay?", ":TRIGger:STATus?"),
}
_OUTPUT_QUERIES = {"DP832": SAFE_STATE_CHECKS["DP832"][:3], "DL3021": (":INPut?",)}
_MEASUREMENT_QUERIES: dict[str, dict[str, tuple[str, ...]]] = {
    "DP832": {
        "voltage": SAFE_STATE_CHECKS["DP832"][3:6],
        "current": SAFE_STATE_CHECKS["DP832"][6:9],
    },
    "DL3021": {
        "voltage": (":MEASure:VOLTage?",),
        "current": (":MEASure:CURRent?",),
    },
}
_DEFAULT_SAFE_VOLTAGE = 0.1
_DEFAULT_SAFE_CURRENT = 0.01


def _parse_idn(idn: str) -> tuple[str, str, str] | None:
    parts = tuple(part.strip() for part in idn.split(","))
    if len(parts) < 3 or any(not part for part in parts[:3]):
        return None
    return parts[0], parts[1], parts[2]


def _model_from_idn(idn: str) -> str:
    parsed = _parse_idn(idn)
    if parsed is None:
        return "UNKNOWN"
    received_model = parsed[1].casefold()
    for model in (*SAFE_STATE_COMMANDS, *SAFE_STATE_CHECKS):
        if model.casefold() == received_model:
            return model
    return "UNKNOWN"


def _instrument_specs(config: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not config:
        return []
    rig = config.get("rig") or {}
    configured = rig.get("instruments") if isinstance(rig, Mapping) else None
    return [spec for spec in (configured or []) if isinstance(spec, Mapping)]


def _expected_identity_fields(expected: Mapping[str, Any]) -> tuple[str, str, str] | None:
    expected_idn = str(
        expected.get("expected_identity") or expected.get("expected_idn") or ""
    ).strip()
    parsed_expected = _parse_idn(expected_idn) if expected_idn else None
    explicit = (
        str(expected.get("expected_manufacturer") or expected.get("manufacturer") or "").strip(),
        str(expected.get("expected_model") or expected.get("model") or "").strip(),
        str(expected.get("expected_serial") or expected.get("serial") or "").strip(),
    )
    if any(explicit):
        if not all(explicit):
            return None
        if parsed_expected is not None and tuple(value.casefold() for value in parsed_expected) != tuple(
            value.casefold() for value in explicit
        ):
            return None
        return explicit
    return parsed_expected


def _validate_bindings(
    expected_devices: Mapping[str, Mapping[str, Any]] | None,
    config: Mapping[str, Any] | None,
) -> None:
    """Validate configured inventory and source bindings before opening any resource."""
    if config is not None:
        if not isinstance(config, Mapping):
            raise SafeStateConfigError("safe-state config must be a mapping")
        if "instruments" in config:
            raise SafeStateConfigError(
                "top-level instruments are not supported; use rig.instruments"
            )
        rig = config.get("rig")
        if not isinstance(rig, Mapping):
            raise SafeStateConfigError("safe-state config requires a rig mapping")
        inventory = rig.get("instruments")
        if not isinstance(inventory, list) or not inventory:
            raise SafeStateConfigError("rig.instruments must be a non-empty list")
        names: set[str] = set()
        resources: set[str] = set()
        for index, spec in enumerate(inventory, start=1):
            if not isinstance(spec, Mapping):
                raise SafeStateConfigError(f"rig.instruments[{index}] must be a mapping")
            name_value = spec.get("name")
            if not isinstance(name_value, str) or not name_value.strip():
                raise SafeStateConfigError(f"rig.instruments[{index}].name must be a non-empty string")
            name = name_value.strip()
            name_key = name.casefold()
            if name_key in names:
                raise SafeStateConfigError(f"duplicate instrument name: {name!r}")
            names.add(name_key)
            connection_value = spec.get("connection")
            if not isinstance(connection_value, str) or not connection_value.strip():
                raise SafeStateConfigError(
                    f"instrument {name!r} requires a non-empty connection/resource binding"
                )
            connection = connection_value.strip()
            connection_key = connection.casefold()
            if connection_key in resources:
                raise SafeStateConfigError(f"duplicate instrument connection: {connection!r}")
            resources.add(connection_key)
            safety = spec.get("safety", {})
            if not isinstance(safety, Mapping):
                raise SafeStateConfigError(f"instrument {name!r} safety must be a mapping")
            merged = dict(spec)
            merged.update(safety)
            if _expected_identity_fields(merged) is None:
                raise SafeStateConfigError(
                    f"instrument {name!r} requires exact expected manufacturer, model, and serial"
                )

    supported_models = {model.casefold() for model in SAFE_STATE_COMMANDS}
    for resource, expected in (expected_devices or {}).items():
        if not isinstance(resource, str) or not resource.strip() or not isinstance(expected, Mapping):
            raise SafeStateConfigError("expected_devices requires non-empty resource-to-mapping bindings")
        expected_model = str(expected.get("expected_model") or expected.get("model") or "").strip()
        is_source = bool(expected.get("energy_source") or expected.get("is_energy_source"))
        if (is_source or expected_model.casefold() in supported_models) and (
            _expected_identity_fields(expected) is None
        ):
            raise SafeStateConfigError(
                f"supported source {resource!r} requires exact expected manufacturer, model, and serial"
            )


def _bindings(expected_devices: Mapping[str, Mapping[str, Any]] | None,
              config: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    answer: dict[str, dict[str, Any]] = {}
    for spec in _instrument_specs(config):
        connection = spec.get("connection")
        if isinstance(connection, str) and connection.strip():
            merged = dict(spec)
            merged.update(spec.get("safety") or {})
            answer[connection.strip()] = merged
    for resource, expected in (expected_devices or {}).items():
        answer.setdefault(resource.strip(), {}).update(expected)
    return answer


def _identity_error(idn: str, expected: Mapping[str, Any]) -> str | None:
    parsed = _parse_idn(idn)
    if parsed is None:
        return f"malformed instrument identity: {idn}"
    manufacturer, model, serial = parsed
    wanted_idn = str(expected.get("expected_identity") or expected.get("expected_idn") or "").strip()
    wanted_manufacturer = str(
        expected.get("expected_manufacturer") or expected.get("manufacturer") or ""
    ).strip()
    wanted_model = str(expected.get("expected_model") or expected.get("model") or "").strip()
    wanted_serial = str(expected.get("expected_serial") or expected.get("serial") or "").strip()
    if wanted_idn and wanted_idn.casefold() != idn.casefold():
        return f"identity mismatch: expected {wanted_idn}; received {idn}"
    if wanted_manufacturer and wanted_manufacturer.casefold() != manufacturer.casefold():
        return (
            f"identity mismatch: expected manufacturer {wanted_manufacturer}; "
            f"received {manufacturer}"
        )
    if wanted_model and wanted_model.casefold() != model.casefold():
        return f"identity mismatch: expected model {wanted_model}; received {model}"
    if wanted_serial and wanted_serial.casefold() != serial.casefold():
        return f"identity mismatch: expected serial {wanted_serial}; received {serial}"
    return None


def _output_is_off(response: str) -> bool:
    tokens = response.strip().upper().replace(",", " ").split()
    return bool(tokens) and tokens[-1] in {"0", "OFF", "FALSE"}


def _threshold(binding: Mapping[str, Any], kind: str, default: float) -> float | None:
    for key in (f"safe_{kind}_threshold", f"max_safe_{kind}", f"{kind}_threshold"):
        if key in binding:
            try:
                value = float(binding[key])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value >= 0 else None
    return default


def _measurement_state(
    model: str, checks: list[tuple[str, str]], binding: Mapping[str, Any]
) -> SafeState:
    responses = dict(checks)
    for kind, default in (("voltage", _DEFAULT_SAFE_VOLTAGE), ("current", _DEFAULT_SAFE_CURRENT)):
        threshold = _threshold(binding, kind, default)
        if threshold is None:
            return "unverifiable"
        queries = _MEASUREMENT_QUERIES[model][kind]
        if any(query not in responses for query in queries):
            return "unverifiable"
        try:
            measured = [abs(float(responses[query].strip())) for query in queries]
        except ValueError:
            return "unverifiable"
        if any(not math.isfinite(value) for value in measured):
            return "unverifiable"
        if any(value > threshold for value in measured):
            return "unsafe"
    return "verified_safe"


def _explicitly_non_energy(binding: Mapping[str, Any]) -> bool:
    if binding.get("validated_non_energy") is True:
        return True
    return any(
        key in binding and binding[key] is False
        for key in ("energy_source", "is_energy_source")
    )


def apply_safe_state(resource_names: Iterable[str] | None = None, *,
                     expected_devices: Mapping[str, Mapping[str, Any]] | None = None,
                     config: Mapping[str, Any] | None = None) -> list[SafeStateResult]:
    """De-energize exactly bound VISA sources and prove the resulting output state.

    A no-config invocation performs read-only discovery only. Every discovered device
    is unverifiable because discovery alone cannot authorize writes or prove that an
    unknown device is non-energizing.
    """
    _validate_bindings(expected_devices, config)
    bindings = _bindings(expected_devices, config)
    rm = pyvisa.ResourceManager("@py")
    try:
        return _apply_safe_state_with_manager(rm, resource_names, bindings)
    finally:
        # This outer finally deliberately covers list_resources and all processing.
        try:
            rm.close()
        except Exception:
            pass


def _apply_safe_state_with_manager(
    rm: Any,
    resource_names: Iterable[str] | None,
    bindings: Mapping[str, Mapping[str, Any]],
) -> list[SafeStateResult]:
    if resource_names is not None:
        resources = tuple(dict.fromkeys((*resource_names, *bindings)))
    else:
        resources = tuple(dict.fromkeys((*rm.list_resources(), *bindings)))
    results = []
    for resource in resources:
        actions: list[str] = []
        checks: list[tuple[str, str]] = []
        errors: list[str] = []
        idn, model, instrument = "UNKNOWN", "UNKNOWN", None
        try:
            instrument = cast(Any, rm.open_resource(resource))
            instrument.timeout = 3000
            idn = instrument.query("*IDN?").strip().replace("\x00", "")
            model = _model_from_idn(idn)
            binding = bindings.get(resource, {})
            mismatch = _identity_error(idn, binding) if resource in bindings else None
            if model in SAFE_STATE_COMMANDS and (
                resource not in bindings or _expected_identity_fields(binding) is None
            ):
                errors.append(
                    "supported source is not bound to an exact expected manufacturer/model/serial identity"
                )
            elif mismatch:
                errors.append(mismatch)
            else:
                for command in SAFE_STATE_COMMANDS.get(model, ()):
                    try:
                        instrument.write(command)
                        actions.append(command)
                        time.sleep(0.1)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{command}: {exc}")
                for query in SAFE_STATE_CHECKS.get(model, ()):
                    try:
                        checks.append((query, instrument.query(query).strip()))
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{query}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass

        binding = bindings.get(resource, {})
        if errors:
            state: SafeState = "unverifiable"
        elif model in SAFE_STATE_COMMANDS:
            readbacks = [value for query, value in checks if query in _OUTPUT_QUERIES[model]]
            outputs_off = (
                len(readbacks) == len(_OUTPUT_QUERIES[model])
                and all(map(_output_is_off, readbacks))
            )
            state = _measurement_state(model, checks, binding) if outputs_off else "unsafe"
        elif resource in bindings and _explicitly_non_energy(binding):
            state = "no_action_required"
        else:
            if resource in bindings:
                errors.append("expected instrument has no known safe-state procedure or non-energy validation")
            else:
                errors.append(
                    "no-config device was identified by read-only discovery only; safe state cannot be verified"
                )
            state = "unverifiable"
        results.append(SafeStateResult(resource, idn, model, tuple(actions), tuple(checks), tuple(errors), state))
    return results


def apply_usb_safe_state() -> list[SafeStateResult]:
    """Safely bind and de-energize each specifically discovered LabJack U3."""
    results = []
    for identity in discover_usb():
        if (identity.vendor_id, identity.product_id) != ("0cd5", "0003"):
            continue
        actions: list[str] = []
        checks: list[tuple[str, str]] = []
        errors: list[str] = []
        driver = None
        try:
            if not identity.serial or identity.serial.upper() == "UNKNOWN":
                raise RuntimeError("LabJack serial is unavailable; refusing to open an unbound device")
            driver = LabJackU3Driver(serial=identity.serial)
            driver.safe_state()
            actions.extend(("DAC0=0.0 V", "DAC1=0.0 V"))
            errors.append(
                "DAC writes acknowledged, but the LabJack adapter API provides no DAC readback"
            )
        except LabJackDependencyError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LabJack U3 safe-state failed: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass
        state: SafeState = "verified_safe" if not errors else "unverifiable"
        results.append(SafeStateResult(identity.resource, identity.idn, "U3", tuple(actions), tuple(checks), tuple(errors), state))
    return results


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Put supported lab equipment into a checked safe state")
    parser.add_argument("config", nargs="?", help="Optional bench/preflight YAML declaring expected equipment")
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    config = None
    if args.config:
        try:
            loaded = yaml.safe_load(Path(args.config).read_text()) or {}
            if not isinstance(loaded, Mapping):
                raise ValueError("config must be a mapping")
            config = loaded
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"safe-state config error: {exc}")
            return 2
    print("--- Long Game SDK Safe-State ---")
    try:
        visa_results = apply_safe_state(config=config) if config is not None else apply_safe_state()
    except SafeStateConfigError as exc:
        print(f"safe-state config error: {exc}")
        return 2
    results = [*visa_results, *apply_usb_safe_state()]
    for item in results:
        print(f"\n{item.resource}\n  IDN: {item.idn}\n  Model: {item.model}\n  State: {item.state}")
        print("  Safe actions:" if item.actions else "  Safe actions: none required/known")
        for action in item.actions:
            print(f"    wrote {action}")
        if item.checks:
            print("  Verification:")
        for query, response in item.checks:
            print(f"    {query} -> {response}")
        if item.errors:
            print("  Errors:")
        for error in item.errors:
            print(f"    {error}")
    return 2 if any(not item.safe for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
