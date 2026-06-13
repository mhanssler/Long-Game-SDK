"""Generate client-facing HV/PCBA test safety plans from rig YAML.

The safety-plan generator intentionally shares the same high-level config shape as
``lg-preflight`` so a bench definition can produce both a readiness diagnostic and
an operator-facing safety plan.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


@dataclass(frozen=True)
class SafetyPlan:
    """Structured representation of an HV/PCBA test safety plan."""

    rig_name: str
    dut_type: str
    dut_summary: str
    generated_at: str
    operator: str | None
    reviewer: str | None
    test_location: str | None
    max_voltage_v: float | int | None
    max_current_a: float | int | None
    instruments: tuple[str, ...]
    energy_sources: tuple[str, ...]
    hazards: tuple[str, ...]
    ppe: tuple[str, ...]
    estop_location: str | None
    estop_verification: str | None
    disconnects: tuple[str, ...]
    discharge_method: str | None
    discharge_verification: str | None
    interlocks: tuple[str, ...]
    safe_state: tuple[str, ...]
    pre_job_briefing: tuple[str, ...]
    stop_work_criteria: tuple[str, ...]
    missing_controls: tuple[str, ...]

    @property
    def required_sections_present(self) -> bool:
        """Return True when critical HV controls are populated."""

        return not self.missing_controls


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _format_instrument(item: Mapping[str, Any]) -> str:
    name = item.get("name", "unnamed")
    model = item.get("expected_model") or item.get("model") or "model not specified"
    connection = item.get("connection")
    if connection:
        return f"{name}: {model} ({connection})"
    return f"{name}: {model}"


def _format_dut(rig: Mapping[str, Any], runtime: Mapping[str, Any]) -> str:
    dut = rig.get("dut") or {}
    if isinstance(dut, Mapping):
        name = dut.get("name") or rig.get("dut_type") or "DUT"
        serial = dut.get("serial") or runtime.get("dut_serial")
        if serial:
            return f"{name} ({serial})"
        return str(name)
    if dut:
        return str(dut)
    serial = runtime.get("dut_serial")
    dut_type = rig.get("dut_type") or "DUT"
    return f"{dut_type} ({serial})" if serial else str(dut_type)


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML rig/safety-plan config."""

    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"HV safety plan config must be a mapping: {config_path}")
    return data


def generate_safety_plan(config: Mapping[str, Any]) -> SafetyPlan:
    """Create a structured safety plan from a lab rig configuration."""

    rig = dict(config.get("rig") or {})
    runtime = dict(config.get("runtime") or {})
    safety = dict(config.get("safety_plan") or config.get("hv_safety_plan") or {})
    estop = dict(safety.get("estop") or {})
    discharge = dict(safety.get("discharge") or {})
    instruments = rig.get("instruments") or []

    formatted_instruments = tuple(
        _format_instrument(item) for item in instruments if isinstance(item, Mapping)
    )

    missing: list[str] = []
    if not _as_tuple(safety.get("hazards")):
        missing.append("HV hazard inventory")
    if not _as_tuple(safety.get("ppe")):
        missing.append("Required PPE")
    if not (estop.get("location") and estop.get("verification")):
        missing.append("E-stop verification")
    if not _as_tuple(safety.get("disconnects")):
        missing.append("Disconnect list")
    if not (discharge.get("method") and discharge.get("verification")):
        missing.append("Discharge / bleeder-resistor check")
    if not _as_tuple(safety.get("interlocks")):
        missing.append("Interlock checklist")
    if not _as_tuple(safety.get("safe_state")):
        missing.append("Safe-state requirements")
    if not _as_tuple(safety.get("stop_work_criteria")):
        missing.append("Stop-work criteria")

    return SafetyPlan(
        rig_name=str(rig.get("name", "unknown-rig")),
        dut_type=str(rig.get("dut_type", "unknown-dut")),
        dut_summary=_format_dut(rig, runtime),
        generated_at=datetime.now(UTC).isoformat(),
        operator=safety.get("operator") or runtime.get("operator"),
        reviewer=safety.get("reviewer"),
        test_location=safety.get("test_location") or runtime.get("test_location"),
        max_voltage_v=safety.get("max_voltage_v"),
        max_current_a=safety.get("max_current_a"),
        instruments=formatted_instruments,
        energy_sources=_as_tuple(safety.get("energy_sources")),
        hazards=_as_tuple(safety.get("hazards")),
        ppe=_as_tuple(safety.get("ppe")),
        estop_location=estop.get("location"),
        estop_verification=estop.get("verification"),
        disconnects=_as_tuple(safety.get("disconnects")),
        discharge_method=discharge.get("method"),
        discharge_verification=discharge.get("verification"),
        interlocks=_as_tuple(safety.get("interlocks")),
        safe_state=_as_tuple(safety.get("safe_state")),
        pre_job_briefing=_as_tuple(safety.get("pre_job_briefing")),
        stop_work_criteria=_as_tuple(safety.get("stop_work_criteria")),
        missing_controls=tuple(missing),
    )


def _bullet_lines(items: Sequence[str], *, missing_label: str) -> list[str]:
    if not items:
        return [f"- MISSING: {missing_label}"]
    return [f"- {item}" for item in items]


def render_markdown(plan: SafetyPlan) -> str:
    """Render a client-facing Markdown HV/PCBA safety plan."""

    status = "READY FOR REVIEW" if plan.required_sections_present else "INCOMPLETE - REVIEW REQUIRED"
    lines = [
        "# HV/PCBA Test Safety Plan",
        "",
        f"- Rig: {plan.rig_name}",
        f"- DUT type: {plan.dut_type}",
        f"- DUT: {plan.dut_summary}",
        f"- Location: {plan.test_location or 'Not specified'}",
        f"- Generated: {plan.generated_at}",
        f"- Operator: {plan.operator or 'Not assigned'}",
        f"- Reviewer: {plan.reviewer or 'Not assigned'}",
        f"- Plan status: {status}",
        "",
        "## DUT and Test Setup Summary",
        "",
        f"- Maximum voltage: {plan.max_voltage_v if plan.max_voltage_v is not None else 'Not specified'} V",
        f"- Maximum current: {plan.max_current_a if plan.max_current_a is not None else 'Not specified'} A",
        "- Instruments:",
    ]
    lines.extend(f"  - {item}" for item in plan.instruments) if plan.instruments else lines.append("  - MISSING: Instrument inventory")
    lines.extend(["- Energy sources:"])
    lines.extend(f"  - {item}" for item in plan.energy_sources) if plan.energy_sources else lines.append("  - MISSING: Energy source inventory")

    sections = [
        ("HV Hazard Inventory", _bullet_lines(plan.hazards, missing_label="HV hazard inventory")),
        ("Required PPE", _bullet_lines(plan.ppe, missing_label="Required PPE")),
        (
            "E-stop and Disconnect Verification",
            [
                f"- E-stop location: {plan.estop_location}" if plan.estop_location else "- MISSING: E-stop location",
                f"- E-stop verification: {plan.estop_verification}" if plan.estop_verification else "- MISSING: E-stop verification",
                "- Disconnects:",
                *([f"  - {item}" for item in plan.disconnects] if plan.disconnects else ["  - MISSING: Disconnect list"]),
            ],
        ),
        (
            "Discharge / Bleeder-Resistor Checks",
            [
                f"- Method: {plan.discharge_method}" if plan.discharge_method else "- MISSING: Discharge method",
                f"- Verification: {plan.discharge_verification}" if plan.discharge_verification else "- MISSING: Discharge / bleeder-resistor check",
            ],
        ),
        ("Interlock Checklist", _bullet_lines(plan.interlocks, missing_label="Interlock checklist")),
        ("Safe-State Requirements", _bullet_lines(plan.safe_state, missing_label="Safe-state requirements")),
        ("Operator Pre-Job Briefing", _bullet_lines(plan.pre_job_briefing, missing_label="Operator pre-job briefing")),
        ("Stop-Work Criteria", _bullet_lines(plan.stop_work_criteria, missing_label="Stop-work criteria")),
    ]
    for title, bullets in sections:
        lines.extend(["", f"## {title}", "", *bullets])

    lines.extend(
        [
            "",
            "## Sign-Off",
            "",
            "- Operator: ____________________  Date: __________",
            "- Reviewer: ____________________  Date: __________",
            "- Notes / deviations: ________________________________________________",
            "",
        ]
    )
    if plan.missing_controls:
        lines.extend(["## Open Safety Controls", ""])
        lines.extend(f"- MISSING: {item}" for item in plan.missing_controls)
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an HV/PCBA test safety plan from a rig YAML config.")
    parser.add_argument("config", help="Path to lab rig / safety-plan YAML config")
    parser.add_argument("--output", "-o", help="Optional Markdown safety-plan output path")
    args = parser.parse_args()

    plan = generate_safety_plan(load_config(args.config))
    markdown = render_markdown(plan)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        print(f"Wrote HV/PCBA safety plan: {output}")
    else:
        print(markdown)

    if not plan.required_sections_present:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
