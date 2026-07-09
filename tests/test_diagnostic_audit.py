from __future__ import annotations

from long_game_sdk.sdk.diagnostic_audit import build_audit, render_markdown
from long_game_sdk.sdk.preflight.checks import run_preflight


class FakeInstrument:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def query(self, command: str) -> str:
        return self.responses[command]

    def write(self, command: str) -> None:
        self.responses[f"write:{command}"] = "ok"


def test_diagnostic_audit_scores_and_renders_client_summary(tmp_path):
    config = {
        "rig": {
            "name": "bench-a",
            "dut_type": "pcba",
            "instruments": [
                {
                    "name": "main_psu",
                    "expected_model": "Rigol DP832",
                    "checks": ["identity", "output_disabled_on_start", "calibration_date"],
                    "safety": {"output_query": ":OUTPut? CH1", "calibration_due": "2027-06-01"},
                }
            ],
        },
        "runtime": {"output_dir": str(tmp_path / "data")},
    }
    report = run_preflight(
        config,
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL TECHNOLOGIES,DP832,123,1.0", ":OUTPut? CH1": "OFF"})},
        env={"LG_OPERATOR": "Morgan", "LG_DUT_SERIAL": "DUT-001"},
        repo=tmp_path,
    )

    audit = build_audit(report)
    markdown = render_markdown(audit)

    assert audit.ready_for_client_demo
    assert audit.health_score >= 90
    assert audit.readiness_band == "Client-demo ready"
    assert "Long Game Diagnostic Audit" in markdown
    assert "Lab health score:" in markdown
    assert "No blocking risks detected" in markdown
    assert "Recommended 30-Day Improvement Plan" in markdown


def test_diagnostic_audit_flags_blockers_and_penalizes_score(tmp_path):
    config = {
        "rig": {
            "name": "bench-a",
            "dut_type": "pcba",
            "instruments": [{"name": "main_psu", "expected_model": "Rigol DP832", "checks": ["identity"]}],
        },
        "runtime": {"output_dir": str(tmp_path)},
    }
    report = run_preflight(
        config,
        instruments={"main_psu": FakeInstrument({"*IDN?": "KEYSIGHT,34970A,123,1.0"})},
        env={"LG_OPERATOR": "Morgan", "LG_DUT_SERIAL": "DUT-001"},
        repo=tmp_path,
    )

    audit = build_audit(report)
    markdown = render_markdown(audit)

    assert not audit.ready_for_client_demo
    assert audit.health_score < 100
    assert audit.blockers
    assert "REMEDIATION REQUIRED" in markdown
    assert "expected Rigol DP832" in markdown
