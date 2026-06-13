from __future__ import annotations

from long_game_sdk.sdk.preflight.checks import run_preflight
from long_game_sdk.sdk.preflight.report import render_markdown


class FakeInstrument:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def query(self, command: str) -> str:
        return self.responses[command]

    def write(self, command: str) -> None:
        self.responses[f"write:{command}"] = "ok"


def test_preflight_passes_with_fake_rigol_psu(tmp_path):
    config = {
        "rig": {
            "name": "bench-a",
            "dut_type": "pcba",
            "instruments": [
                {
                    "name": "main_psu",
                    "expected_model": "Rigol DP832",
                    "checks": ["identity", "output_disabled_on_start", "voltage_limit", "current_limit", "calibration_date"],
                    "safety": {
                        "calibration_due": "2027-06-01",
                        "output_query": ":OUTPut? CH1",
                        "voltage_limit": "CH1 <= 5.5 V",
                        "current_limit": "CH1 <= 1.0 A",
                    },
                }
            ],
        },
        "runtime": {"output_dir": str(tmp_path / "data"), "required_env": ["LG_OPERATOR", "LG_DUT_SERIAL"]},
    }
    report = run_preflight(
        config,
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL TECHNOLOGIES,DP832,123,1.0", ":OUTPut? CH1": "OFF"})},
        env={"LG_OPERATOR": "Morgan", "LG_DUT_SERIAL": "DUT-001"},
        repo=tmp_path,
    )

    assert report.ready
    counts = report.summary_counts
    assert counts["fail"] == 0
    assert any(item.name == "identity" and item.status == "pass" for item in report.results)


def test_preflight_fails_on_identity_mismatch(tmp_path):
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

    assert not report.ready
    markdown = render_markdown(report)
    assert "NOT READY" in markdown
    assert "expected Rigol DP832" in markdown
