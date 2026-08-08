from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


class GuidedSetupError(ValueError):
    """Raised when a guided setup context cannot be built safely."""


_ALLOWED_ISOLATION = frozenset({"common_ground", "isolated_channel", "differential", "earth_referenced"})
_ALLOWED_POLARITY = frozenset({"positive", "negative", "signal", "reference"})


def _canonical_enum(mapping: Mapping[str, Any], field: str, allowed: frozenset[str], label: str) -> str:
    value = _required_text(mapping.get(field), f"{label} {field}")
    if value not in allowed:
        raise GuidedSetupError(f"{label} {field} {value!r} is unsupported; allowed values: {sorted(allowed)}")
    return value


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


def _reject_ambiguous_canonical_names(
    mapping: Mapping[Any, Any], namespace: str, label: str
) -> None:
    seen: dict[str, str] = {}
    for raw_name in mapping:
        if not isinstance(raw_name, str):
            continue
        normalized = raw_name.strip().casefold()
        previous = seen.get(normalized)
        if previous is not None and previous != raw_name:
            raise GuidedSetupError(
                f"ambiguous canonical {namespace} names in {label}: {previous!r} and {raw_name!r}"
            )
        seen[normalized] = raw_name


def _find_requirement(requirements_data: Mapping[str, Any], requirement_id: str) -> Mapping[str, Any]:
    for requirement in _list(requirements_data.get("requirements")):
        if isinstance(requirement, Mapping) and str(requirement.get("id")) == requirement_id:
            return requirement
    raise GuidedSetupError(f"requirement not found: {requirement_id}")


def _bench_summary(bench_data: Mapping[str, Any]) -> dict[str, Any]:
    bench = _mapping(bench_data.get("bench", {}), "bench")
    raw_instruments = bench_data.get("instruments")
    if not isinstance(raw_instruments, list) or not raw_instruments:
        raise GuidedSetupError("bench instruments must be a non-empty list")
    instruments: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw_instrument in enumerate(raw_instruments, start=1):
        instrument = _mapping(raw_instrument, f"bench instruments[{index}]")
        name = _required_text(instrument.get("name"), f"bench instruments[{index}] name")
        name_key = name.casefold()
        if name_key in names:
            raise GuidedSetupError(f"duplicate bench instrument name: {name!r}")
        names.add(name_key)
        terminals = instrument.get("terminals", {})
        if not isinstance(terminals, Mapping):
            raise GuidedSetupError(f"bench instrument {name!r} terminals must be a mapping")
        _reject_ambiguous_canonical_names(terminals, "terminal", f"bench instrument {name!r}")
        instruments.append(dict(instrument))
    return {
        "name": bench.get("name", "unnamed_bench"),
        "dut": bench.get("dut"),
        "evidence_root": bench.get("evidence_root", "reports/guided-tests"),
        "instruments": instruments,
        "connectors": [item for item in _list(bench_data.get("connectors")) if isinstance(item, Mapping)],
        "safety_controls": _list(bench_data.get("safety_controls")),
    }


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuidedSetupError(f"{label} requires a non-empty string")
    return value.strip()


def _validate_endpoint(
    endpoint: Mapping[str, Any],
    *,
    dut: Mapping[str, Any],
    fixture: Mapping[str, Any] | None,
    label: str,
) -> Mapping[str, Any]:
    net = _required_text(endpoint.get("net"), f"{label} net")
    connector = endpoint.get("connector")
    pin = endpoint.get("pin")
    test_point = endpoint.get("test_point")
    has_connector = connector is not None or pin is not None
    has_test_point = test_point is not None
    if has_connector == has_test_point:
        raise GuidedSetupError(f"{label} requires exactly one connector and pin or test_point")

    scope = endpoint.get("scope", "dut")
    if scope not in ("dut", "fixture"):
        raise GuidedSetupError(f"{label} scope must be 'dut' or 'fixture'")
    canonical = dut if scope == "dut" else fixture
    if canonical is None:
        raise GuidedSetupError(f"{label} references fixture but no canonical fixture mapping exists")

    if has_connector:
        connector_name = _required_text(connector, f"{label} connector")
        pin_name = _required_text(pin, f"{label} pin")
        connectors = _mapping(canonical.get("connectors", {}), f"schematic_context.{scope}.connectors")
        if connector_name not in connectors:
            raise GuidedSetupError(f"{label} connector {connector_name!r} has no canonical mapping")
        connector_data = _mapping(connectors[connector_name], f"canonical connector {connector_name}")
        pins = _mapping(connector_data.get("pins", {}), f"canonical connector {connector_name} pins")
        pin_data = pins.get(pin_name)
        if not isinstance(pin_data, Mapping):
            raise GuidedSetupError(f"{label} pin {connector_name}.{pin_name} has no canonical mapping")
        canonical_net = _required_text(pin_data.get("net"), f"canonical pin {connector_name}.{pin_name} net")
        endpoint_data = pin_data
    else:
        test_point_name = _required_text(test_point, f"{label} test point")
        test_points = _mapping(canonical.get("test_points", {}), f"schematic_context.{scope}.test_points")
        point_data = test_points.get(test_point_name)
        if not isinstance(point_data, Mapping):
            raise GuidedSetupError(f"{label} test point {test_point_name!r} has no canonical mapping")
        canonical_net = _required_text(point_data.get("net"), f"canonical test point {test_point_name} net")
        endpoint_data = point_data
    if net != canonical_net:
        raise GuidedSetupError(f"{label} net {net!r} contradicts canonical net {canonical_net!r}")
    return endpoint_data


def _finite_limit(mapping: Mapping[str, Any], field: str, label: str) -> float:
    value = mapping.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise GuidedSetupError(f"{label} requires finite non-negative numeric {field}")
    try:
        finite_value = float(value)
    except (OverflowError, ValueError):
        raise GuidedSetupError(
            f"{label} requires finite non-negative numeric {field}"
        ) from None
    if not math.isfinite(finite_value) or finite_value < 0:
        raise GuidedSetupError(f"{label} requires finite non-negative numeric {field}")
    return finite_value


def _canonical_terminal(instrument: Mapping[str, Any], terminal_name: str, label: str) -> Mapping[str, Any]:
    terminals = _mapping(instrument.get("terminals", {}), f"bench instrument {instrument.get('name')} terminals")
    terminal = terminals.get(terminal_name)
    if not isinstance(terminal, Mapping):
        raise GuidedSetupError(f"{label} {terminal_name!r} has no canonical bench terminal definition")
    return terminal


def _endpoint_key(endpoint: Mapping[str, Any]) -> tuple[str, str, str]:
    scope = str(endpoint.get("scope", "dut")).casefold()
    if endpoint.get("test_point") is not None:
        return scope, "test_point", str(endpoint["test_point"]).casefold()
    return scope, str(endpoint.get("connector", "")).casefold(), str(endpoint.get("pin", "")).casefold()


def _validate_canonical_endpoint_names(canonical: Mapping[str, Any], scope: str) -> None:
    connectors = _mapping(
        canonical.get("connectors", {}), f"schematic_context.{scope}.connectors"
    )
    _reject_ambiguous_canonical_names(
        connectors, "connector", f"schematic_context.{scope}.connectors"
    )
    for connector_name, raw_connector in connectors.items():
        connector = _mapping(
            raw_connector, f"schematic_context.{scope}.connector {connector_name!r}"
        )
        pins = _mapping(
            connector.get("pins", {}),
            f"schematic_context.{scope}.connector {connector_name!r} pins",
        )
        _reject_ambiguous_canonical_names(
            pins, "pin", f"schematic_context.{scope}.connector {connector_name!r} pins"
        )
    test_points = _mapping(
        canonical.get("test_points", {}), f"schematic_context.{scope}.test_points"
    )
    _reject_ambiguous_canonical_names(
        test_points, "test_point", f"schematic_context.{scope}.test_points"
    )


def _schematic_summary(
    schematic_data: Mapping[str, Any], *, instruments: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any]:
    context = _mapping(schematic_data.get("schematic_context"), "schematic_context")
    dut = _mapping(context.get("dut"), "schematic_context.dut")
    fixture_raw = context.get("fixture")
    fixture = _mapping(fixture_raw, "schematic_context.fixture") if fixture_raw is not None else None
    if not dut.get("connectors") and not dut.get("test_points"):
        raise GuidedSetupError("schematic context must include connectors or test_points")
    _validate_canonical_endpoint_names(dut, "dut")
    if fixture is not None:
        _validate_canonical_endpoint_names(fixture, "fixture")
    revision = str(context.get("revision", "")).strip()
    raw_connections = context.get("connections")
    if "connections" in context and not isinstance(raw_connections, list):
        raise GuidedSetupError("schematic_context connections must be a list")
    connections = _list(raw_connections)
    source_terminals: set[tuple[str, str]] = set()
    reference_terminals: set[tuple[str, str]] = set()
    destination_endpoints: set[tuple[str, str, str]] = set()
    reference_endpoints: set[tuple[str, str, str]] = set()
    for index, raw_connection in enumerate(connections, start=1):
        connection = _mapping(raw_connection, f"schematic_context.connections[{index}]")
        if connection.get("approved") is not True:
            raise GuidedSetupError(f"connection {index} must be explicitly approved")
        source_revision = str(connection.get("source_revision", "")).strip()
        if not revision or source_revision != revision:
            raise GuidedSetupError(
                f"connection {index} source_revision must match schematic_context.revision"
            )
        for field in ("instrument", "terminal", "signal_type", "isolation"):
            _required_text(connection.get(field), f"connection {index} {field}")
        instrument_name = str(connection["instrument"])
        instrument = instruments.get(instrument_name)
        if instrument is None:
            raise GuidedSetupError(
                f"connection {index} instrument {instrument_name!r} is not in bench inventory"
            )
        if not isinstance(instrument.get("energizing"), bool):
            raise GuidedSetupError(
                f"bench instrument {instrument_name!r} requires explicit boolean energizing classification"
            )
        source_terminal_name = str(connection["terminal"])
        source_terminal = _canonical_terminal(
            instrument, source_terminal_name, f"connection {index} terminal"
        )
        reference_instrument_name = _required_text(
            connection.get("reference", {}).get("instrument", instrument_name),
            f"connection {index} reference instrument",
        ) if isinstance(connection.get("reference"), Mapping) else instrument_name
        reference_instrument = instruments.get(reference_instrument_name)
        if reference_instrument is None:
            raise GuidedSetupError(
                f"connection {index} reference instrument {reference_instrument_name!r} is not in bench inventory"
            )
        if not isinstance(reference_instrument.get("energizing"), bool):
            raise GuidedSetupError(
                f"bench instrument {reference_instrument_name!r} requires explicit boolean energizing classification"
            )
        destination = _mapping(connection.get("destination"), f"connection {index} destination")
        reference = _mapping(connection.get("reference"), f"connection {index} reference")
        reference_terminal_name = _required_text(
            reference.get("instrument_terminal"), f"connection {index} reference instrument_terminal"
        )
        reference_terminal = _canonical_terminal(
            reference_instrument, reference_terminal_name, f"connection {index} reference terminal"
        )
        destination_data = _validate_endpoint(
            destination, dut=dut, fixture=fixture, label=f"connection {index} destination"
        )
        reference_data = _validate_endpoint(
            reference, dut=dut, fixture=fixture, label=f"connection {index} reference"
        )

        source_key = (instrument_name.casefold(), source_terminal_name.casefold())
        reference_key = (reference_instrument_name.casefold(), reference_terminal_name.casefold())
        destination_key = _endpoint_key(destination)
        reference_endpoint_key = _endpoint_key(reference)
        if source_key in source_terminals:
            raise GuidedSetupError(f"connection {index} reuses source terminal {instrument_name}.{source_terminal_name}")
        if reference_key in reference_terminals:
            raise GuidedSetupError(
                f"connection {index} reuses reference terminal {reference_instrument_name}.{reference_terminal_name}"
            )
        if destination_key in destination_endpoints:
            raise GuidedSetupError(f"connection {index} reuses a destination endpoint")
        if reference_endpoint_key in reference_endpoints:
            raise GuidedSetupError(f"connection {index} reuses a reference endpoint")
        if source_key in reference_terminals or reference_key in source_terminals or source_key == reference_key:
            raise GuidedSetupError(f"connection {index} has conflicting source/reference terminal topology")
        if (
            destination_key in reference_endpoints
            or reference_endpoint_key in destination_endpoints
            or destination_key == reference_endpoint_key
        ):
            raise GuidedSetupError(f"connection {index} has conflicting destination/reference endpoint topology")
        source_terminals.add(source_key)
        reference_terminals.add(reference_key)
        destination_endpoints.add(destination_key)
        reference_endpoints.add(reference_endpoint_key)

        isolation = _canonical_enum(connection, "isolation", _ALLOWED_ISOLATION, f"connection {index}")
        source_isolation = _canonical_enum(
            source_terminal, "isolation", _ALLOWED_ISOLATION, f"connection {index} source terminal"
        )
        destination_isolation = _canonical_enum(
            destination_data, "isolation", _ALLOWED_ISOLATION, f"connection {index} destination endpoint"
        )
        reference_terminal_isolation = _canonical_enum(
            reference_terminal, "isolation", _ALLOWED_ISOLATION, f"connection {index} reference terminal"
        )
        reference_endpoint_isolation = _canonical_enum(
            reference_data, "isolation", _ALLOWED_ISOLATION, f"connection {index} reference endpoint"
        )
        if any(value != isolation for value in (
            source_isolation, destination_isolation,
            reference_terminal_isolation, reference_endpoint_isolation,
        )):
            raise GuidedSetupError(f"connection {index} isolation is incompatible with canonical wiring metadata")

        source_polarity = _canonical_enum(
            source_terminal, "polarity", _ALLOWED_POLARITY, f"connection {index} source terminal"
        )
        destination_polarity = _canonical_enum(
            destination_data, "polarity", _ALLOWED_POLARITY, f"connection {index} destination endpoint"
        )
        reference_polarity = _canonical_enum(
            reference_terminal, "polarity", _ALLOWED_POLARITY, f"connection {index} reference terminal"
        )
        endpoint_reference_polarity = _canonical_enum(
            reference_data, "polarity", _ALLOWED_POLARITY, f"connection {index} reference endpoint"
        )
        if source_polarity != destination_polarity or reference_polarity != endpoint_reference_polarity:
            raise GuidedSetupError(f"connection {index} polarity is incompatible with canonical wiring metadata")

        signal_type = str(connection["signal_type"])
        for canonical, label in (
            (source_terminal, "source terminal"),
            (destination_data, "destination endpoint"),
        ):
            canonical_signal = _required_text(
                canonical.get("signal_type"), f"connection {index} canonical {label} signal_type"
            )
            if signal_type != canonical_signal:
                raise GuidedSetupError(
                    f"connection {index} signal_type {signal_type!r} contradicts canonical {label} "
                    f"signal_type {canonical_signal!r}"
                )
        reference_signal = _required_text(
            reference_terminal.get("signal_type"),
            f"connection {index} canonical reference terminal signal_type",
        )
        endpoint_reference_signal = _required_text(
            reference_data.get("signal_type"),
            f"connection {index} canonical reference endpoint signal_type",
        )
        if reference_signal != endpoint_reference_signal:
            raise GuidedSetupError(f"connection {index} reference terminal signal_type contradicts reference endpoint")
        for field in ("max_voltage_v", "max_current_a"):
            connection_limit = _finite_limit(connection, field, f"connection {index}")
            source_limit = _finite_limit(source_terminal, field, f"connection {index} source terminal")
            destination_limit = _finite_limit(destination_data, field, f"connection {index} destination endpoint")
            reference_terminal_limit = _finite_limit(
                reference_terminal, field, f"connection {index} reference terminal"
            )
            reference_endpoint_limit = _finite_limit(
                reference_data, field, f"connection {index} reference endpoint"
            )
            restrictive_limit = min(
                source_limit,
                destination_limit,
                reference_terminal_limit,
                reference_endpoint_limit,
            )
            if connection_limit > restrictive_limit:
                raise GuidedSetupError(
                    f"connection {index} {field} {connection_limit} exceeds the most restrictive canonical "
                    f"limit {restrictive_limit}"
                )
    summary: dict[str, Any] = {
        "revision": revision,
        "dut": dict(dut),
        "connections": [dict(item) for item in connections],
    }
    if fixture is not None:
        summary["fixture"] = dict(fixture)
    return summary


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
    instruments = {
        _required_text(item.get("name"), "bench instrument name"): item
        for item in bench["instruments"]
    }
    schematic = _schematic_summary(schematic_data, instruments=instruments)
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
                "Run lg-safe with the expected bench/preflight config before wiring changes.",
                "Confirm schematic-to-harness mapping.",
                "Confirm preflight passes.",
                "Confirm operator wiring before energizing outputs.",
            ],
            "required_after_execution": [
                "Run lg-safe with the expected bench/preflight config after every test, including failures."
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


def _format_endpoint(endpoint: Mapping[str, Any]) -> str:
    scope = "DUT" if endpoint.get("scope", "dut") == "dut" else "fixture"
    if endpoint.get("test_point"):
        return f"{scope} test point {endpoint['test_point']} / net {endpoint['net']}"
    return f"{scope} {endpoint['connector']} pin {endpoint.get('pin')} / net {endpoint['net']}"


def _connection_steps(pack: Mapping[str, Any]) -> list[str]:
    schematic = _mapping(pack.get("schematic", {}), "schematic")
    revision = str(schematic.get("revision", "")).strip()
    connections = _list(schematic.get("connections"))
    if not connections:
        return ["STOP: No approved explicit connection records were provided; do not infer wiring from net names."]

    steps: list[str] = []
    for index, raw_connection in enumerate(connections, start=1):
        connection = _mapping(raw_connection, f"connection {index}")
        destination = _mapping(connection.get("destination"), f"connection {index} destination")
        reference = _mapping(connection.get("reference"), f"connection {index} reference")
        destination_text = _format_endpoint(destination)
        reference_text = _format_endpoint(reference)
        reference_instrument = reference.get("instrument", connection["instrument"])
        if reference_instrument == connection["instrument"]:
            reference_source = f"reference {reference['instrument_terminal']}"
        else:
            reference_source = (
                f"reference {reference_instrument} terminal {reference['instrument_terminal']}"
            )
        steps.append(
            f"Connect {connection['instrument']} terminal {connection['terminal']} to {destination_text}; "
            f"connect {reference_source} to {reference_text}. "
            f"Limits: {connection['max_voltage_v']} V, {connection['max_current_a']} A; "
            f"signal {connection['signal_type']}; isolation {connection['isolation']}; "
            f"source revision {revision}."
        )
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
        "- Run `lg-safe <bench/preflight-config.yaml>` before wiring changes.",
        "- Do not energize outputs until preflight and wiring confirmation pass.",
        "- MANDATORY FINAL GATE: Run `lg-safe <bench/preflight-config.yaml>` after every test, including failures.",
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
