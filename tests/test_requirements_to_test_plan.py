from __future__ import annotations

from pathlib import Path
import re

import pytest

from long_game_sdk.sdk.requirements_to_test_plan import (
    RequirementValidationError,
    generate_bench_conftest,
    generate_pytest_skeleton,
    generate_test_plan,
    load_requirements,
    write_pytest_skeletons,
)


def _write_requirements(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "requirements.yaml"
    path.write_text(body)
    return path


def test_load_requirements_parses_valid_yaml(tmp_path):
    path = _write_requirements(
        tmp_path,
        """
project:
  client: Example Hardware Co.
  product: BMS HiL Bench
requirements:
  - id: BMS-REQ-001
    title: Pack voltage accuracy
    text: The BMS shall report pack voltage within 0.5%.
    type: performance
    verification_method: test
    acceptance_criteria:
      pass_condition: Error is <= 0.5% at all voltage points.
    instrumentation:
      - HV supply
      - CAN interface
    evidence:
      - voltage_sweep.csv
""",
    )

    document = load_requirements(path)

    assert document.project["product"] == "BMS HiL Bench"
    assert len(document.requirements) == 1
    assert document.requirements[0].id == "BMS-REQ-001"
    assert document.requirements[0].verification_method == "test"
    assert document.requirements[0].acceptance_summary == "Error is <= 0.5% at all voltage points."


@pytest.mark.parametrize(
    ("field", "yaml_body", "expected"),
    [
        (
            "id",
            """
requirements:
  - title: Missing ID
    text: Requirement text.
    verification_method: test
    acceptance_criteria:
      pass_condition: Must pass.
""",
            "requirement[0] missing required field: id",
        ),
        (
            "acceptance_criteria",
            """
requirements:
  - id: REQ-001
    title: Missing acceptance criteria
    text: Requirement text.
    verification_method: test
""",
            "REQ-001 missing required field: acceptance_criteria",
        ),
    ],
)
def test_load_requirements_fails_clear_for_missing_required_fields(tmp_path, field, yaml_body, expected):
    path = _write_requirements(tmp_path, yaml_body)

    with pytest.raises(RequirementValidationError, match=re.escape(expected)):
        load_requirements(path)


def test_load_requirements_rejects_unsupported_verification_method(tmp_path):
    path = _write_requirements(
        tmp_path,
        """
requirements:
  - id: REQ-001
    title: Bad method
    text: Requirement text.
    verification_method: vibes
    acceptance_criteria:
      pass_condition: Must pass.
""",
    )

    with pytest.raises(RequirementValidationError, match="REQ-001 has unsupported verification_method: vibes"):
        load_requirements(path)


def test_generate_test_plan_renders_traceability_and_test_cases(tmp_path):
    path = _write_requirements(
        tmp_path,
        """
project:
  client: Example Hardware Co.
  product: BMS HiL Bench
  phase: DVT
requirements:
  - id: BMS-REQ-002
    title: Over-voltage fault response
    text: The BMS shall assert an over-voltage fault within 100 ms.
    type: safety
    verification_method: test
    priority: safety-critical
    acceptance_criteria:
      pass_condition: Fault asserted and contactor-open command observed within 100 ms.
      timing_requirement_ms: 100
    instrumentation:
      - Cell simulator
      - Logic analyzer
      - CAN interface
    safety_controls:
      - Force safe-state before and after test.
    evidence:
      - fault_timing_capture.csv
""",
    )
    document = load_requirements(path)

    markdown = generate_test_plan(document)

    assert "# Verification Test Plan: BMS HiL Bench" in markdown
    assert "## Requirement Traceability Matrix" in markdown
    assert "BMS-REQ-002" in markdown
    assert "TC-BMS-REQ-002" in markdown
    assert "Fault asserted and contactor-open command observed within 100 ms." in markdown
    assert "## Safety / Preflight Controls" in markdown
    assert "Force safe-state before and after test." in markdown
    assert "fault_timing_capture.csv" in markdown


def test_cli_writes_markdown_report(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "hil_bms_requirements.yaml"
    assert source.exists()
    output = tmp_path / "hil-bms-test-plan.md"

    from long_game_sdk.sdk.requirements_to_test_plan import main

    main([str(source), "-o", str(output)])

    text = output.read_text()
    assert "Verification Test Plan" in text
    assert "BMS-REQ-001" in text
    assert "TC-BMS-REQ-001" in text


def test_generate_pytest_skeleton_embeds_requirement_traceability_and_safe_state(tmp_path):
    path = _write_requirements(
        tmp_path,
        """
project:
  client: Example Hardware Co.
  product: BMS HiL Bench
requirements:
  - id: BMS-REQ-002
    title: Over-voltage fault response
    text: The BMS shall assert an over-voltage fault within 100 ms.
    type: safety
    verification_method: test
    priority: safety-critical
    acceptance_criteria:
      pass_condition: Fault asserted and contactor-open command observed within 100 ms.
    instrumentation:
      - Cell simulator
      - CAN interface
    safety_controls:
      - Force safe-state before and after test.
    evidence:
      - fault_timing_capture.csv
""",
    )
    document = load_requirements(path)

    code = generate_pytest_skeleton(document.requirements[0])

    assert "def test_bms_req_002_over_voltage_fault_response" in code
    assert '@pytest.mark.requirement("BMS-REQ-002")' in code
    assert "@pytest.mark.safety_critical" in code
    assert "safe_state" in code
    assert "fault_timing_capture.csv" in code
    assert "pytest.skip" in code


def test_write_pytest_skeletons_creates_one_file_per_requirement(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "hil_bms_requirements.yaml"
    document = load_requirements(source)
    output_dir = tmp_path / "generated"

    written = write_pytest_skeletons(document, output_dir)

    assert len(written) == 2
    names = {path.name for path in written}
    assert "test_bms_req_001_pack_voltage_measurement_accuracy.py" in names
    assert "test_bms_req_002_over_voltage_fault_response.py" in names
    text = (output_dir / "test_bms_req_002_over_voltage_fault_response.py").read_text()
    assert "TC-BMS-REQ-002" in text
    assert "safe_state" in text


def test_cli_can_generate_test_plan_and_pytest_skeletons(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "hil_bms_requirements.yaml"
    report = tmp_path / "hil-bms-test-plan.md"
    skeleton_dir = tmp_path / "generated-tests"

    from long_game_sdk.sdk.requirements_to_test_plan import main

    main([str(source), "-o", str(report), "--pytest-dir", str(skeleton_dir)])

    assert report.exists()
    assert (skeleton_dir / "test_bms_req_001_pack_voltage_measurement_accuracy.py").exists()
    assert (skeleton_dir / "test_bms_req_002_over_voltage_fault_response.py").exists()



def test_generate_bench_conftest_exposes_config_instruments_and_safe_state():
    bench_config = Path(__file__).parents[1] / "examples" / "hv_safety_plan_bench_a.yaml"

    code = generate_bench_conftest(bench_config)

    assert "BENCH_CONFIG_PATH" in code
    assert "def bench_config" in code
    assert "def instruments" in code
    assert "def safe_state" in code
    assert "main_psu" in code
    assert "electronic_load" in code
    assert "Run lg-safe before and after live hardware tests" in code


def test_write_pytest_skeletons_can_bind_to_bench_config(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "hil_bms_requirements.yaml"
    bench_config = Path(__file__).parents[1] / "examples" / "hv_safety_plan_bench_a.yaml"
    document = load_requirements(source)
    output_dir = tmp_path / "bound-tests"

    written = write_pytest_skeletons(document, output_dir, bench_config_path=bench_config)

    assert output_dir.joinpath("conftest.py").exists()
    assert output_dir.joinpath("bench_config.yaml").exists()
    assert output_dir.joinpath("conftest.py") in written
    text = output_dir.joinpath("test_bms_req_002_over_voltage_fault_response.py").read_text()
    assert "def safe_state" not in text
    assert "bench_config" in text
    assert "instruments" in text
    assert "bench_config.yaml" not in text


def test_cli_can_generate_bound_pytest_skeletons(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "hil_bms_requirements.yaml"
    bench_config = Path(__file__).parents[1] / "examples" / "hv_safety_plan_bench_a.yaml"
    skeleton_dir = tmp_path / "generated-bound-tests"

    from long_game_sdk.sdk.requirements_to_test_plan import main

    main([str(source), "--pytest-dir", str(skeleton_dir), "--bench-config", str(bench_config)])

    assert skeleton_dir.joinpath("conftest.py").exists()
    assert skeleton_dir.joinpath("bench_config.yaml").exists()
    text = skeleton_dir.joinpath("test_bms_req_001_pack_voltage_measurement_accuracy.py").read_text()
    assert "def test_bms_req_001_pack_voltage_measurement_accuracy(safe_state, evidence_dir: Path, bench_config, instruments):" in text
