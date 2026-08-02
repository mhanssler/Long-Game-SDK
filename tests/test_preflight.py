from __future__ import annotations

import pytest

from long_game_sdk.sdk.preflight import instrument_checks
from long_game_sdk.sdk.preflight.checks import PreflightConfigError, run_preflight
from long_game_sdk.sdk.preflight.report import render_markdown


class FakeInstrument:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def query(self, command: str) -> str:
        return self.responses[command]

    def write(self, command: str) -> None:
        self.responses[f"write:{command}"] = "ok"

    def close(self) -> None:
        return None


def _safety_config(safety: dict, checks: list[str] | None = None):
    safety = dict(safety)
    if safety.get("energy_source") and "channels" not in safety:
        channel_template = {
            key: safety.get(key)
            for key in (
                "output_query", "voltage_limit", "voltage_query",
                "current_limit", "current_query",
            )
        }
        safety["channels"] = [
            {
                "channel": f"CH{number}",
                **{
                    key: value.replace("CH1", f"CH{number}").replace("ce1", f"ce{number}")
                    if isinstance(value, str) else value
                    for key, value in channel_template.items()
                },
            }
            for number in (1, 2, 3)
        ]
    return {
        "rig": {
            "name": "bench-a",
            "dut_type": "pcba",
            "instruments": [{
                "name": "main_psu",
                "expected_manufacturer": "RIGOL",
                "expected_model": "DP832",
                "expected_serial": "SN1",
                "checks": checks or ["identity", "output_disabled_on_start", "voltage_limit", "current_limit"],
                "safety": safety,
            }],
        },
        "runtime": {},
    }


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"rig": {}},
        {"rig": {"instruments": "main_psu"}},
        {"rig": {"instruments": []}},
        {"rig": {"instruments": ["main_psu"]}},
        {"rig": {"instruments": [{"name": "main_psu"}]}},
        {"rig": {"instruments": [{"name": "main_psu", "checks": []}]}},
        {"rig": {"instruments": [{"name": "main_psu", "checks": "identity"}]}},
        {"rig": {"instruments": [{"name": "main_psu", "checks": ["invented_check"]}]}},
    ],
)
def test_preflight_rejects_missing_empty_or_malformed_inventory_and_checks(config) -> None:
    with pytest.raises(PreflightConfigError):
        run_preflight(config, instruments={})


def test_preflight_rejects_duplicate_names_before_opening_resources(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    spec = {"name": "psu", "connection": "USB::PSU", "checks": ["identity"]}

    with pytest.raises(PreflightConfigError, match="duplicate instrument name"):
        run_preflight({"rig": {"instruments": [spec, dict(spec)]}})

    assert opened == []


def test_preflight_rejects_duplicate_connections_before_opening_resources(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    instruments = [
        {"name": "psu-a", "connection": "USB::SHARED", "checks": ["identity"]},
        {"name": "psu-b", "connection": "USB::SHARED", "checks": ["identity"]},
    ]

    with pytest.raises(PreflightConfigError, match="duplicate instrument connection"):
        run_preflight({"rig": {"instruments": instruments}})

    assert opened == []


def test_preflight_rejects_duplicate_checks() -> None:
    config = _safety_config({}, checks=["identity", "identity"])

    with pytest.raises(PreflightConfigError, match="duplicate checks"):
        run_preflight(config, instruments={})


def test_energy_source_requires_exact_expected_identity_fields() -> None:
    for missing in ("expected_manufacturer", "expected_model", "expected_serial"):
        config = _safety_config({"energy_source": True})
        config["rig"]["instruments"][0].pop(missing)
        with pytest.raises(PreflightConfigError, match=missing):
            run_preflight(config, instruments={})


def test_dp832_energy_source_requires_explicit_live_evidence_for_all_channels() -> None:
    config = _safety_config({
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
    })
    config["rig"]["instruments"][0]["safety"]["channels"].pop()

    with pytest.raises(PreflightConfigError, match="CH1, CH2, and CH3"):
        run_preflight(config, instruments={})


def test_energy_source_channel_results_include_explicit_live_evidence(tmp_path) -> None:
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
    }
    responses = {"*IDN?": "RIGOL,DP832,SN1,1"}
    for channel in (1, 2, 3):
        responses[f":OUTPut? CH{channel}"] = "OFF"
        responses[f":SOURce{channel}:VOLTage?"] = "0 V"
        responses[f":SOURce{channel}:CURRent?"] = "0 A"

    report = run_preflight(
        _safety_config(safety), instruments={"main_psu": FakeInstrument(responses)},
        env={}, repo=tmp_path,
    )

    safety_results = [item for item in report.results if item.category == "safety"]
    assert {item.evidence.get("channel") for item in safety_results} >= {"CH1", "CH2", "CH3"}
    for item in safety_results:
        if item.name in {"voltage_limit", "current_limit"}:
            assert {"query", "response", "actual"} <= item.evidence.keys()


def test_preflight_passes_with_fake_rigol_psu(tmp_path):
    config = {
        "rig": {
            "name": "bench-a",
            "dut_type": "pcba",
            "instruments": [
                {
                    "name": "main_psu",
                    "expected_model": "DP832",
                    "checks": ["identity", "output_disabled_on_start", "voltage_limit", "current_limit", "calibration_date"],
                    "safety": {
                        "calibration_due": "2027-06-01",
                        "output_query": ":OUTPut? CH1",
                        "voltage_limit": {"value": 5.5, "unit": "V"},
                        "voltage_setpoint": {"value": 5.0, "unit": "V"},
                        "current_limit": {"value": 1.0, "unit": "A"},
                        "current_setpoint": {"value": 0.75, "unit": "A"},
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
            "instruments": [{"name": "main_psu", "expected_model": "DP832", "checks": ["identity"]}],
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
    assert "expected DP832" in markdown


def test_preflight_model_expectation_never_uses_idn_substring_matching(tmp_path):
    config = {
        "rig": {"instruments": [{
            "name": "scope", "expected_model": "DP832", "checks": ["identity"],
        }]},
        "runtime": {},
    }
    report = run_preflight(
        config,
        instruments={"scope": FakeInstrument({"*IDN?": "ACME,NOT-DP832,DP832,1"})},
        env={},
        repo=tmp_path,
    )

    assert any(item.name == "identity" and item.status == "fail" for item in report.results)


def test_preflight_malformed_idn_cannot_satisfy_expected_identity(tmp_path):
    config = {
        "rig": {"instruments": [{
            "name": "scope", "expected_model": "DP832", "checks": ["identity"],
        }]},
        "runtime": {},
    }
    report = run_preflight(
        config,
        instruments={"scope": FakeInstrument({"*IDN?": "DP832"})},
        env={},
        repo=tmp_path,
    )

    assert any(item.name == "identity" and item.status == "fail" for item in report.results)


def test_preflight_rejects_invalid_limits(tmp_path):
    for key, bad_value in (("voltage_limit", "CH1 <= 5.5 V"), ("voltage_limit", float("nan")),
                           ("current_limit", -1), ("current_limit", True),
                           ("current_limit", {"value": 1.0})):
        safety = {"output_query": ":OUTPut? CH1", "voltage_limit": {"value": 5.5, "unit": "V"},
                  "current_limit": {"value": 1.0, "unit": "A"}}
        safety[key] = bad_value
        report = run_preflight(_safety_config(safety), instruments={"main_psu": FakeInstrument(
            {"*IDN?": "RIGOL,DP832,SN1,1", ":OUTPut? CH1": "OFF"})}, env={}, repo=tmp_path)
        assert not report.ready
        assert any(item.name == key and item.status == "fail" for item in report.results)


def test_preflight_compares_limits_to_live_queries_and_ignores_static_actuals(tmp_path):
    safety = {"energy_source": True, "output_query": ":OUTPut? CH1", "voltage_limit": 5.5,
              "voltage_query": ":SOURce1:VOLTage?", "current_limit": {"value": 1.0, "unit": "A"},
              "current_setpoint": 0.5, "current_actual": 0.5}
    report = run_preflight(_safety_config(safety), instruments={"main_psu": FakeInstrument({
        "*IDN?": "RIGOL,DP832,SN1,1", ":OUTPut? CH1": "0", ":SOURce1:VOLTage?": "5.000 V",
    })}, env={}, repo=tmp_path)
    assert not report.ready
    assert any(item.name == "voltage_limit" and item.status == "pass" for item in report.results)
    current = next(item for item in report.results if item.name == "current_limit")
    assert current.status == "fail"
    assert "live" in current.message


@pytest.mark.parametrize(
    "field,value",
    [
        ("output_query", ":OUTPut ON"),
        ("output_query", ":OUTPut? CH1;:OUTPut ON"),
        ("voltage_query", ":SOURce1:VOLTage 5;*OPC?"),
        ("current_query", "SYST:ERR?"),
    ],
)
def test_preflight_rejects_non_read_only_or_untrusted_queries(tmp_path, field, value) -> None:
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
        field: value,
    }
    report = run_preflight(
        _safety_config(safety),
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})},
        env={},
        repo=tmp_path,
    )
    assert not report.ready
    assert any("query" in item.message and item.status == "fail" for item in report.results)


@pytest.mark.parametrize("due", ["not-a-date", "2020-01-01", "2027-02-30"])
def test_preflight_calibration_due_must_be_valid_and_not_expired(tmp_path, due) -> None:
    config = _safety_config({"calibration_due": due}, checks=["identity", "calibration_date"])
    report = run_preflight(
        config,
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})},
        env={},
        repo=tmp_path,
    )
    assert not report.ready
    assert any(item.name == "calibration_date" and item.status == "fail" for item in report.results)


def test_energy_source_identity_matches_exact_manufacturer_model_and_serial(tmp_path) -> None:
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
    }
    report = run_preflight(
        _safety_config(safety),
        instruments={"main_psu": FakeInstrument({
            "*IDN?": "NOT-RIGOL,DP832,SN1,1",
            ":OUTPut? CH1": "OFF",
            ":SOURce1:VOLTage?": "5 V",
            ":SOURce1:CURRent?": "0.5 A",
        })},
        env={},
        repo=tmp_path,
    )
    assert not report.ready
    assert any(item.name == "identity" and item.status == "fail" for item in report.results)


def test_preflight_energy_source_limits_fail_without_setpoint_or_readback_evidence(tmp_path):
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": {"value": 5.5, "unit": "V"},
        "current_limit": {"value": 1.0, "unit": "A"},
    }
    report = run_preflight(
        _safety_config(safety),
        instruments={"main_psu": FakeInstrument({
            "*IDN?": "RIGOL,DP832,SN1,1",
            ":OUTPut? CH1": "OFF",
        })},
        env={},
        repo=tmp_path,
    )

    assert not report.ready
    for key in ("voltage_limit", "current_limit"):
        check = next(item for item in report.results if item.name == key)
        assert check.status == "fail"
        assert "evidence" in check.message


def test_preflight_energy_source_requires_both_limit_checks_even_when_omitted(tmp_path):
    safety = {"energy_source": True, "output_query": ":OUTPut? CH1"}
    report = run_preflight(
        _safety_config(safety, checks=["identity"]),
        instruments={"main_psu": FakeInstrument({
            "*IDN?": "RIGOL,DP832,SN1,1",
            ":OUTPut? CH1": "OFF",
        })},
        env={},
        repo=tmp_path,
    )

    assert not report.ready
    assert {
        item.name for item in report.results if item.status == "fail"
    } >= {"voltage_limit", "current_limit"}


def test_preflight_energy_source_missing_or_failed_output_readback_fails(tmp_path):
    base = {"energy_source": True, "voltage_limit": 5.5, "current_limit": 1.0}
    missing = run_preflight(_safety_config(base), instruments={"main_psu": FakeInstrument(
        {"*IDN?": "RIGOL,DP832,SN1,1"})}, env={}, repo=tmp_path)
    assert not missing.ready
    assert any(item.name == "output_disabled_on_start" and item.status == "fail" for item in missing.results)
    errored = run_preflight(_safety_config(dict(base, output_query=":OUTPut? CH1")),
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL,DP832,SN1,1"})}, env={}, repo=tmp_path)
    assert not errored.ready
    assert any(item.name == "output_disabled_on_start" and item.status == "fail" for item in errored.results)


def test_preflight_expected_serial_binding_must_match(tmp_path):
    config = _safety_config({}, checks=["identity"])
    config["rig"]["instruments"][0]["expected_serial"] = "BOUND-123"
    report = run_preflight(config, instruments={"main_psu": FakeInstrument(
        {"*IDN?": "RIGOL,DP832,OTHER-999,1"})}, env={}, repo=tmp_path)
    assert not report.ready
    assert any(item.name == "identity" and item.status == "fail" and "BOUND-123" in item.message for item in report.results)


def test_production_preflight_reuses_live_adapter_for_safety_and_closes_it_last(
    monkeypatch, tmp_path
):
    events: list[str] = []

    class ProductionAdapter:
        def __init__(self, resource: str):
            events.append(f"open:{resource}")

        def query(self, command: str) -> str:
            events.append(f"query:{command}")
            return {
                "*IDN?": "RIGOL,DP832,SN1,1",
                    ":OUTPut? CH1": "OFF", ":OUTPut? CH2": "OFF", ":OUTPut? CH3": "OFF",
                    ":SOURce1:VOLTage?": "5.0 V", ":SOURce2:VOLTage?": "0 V",
                    ":SOURce3:VOLTage?": "0 V",
                    ":SOURce1:CURRent?": "0.5 A", ":SOURce2:CURRent?": "0 A",
                    ":SOURce3:CURRent?": "0 A",
            }[command]

        def write(self, command: str) -> None:
            events.append(f"write:{command}")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(instrument_checks, "VisaInstrumentAdapter", ProductionAdapter)
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": {"value": 5.5, "unit": "V"},
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": {"value": 1.0, "unit": "A"},
        "current_query": ":SOURce1:CURRent?",
    }
    config = _safety_config(safety)
    config["rig"]["instruments"][0]["connection"] = "USB::DP832"

    report = run_preflight(config, env={}, repo=tmp_path)

    assert report.ready
    assert events == [
        "open:USB::DP832",
        "query:*IDN?",
        "query::OUTPut? CH1",
        "query::SOURce1:VOLTage?",
        "query::SOURce1:CURRent?",
        "query::OUTPut? CH2",
        "query::SOURce2:VOLTage?",
        "query::SOURce2:CURRent?",
        "query::OUTPut? CH3",
        "query::SOURce3:VOLTage?",
        "query::SOURce3:CURRent?",
        "close",
    ]
