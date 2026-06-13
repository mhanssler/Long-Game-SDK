"""Instrument presence and identity checks."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

import pyvisa

from long_game_sdk.sdk.preflight.results import result


class InstrumentAdapter(Protocol):
    """Minimal query/write interface used by preflight checks."""

    def query(self, command: str) -> str: ...
    def write(self, command: str) -> Any: ...


class VisaInstrumentAdapter:
    """Thin PyVISA adapter."""

    def __init__(self, resource: str, timeout_ms: int = 3000):
        self.resource = resource
        self._rm = pyvisa.ResourceManager("@py")
        self._instrument = self._rm.open_resource(resource)
        self._instrument.timeout = timeout_ms

    def query(self, command: str) -> str:
        return str(self._instrument.query(command)).strip().replace("\x00", "")

    def write(self, command: str) -> Any:
        return self._instrument.write(command)

    def close(self) -> None:
        self._instrument.close()


def _instrument_configs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    rig = config.get("rig") or {}
    return [dict(item) for item in rig.get("instruments") or []]


def _get_adapter(spec: Mapping[str, Any], instruments: Mapping[str, InstrumentAdapter] | None) -> InstrumentAdapter | None:
    name = str(spec.get("name", ""))
    if instruments and name in instruments:
        return instruments[name]
    connection = spec.get("connection")
    if connection:
        return VisaInstrumentAdapter(str(connection))
    return None


def _model_matches(expected_model: str, idn: str) -> bool:
    expected_tokens = [token for token in expected_model.lower().replace(",", " ").split() if token]
    normalized_idn = idn.lower()
    return all(token in normalized_idn for token in expected_tokens)


def run_instrument_checks(config: Mapping[str, Any], *, instruments: Mapping[str, InstrumentAdapter] | None = None):
    checks = []
    for spec in _instrument_configs(config):
        name = str(spec.get("name", "unnamed_instrument"))
        expected_model = str(spec.get("expected_model", "")).strip()
        adapter = None
        try:
            adapter = _get_adapter(spec, instruments)
            if adapter is None:
                checks.append(result("instrument_reachable", "instrument", "fail", f"{name}: no connection configured."))
                continue
            idn = adapter.query("*IDN?").strip()
            checks.append(result("instrument_reachable", "instrument", "pass", f"{name}: reachable.", evidence={"idn": idn}))
            if expected_model:
                status = "pass" if _model_matches(expected_model, idn) else "fail"
                checks.append(
                    result(
                        "identity",
                        "instrument",
                        status,
                        f"{name}: expected {expected_model}; received {idn}.",
                        evidence={"expected_model": expected_model, "idn": idn},
                    )
                )
            else:
                checks.append(result("identity", "instrument", "warn", f"{name}: expected_model not configured."))
        except Exception as exc:  # noqa: BLE001 - preflight should report all failures, not crash early
            checks.append(result("instrument_reachable", "instrument", "fail", f"{name}: {exc}"))
        finally:
            if adapter is not None and hasattr(adapter, "close") and (not instruments or name not in instruments):
                try:
                    adapter.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
    return checks
