from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


class GuidedSetupError(ValueError):
    """Raised when a guided setup context cannot be built safely."""


def _load_yaml(path: str | Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text())
    except FileNotFoundError as exc:
        raise GuidedSetupError(f"file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise GuidedSetupError(f"invalid YAML in {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GuidedSetupError(f"{label} must be a mapping")
    return value


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _find_requirement(requirements_data: Mapping[str, Any], requirement_id: str) -> Mapping[str, Any]:
    for requirement in _list(requirements_data.get("requirements")):
        if isinstance(requirement, Mapping) and str(requirement.get("id")) == requirement_id:
            return requirement
    raise GuidedSetupError(f"requirement not found: {requirement_id}")


def _bench_summary(bench_data: Mapping[str, Any]) -> dict[str, Any]:
    bench = _mapping(bench_data.get("bench", {}), "bench")
    return {
        "name": bench.get("name", "unnamed_bench"),
        "dut": bench.get("dut"),
        "evidence_root": bench.get("evidence_root", "reports/guided-tests"),
        "instruments": [item for item in _list(bench_data.get("instruments")) if isinstance(item, Mapping)],
        "connectors": [item for item in _list(bench_data.get("connectors")) if isinstance(item, Mapping)],
        "safety_controls": _list(bench_data.get("safety_controls")),
    }


def _schematic_summary(schematic_data: Mapping[str, Any]) -> Mapping[str, Any]:
    context = _mapping(schematic_data.get("schematic_context"), "schematic_context")
    dut = _mapping(context.get("dut"), "schematic_context.dut")
    if not dut.get("connectors") and not dut.get("test_points"):
        raise GuidedSetupError("schematic context must include connectors or test_points")
    return {"dut": dict(dut)}


def build_context_pack(
    requirements_path: str | Path,
    requirement_id: str,
    bench_config_path: str | Path,
    schematic_context_path: str | Path,
    *,
    pytest_target: str | None = None,
    flash_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the deterministic context pack used before any LLM guidance."""
    requirements_data = _mapping(_load_yaml(requirements_path), "requirements file")
    bench_data = _mapping(_load_yaml(bench_config_path), "bench config")
    schematic_data = _mapping(_load_yaml(schematic_context_path), "schematic context")

    requirement = _find_requirement(requirements_data, requirement_id)
    bench = _bench_summary(bench_data)
    schematic = _schematic_summary(schematic_data)
    evidence_root = bench.get("evidence_root") or "reports/guided-tests"
    evidence = _list(requirement.get("evidence"))

    pack: dict[str, Any] = {
        "project": requirements_data.get("project", {}),
        "requirement": dict(requirement),
        "bench": bench,
        "schematic": schematic,
        "evidence_artifacts": evidence,
        "safety_gates": {
            "bench": bench.get("safety_controls", []),
            "requirement": _list(requirement.get("safety_controls")),
            "required_before_execution": [
                "Run lg-safe before wiring changes.",
                "Confirm schematic-to-harness mapping.",
                "Confirm preflight passes.",
                "Confirm operator wiring before energizing outputs.",
            ],
        },
        "execution": {
            "default_mode": "guide-only",
            "pytest_target": pytest_target,
            "evidence_dir": f"{evidence_root}/{requirement_id}",
            "flash_config": str(flash_config_path) if flash_config_path else None,
        },
    }
    return pack


def _instrument_names(pack: Mapping[str, Any]) -> set[str]:
    bench = _mapping(pack.get("bench", {}), "bench")
    requirement = _mapping(pack.get("requirement", {}), "requirement")
    names = {str(item.get("name", "")).lower() for item in _list(bench.get("instruments")) if isinstance(item, Mapping)}
    for item in _list(requirement.get("instrumentation")):
        text = str(item).lower()
        if "cell simulator" in text:
            names.add("cell_simulator")
        if "logic analyzer" in text:
            names.add("logic_analyzer")
        if "daq" in text:
            names.add("daq")
        if "can" in text:
            names.add("can_interface")
        if "supply" in text or "simulator" in text:
            names.add("hv_supply")
    return names


def _connection_steps(pack: Mapping[str, Any]) -> list[str]:
    names = _instrument_names(pack)
    schematic = _mapping(pack.get("schematic", {}), "schematic")
    dut = _mapping(schematic.get("dut", {}), "schematic.dut")
    connectors = _mapping(dut.get("connectors", {}), "schematic.dut.connectors") if dut.get("connectors") else {}
    test_points = _mapping(dut.get("test_points", {}), "schematic.dut.test_points") if dut.get("test_points") else {}
    steps: list[str] = []

    for connector_name, connector in connectors.items():
        if not isinstance(connector, Mapping):
            continue
        pins = connector.get("pins", {})
        if not isinstance(pins, Mapping):
            continue
        for pin, meta in pins.items():
            if not isinstance(meta, Mapping):
                continue
            net = str(meta.get("net", "")).upper()
            if "FAULT" in net and ("logic_analyzer" in names or "daq" in names):
                instrument = "logic_analyzer" if "logic_analyzer" in names else "DAQ"
                steps.append(f"Connect {instrument} to {connector_name} pin {pin} / net {meta.get('net')}.")
            elif "VIN" in net or "PACK" in net:
                source = "hv_supply" if "hv_supply" in names else "power_source"
                steps.append(f"Connect {source} to {connector_name} pin {pin} / net {meta.get('net')}.")

    for tp_name, meta in test_points.items():
        if not isinstance(meta, Mapping):
            continue
        net = str(meta.get("net", "")).upper()
        if "CELL" in net and "cell_simulator" in names:
            steps.append(f"Connect cell_simulator to test point {tp_name} / net {meta.get('net')}.")
        else:
            steps.append(f"Review schematic mapping for test point {tp_name} / net {meta.get('net')}.")

    if not steps:
        steps.append("STOP: No deterministic connection steps could be resolved from schematic context.")
    return steps


def generate_operator_guide(pack: Mapping[str, Any]) -> str:
    requirement = _mapping(pack.get("requirement", {}), "requirement")
    bench = _mapping(pack.get("bench", {}), "bench")
    safety = _mapping(pack.get("safety_gates", {}), "safety_gates")
    execution = _mapping(pack.get("execution", {}), "execution")
    connection_steps = _connection_steps(pack)

    lines = [
        "# Guided Test Setup",
        "",
        f"- Requirement: {requirement.get('id')} — {requirement.get('title', '')}",
        f"- Bench: {bench.get('name')}",
        f"- Mode: {execution.get('default_mode')}",
        f"- Evidence directory: {execution.get('evidence_dir')}",
        "",
        "## What this test verifies",
        "",
        str(requirement.get("text", "")),
        "",
        "## Connection Steps",
        "",
        *[f"{idx}. {step}" for idx, step in enumerate(connection_steps, start=1)],
        "",
        "## Safety Gates",
        "",
        "- Run `lg-safe` before wiring changes.",
        "- Do not energize outputs until preflight and wiring confirmation pass.",
    ]
    for item in _list(safety.get("bench")) + _list(safety.get("requirement")):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Evidence Artifacts",
            "",
        ]
    )
    for artifact in _list(pack.get("evidence_artifacts")):
        lines.append(f"- {artifact}")
    lines.extend(
        [
            "",
            "## Execution",
            "",
            "This MVP is guide-only. Use this context pack to review wiring, then run bench preflight and the generated pytest target once available.",
        ]
    )
    if execution.get("pytest_target"):
        lines.append(f"- Pytest target: `{execution['pytest_target']}`")
    if execution.get("flash_config"):
        lines.append(f"- Firmware flash config: `{execution['flash_config']}`")
    return "\n".join(lines).rstrip() + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an LLM-ready guided hardware test setup context pack")
    parser.add_argument("requirements", help="Requirements YAML")
    parser.add_argument("--requirement-id", required=True, help="Requirement ID to guide")
    parser.add_argument("--bench-config", required=True, help="Bench config/setup architecture YAML")
    parser.add_argument("--schematic-context", required=True, help="Canonical schematic_context YAML")
    parser.add_argument("--pytest-target", help="Optional pytest target for later execution")
    parser.add_argument("--flash-config", help="Optional firmware flash config path")
    parser.add_argument("-o", "--output-dir", default="reports/guided-test", help="Output directory")
    parser.add_argument("--execute", action="store_true", help="Reserved for future execution flow")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    if args.execute:
        import sys

        print("lg-guide-test is a guide-only MVP; execution will be added behind safety gates.", file=sys.stderr)
        return 2
    try:
        pack = build_context_pack(
            args.requirements,
            args.requirement_id,
            args.bench_config,
            args.schematic_context,
            pytest_target=args.pytest_target,
            flash_config_path=args.flash_config,
        )
    except GuidedSetupError as exc:
        import sys

        print(f"guided setup error: {exc}", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "test-context-pack.yaml").write_text(yaml.safe_dump({"test_context_pack": pack}, sort_keys=False))
    (output_dir / "operator-guide.md").write_text(generate_operator_guide(pack))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
