from __future__ import annotations

import csv
from pathlib import Path

import pytest

from long_game_sdk.sdk.bench_bom import (
    BenchBomValidationError,
    generate_bench_config,
    generate_connector_csv,
    generate_equipment_bom_csv,
    generate_setup_report,
    load_architecture,
)


def _write_architecture(tmp_path: Path) -> Path:
    path = tmp_path / "bench_architecture.yaml"
    path.write_text(
        """
project:
  client: Example Hardware Co.
  project: 800 V BMS HiL Bench
  phase: DVT
bench:
  name: bms_hil_bench_a
  dut: 800 V BMS controller
  evidence_root: reports/bms-hil
  automation_host: Linux laptop
requirements:
  - BMS-REQ-001
instruments:
  - name: hv_supply
    category: power
    role: pack_voltage_source
    purpose: Simulate pack voltage.
    required_optional: required
    quantity: 1
    manufacturer: EA Elektro-Automatik / equivalent
    model: TBD
    control_interface: ethernet
    calibration_required: true
    selection_criteria:
      - Voltage range covers requirements with margin.
      - API supports output enable/disable.
    safety_notes:
      - Output OFF before wiring changes.
    estimated_cost_usd: TBD
    lead_time: TBD
    replacement_option: Lower voltage supply for early firmware tests.
connectors:
  - name: lv_control_header
    location: DUT LV connector
    family: Molex Micro-Fit / equivalent
    mating_connector: TBD
    pin_count: 12
    keying: polarized
    voltage_rating_v: 60
    current_rating_a: 8
    signal_type: LV power, CAN, enables
    cable_type: shielded multi-conductor
    shielding_grounding: shield drain to fixture ground
    strain_relief: boot and cable tie
    required_optional: required
    risk_notes: Prevent swapped CAN pins.
safety_controls:
  - All outputs OFF before connect/disconnect.
""".strip()
    )
    return path


def test_load_architecture_requires_core_sections(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("project: {}\nbench: {}\n")

    with pytest.raises(BenchBomValidationError, match="instruments"):
        load_architecture(path)


def test_generate_equipment_bom_csv_contains_procurement_fields(tmp_path):
    architecture = load_architecture(_write_architecture(tmp_path))

    csv_text = generate_equipment_bom_csv(architecture)
    rows = list(csv.DictReader(csv_text.splitlines()))

    assert rows[0]["name"] == "hv_supply"
    assert rows[0]["category"] == "power"
    assert rows[0]["control_interface"] == "ethernet"
    assert rows[0]["calibration_required"] == "yes"
    assert "output" in rows[0]["safety_notes"].lower()


def test_generate_connector_csv_contains_harness_map(tmp_path):
    architecture = load_architecture(_write_architecture(tmp_path))

    csv_text = generate_connector_csv(architecture)
    rows = list(csv.DictReader(csv_text.splitlines()))

    assert rows[0]["connector_name"] == "lv_control_header"
    assert rows[0]["family"] == "Molex Micro-Fit / equivalent"
    assert rows[0]["pin_count"] == "12"
    assert rows[0]["signal_type"] == "LV power, CAN, enables"


def test_generate_bench_config_maps_instruments_for_preflight_and_test_plan(tmp_path):
    architecture = load_architecture(_write_architecture(tmp_path))

    yaml_text = generate_bench_config(architecture)

    assert "rig:" in yaml_text
    assert "name: bms_hil_bench_a" in yaml_text
    assert "instruments:" in yaml_text
    assert "name: hv_supply" in yaml_text
    assert "role: pack_voltage_source" in yaml_text
    assert "data:" in yaml_text
    assert "reports/bms-hil" in yaml_text


def test_generate_setup_report_links_requirements_diagram_bom_and_safety(tmp_path):
    architecture = load_architecture(_write_architecture(tmp_path))

    report = generate_setup_report(architecture)

    assert "# 800 V BMS HiL Bench — Test Setup Architecture" in report
    assert "BMS-REQ-001" in report
    assert "hv_supply" in report
    assert "lv_control_header" in report
    assert "All outputs OFF before connect/disconnect." in report
    assert "Equipment BOM" in report
    assert "Connector / Harness Map" in report
