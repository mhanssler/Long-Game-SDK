from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


class BenchBomValidationError(ValueError):
    """Raised when a bench architecture YAML file is incomplete or malformed."""


REQUIRED_TOP_LEVEL = ("project", "bench", "instruments", "connectors")

EQUIPMENT_FIELDS = [
    "category",
    "name",
    "purpose",
    "required_optional",
    "quantity",
    "manufacturer",
    "model",
    "selection_criteria",
    "control_interface",
    "calibration_required",
    "safety_notes",
    "estimated_cost_usd",
    "lead_time",
    "replacement_option",
]

CONNECTOR_FIELDS = [
    "connector_name",
    "location",
    "family",
    "mating_connector",
    "pin_count",
    "keying",
    "voltage_rating_v",
    "current_rating_a",
    "signal_type",
    "cable_type",
    "shielding_grounding",
    "strain_relief",
    "required_optional",
    "risk_notes",
]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _require_mapping(data: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise BenchBomValidationError(f"{label} must be a mapping")
    return data


def _require_list(data: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(data, list) or not data:
        raise BenchBomValidationError(f"{label} must be a non-empty list")
    items: list[Mapping[str, Any]] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, Mapping):
            raise BenchBomValidationError(f"{label}[{idx}] must be a mapping")
        items.append(item)
    return items


def load_architecture(path: str | Path) -> dict[str, Any]:
    """Load and validate a test setup architecture YAML file."""
    yaml_path = Path(path)
    try:
        data = yaml.safe_load(yaml_path.read_text())
    except FileNotFoundError as exc:
        raise BenchBomValidationError(f"architecture file not found: {yaml_path}") from exc
    except yaml.YAMLError as exc:
        raise BenchBomValidationError(f"invalid YAML in {yaml_path}: {exc}") from exc

    architecture = dict(_require_mapping(data, "architecture"))
    for section in REQUIRED_TOP_LEVEL:
        if section not in architecture:
            raise BenchBomValidationError(f"missing required section: {section}")

    _require_mapping(architecture["project"], "project")
    _require_mapping(architecture["bench"], "bench")
    instruments = _require_list(architecture["instruments"], "instruments")
    connectors = _require_list(architecture["connectors"], "connectors")

    for idx, instrument in enumerate(instruments, start=1):
        for field in ("name", "category", "role", "purpose"):
            if not instrument.get(field):
                raise BenchBomValidationError(f"instruments[{idx}] missing required field: {field}")

    for idx, connector in enumerate(connectors, start=1):
        if not connector.get("name"):
            raise BenchBomValidationError(f"connectors[{idx}] missing required field: name")
        if not connector.get("family"):
            raise BenchBomValidationError(f"connectors[{idx}] missing required field: family")

    return architecture


def generate_equipment_bom_csv(architecture: Mapping[str, Any]) -> str:
    """Generate a procurement-ready equipment BOM CSV from setup architecture."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EQUIPMENT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for instrument in _as_list(architecture.get("instruments")):
        item = _require_mapping(instrument, "instrument")
        row = {field: _csv_value(item.get(field)) for field in EQUIPMENT_FIELDS}
        writer.writerow(row)
    return output.getvalue()


def generate_connector_csv(architecture: Mapping[str, Any]) -> str:
    """Generate a connector/harness map CSV from setup architecture."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CONNECTOR_FIELDS, lineterminator="\n")
    writer.writeheader()
    for connector in _as_list(architecture.get("connectors")):
        item = _require_mapping(connector, "connector")
        row = {field: _csv_value(item.get(field)) for field in CONNECTOR_FIELDS}
        row["connector_name"] = _csv_value(item.get("name"))
        writer.writerow(row)
    return output.getvalue()


def generate_bench_config(architecture: Mapping[str, Any]) -> str:
    """Generate a bench config YAML that can feed preflight/test-plan workflows."""
    bench = _require_mapping(architecture.get("bench"), "bench")
    project = _require_mapping(architecture.get("project"), "project")
    instruments = []
    for item in _as_list(architecture.get("instruments")):
        instrument = _require_mapping(item, "instrument")
        instruments.append(
            {
                "name": instrument.get("name"),
                "role": instrument.get("role"),
                "category": instrument.get("category"),
                "model": instrument.get("model", "TBD"),
                "control_interface": instrument.get("control_interface", "TBD"),
                "calibration_required": bool(instrument.get("calibration_required", False)),
                "safety_notes": _as_list(instrument.get("safety_notes")),
            }
        )

    config = {
        "rig": {
            "name": bench.get("name"),
            "dut": bench.get("dut"),
            "project": project.get("project"),
            "client": project.get("client"),
            "automation_host": bench.get("automation_host"),
            "instruments": instruments,
        },
        "connectors": list(_as_list(architecture.get("connectors"))),
        "safety": {
            "controls": _as_list(architecture.get("safety_controls")),
            "safe_state_required": True,
        },
        "data": {
            "root": bench.get("evidence_root", "reports/evidence"),
        },
        "requirements": _as_list(architecture.get("requirements")),
    }
    return yaml.safe_dump(config, sort_keys=False)


def _bullet_lines(items: Sequence[Any]) -> list[str]:
    if not items:
        return ["- TBD"]
    return [f"- {item}" for item in items]


def generate_setup_report(architecture: Mapping[str, Any]) -> str:
    """Generate a client-facing test setup architecture report."""
    project = _require_mapping(architecture.get("project"), "project")
    bench = _require_mapping(architecture.get("bench"), "bench")
    project_name = str(project.get("project") or bench.get("name") or "Bench")
    client = project.get("client", "TBD")
    phase = project.get("phase", "TBD")
    requirements = _as_list(architecture.get("requirements"))
    instruments = _as_list(architecture.get("instruments"))
    connectors = _as_list(architecture.get("connectors"))
    safety_controls = _as_list(architecture.get("safety_controls"))

    lines = [
        f"# {project_name} — Test Setup Architecture",
        "",
        "## Executive Summary",
        "",
        f"Long Game translated the validation setup for **{project_name}** into a reviewable, buildable, and automatable bench package.",
        "",
        "## Project Metadata",
        "",
        f"- Client: {client}",
        f"- Phase: {phase}",
        f"- Bench: {bench.get('name', 'TBD')}",
        f"- DUT: {bench.get('dut', 'TBD')}",
        f"- Automation host: {bench.get('automation_host', 'TBD')}",
        f"- Evidence root: {bench.get('evidence_root', 'reports/evidence')}",
        "",
        "## Requirements Covered",
        "",
        *_bullet_lines(requirements),
        "",
        "## Test Setup Diagram Blocks",
        "",
        "- DUT / subsystem under test",
        "- Fixture, breakout, or harness",
        "- Power supplies and loads",
        "- Measurement instruments",
        "- Communication interfaces",
        "- Safety controls and E-stop chain",
        "- Automation host and evidence storage",
        "",
        "## Equipment BOM",
        "",
    ]

    for item in instruments:
        instrument = _require_mapping(item, "instrument")
        lines.extend(
            [
                f"### {instrument.get('name', 'unnamed')} — {instrument.get('role', 'TBD')}",
                "",
                f"- Category: {instrument.get('category', 'TBD')}",
                f"- Purpose: {instrument.get('purpose', 'TBD')}",
                f"- Required/optional: {instrument.get('required_optional', 'TBD')}",
                f"- Quantity: {instrument.get('quantity', 'TBD')}",
                f"- Manufacturer/model: {instrument.get('manufacturer', 'TBD')} / {instrument.get('model', 'TBD')}",
                f"- Control interface: {instrument.get('control_interface', 'TBD')}",
                f"- Calibration required: {_csv_value(instrument.get('calibration_required'))}",
                "- Selection criteria:",
                *_bullet_lines(_as_list(instrument.get("selection_criteria"))),
                "- Safety notes:",
                *_bullet_lines(_as_list(instrument.get("safety_notes"))),
                "",
            ]
        )

    lines.extend(["## Connector / Harness Map", ""])
    for item in connectors:
        connector = _require_mapping(item, "connector")
        lines.extend(
            [
                f"### {connector.get('name', 'unnamed')}",
                "",
                f"- Location: {connector.get('location', 'TBD')}",
                f"- Family: {connector.get('family', 'TBD')}",
                f"- Mating connector: {connector.get('mating_connector', 'TBD')}",
                f"- Pin count: {connector.get('pin_count', 'TBD')}",
                f"- Voltage/current rating: {connector.get('voltage_rating_v', 'TBD')} V / {connector.get('current_rating_a', 'TBD')} A",
                f"- Signal type: {connector.get('signal_type', 'TBD')}",
                f"- Cable type: {connector.get('cable_type', 'TBD')}",
                f"- Shielding/grounding: {connector.get('shielding_grounding', 'TBD')}",
                f"- Strain relief: {connector.get('strain_relief', 'TBD')}",
                f"- Risk notes: {connector.get('risk_notes', 'TBD')}",
                "",
            ]
        )

    lines.extend(
        [
            "## Safety Controls",
            "",
            *_bullet_lines(safety_controls),
            "",
            "## Generated Artifacts",
            "",
            "- Setup architecture report: human-readable design review package.",
            "- Equipment BOM CSV: procurement and calibration planning.",
            "- Connector/harness map CSV: fixture/cable build guidance.",
            "- Bench config YAML: machine-readable input for preflight and test execution.",
            "",
            "## Next Step",
            "",
            "Review this package with hardware, firmware, safety, and test owners before procurement or live hardware execution.",
            "",
        ]
    )
    return "\n".join(str(line) for line in lines)


def write_outputs(
    architecture: Mapping[str, Any],
    output_dir: str | Path,
    *,
    prefix: str | None = None,
) -> dict[str, Path]:
    """Write report, BOM CSV, connector CSV, and bench config YAML."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bench = _require_mapping(architecture.get("bench"), "bench")
    base = prefix or str(bench.get("name") or "bench").replace(" ", "_").replace("-", "_")
    paths = {
        "report": out / f"{base}-setup-report.md",
        "equipment_bom": out / f"{base}-equipment-bom.csv",
        "connectors": out / f"{base}-connector-map.csv",
        "bench_config": out / f"{base}-bench-config.yaml",
    }
    paths["report"].write_text(generate_setup_report(architecture))
    paths["equipment_bom"].write_text(generate_equipment_bom_csv(architecture))
    paths["connectors"].write_text(generate_connector_csv(architecture))
    paths["bench_config"].write_text(generate_bench_config(architecture))
    return paths


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate setup diagrams/BOM artifacts from bench architecture YAML.")
    parser.add_argument("architecture", help="Path to test setup architecture YAML")
    parser.add_argument("--output-dir", "-o", default="reports/bench-bom", help="Directory for generated artifacts")
    parser.add_argument("--prefix", help="Optional output filename prefix")
    args = parser.parse_args(argv)

    architecture = load_architecture(args.architecture)
    paths = write_outputs(architecture, args.output_dir, prefix=args.prefix)
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
