from __future__ import annotations

from pathlib import Path

import yaml

from long_game_sdk.sdk.openocd_flash import (
    FlashConfigError,
    FlashResult,
    build_openocd_command,
    flash_firmware,
    load_flash_config,
    main,
)


def _write_config(tmp_path: Path) -> Path:
    firmware = tmp_path / "firmware.elf"
    firmware.write_text("fake firmware")
    config = {
        "flash": {
            "target": "stm32f407",
            "interface": "stlink",
            "transport": "hla_swd",
            "firmware": str(firmware),
            "format": "elf",
            "verify": True,
            "reset": True,
            "adapter_speed_khz": 4000,
            "openocd": {
                "interface_cfg": "interface/stlink.cfg",
                "target_cfg": "target/stm32f4x.cfg",
            },
            "safety": {
                "require_unpowered_outputs": True,
                "notes": ["DUT powered from current-limited bench supply"],
            },
        }
    }
    path = tmp_path / "flash.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def test_load_flash_config_validates_required_fields_and_firmware(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    config = load_flash_config(path)

    assert config.target == "stm32f407"
    assert config.interface == "stlink"
    assert config.transport == "hla_swd"
    assert config.firmware.name == "firmware.elf"
    assert config.verify is True
    assert config.reset is True
    assert config.adapter_speed_khz == 4000
    assert config.safety.require_unpowered_outputs is True


def test_load_flash_config_rejects_missing_firmware(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "flash": {
                    "target": "stm32f407",
                    "interface": "stlink",
                    "firmware": str(tmp_path / "missing.elf"),
                    "openocd": {
                        "interface_cfg": "interface/stlink.cfg",
                        "target_cfg": "target/stm32f4x.cfg",
                    },
                }
            }
        )
    )

    try:
        load_flash_config(path)
    except FlashConfigError as exc:
        assert "firmware not found" in str(exc)
    else:
        raise AssertionError("expected FlashConfigError")


def test_build_openocd_command_generates_safe_flash_sequence(tmp_path: Path) -> None:
    config = load_flash_config(_write_config(tmp_path))

    command = build_openocd_command(config)

    assert command[:5] == [
        "openocd",
        "-f",
        "interface/stlink.cfg",
        "-f",
        "target/stm32f4x.cfg",
    ]
    joined = " ".join(command)
    assert "transport select hla_swd" in joined
    assert "adapter speed 4000" in joined
    assert "init" in joined
    assert "reset halt" in joined
    assert f"program {config.firmware.as_posix()} verify reset" in joined
    assert "shutdown" in joined


def test_flash_firmware_dry_run_does_not_execute_runner(tmp_path: Path) -> None:
    config = load_flash_config(_write_config(tmp_path))
    calls: list[list[str]] = []

    result = flash_firmware(config, execute=False, runner=lambda command: calls.append(command))

    assert result.executed is False
    assert result.returncode == 0
    assert calls == []
    assert result.command == build_openocd_command(config)


def test_flash_firmware_execute_uses_runner_and_reports_output(tmp_path: Path) -> None:
    config = load_flash_config(_write_config(tmp_path))
    calls: list[list[str]] = []

    def runner(command: list[str]) -> FlashResult:
        calls.append(command)
        return FlashResult(command=command, executed=True, returncode=0, stdout="verified", stderr="")

    result = flash_firmware(config, execute=True, runner=runner)

    assert calls == [build_openocd_command(config)]
    assert result.executed is True
    assert result.returncode == 0
    assert result.stdout == "verified"


def test_cli_defaults_to_dry_run_and_writes_plan(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    output = tmp_path / "flash-plan.md"

    exit_code = main([str(config_path), "-o", str(output)])

    assert exit_code == 0
    plan = output.read_text()
    assert "# OpenOCD Flash Plan" in plan
    assert "Mode: dry-run" in plan
    assert "stm32f407" in plan
    assert "DUT powered from current-limited bench supply" in plan


def test_cli_requires_confirmation_to_execute(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path)

    exit_code = main([str(config_path), "--execute"])

    assert exit_code == 2
    assert "--yes-i-confirm-target-wiring" in capsys.readouterr().err
