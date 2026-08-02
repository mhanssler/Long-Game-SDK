from __future__ import annotations

from pathlib import Path

import pytest
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
            {
                "name": "cell_simulator",
                "role": "cell_voltage_source",
                "purpose": "Simulate cell over-voltage.",
                "energizing": True,
                "terminals": {
                    "CH1+": {"signal_type": "isolated_analog_source", "isolation": "isolated_channel", "polarity": "positive", "max_voltage_v": 5.0, "max_current_a": 0.1},
                    "CH1-": {"signal_type": "power_return", "isolation": "isolated_channel", "polarity": "negative", "max_voltage_v": 5.0, "max_current_a": 0.1},
                    "COM": {"signal_type": "power_return", "isolation": "common_ground", "polarity": "reference", "max_voltage_v": 5.0, "max_current_a": 0.1},
                },
            },
            {
                "name": "logic_analyzer",
                "role": "fault_timing_capture",
                "purpose": "Measure BMS fault output timing.",
                "energizing": False,
                "terminals": {
                    "D0": {"signal_type": "open_drain_logic", "isolation": "common_ground", "polarity": "signal", "max_voltage_v": 5.0, "max_current_a": 0.01},
                    "GND": {"signal_type": "power_return", "isolation": "common_ground", "polarity": "reference", "max_voltage_v": 5.0, "max_current_a": 1.0},
                },
            },
            {"name": "can_interface", "role": "bms_telemetry", "purpose": "Log BMS CAN traffic.",
             "energizing": False, "terminals": {}},
        ],
        "connectors": [
            {"name": "lv_control_header", "location": "DUT J1", "signal_type": "LV power, CAN, faults"}
        ],
        "safety_controls": ["All sources output OFF before connect/disconnect."],
    }
    schematic = {
        "schematic_context": {
            "revision": "SCH-BMS-004-C",
            "dut": {
                "name": "bms_controller",
                "source_files": ["bms_pin_map.csv"],
                "connectors": {
                    "J1": {
                        "pins": {
                            "2": {
                                "net": "VIN-", "description": "Input return",
                                "signal_type": "power_return", "isolation": "common_ground", "polarity": "reference", "max_voltage_v": 5.0, "max_current_a": 1.0,
                            },
                            "7": {
                                "net": "BMS_FAULT_N",
                                "description": "Fault output to fixture logic",
                                "signal_type": "open_drain_logic",
                                "isolation": "common_ground",
                                "polarity": "signal",
                                "max_voltage_v": 5,
                                "max_current_a": 0.01,
                            }
                        }
                    }
                },
                "test_points": {
                    "TP12": {
                        "net": "CELL_SIM_1", "description": "Simulated cell 1 sense node",
                        "signal_type": "isolated_analog_source", "isolation": "isolated_channel",
                        "polarity": "positive", "max_voltage_v": 5.0, "max_current_a": 0.1,
                    },
                    "TP13": {
                        "net": "CELL_SIM_1_RETURN", "description": "Simulated cell 1 return",
                        "signal_type": "power_return", "isolation": "isolated_channel",
                        "polarity": "negative", "max_voltage_v": 5.0, "max_current_a": 0.1,
                    }
                },
            },
            "connections": [
                {
                    "instrument": "logic_analyzer",
                    "terminal": "D0",
                    "destination": {"connector": "J1", "pin": "7", "net": "BMS_FAULT_N"},
                    "reference": {"instrument_terminal": "GND", "connector": "J1", "pin": "2", "net": "VIN-"},
                    "signal_type": "open_drain_logic",
                    "max_voltage_v": 5.0,
                    "max_current_a": 0.01,
                    "isolation": "common_ground",
                    "approved": True,
                    "source_revision": "SCH-BMS-004-C",
                },
                {
                    "instrument": "cell_simulator",
                    "terminal": "CH1+",
                    "destination": {"test_point": "TP12", "net": "CELL_SIM_1"},
                    "reference": {"instrument_terminal": "CH1-", "test_point": "TP13", "net": "CELL_SIM_1_RETURN"},
                    "signal_type": "isolated_analog_source",
                    "max_voltage_v": 5.0,
                    "max_current_a": 0.1,
                    "isolation": "isolated_channel",
                    "approved": True,
                    "source_revision": "SCH-BMS-004-C",
                },
            ],
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
    assert "Connect logic_analyzer terminal D0 to DUT J1 pin 7 / net BMS_FAULT_N" in guide
    assert "reference GND to DUT J1 pin 2 / net VIN-" in guide
    assert "Connect cell_simulator terminal CH1+ to DUT test point TP12 / net CELL_SIM_1" in guide
    assert "source revision SCH-BMS-004-C" in guide
    assert "Run `lg-safe <bench/preflight-config.yaml>` before wiring changes" in guide
    assert "Do not energize outputs until preflight and wiring confirmation pass" in guide
    assert "MANDATORY FINAL GATE" in guide
    assert "after every test, including failures" in guide


def test_operator_guide_distinguishes_fixture_connector_scope(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    context = schematic["schematic_context"]
    context["fixture"] = {
        "name": "bms_fixture",
        "connectors": {"J1": {"pins": {"2": {
            "net": "FIXTURE_GND", "signal_type": "power_return",
            "isolation": "common_ground", "polarity": "reference",
            "max_voltage_v": 5.0, "max_current_a": 1.0,
        }}}},
    }
    reference = context["connections"][0]["reference"]
    reference.update(scope="fixture", net="FIXTURE_GND")
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    pack = build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)
    guide = generate_operator_guide(pack)

    assert "to DUT J1 pin 7 / net BMS_FAULT_N" in guide
    assert "reference GND to fixture J1 pin 2 / net FIXTURE_GND" in guide


def test_operator_guide_names_a_different_reference_instrument(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    reference = schematic["schematic_context"]["connections"][0]["reference"]
    reference.update(instrument="cell_simulator", instrument_terminal="COM")
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    pack = build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)
    guide = generate_operator_guide(pack)

    assert "connect reference cell_simulator terminal COM to DUT J1 pin 2 / net VIN-" in guide


def test_documented_guided_setup_example_builds() -> None:
    repository = Path(__file__).resolve().parents[1]

    pack = build_context_pack(
        repository / "examples/hil_bms_requirements.yaml",
        "BMS-REQ-002",
        repository / "examples/bms_hil_bench_architecture.yaml",
        repository / "examples/guided_test_setup/bms_schematic_context.yaml",
        pytest_target="tests/generated/hil_bms/test_bms_req_002.py",
        flash_config_path=repository / "examples/openocd/stm32f4_flash.yaml",
    )

    guide = generate_operator_guide(pack)
    assert "logic_analyzer terminal D0" in guide
    assert "cell_simulator terminal CH1+" in guide


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


def test_operator_guide_never_infers_connections_from_net_names(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    schematic["schematic_context"].pop("connections")
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    pack = build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)
    guide = generate_operator_guide(pack)

    assert "STOP: No approved explicit connection records" in guide
    assert "Connect logic_analyzer" not in guide
    assert "Connect cell_simulator" not in guide


def test_context_pack_rejects_revision_mismatched_connection(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    schematic["schematic_context"]["connections"][0]["source_revision"] = "SCH-BMS-003-A"
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    try:
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)
    except GuidedSetupError as exc:
        assert "source_revision" in str(exc)
    else:
        raise AssertionError("expected revision mismatch to stop guided setup")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda connection: connection.update(instrument=123), "instrument"),
        (lambda connection: connection.update(terminal=""), "terminal"),
        (lambda connection: connection["destination"].pop("pin"), "destination"),
        (lambda connection: connection["reference"].pop("connector"), "reference"),
        (lambda connection: connection["reference"].update(pin=2), "reference"),
        (lambda connection: connection.update(max_voltage_v=float("nan")), "max_voltage_v"),
        (lambda connection: connection.update(max_current_a=float("inf")), "max_current_a"),
    ],
)
def test_context_pack_rejects_incomplete_or_untyped_wiring_before_rendering(
    tmp_path: Path, mutation, message: str
) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    mutation(schematic["schematic_context"]["connections"][0])
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    with pytest.raises(GuidedSetupError, match=message):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda connection: connection.update(instrument="invented_scope"), "bench inventory"),
        (lambda connection: connection["destination"].update(connector="J404"), "connector"),
        (lambda connection: connection["destination"].update(pin="404"), "pin"),
        (lambda connection: connection["destination"].update(net="WRONG_NET"), "net"),
        (lambda connection: connection["reference"].update(net="WRONG_RETURN"), "net"),
        (
            lambda connection: connection["destination"].update(
                test_point="TP404", connector=None, pin=None
            ),
            "test point",
        ),
    ],
)
def test_context_pack_stops_on_noncanonical_wiring_references(
    tmp_path: Path, mutation, message: str
) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    mutation(schematic["schematic_context"]["connections"][0])
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    with pytest.raises(GuidedSetupError, match=message):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


def test_context_pack_rejects_duplicate_bench_instrument_names(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    bench = yaml.safe_load(bench_path.read_text())
    bench["instruments"].append(dict(bench["instruments"][0]))
    bench_path.write_text(yaml.safe_dump(bench, sort_keys=False))

    with pytest.raises(GuidedSetupError, match="duplicate bench instrument name"):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


def test_context_pack_rejects_case_insensitive_duplicate_bench_names(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    bench = yaml.safe_load(bench_path.read_text())
    duplicate = dict(bench["instruments"][0])
    duplicate["name"] = duplicate["name"].upper()
    bench["instruments"].append(duplicate)
    bench_path.write_text(yaml.safe_dump(bench, sort_keys=False))

    with pytest.raises(GuidedSetupError, match="duplicate bench instrument name"):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


def test_connection_limit_includes_reference_terminal_and_endpoint(tmp_path: Path) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    bench = yaml.safe_load(bench_path.read_text())
    bench["instruments"][1]["terminals"]["GND"]["max_current_a"] = 0.005
    bench_path.write_text(yaml.safe_dump(bench, sort_keys=False))

    with pytest.raises(GuidedSetupError, match="max_current_a.*0.005"):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda bench, connection: connection.update(terminal="CH404"), "terminal"),
        (
            lambda bench, connection: connection["reference"].update(instrument_terminal="RETURN404"),
            "reference terminal",
        ),
        (lambda bench, connection: connection.update(signal_type="digital_logic"), "signal_type"),
        (lambda bench, connection: connection.update(max_voltage_v=6.0), "max_voltage_v"),
        (lambda bench, connection: connection.update(max_current_a=0.2), "max_current_a"),
        (
            lambda bench, connection: bench["instruments"][0]["terminals"]["CH1+"].pop("max_voltage_v"),
            "max_voltage_v",
        ),
    ],
)
def test_energizing_wiring_binds_canonical_terminals_signal_and_limits(
    tmp_path: Path, mutation, message: str
) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    bench = yaml.safe_load(bench_path.read_text())
    schematic = yaml.safe_load(schematic_path.read_text())
    connection = schematic["schematic_context"]["connections"][1]
    mutation(bench, connection)
    bench_path.write_text(yaml.safe_dump(bench, sort_keys=False))
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))
    with pytest.raises(GuidedSetupError, match=message):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda connections: (
            connections[1].update(
                instrument=connections[0]["instrument"], terminal=connections[0]["terminal"]
            ),
            connections[1]["reference"].update(instrument="cell_simulator"),
        ), "reuses source terminal"),
        (lambda connections: connections[1].update(
            destination=dict(connections[0]["destination"])
        ), "reuses a destination endpoint"),
        (lambda connections: connections[1]["reference"].update(
            instrument=connections[0]["instrument"],
            instrument_terminal=connections[0]["reference"]["instrument_terminal"],
        ), "reuses reference terminal"),
        (lambda connections: connections[1].update(
            terminal=connections[1]["reference"]["instrument_terminal"]
        ), "source/reference terminal topology"),
    ],
)
def test_connection_graph_rejects_duplicate_or_conflicting_reuse(
    tmp_path: Path, mutation, message: str
) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    mutation(schematic["schematic_context"]["connections"])
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    with pytest.raises(GuidedSetupError, match=message):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)


@pytest.mark.parametrize("isolation", ["floating-ish", "", None])
def test_connection_rejects_noncanonical_or_missing_isolation_metadata(
    tmp_path: Path, isolation
) -> None:
    req_path, bench_path, schematic_path = _write_inputs(tmp_path)
    schematic = yaml.safe_load(schematic_path.read_text())
    schematic["schematic_context"]["connections"][0]["isolation"] = isolation
    schematic_path.write_text(yaml.safe_dump(schematic, sort_keys=False))

    with pytest.raises(GuidedSetupError, match="isolation"):
        build_context_pack(req_path, "BMS-REQ-002", bench_path, schematic_path)
