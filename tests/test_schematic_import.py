from __future__ import annotations

from pathlib import Path

import yaml

from long_game_sdk.sdk.schematic_import import (
    SchematicImportError,
    import_altium_pin_csv,
    import_kicad_netlist,
    import_pin_map_csv,
    import_text_schematic,
)


def test_import_pin_map_csv_groups_connector_pins_and_test_points(tmp_path: Path) -> None:
    csv_path = tmp_path / "pin_map.csv"
    csv_path.write_text(
        "connector,pin,net,description,max_voltage_v,max_current_a,signal_type,test_point\n"
        "J1,1,VIN+,Input supply positive,60,1.0,power,\n"
        "J1,2,VIN-,Input supply return,0,1.0,power_return,\n"
        "J1,7,BMS_FAULT_N,Fault output,5,0.01,open_drain_logic,\n"
        ",,CELL_SIM_1,Cell simulator sense,,,,TP12\n"
    )

    context = import_pin_map_csv(csv_path, dut_name="bms_controller")

    dut = context["schematic_context"]["dut"]
    assert dut["name"] == "bms_controller"
    assert dut["connectors"]["J1"]["pins"]["1"]["net"] == "VIN+"
    assert dut["connectors"]["J1"]["pins"]["7"]["signal_type"] == "open_drain_logic"
    assert dut["connectors"]["J1"]["pins"]["1"]["max_voltage_v"] == 60
    assert dut["test_points"]["TP12"]["net"] == "CELL_SIM_1"
    assert csv_path.as_posix() in dut["source_files"]


def test_import_altium_pin_csv_accepts_common_export_headers(tmp_path: Path) -> None:
    csv_path = tmp_path / "altium_pin_export.csv"
    csv_path.write_text(
        "Designator,Pin Number,Net Name,Pin Name,Electrical Type\n"
        "J2,3,CAN_H,CAN high,Passive\n"
        "J2,4,CAN_L,CAN low,Passive\n"
    )

    context = import_altium_pin_csv(csv_path, dut_name="evse_controller")

    pins = context["schematic_context"]["dut"]["connectors"]["J2"]["pins"]
    assert pins["3"] == {"net": "CAN_H", "description": "CAN high", "signal_type": "Passive"}
    assert pins["4"]["net"] == "CAN_L"


def test_import_kicad_netlist_extracts_connector_nodes_and_test_points(tmp_path: Path) -> None:
    netlist = tmp_path / "bms.net"
    netlist.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<export>
  <components>
    <comp ref="J1"><value>Conn_01x03</value></comp>
    <comp ref="TP12"><value>TestPoint</value></comp>
    <comp ref="R1"><value>10k</value></comp>
  </components>
  <nets>
    <net code="1" name="VIN+"><node ref="J1" pin="1"/><node ref="R1" pin="1"/></net>
    <net code="2" name="VIN-"><node ref="J1" pin="2"/></net>
    <net code="3" name="CELL_SIM_1"><node ref="TP12" pin="1"/><node ref="J1" pin="3"/></net>
  </nets>
</export>
"""
    )

    context = import_kicad_netlist(netlist, dut_name="bms_controller")

    dut = context["schematic_context"]["dut"]
    assert dut["connectors"]["J1"]["pins"]["1"]["net"] == "VIN+"
    assert dut["connectors"]["J1"]["pins"]["3"]["net"] == "CELL_SIM_1"
    assert dut["test_points"]["TP12"]["net"] == "CELL_SIM_1"
    assert dut["source_files"] == [netlist.as_posix()]


def test_import_text_schematic_extracts_simple_connector_and_tp_lines(tmp_path: Path) -> None:
    text_file = tmp_path / "schematic.txt"
    text_file.write_text(
        "Connector J3 pin 1 net PACK+ max 400V\n"
        "Connector J3 pin 2 net PACK- max 400V\n"
        "TP7 net HV_SENSE divider output\n"
    )

    context = import_text_schematic(text_file, dut_name="pack_monitor")

    dut = context["schematic_context"]["dut"]
    assert dut["connectors"]["J3"]["pins"]["1"]["net"] == "PACK+"
    assert dut["connectors"]["J3"]["pins"]["1"]["max_voltage_v"] == 400
    assert dut["test_points"]["TP7"]["net"] == "HV_SENSE"


def test_pin_map_import_rejects_rows_without_connection_or_test_point(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("connector,pin,net\nJ1,,VIN+\n")

    try:
        import_pin_map_csv(csv_path)
    except SchematicImportError as exc:
        assert "row 1" in str(exc)
        assert "connector+pin or test_point" in str(exc)
    else:
        raise AssertionError("expected SchematicImportError")


def test_context_can_be_serialized_to_yaml(tmp_path: Path) -> None:
    csv_path = tmp_path / "pin_map.csv"
    csv_path.write_text("connector,pin,net\nJ1,1,VIN+\n")

    context = import_pin_map_csv(csv_path)
    dumped = yaml.safe_dump(context, sort_keys=False)

    assert "schematic_context:" in dumped
    assert "VIN+" in dumped
