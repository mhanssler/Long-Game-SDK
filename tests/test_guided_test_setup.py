from __future__ import annotations

from pathlib import Path

import yaml

from long_game_sdk.sdk.guided_test_setup import (
    GuidedSetupError,
    build_context_pack,
    generate_operator_guide,
    main,
)


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    requirements = {
        "project": {"client": "Example Hardware Co.", "product": "BMS"},
        "requirements": [
            {
                "id": "BMS-REQ-002",
                "title": "Over-voltage fault response",
                "text": "The BMS shall assert an over-voltage fault within 100 ms.",
                "acceptance_criteria": {"pass_condition": "Fault asserted within 100 ms."},
                "instrumentation": ["Cell simulator", "Logic analyzer or DAQ", "CAN interface"],
                "safety_controls": ["Force safe-state before and after test."],
                "evidence": ["fault_timing_capture.csv", "can_fault_log.asc"],
            }
        ],
    }
    bench = {
        "bench": {"name": "bms_hil_bench_a", "evidence_root": "reports/bms-hil"},
        "instruments": [
            {"name": "cell_simulator", "role": "cell_voltage_source", "purpose": "Simulate cell over-voltage."},
            {"name": "logic_analyzer", "role": "fault_timing_capture", "purpose": "Measure BMS fault output timing."},
            {"name": "can_interface", "role": "bms_telemetry", "purpose": "Log BMS CAN traffic."},
        ],
        "connectors": [
            {"name": "lv_control_header", "location": "DUT J1", "signal_type": "LV power, CAN, faults"}
        ],
        "safety_controls": ["All sources output OFF before connect/disconnect."],
    }
    schematic = {
        "schematic_context": {
            "dut": {
                "name": "bms_controller",
                "source_files": ["bms_pin_map.csv"],
                "connectors": {
                    "J1": {
                        "pins": {
                            "7": {
                                "net": "BMS_FAULT_N",
                                "description": "Fault output to fixture logic",
                                "signal_type": "open_drain_logic",
                                "max_voltage_v": 5,
                                "max_current_a": 0.01,
                            }
                        }
                    }
                },
                "test_points": {
                    "TP12": {"net": "CELL_SIM_1", "description": "Simulated cell 1 sense node"}
                },
            }
        }
    }
    req_path = tmp_path / "requirements.yaml"
    bench_path = tmp_path / "bench.yaml"
    schematic_path = tmp_path / "schematic.yaml"
    req_path.write_text(yaml.safe_dump(requirements, sort_keys=False))
    bench_path.write_text(yaml.safe_dump(bench, sort_keys=False))
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))
    return req_path, bench_path, schematic_path


def test_build_context_pack_combines_requirement_bench_and_schematic(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)

    pack = build_context_pack(
        requirements_path=req_path,
        requirement_id="BMS-REQ-002",
        bench_config_path=bench_path,
        schematic_context_path=schematic_path,
    )

    assert pack["requirement"]["id"] == "BMS-REQ-002"
    assert pack["bench"]["name"] == "bms_hil_bench_a"
    assert "cell_simulator" in [item["name"] for item in pack["bench"]["instruments"]]
    assert pack["schematic"]["dut"]["connectors"]["J1"]["pins"]["7"]["net"] == "BMS_FAULT_N"
    assert pack["schematic"]["dut"]["test_points"]["TP12"]["net"] == "CELL_SIM_1"
    assert pack["execution"]["default_mode"] == "guide-only"
    assert "fault_timing_capture.csv" in pack["evidence_artifacts"]


def test_build_context_pack_fails_when_requirement_missing(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)

    try:
        build_context_pack(
            requirements_path=req_path,
            requirement_id="NOPE",
            bench_config_path=bench_path,
            schematic_context_path=schematic_path,
        )
    except GuidedSetupError as exc:
        assert "requirement not found" in str(exc)
    else:
        raise AssertionError("expected GuidedSetupError")


def test_operator_guide_includes_wiring_safety_and_stop_if_missing(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    pack = build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)

    guide = generate_operator_guide(pack)

    assert "# Guided Test Setup" in guide
    assert "BMS-REQ-002" in guide
    assert "Connect logic_analyzer to J1 pin 7 / net BMS_FAULT_N" in guide
    assert "Connect cell_simulator to test point TP12 / net CELL_SIM_1" in guide
    assert "Run `lg-safe` before wiring changes" in guide
    assert "Do not energize outputs until preflight and wiring confirmation pass" in guide


def test_cli_writes_context_pack_and_operator_guide(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "guided"

    exit_code = main(
        [
            str(req_path),
            "--requirement-id",
            "BMS-REQ-002",
            "--bench-config",
            str(bench_path),
            "--schematic-context",
            str(schematic_path),
            "-o",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    context = yaml.safe_load((output_dir / "test-context-pack.yaml").read_text())
    assert context["test_context_pack"]["requirement"]["id"] == "BMS-REQ-002"
    guide = (output_dir / "operator-guide.md").read_text()
    assert "Guided Test Setup" in guide


def test_cli_refuses_execute_for_now(tmp_path: Path, capsys) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)

    exit_code = main(
        [
            str(req_path),
            "--requirement-id",
            "BMS-REQ-002",
            "--bench-config",
            str(bench_path),
            "--schematic-context",
            str(schematic_path),
            "--execute",
        ]
    )

    assert exit_code == 2
    assert "guide-only MVP" in capsys.readouterr().err
