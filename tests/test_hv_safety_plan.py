from __future__ import annotations

from long_game_sdk.sdk.hv_safety_plan import generate_safety_plan, render_markdown


def example_config() -> dict:
    return {
        "rig": {
            "name": "bench-a",
            "dut_type": "hv-pcba",
            "dut": {"name": "inverter-control-board", "serial": "DUT-HV-001"},
            "instruments": [
                {"name": "main_psu", "expected_model": "Rigol DP832", "connection": "USB0::DP832::INSTR"},
                {"name": "load", "expected_model": "Rigol DL3021", "connection": "USB0::DL3021::INSTR"},
            ],
        },
        "safety_plan": {
            "operator": "Morgan",
            "reviewer": "Safety Lead",
            "test_location": "Oakland bench A",
            "max_voltage_v": 420,
            "max_current_a": 8,
            "energy_sources": ["420 VDC battery simulator", "24 V auxiliary supply"],
            "hazards": ["Stored energy in DC link capacitors", "Arc flash during incorrect probing"],
            "ppe": ["Safety glasses", "Class 0 gloves", "Insulated tools"],
            "estop": {
                "location": "Front-left mushroom switch",
                "verification": "Press E-stop and confirm PSU output relay opens before test.",
            },
            "disconnects": ["Bench DC disconnect", "Battery simulator contactor"],
            "discharge": {
                "method": "Use rated bleeder fixture across HV bus.",
                "verification": "Verify bus below 50 V with CAT III DMM before handling.",
            },
            "interlocks": ["Lid switch closed", "Area rope line installed"],
            "safe_state": ["All PSU outputs OFF", "Electronic load input OFF", "DUT contactors open"],
            "pre_job_briefing": ["Review shock boundaries", "Assign one operator and one observer"],
            "stop_work_criteria": ["Unexpected smell/smoke", "Measured voltage exceeds configured limit"],
        },
    }


def test_generate_safety_plan_captures_required_hv_sections():
    plan = generate_safety_plan(example_config())

    assert plan.rig_name == "bench-a"
    assert plan.dut_summary == "inverter-control-board (DUT-HV-001)"
    assert plan.max_voltage_v == 420
    assert plan.required_sections_present

    markdown = render_markdown(plan)
    required_headings = [
        "# HV/PCBA Test Safety Plan",
        "## DUT and Test Setup Summary",
        "## HV Hazard Inventory",
        "## Required PPE",
        "## E-stop and Disconnect Verification",
        "## Discharge / Bleeder-Resistor Checks",
        "## Interlock Checklist",
        "## Safe-State Requirements",
        "## Operator Pre-Job Briefing",
        "## Stop-Work Criteria",
        "## Sign-Off",
    ]
    for heading in required_headings:
        assert heading in markdown
    assert "420 VDC battery simulator" in markdown
    assert "Verify bus below 50 V" in markdown


def test_generate_safety_plan_marks_missing_critical_controls():
    config = example_config()
    del config["safety_plan"]["estop"]
    config["safety_plan"]["interlocks"] = []

    plan = generate_safety_plan(config)
    markdown = render_markdown(plan)

    assert not plan.required_sections_present
    assert "MISSING: E-stop verification" in markdown
    assert "MISSING: Interlock checklist" in markdown
