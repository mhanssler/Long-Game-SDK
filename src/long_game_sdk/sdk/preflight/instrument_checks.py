"""Instrument presence and identity checks."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, cast

import pyvisa

from long_game_sdk.sdk.preflight.results import result


class InstrumentAdapter(Protocol):
    """Minimal query/write interface used by preflight checks."""

    def query(self, command: str) -> str: ...
    def write(self, command: str) -> Any: ...
    def close(self) -> None: ...


class VisaInstrumentAdapter:
    """Thin PyVISA adapter."""

    def __init__(self, resource: str, timeout_ms: int = 3000):
        self.resource = resource
        self._rm = pyvisa.ResourceManager("@py")
        try:
            self._instrument = cast(Any, self._rm.open_resource(resource))
            self._instrument.timeout = timeout_ms
        except Exception:
            self._rm.close()
            raise

    def query(self, command: str) -> str:
        return str(self._instrument.query(command)).strip().replace("\x00", "")

    def write(self, command: str) -> Any:
        return self._instrument.write(command)

    def close(self) -> None:
        try:
            self._instrument.close()
        finally:
            self._rm.close()


def _instrument_configs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    rig = config.get("rig") or {}
    return [dict(item) for item in rig.get("instruments") or []]


def _get_adapter(spec: Mapping[str, Any], instruments: Mapping[str, InstrumentAdapter] | None) -> InstrumentAdapter | None:
    name = str(spec.get("name", ""))
    if instruments is not None:
        return instruments.get(name)
    connection = spec.get("connection")
    if connection:
        return VisaInstrumentAdapter(str(connection))
    return None


def _parse_idn(idn: str) -> tuple[str, str, str] | None:
    parts = tuple(part.strip() for part in idn.split(","))
    if len(parts) < 3 or any(not part for part in parts[:3]):
        return None
    return parts[0], parts[1], parts[2]


def run_instrument_checks(config: Mapping[str, Any], *, instruments: Mapping[str, InstrumentAdapter] | None = None):
    checks = []
    for spec in _instrument_configs(config):
        name = str(spec.get("name", "unnamed_instrument"))
        expected_manufacturer = str(spec.get("expected_manufacturer", "")).strip()
        expected_model = str(spec.get("expected_model", "")).strip()
        expected_serial = str(spec.get("expected_serial", "")).strip()
        expected_idn = str(spec.get("expected_identity") or spec.get("expected_idn") or "").strip()
        adapter = None
        try:
            adapter = _get_adapter(spec, instruments)
            if adapter is None:
                checks.append(result("instrument_reachable", "instrument", "fail", f"{name}: no connection configured."))
                continue
            idn = adapter.query("*IDN?").strip()
            checks.append(result("instrument_reachable", "instrument", "pass", f"{name}: reachable.", evidence={"idn": idn}))
            if expected_manufacturer or expected_model or expected_serial or expected_idn:
                parsed = _parse_idn(idn)
                if parsed is None:
                    received_manufacturer = received_model = received_serial = "UNKNOWN"
                else:
                    received_manufacturer, received_model, received_serial = parsed
                manufacturer_ok = bool(
                    parsed is not None
                    and (
                        not expected_manufacturer
                        or received_manufacturer.casefold() == expected_manufacturer.casefold()
                    )
                )
                model_ok = bool(
                    parsed is not None
                    and (
                        not expected_model
                        or received_model.casefold() == expected_model.casefold()
                    )
                )
                serial_ok = bool(
                    parsed is not None
                    and (
                        not expected_serial
                        or received_serial.casefold() == expected_serial.casefold()
                    )
                )
                idn_ok = not expected_idn or idn.casefold() == expected_idn.casefold()
                status = "pass" if manufacturer_ok and model_ok and serial_ok and idn_ok else "fail"
                expected_parts = []
                if expected_manufacturer:
                    expected_parts.append(expected_manufacturer)
                if expected_model:
                    expected_parts.append(expected_model)
                if expected_serial:
                    expected_parts.append(f"serial {expected_serial}")
                if expected_idn:
                    expected_parts.append(f"identity {expected_idn}")
                expected_description = ", ".join(expected_parts)
                checks.append(
                    result(
                        "identity",
                        "instrument",
                        status,
                        f"{name}: expected {expected_description}; received {idn}.",
                        evidence={"expected_manufacturer": expected_manufacturer,
                                  "expected_model": expected_model, "expected_serial": expected_serial,
                                  "expected_identity": expected_idn, "idn": idn},
                    )
                )
            else:
                checks.append(result("identity", "instrument", "warn", f"{name}: expected identity/model/serial not configured."))
        except Exception as exc:  # noqa: BLE001 - preflight should report all failures, not crash early
            checks.append(result("instrument_reachable", "instrument", "fail", f"{name}: {exc}"))
        finally:
            if adapter is not None and hasattr(adapter, "close") and (not instruments or name not in instruments):
                try:
                    adapter.close()
                except Exception:
                    pass
    return checks
