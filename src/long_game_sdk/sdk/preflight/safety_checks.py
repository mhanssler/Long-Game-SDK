"""Safety-oriented preflight checks.

These checks are read-only by default. Safe-state writes belong in `lg-safe`; preflight
verifies the lab is ready before a run starts and flags risky configuration gaps.
"""

from __future__ import annotations

from typing import Any, Mapping

from long_game_sdk.sdk.preflight.instrument_checks import InstrumentAdapter
from long_game_sdk.sdk.preflight.results import result


def _instrument_configs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    rig = config.get("rig") or {}
    return [dict(item) for item in rig.get("instruments") or []]


def _adapter_for(name: str, instruments: Mapping[str, InstrumentAdapter] | None) -> InstrumentAdapter | None:
    if not instruments:
        return None
    return instruments.get(name)


def run_safety_checks(config: Mapping[str, Any], *, instruments: Mapping[str, InstrumentAdapter] | None = None):
    checks = []
    for spec in _instrument_configs(config):
        name = str(spec.get("name", "unnamed_instrument"))
        configured_checks = set(spec.get("checks") or [])
        safety = dict(spec.get("safety") or {})
        adapter = _adapter_for(name, instruments)

        if "calibration_date" in configured_checks or safety.get("calibration_due"):
            if safety.get("calibration_due"):
                checks.append(result("calibration_date", "safety", "pass", f"{name}: calibration due {safety['calibration_due']}."))
            else:
                checks.append(result("calibration_date", "safety", "warn", f"{name}: calibration date not documented."))

        if "output_disabled_on_start" in configured_checks:
            output_query = safety.get("output_query") or spec.get("output_query")
            if adapter is None or not output_query:
                checks.append(result("output_disabled_on_start", "safety", "warn", f"{name}: output-state query not configured/injected."))
            else:
                response = adapter.query(str(output_query)).strip().upper()
                off_tokens = {"0", "OFF", "FALSE"}
                status = "pass" if response in off_tokens else "fail"
                checks.append(
                    result(
                        "output_disabled_on_start",
                        "safety",
                        status,
                        f"{name}: {output_query} returned {response}.",
                        severity="high",
                        evidence={"query": output_query, "response": response},
                    )
                )

        for limit_key in ("voltage_limit", "current_limit"):
            if limit_key in configured_checks:
                if limit_key in safety:
                    checks.append(result(limit_key, "safety", "pass", f"{name}: {limit_key} configured as {safety[limit_key]}."))
                else:
                    checks.append(result(limit_key, "safety", "fail", f"{name}: {limit_key} missing from safety config.", severity="high"))
    return checks
