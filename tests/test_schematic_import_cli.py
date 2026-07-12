from __future__ import annotations

from pathlib import Path

import yaml

from long_game_sdk.sdk.schematic_import import main


def test_lg_schematic_import_writes_yaml_output(tmp_path: Path) -> None:
    source = tmp_path / "pins.csv"
    output = tmp_path / "schematic_context.yaml"
    source.write_text("connector,pin,net\nJ1,1,VIN+\n")

    exit_code = main([str(source), "--dut-name", "bms_controller", "-o", str(output)])

    assert exit_code == 0
    data = yaml.safe_load(output.read_text())
    assert data["schematic_context"]["dut"]["name"] == "bms_controller"
    assert data["schematic_context"]["dut"]["connectors"]["J1"]["pins"]["1"]["net"] == "VIN+"


def test_lg_schematic_import_returns_2_for_bad_input_type(tmp_path: Path, capsys) -> None:
    source = tmp_path / "pins.unknown"
    source.write_text("bad")

    exit_code = main([str(source), "--type", "not-real"])

    assert exit_code == 2
    assert "invalid choice" in capsys.readouterr().err
