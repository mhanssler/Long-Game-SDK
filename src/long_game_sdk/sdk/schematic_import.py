from __future__ import annotations

import argparse
import csv
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml


class SchematicImportError(ValueError):
    """Raised when a schematic/netlist import cannot produce safe context."""


MAX_INPUT_BYTES = 1_000_000


def _check_input_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SchematicImportError(f"cannot read schematic input {path}: {exc}") from exc
    if size > MAX_INPUT_BYTES:
        raise SchematicImportError(f"schematic input exceeds {MAX_INPUT_BYTES} bytes: {path}")


def _require_records(context: Mapping[str, Any]) -> None:
    dut = _dut(context)
    if not dut.get("connectors") and not dut.get("test_points"):
        raise SchematicImportError("schematic import produced no connector or test-point records")


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(row: Mapping[str, Any], *names: str) -> str:
    normalized = {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items()}
    for name in names:
        key = name.strip().lower().replace(" ", "_")
        if key in normalized and _clean(normalized[key]):
            return _clean(normalized[key])
    return ""


def _number(value: str, label: str) -> int | float | None:
    if value == "":
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise SchematicImportError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise SchematicImportError(f"{label} must be finite and non-negative")
    return int(parsed) if parsed.is_integer() else parsed


def _base_context(path: str | Path, dut_name: str = "dut") -> dict[str, Any]:
    return {
        "schematic_context": {
            "dut": {
                "name": dut_name,
                "source_files": [Path(path).as_posix()],
                "connectors": {},
                "test_points": {},
            }
        }
    }


def _dut(context: Mapping[str, Any]) -> dict[str, Any]:
    return context["schematic_context"]["dut"]  # type: ignore[index,return-value]


def _add_connector_pin(
    context: dict[str, Any],
    connector: str,
    pin: str,
    net: str,
    *,
    description: str = "",
    signal_type: str = "",
    max_voltage_v: int | float | None = None,
    max_current_a: int | float | None = None,
) -> None:
    if not connector or not pin or not net:
        raise SchematicImportError("connector pin entries require connector, pin, and net")
    connectors = _dut(context).setdefault("connectors", {})
    connector_entry = connectors.setdefault(connector, {"pins": {}})
    pins = connector_entry.setdefault("pins", {})
    existing = pins.get(str(pin))
    if existing is not None:
        if not isinstance(existing, Mapping) or existing.get("net") != net:
            raise SchematicImportError(
                f"conflicting mapping for {connector} pin {pin}: {getattr(existing, 'get', lambda *_: None)('net')} vs {net}"
            )
        return
    pin_entry: dict[str, Any] = {"net": net}
    if description:
        pin_entry["description"] = description
    if signal_type:
        pin_entry["signal_type"] = signal_type
    if max_voltage_v is not None:
        pin_entry["max_voltage_v"] = max_voltage_v
    if max_current_a is not None:
        pin_entry["max_current_a"] = max_current_a
    pins[str(pin)] = pin_entry


def _add_test_point(
    context: dict[str, Any],
    test_point: str,
    net: str,
    *,
    description: str = "",
) -> None:
    if not test_point or not net:
        raise SchematicImportError("test point entries require test_point and net")
    entry: dict[str, Any] = {"net": net}
    if description:
        entry["description"] = description
    test_points = _dut(context).setdefault("test_points", {})
    existing = test_points.get(test_point)
    if existing is not None:
        if not isinstance(existing, Mapping) or existing.get("net") != net:
            raise SchematicImportError(f"conflicting mapping for test point {test_point}")
        return
    test_points[test_point] = entry


def import_pin_map_csv(path: str | Path, dut_name: str = "dut") -> dict[str, Any]:
    """Import a curated connector/test-point CSV into canonical schematic context."""
    csv_path = Path(path)
    _check_input_size(csv_path)
    context = _base_context(csv_path, dut_name)
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SchematicImportError(f"{csv_path} has no CSV header")
        for index, row in enumerate(reader, start=1):
            connector = _first(row, "connector", "designator", "refdes", "ref")
            pin = _first(row, "pin", "pin_number", "pin_num")
            net = _first(row, "net", "net_name", "signal")
            test_point = _first(row, "test_point", "tp", "testpoint")
            description = _first(row, "description", "pin_name", "notes")
            signal_type = _first(row, "signal_type", "electrical_type", "type")
            max_voltage = _number(_first(row, "max_voltage_v", "voltage_rating_v", "max_voltage"), "max_voltage_v")
            max_current = _number(_first(row, "max_current_a", "current_rating_a", "max_current"), "max_current_a")
            if test_point:
                _add_test_point(context, test_point, net, description=description)
            elif connector and pin and net:
                _add_connector_pin(
                    context,
                    connector,
                    pin,
                    net,
                    description=description,
                    signal_type=signal_type,
                    max_voltage_v=max_voltage,
                    max_current_a=max_current,
                )
            else:
                raise SchematicImportError(
                    f"row {index} must include connector+pin or test_point with net"
                )
    _require_records(context)
    return context


def import_altium_pin_csv(path: str | Path, dut_name: str = "dut") -> dict[str, Any]:
    """Import common Altium pin/net CSV exports.

    This is intentionally a thin compatibility wrapper over the curated CSV
    importer; it accepts common Altium headers like Designator, Pin Number,
    Net Name, Pin Name, and Electrical Type.
    """
    return import_pin_map_csv(path, dut_name=dut_name)


def _component_values(root: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for comp in root.findall(".//components/comp"):
        ref = _clean(comp.attrib.get("ref"))
        value = _clean(comp.findtext("value"))
        if ref:
            values[ref] = value
    return values


def _is_connector(ref: str, value: str) -> bool:
    ref_upper = ref.upper()
    value_lower = value.lower()
    return ref_upper.startswith(("J", "P", "CONN")) or "conn" in value_lower or "connector" in value_lower


def _is_test_point(ref: str, value: str) -> bool:
    return ref.upper().startswith("TP") or "testpoint" in value.lower() or "test point" in value.lower()


def import_kicad_netlist(path: str | Path, dut_name: str = "dut") -> dict[str, Any]:
    """Import a KiCad generic XML netlist into canonical schematic context."""
    netlist_path = Path(path)
    _check_input_size(netlist_path)
    try:
        root = ET.fromstring(netlist_path.read_text())
    except (OSError, ET.ParseError) as exc:
        raise SchematicImportError(f"invalid KiCad netlist {netlist_path}: {exc}") from exc

    context = _base_context(netlist_path, dut_name)
    values = _component_values(root)
    for net in root.findall(".//nets/net"):
        net_name = _clean(net.attrib.get("name"))
        if not net_name:
            continue
        for node in net.findall("node"):
            ref = _clean(node.attrib.get("ref"))
            pin = _clean(node.attrib.get("pin"))
            value = values.get(ref, "")
            if _is_test_point(ref, value):
                _add_test_point(context, ref, net_name)
            elif _is_connector(ref, value) and pin:
                _add_connector_pin(context, ref, pin, net_name)
    _require_records(context)
    return context


_CONNECTOR_LINE = re.compile(
    r"^(?:connector\s+)?(?P<connector>[JP]\w*)\s+(?:pin\s+)?(?P<pin>\w+)\s+(?:net\s+)?(?P<net>[A-Za-z0-9_+\-./]+)(?P<rest>.*)",
    re.IGNORECASE,
)
_TP_LINE = re.compile(
    r"^(?P<tp>TP\w*)\s+(?:net\s+)?(?P<net>[A-Za-z0-9_+\-./]+)(?P<rest>.*)",
    re.IGNORECASE,
)
_MAX_VOLTAGE = re.compile(r"max\s+(?P<voltage>\d+(?:\.\d+)?)\s*V", re.IGNORECASE)


def _extract_text(path: Path) -> str:
    _check_input_size(path)
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - dependency is in pyproject
            raise SchematicImportError("PDF import requires pypdf") from exc
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text()


def import_text_schematic(path: str | Path, dut_name: str = "dut") -> dict[str, Any]:
    """Best-effort importer for text/OCR schematic notes and simple PDF text."""
    text_path = Path(path)
    context = _base_context(text_path, dut_name)
    text = _extract_text(text_path)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tp_match = _TP_LINE.search(stripped)
        if tp_match:
            _add_test_point(
                context,
                tp_match.group("tp").upper(),
                tp_match.group("net"),
                description=tp_match.group("rest").strip(),
            )
            continue
        conn_match = _CONNECTOR_LINE.search(stripped)
        if conn_match:
            rest = conn_match.group("rest").strip()
            voltage_match = _MAX_VOLTAGE.search(rest)
            max_voltage = _number(voltage_match.group("voltage"), "max_voltage_v") if voltage_match else None
            _add_connector_pin(
                context,
                conn_match.group("connector").upper(),
                conn_match.group("pin"),
                conn_match.group("net"),
                description=rest,
                max_voltage_v=max_voltage,
            )
    _require_records(context)
    return context


IMPORTERS: dict[str, Callable[[str | Path, str], dict[str, Any]]] = {
    "pin-map-csv": import_pin_map_csv,
    "altium-csv": import_altium_pin_csv,
    "kicad-netlist": import_kicad_netlist,
    "text": import_text_schematic,
    "pdf": import_text_schematic,
}


def import_schematic(path: str | Path, *, kind: str = "auto", dut_name: str = "dut") -> dict[str, Any]:
    schematic_path = Path(path)
    resolved_kind = kind
    if kind == "auto":
        suffix = schematic_path.suffix.lower()
        if suffix == ".csv":
            resolved_kind = "pin-map-csv"
        elif suffix in {".net", ".xml"}:
            resolved_kind = "kicad-netlist"
        elif suffix == ".pdf":
            resolved_kind = "pdf"
        else:
            resolved_kind = "text"
    try:
        importer = IMPORTERS[resolved_kind]
    except KeyError as exc:
        raise SchematicImportError(f"unsupported schematic import type: {kind}") from exc
    return importer(schematic_path, dut_name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import schematics/netlists into Long Game schematic context YAML")
    parser.add_argument("input", help="Input CSV, KiCad XML/netlist, text, or PDF file")
    parser.add_argument("-o", "--output", help="Output YAML path. Defaults to stdout")
    parser.add_argument(
        "--type",
        default="auto",
        choices=["auto", *IMPORTERS.keys()],
        help="Input type. Default: auto from file extension",
    )
    parser.add_argument("--dut-name", default="dut", help="DUT name for schematic_context.dut.name")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        context = import_schematic(args.input, kind=args.type, dut_name=args.dut_name)
    except SchematicImportError as exc:
        print(f"schematic import failed: {exc}")
        return 2
    output = yaml.safe_dump(context, sort_keys=False)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
