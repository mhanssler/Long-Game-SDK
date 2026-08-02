from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import long_game_sdk.sdk.openocd_flash as openocd_flash
from long_game_sdk.sdk.openocd_flash import (
    EXECUTION_UNAVAILABLE_MESSAGE,
    ExecutionUnavailableError,
    FlashConfigError,
    authorize_flash,
    build_openocd_command,
    create_safe_state_attestation,
    flash_firmware,
    generate_flash_plan,
    load_flash_config,
    main,
)


def _write_config(tmp_path: Path, *, image_format: str = "elf") -> Path:
    firmware = tmp_path / f"firmware.{image_format}"
    firmware.write_text("fake firmware")
    config = {
        "flash": {
            "target": "stm32f407",
            "interface": "stlink",
            "transport": "hla_swd",
            "firmware": str(firmware),
            "format": image_format,
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
    path = tmp_path / f"flash-{image_format}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def test_load_flash_config_and_build_fixed_plan(tmp_path: Path) -> None:
    config = load_flash_config(_write_config(tmp_path))

    command = build_openocd_command(config)

    assert command[:5] == [
        "openocd",
        "-f",
        "interface/stlink.cfg",
        "-f",
        "target/stm32f4x.cfg",
    ]
    script = command[-1]
    assert "transport select hla_swd" in script
    assert "adapter speed 4000" in script
    assert "init; reset halt" in script
    assert f'program "{config.firmware.as_posix()}" verify reset' in script
    assert script.endswith("shutdown")


@pytest.mark.parametrize("image_format", ["elf", "bin", "hex", "s19"])
def test_all_image_formats_produce_accurate_dry_run_reports(
    tmp_path: Path, image_format: str
) -> None:
    config = load_flash_config(_write_config(tmp_path, image_format=image_format))
    result = flash_firmware(config)
    report = generate_flash_plan(config, result)

    assert result.executed is False
    assert result.returncode == 0
    assert f"Image format: {image_format}" in report
    assert "Mode: dry-run only (not executed)" in report
    assert "no hardware or image verification was performed" in report
    assert "Proposed OpenOCD Command (not executed)" in report
    expected_suffix = " verify reset" if image_format == "elf" else f" verify reset {image_format}"
    assert expected_suffix in result.command[-1]


def test_non_elf_plan_never_claims_verified_execution(tmp_path: Path) -> None:
    config = load_flash_config(_write_config(tmp_path, image_format="bin"))
    report = generate_flash_plan(config, flash_firmware(config))

    assert "Verify requested in proposed command: yes" in report
    assert "verified execution" not in report.lower()
    assert "no hardware or image verification was performed" in report


def test_dry_run_does_not_spawn_or_stage(tmp_path: Path, monkeypatch) -> None:
    config = load_flash_config(_write_config(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: pytest.fail("must not stage"))

    result = flash_firmware(config)

    assert result.command == build_openocd_command(config)
    assert not list(tmp_path.glob("long-game-openocd-*"))


class _CraftedAuthorization:
    @property
    def executable_path(self):
        pytest.fail("authorization must not be inspected")

    @property
    def safe_state_attestation(self):
        pytest.fail("attestation must not be accepted or inspected")


@pytest.mark.parametrize("timeout", [None, 0.01, float("nan")])
def test_execute_fails_before_spawn_staging_or_authorization_inspection(
    tmp_path: Path, monkeypatch, timeout: float | None
) -> None:
    config = load_flash_config(_write_config(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: pytest.fail("must not stage"))

    with pytest.raises(ExecutionUnavailableError, match="pending hardened sandbox"):
        flash_firmware(
            config,
            execute=True,
            authorization=_CraftedAuthorization(),
            timeout_seconds=timeout,
        )


def test_execute_fails_closed_before_even_validating_crafted_config(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    monkeypatch.setattr(
        openocd_flash,
        "build_openocd_command",
        lambda *a, **k: pytest.fail("execute must fail before planning or input inspection"),
    )

    with pytest.raises(ExecutionUnavailableError, match="pending hardened sandbox"):
        flash_firmware(object(), execute=True, authorization=object())  # type: ignore[arg-type]


def test_implementation_contains_no_process_or_mutable_staging_boundary() -> None:
    source = Path(openocd_flash.__file__).read_text()
    for forbidden in ("subprocess", "Popen", "posix_spawn", "TemporaryDirectory", "write_bytes"):
        assert forbidden not in source


@pytest.mark.parametrize("legacy_api", [authorize_flash, create_safe_state_attestation])
def test_legacy_execution_security_apis_are_unreachable(legacy_api) -> None:
    crafted = _CraftedAuthorization()
    with pytest.raises(ExecutionUnavailableError, match="pending hardened sandbox"):
        legacy_api(crafted, safe_state_attestation=crafted)


def test_firmware_path_is_tcl_quoted_in_plan(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    firmware = tmp_path / "image with spaces;[shutdown].elf"
    firmware.write_text("fake")
    data = yaml.safe_load(path.read_text())
    data["flash"]["firmware"] = str(firmware)
    path.write_text(yaml.safe_dump(data))

    command_script = build_openocd_command(load_flash_config(path))[-1]

    assert r"\[shutdown]" in command_script
    assert ";[shutdown]" not in command_script


@pytest.mark.parametrize("field", ["bin", "pre_commands", "post_commands", "extra_cfg"])
def test_yaml_rejects_executable_and_openocd_code_injection(tmp_path: Path, field: str) -> None:
    path = _write_config(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["flash"]["openocd"][field] = "/tmp/attacker"
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(FlashConfigError, match=field):
        load_flash_config(path)


@pytest.mark.parametrize(
    ("location", "value"),
    [
        (("flash", "transport"), "swd; shutdown"),
        (("flash", "format"), "bin; shutdown"),
        (("flash", "openocd", "interface_cfg"), "/tmp/evil.cfg"),
        (("flash", "openocd", "target_cfg"), "../evil.cfg"),
    ],
)
def test_yaml_rejects_non_allowlisted_plan_values(
    tmp_path: Path, location: tuple[str, ...], value: str
) -> None:
    path = _write_config(tmp_path)
    data = yaml.safe_load(path.read_text())
    node = data
    for key in location[:-1]:
        node = node[key]
    node[location[-1]] = value
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(FlashConfigError):
        load_flash_config(path)


def test_build_revalidates_programmatic_configs(tmp_path: Path) -> None:
    config = replace(load_flash_config(_write_config(tmp_path)), transport="swd; shutdown")
    with pytest.raises(FlashConfigError, match="transport"):
        build_openocd_command(config)


def test_cli_defaults_to_dry_run_and_writes_plan(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    output = tmp_path / "flash-plan.md"

    exit_code = main([str(config_path), "-o", str(output)])

    assert exit_code == 0
    plan = output.read_text()
    assert "Mode: dry-run only (not executed)" in plan
    assert "stm32f407" in plan


def test_cli_execute_fails_before_config_read_spawn_or_output_write(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    missing_config = tmp_path / "does-not-exist.yaml"
    output = tmp_path / "must-not-exist.md"
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))

    exit_code = main(
        [
            str(missing_config),
            "--execute",
            "--yes-i-confirm-target-wiring",
            "--openocd-executable",
            "/tmp/crafted-openocd",
            "--trusted-config-root",
            "/tmp/crafted-scripts",
            "--safe-state-config",
            "/tmp/crafted-attestation-input",
            "-o",
            str(output),
        ]
    )

    assert exit_code != 0
    assert EXECUTION_UNAVAILABLE_MESSAGE in capsys.readouterr().err
    assert not output.exists()
