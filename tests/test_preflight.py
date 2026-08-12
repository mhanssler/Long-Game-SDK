from __future__ import annotations

import pytest

from long_game_sdk.sdk.preflight import checks as preflight_checks
from long_game_sdk.sdk.preflight import instrument_checks
from long_game_sdk.sdk.preflight.checks import PreflightConfigError, run_preflight
from long_game_sdk.sdk.preflight.instrument_checks import (
    InstrumentCheckOutcome,
    ParsedLiveIdentity,
)
from long_game_sdk.sdk.preflight.report import render_markdown
from long_game_sdk.sdk.preflight.safety_checks import (
    is_energy_controlling,
    is_energy_source,
    run_safety_checks,
)


class FakeInstrument:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def query(self, command: str) -> str:
        return self.responses[command]

    def write(self, command: str) -> None:
        self.responses[f"write:{command}"] = "ok"

    def close(self) -> None:
        return None


class TrackingInstrument(FakeInstrument):
    def __init__(self, responses: dict[str, str]):
        super().__init__(responses)
        self.queries: list[str] = []
        self.close_count = 0

    def query(self, command: str) -> str:
        self.queries.append(command)
        return super().query(command)

    def close(self) -> None:
        self.close_count += 1


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


def _valid_dp832_safety(**overrides):
    safety = {
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
    }
    safety.update(overrides)
    return safety


def _dl3021_config(safety: dict | None = None):
    return {
        "rig": {
            "name": "load-bench",
            "dut_type": "pcba",
            "instruments": [{
                "name": "electronic_load",
                "expected_manufacturer": "RIGOL",
                "expected_model": "DL3021",
                "expected_serial": "DL3-123",
                "checks": ["identity"],
                "safety": dict(safety or {}),
            }],
        },
        "runtime": {},
    }


def _valid_dl3021_safety(**overrides):
    safety = {
        "input_query": ":INPut?",
        "voltage_limit": 0.1,
        "voltage_query": ":MEASure:VOLTage?",
        "current_limit": 0.01,
        "current_query": ":MEASure:CURRent?",
        "power_limit": 0.001,
        "power_query": ":MEASure:POWer?",
    }
    safety.update(overrides)
    return safety


def _unbound_config(expected_manufacturer: str | None = None):
    spec = {"name": "discovered", "checks": ["identity"], "safety": {}}
    if expected_manufacturer is not None:
        spec["expected_manufacturer"] = expected_manufacturer
    return {"rig": {"instruments": [spec]}, "runtime": {}}


@pytest.mark.parametrize(
    ("idn", "expected_safety_names"),
    [
        ("RIGOL,DP832,DP8-001,1.0", {"output_disabled_on_start", "voltage_limit", "current_limit"}),
        ("RIGOL,DL3021,DL3-001,1.0", {"input_disabled_on_start", "voltage_limit", "current_limit", "power_limit"}),
    ],
)
@pytest.mark.parametrize("expected_manufacturer", [None, "RIGOL"])
def test_live_known_energy_controller_requires_complete_binding_and_model_evidence(
    tmp_path, idn, expected_safety_names, expected_manufacturer
) -> None:
    adapter = TrackingInstrument({"*IDN?": idn})

    report = run_preflight(
        _unbound_config(expected_manufacturer),
        instruments={"discovered": adapter},
        env={},
        repo=tmp_path,
    )

    assert not report.ready
    binding = next(item for item in report.results if item.name == "energy_controller_binding")
    assert binding.status == "fail"
    assert binding.severity == "high"
    assert "expected_manufacturer" in binding.message or "expected_model" in binding.message
    safety_failures = {
        item.name for item in report.results if item.category == "safety" and item.status == "fail"
    }
    assert safety_failures >= expected_safety_names
    assert adapter.queries == ["*IDN?"]
    assert adapter.close_count == 0


def test_malformed_live_idn_is_high_severity_failure_and_not_ready(tmp_path) -> None:
    adapter = TrackingInstrument({"*IDN?": "RIGOL,DP832"})

    report = run_preflight(
        _unbound_config(), instruments={"discovered": adapter}, env={}, repo=tmp_path
    )

    assert not report.ready
    identity = next(item for item in report.results if item.name == "identity")
    assert identity.status == "fail"
    assert identity.severity == "high"
    assert "malformed" in identity.message.lower()
    assert adapter.queries == ["*IDN?"]
    assert adapter.close_count == 0


@pytest.mark.parametrize(
    "idn",
    [
        "ACME\nEVIL,SCOPE-1,SN-9,1.0",
        "ACME,SCOPE\r1,SN-9,1.0",
        "ACME,SCOPE-1,SN\x009,1.0",
        "ACME,SCOPE-1,SN-9,1.0\x1fEVIL",
    ],
)
def test_control_characters_anywhere_in_live_idn_fail_closed(tmp_path, idn) -> None:
    adapter = TrackingInstrument({"*IDN?": idn})

    report = run_preflight(
        _unbound_config(), instruments={"discovered": adapter}, env={}, repo=tmp_path
    )

    assert not report.ready
    identity = next(item for item in report.results if item.name == "identity")
    assert identity.status == "fail"
    assert identity.severity == "high"
    assert "malformed" in identity.message.lower()
    assert not any(item.name == "identity" and item.status == "warn" for item in report.results)


def test_instrument_check_outcome_live_identities_are_immutable_snapshot() -> None:
    identity = ParsedLiveIdentity.parse("ACME,SCOPE-1,SN-9,1.0")
    source = {"scope": identity}

    outcome = InstrumentCheckOutcome((), source)
    source["replacement"] = identity

    assert dict(outcome.live_identities) == {"scope": identity}
    with pytest.raises(TypeError):
        outcome.live_identities["replacement"] = identity  # type: ignore[index]


def test_live_idn_query_failure_is_high_severity_and_not_ready(tmp_path) -> None:
    class FailingIdentityInstrument(TrackingInstrument):
        def query(self, command: str) -> str:
            self.queries.append(command)
            raise TimeoutError("IDN timeout")

    adapter = FailingIdentityInstrument({})

    report = run_preflight(
        _unbound_config(), instruments={"discovered": adapter}, env={}, repo=tmp_path
    )

    assert not report.ready
    reachable = next(item for item in report.results if item.name == "instrument_reachable")
    assert reachable.status == "fail"
    assert reachable.severity == "high"
    assert "IDN timeout" in reachable.message
    assert adapter.queries == ["*IDN?"]
    assert adapter.close_count == 0


def test_unknown_live_device_preserves_nonblocking_unconfigured_identity_semantics(tmp_path) -> None:
    adapter = TrackingInstrument({"*IDN?": "ACME,SCOPE-1,SN-9,1.0"})

    report = run_preflight(
        _unbound_config(), instruments={"discovered": adapter}, env={}, repo=tmp_path
    )

    assert report.ready
    assert any(item.name == "identity" and item.status == "warn" for item in report.results)
    assert not any(item.name == "energy_controller_binding" for item in report.results)
    assert adapter.queries == ["*IDN?"]
    assert adapter.close_count == 0


@pytest.mark.parametrize(
    ("config", "name", "responses"),
    [
        (
            _safety_config(_valid_dp832_safety(), checks=["identity"]),
            "main_psu",
            {
                "*IDN?": "RIGOL,DP832,SN1,1",
                **{
                    command: response
                    for channel in (1, 2, 3)
                    for command, response in (
                        (f":OUTPut? CH{channel}", "OFF"),
                        (f":SOURce{channel}:VOLTage?", "0 V"),
                        (f":SOURce{channel}:CURRent?", "0 A"),
                    )
                },
            },
        ),
        (
            _dl3021_config(_valid_dl3021_safety()),
            "electronic_load",
            {
                "*IDN?": "RIGOL,DL3021,DL3-123,1",
                ":INPut?": "OFF",
                ":MEASure:VOLTage?": "0 V",
                ":MEASure:CURRent?": "0 A",
                ":MEASure:POWer?": "0 W",
            },
        ),
    ],
)
def test_complete_live_known_controller_safe_path_queries_idn_once_and_reuses_adapter(
    tmp_path, config, name, responses
) -> None:
    adapter = TrackingInstrument(responses)

    report = run_preflight(config, instruments={name: adapter}, env={}, repo=tmp_path)

    assert report.ready
    assert adapter.queries.count("*IDN?") == 1
    assert len(adapter.queries) > 1
    assert adapter.close_count == 0


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


@pytest.mark.parametrize(
    ("config", "missing"),
    [
        (_safety_config({}, checks=["identity"]), "expected_manufacturer"),
        (_dl3021_config(), "expected_serial"),
    ],
)
def test_known_energy_controller_rejects_partial_identity(config, missing) -> None:
    spec = config["rig"]["instruments"][0]
    spec.pop(missing)

    with pytest.raises(PreflightConfigError, match="partial expected identity"):
        run_preflight(config, instruments={})


@pytest.mark.parametrize(
    ("config", "expected_idn", "safety_error"),
    [
        (_safety_config({}, checks=["identity"]), "RIGOL,DP832,SN1,1", "safety.channels"),
        (_dl3021_config(), "RIGOL,DL3021,DL3-123,1", "input-state, voltage, current, and power"),
    ],
)
def test_known_energy_controller_expected_idn_alias_cannot_bypass_safety(
    config, expected_idn, safety_error
) -> None:
    spec = config["rig"]["instruments"][0]
    for field in ("expected_manufacturer", "expected_model", "expected_serial"):
        spec.pop(field)
    spec["expected_idn"] = expected_idn

    with pytest.raises(PreflightConfigError, match=safety_error):
        run_preflight(config, instruments={})


@pytest.mark.parametrize(
    ("config", "expected_identity"),
    [
        (_safety_config(_valid_dp832_safety(), checks=["identity"]), "RIGOL,DP832,OTHER,1"),
        (_dl3021_config(_valid_dl3021_safety()), "RIGOL,DL3021,OTHER,1"),
    ],
)
def test_known_energy_controller_rejects_conflicting_expected_identity_alias(
    config, expected_identity
) -> None:
    config["rig"]["instruments"][0]["expected_identity"] = expected_identity

    with pytest.raises(PreflightConfigError, match="conflicting expected identity"):
        run_preflight(config, instruments={})


@pytest.mark.parametrize(
    ("expected_idn", "extra"),
    [
        ("RIGOL,DP832,OTHER,1", {}),
        ("RIGOL,DP832,SN1,1", {"expected_identity": "RIGOL,DP832,OTHER,1"}),
    ],
)
def test_expected_idn_conflicts_fail_before_resource_access(
    monkeypatch, expected_idn, extra
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
    spec = config["rig"]["instruments"][0]
    spec.update({"connection": "USB::DP832", "expected_idn": expected_idn, **extra})

    with pytest.raises(PreflightConfigError, match="conflicting expected identity"):
        run_preflight(config)

    assert opened == []


@pytest.mark.parametrize(
    "updates",
    [
        {"expected_identity": "RIGOL,DP832,sn1,1"},
        {
            "expected_identity": "RIGOL,DP832,SN1,1",
            "expected_idn": "rigol,dp832,sn1,1",
        },
    ],
)
def test_serial_case_only_identity_conflicts_fail_before_resource_access(
    monkeypatch, updates
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
    config["rig"]["instruments"][0].update(
        {"connection": "USB::DP832", **updates}
    )

    with pytest.raises(PreflightConfigError, match="conflicting expected identity"):
        run_preflight(config)

    assert opened == []


@pytest.mark.parametrize(
    ("live_idn", "expected_identity", "expected_status"),
    [
        ("acme,scope-1,SN1,1", "ACME,SCOPE-1,SN1,1", "pass"),
        ("ACME,SCOPE-1,sn1,1", "ACME,SCOPE-1,SN1,1", "fail"),
    ],
)
def test_full_identity_uses_case_insensitive_vendor_model_and_exact_serial(
    tmp_path, live_idn, expected_identity, expected_status
) -> None:
    config = _unbound_config()
    config["rig"]["instruments"][0]["expected_identity"] = expected_identity

    report = run_preflight(
        config,
        instruments={"discovered": FakeInstrument({"*IDN?": live_idn})},
        env={},
        repo=tmp_path,
    )

    identity = next(item for item in report.results if item.name == "identity")
    assert identity.status == expected_status


@pytest.mark.parametrize(
    ("live_idn", "expected_manufacturer", "expected_model", "expected_serial", "status"),
    [
        ("acme,scope-1,SN1,1", "ACME", "SCOPE-1", "SN1", "pass"),
        ("ACME,SCOPE-1,sn1,1", "ACME", "SCOPE-1", "SN1", "fail"),
    ],
)
def test_explicit_identity_fields_use_canonical_vendor_model_and_exact_serial(
    tmp_path, live_idn, expected_manufacturer, expected_model, expected_serial, status
) -> None:
    config = _unbound_config()
    config["rig"]["instruments"][0].update({
        "expected_manufacturer": expected_manufacturer,
        "expected_model": expected_model,
        "expected_serial": expected_serial,
    })

    report = run_preflight(
        config,
        instruments={"discovered": FakeInstrument({"*IDN?": live_idn})},
        env={},
        repo=tmp_path,
    )

    identity = next(item for item in report.results if item.name == "identity")
    assert identity.status == status


@pytest.mark.parametrize("expected_idn", ["RIGOL,DL3021", 3021])
def test_malformed_expected_idn_fails_before_resource_access(monkeypatch, expected_idn) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    config = _dl3021_config(_valid_dl3021_safety())
    spec = config["rig"]["instruments"][0]
    for field in ("expected_manufacturer", "expected_model", "expected_serial"):
        spec.pop(field)
    spec.update({"connection": "USB::DL3021", "expected_idn": expected_idn})

    with pytest.raises(PreflightConfigError, match="full IDN|string|manufacturer, model, and serial"):
        run_preflight(config)

    assert opened == []


@pytest.mark.parametrize(
    ("field", "valid_value"),
    [
        ("expected_identity", "ACME,SCOPE-1,SN1,1"),
        ("expected_idn", "ACME,SCOPE-1,SN1,1"),
        ("expected_manufacturer", "ACME"),
        ("expected_model", "SCOPE-1"),
        ("expected_serial", "SN1"),
    ],
)
@pytest.mark.parametrize("control", [*(chr(code) for code in range(32)), "\x7f"])
@pytest.mark.parametrize("edge", ["leading", "trailing"])
def test_configured_identity_controls_are_rejected_before_resource_access(
    monkeypatch, field, valid_value, control, edge
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        instrument_checks,
        "VisaInstrumentAdapter",
        lambda resource: opened.append(resource),
    )
    configured_value = (
        f"{control}{valid_value}" if edge == "leading" else f"{valid_value}{control}"
    )
    config = _unbound_config()
    config["rig"]["instruments"][0].update({
        "connection": "USB::SCOPE",
        field: configured_value,
    })

    with pytest.raises(PreflightConfigError, match="control character"):
        run_preflight(config)

    assert opened == []


@pytest.mark.parametrize(
    ("expected_serial", "binding_status"),
    [("sn1", "fail"), ("SN1", None)],
)
def test_safety_runtime_binding_is_case_insensitive_except_for_serial(
    expected_serial, binding_status
) -> None:
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
    spec = config["rig"]["instruments"][0]
    spec.update({
        "expected_manufacturer": "rigol",
        "expected_model": "dp832",
        "expected_serial": expected_serial,
    })
    live_identity = ParsedLiveIdentity.parse("RIGOL,DP832,SN1,1")

    results = run_safety_checks(
        config,
        instruments={},
        live_identities={"main_psu": live_identity},
    )

    binding = [item for item in results if item.name == "energy_controller_binding"]
    if binding_status is None:
        assert binding == []
    else:
        assert [item.status for item in binding] == [binding_status]
        assert "expected_serial" in binding[0].message
        assert "expected_manufacturer" not in binding[0].message
        assert "expected_model" not in binding[0].message


def test_expected_idn_is_canonicalized_for_downstream_identity_check(monkeypatch, tmp_path) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        preflight_checks,
        "run_instrument_checks",
        lambda config, instruments: captured.append(config)
        or instrument_checks.InstrumentCheckOutcome((), {}),
    )
    monkeypatch.setattr(
        preflight_checks,
        "run_safety_checks",
        lambda config, instruments, live_identities: [],
    )
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
    spec = config["rig"]["instruments"][0]
    for field in ("expected_manufacturer", "expected_model", "expected_serial"):
        spec.pop(field)
    spec["expected_idn"] = " RIGOL, DP832, SN1, 1 "

    run_preflight(config, instruments={}, env={}, repo=tmp_path)

    downstream = captured[0]["rig"]["instruments"][0]
    assert downstream["expected_identity"] == "RIGOL,DP832,SN1,1"
    assert "expected_idn" not in downstream
    assert (
        downstream["expected_manufacturer"],
        downstream["expected_model"],
        downstream["expected_serial"],
    ) == ("RIGOL", "DP832", "SN1")


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


def test_dp832_cannot_opt_out_of_energy_source_requirements_by_omitting_flag() -> None:
    config = _safety_config({}, checks=["identity"])

    with pytest.raises(PreflightConfigError, match="safety.channels readback evidence"):
        run_preflight(config, instruments={})


def test_fully_bound_dp832_without_flag_still_runs_channel_safety_checks(tmp_path) -> None:
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
    config["rig"]["instruments"][0]["safety"].pop("energy_source")
    responses = {"*IDN?": "RIGOL,DP832,SN1,1"}
    for channel in (1, 2, 3):
        responses[f":OUTPut? CH{channel}"] = "ON" if channel == 2 else "OFF"
        responses[f":SOURce{channel}:VOLTage?"] = "0 V"
        responses[f":SOURce{channel}:CURRent?"] = "0 A"

    report = run_preflight(
        config,
        instruments={"main_psu": FakeInstrument(responses)},
        env={},
        repo=tmp_path,
    )

    assert not report.ready
    assert any(
        item.name == "output_disabled_on_start"
        and item.status == "fail"
        and item.evidence.get("channel") == "CH2"
        for item in report.results
    )


def test_exact_rigol_dl3021_is_energy_controlling_but_not_an_energy_source() -> None:
    spec = _dl3021_config()["rig"]["instruments"][0]

    assert is_energy_controlling(spec)
    assert not is_energy_source(spec)


def test_dl3021_cannot_pass_identity_only_without_safe_input_and_live_readbacks() -> None:
    with pytest.raises(PreflightConfigError, match="DL3021.*input-state, voltage, current, and power"):
        run_preflight(_dl3021_config(), instruments={})


@pytest.mark.parametrize(
    "missing",
    [
        "input_query", "voltage_limit", "voltage_query", "current_limit",
        "current_query", "power_limit", "power_query",
    ],
)
def test_dl3021_requires_each_explicit_safe_input_and_live_readback_field(missing) -> None:
    safety = _valid_dl3021_safety()
    safety.pop(missing)

    with pytest.raises(PreflightConfigError, match=missing):
        run_preflight(_dl3021_config(safety), instruments={})


def test_identity_only_dl3021_still_runs_all_intrinsic_safety_checks(tmp_path) -> None:
    responses = {
        "*IDN?": "RIGOL,DL3021,DL3-123,1",
        ":INPut?": "ON",
        ":MEASure:VOLTage?": "0 V",
        ":MEASure:CURRent?": "0 A",
        ":MEASure:POWer?": "0 W",
    }

    report = run_preflight(
        _dl3021_config(_valid_dl3021_safety()),
        instruments={"electronic_load": FakeInstrument(responses)},
        env={},
        repo=tmp_path,
    )

    assert not report.ready
    safety_results = [item for item in report.results if item.category == "safety"]
    assert {item.name for item in safety_results} >= {
        "input_disabled_on_start", "voltage_limit", "current_limit", "power_limit",
    }
    assert next(item for item in safety_results if item.name == "input_disabled_on_start").status == "fail"
    for name in ("voltage_limit", "current_limit", "power_limit"):
        evidence = next(item.evidence for item in safety_results if item.name == name)
        assert {"query", "response", "actual"} <= evidence.keys()


@pytest.mark.parametrize("query_field", ["output_query", "voltage_query", "current_query"])
def test_energy_source_channel_queries_must_address_the_labeled_channel(query_field) -> None:
    config = _safety_config({
        "energy_source": True,
        "output_query": ":OUTPut? CH1",
        "voltage_limit": 5.5,
        "voltage_query": ":SOURce1:VOLTage?",
        "current_limit": 1.0,
        "current_query": ":SOURce1:CURRent?",
    })
    channels = config["rig"]["instruments"][0]["safety"]["channels"]
    channels[1][query_field] = channels[0][query_field]

    with pytest.raises(PreflightConfigError, match=rf"channel 'CH2'.*{query_field}.*address CH2"):
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
                    "expected_model": "GENERIC-PSU",
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
        instruments={"main_psu": FakeInstrument({"*IDN?": "RIGOL TECHNOLOGIES,GENERIC-PSU,123,1.0", ":OUTPut? CH1": "OFF"})},
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
            "instruments": [{"name": "main_psu", "expected_model": "MODEL832", "checks": ["identity"]}],
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
    assert "expected MODEL832" in markdown


def test_preflight_model_expectation_never_uses_idn_substring_matching(tmp_path):
    config = {
        "rig": {"instruments": [{
            "name": "scope", "expected_model": "MODEL832", "checks": ["identity"],
        }]},
        "runtime": {},
    }
    report = run_preflight(
        config,
        instruments={"scope": FakeInstrument({"*IDN?": "ACME,NOT-MODEL832,MODEL832,1"})},
        env={},
        repo=tmp_path,
    )

    assert any(item.name == "identity" and item.status == "fail" for item in report.results)


def test_preflight_malformed_idn_cannot_satisfy_expected_identity(tmp_path):
    config = {
        "rig": {"instruments": [{
            "name": "scope", "expected_model": "MODEL832", "checks": ["identity"],
        }]},
        "runtime": {},
    }
    report = run_preflight(
        config,
        instruments={"scope": FakeInstrument({"*IDN?": "MODEL832"})},
        env={},
        repo=tmp_path,
    )

    assert any(item.name == "identity" and item.status == "fail" for item in report.results)


def test_preflight_rejects_invalid_limits(tmp_path):
    for key, bad_value in (("voltage_limit", "CH1 <= 5.5 V"), ("voltage_limit", float("nan")),
                           ("current_limit", -1), ("current_limit", True),
                           ("current_limit", {"value": 1.0})):
        safety = _valid_dp832_safety(
            voltage_limit={"value": 5.5, "unit": "V"},
            current_limit={"value": 1.0, "unit": "A"},
        )
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
    config = _safety_config(
        _valid_dp832_safety(calibration_due=due),
        checks=["identity", "calibration_date"],
    )
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
    config = _safety_config(_valid_dp832_safety(), checks=["identity"])
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
