"""Instrument presence and identity checks."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Protocol, cast

import pyvisa

from long_game_sdk.sdk.identity import identity_field_equal
from long_game_sdk.sdk.identity import normalize_identity_value as normalized_identity_value
from long_game_sdk.sdk.preflight.results import result

if TYPE_CHECKING:
    from long_game_sdk.sdk.preflight.checks import CheckResult


class InstrumentAdapter(Protocol):
    """Minimal query/write interface used by preflight checks."""

    def query(self, command: str) -> str: ...
    def write(self, command: str) -> Any: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ParsedLiveIdentity:
    """Strict identity observed from exactly one live ``*IDN?`` query."""

    raw: str
    manufacturer: str
    model: str
    serial: str

    @classmethod
    def parse(cls, raw: str) -> ParsedLiveIdentity:
        if any(ord(character) < 32 or ord(character) == 127 for character in raw):
            raise ValueError("malformed *IDN? response; control characters are not allowed")
        parts = tuple(part.strip() for part in raw.split(","))
        if len(parts) < 3 or any(not part for part in parts[:3]):
            raise ValueError("malformed *IDN? response; expected manufacturer, model, and serial")
        return cls(raw=raw, manufacturer=parts[0], model=parts[1], serial=parts[2])


def identities_equal(left: ParsedLiveIdentity, right: ParsedLiveIdentity) -> bool:
    """Compare vendor/model canonically and serial exactly."""
    return all(
        identity_field_equal(field, getattr(left, field), getattr(right, field))
        for field in ("manufacturer", "model", "serial")
    )


@dataclass(frozen=True)
class InstrumentCheckOutcome:
    """Instrument results with a snapshot-isolated, read-only live identity mapping."""

    results: tuple[CheckResult, ...]
    live_identities: Mapping[str, ParsedLiveIdentity]

    def __post_init__(self) -> None:
        object.__setattr__(self, "live_identities", MappingProxyType(dict(self.live_identities)))


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
        return str(self._instrument.query(command))

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


def _get_adapter(
    spec: Mapping[str, Any], instruments: Mapping[str, InstrumentAdapter] | None
) -> InstrumentAdapter | None:
    name = str(spec.get("name", ""))
    if instruments is not None:
        return instruments.get(name)
    connection = spec.get("connection")
    if connection:
        return VisaInstrumentAdapter(str(connection))
    return None


def run_instrument_checks(
    config: Mapping[str, Any], *, instruments: Mapping[str, InstrumentAdapter] | None = None
) -> InstrumentCheckOutcome:
    checks: list[CheckResult] = []
    live_identities: dict[str, ParsedLiveIdentity] = {}
    for spec in _instrument_configs(config):
        name = str(spec.get("name", "unnamed_instrument"))
        expected_manufacturer = normalized_identity_value(
            str(spec.get("expected_manufacturer", ""))
        )
        expected_model = normalized_identity_value(str(spec.get("expected_model", "")))
        expected_serial = normalized_identity_value(str(spec.get("expected_serial", "")))
        expected_idn = normalized_identity_value(
            str(spec.get("expected_identity") or spec.get("expected_idn") or "")
        )
        adapter = None
        try:
            adapter = _get_adapter(spec, instruments)
            if adapter is None:
                checks.append(result(
                    "instrument_reachable", "instrument", "fail",
                    f"{name}: no connection configured.",
                ))
                continue
            idn = adapter.query("*IDN?")
            checks.append(result(
                "instrument_reachable", "instrument", "pass", f"{name}: reachable.",
                evidence={"idn": idn},
            ))
            try:
                live_identity = ParsedLiveIdentity.parse(idn)
            except ValueError as exc:
                checks.append(result(
                    "identity", "instrument", "fail", f"{name}: {exc}.",
                    severity="high", evidence={"idn": idn},
                ))
                continue
            live_identities[name] = live_identity
            if expected_manufacturer or expected_model or expected_serial or expected_idn:
                manufacturer_ok = (
                    not expected_manufacturer
                    or identity_field_equal(
                        "manufacturer", live_identity.manufacturer, expected_manufacturer
                    )
                )
                model_ok = (
                    not expected_model
                    or identity_field_equal("model", live_identity.model, expected_model)
                )
                serial_ok = (
                    not expected_serial
                    or identity_field_equal("serial", live_identity.serial, expected_serial)
                )
                idn_ok = True
                if expected_idn:
                    try:
                        idn_ok = identities_equal(
                            live_identity, ParsedLiveIdentity.parse(expected_idn)
                        )
                    except ValueError:
                        idn_ok = False
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
                checks.append(result(
                    "identity", "instrument", status,
                    f"{name}: expected {', '.join(expected_parts)}; received {idn}.",
                    evidence={
                        "expected_manufacturer": expected_manufacturer,
                        "expected_model": expected_model,
                        "expected_serial": expected_serial,
                        "expected_identity": expected_idn,
                        "idn": idn,
                    },
                ))
            else:
                checks.append(result(
                    "identity", "instrument", "warn",
                    f"{name}: expected identity/model/serial not configured.",
                ))
        except Exception as exc:  # noqa: BLE001 - report all failures instead of crashing
            checks.append(result(
                "instrument_reachable", "instrument", "fail", f"{name}: {exc}",
                severity="high",
            ))
        finally:
            if adapter is not None and hasattr(adapter, "close") and (
                not instruments or name not in instruments
            ):
                try:
                    adapter.close()
                except Exception:
                    pass
    return InstrumentCheckOutcome(tuple(checks), live_identities)
