from __future__ import annotations

import argparse
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml


class FlashConfigError(ValueError):
    """Raised when an OpenOCD flash config is incomplete or unsafe."""


@dataclass(frozen=True)
class FlashSafety:
    require_unpowered_outputs: bool = True
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlashConfig:
    target: str
    interface: str
    firmware: Path
    interface_cfg: str
    target_cfg: str
    transport: str | None = None
    format: str = "elf"
    verify: bool = True
    reset: bool = True
    adapter_speed_khz: int | None = None
    openocd_bin: str = "openocd"
    extra_cfg: tuple[str, ...] = ()
    pre_commands: tuple[str, ...] = ()
    post_commands: tuple[str, ...] = ()
    safety: FlashSafety = field(default_factory=FlashSafety)


@dataclass(frozen=True)
class FlashResult:
    command: list[str]
    executed: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _require_mapping(data: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise FlashConfigError(f"{label} must be a mapping")
    return data


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise FlashConfigError(f"flash.{field_name} is required")
    return value.strip()


def load_flash_config(path: str | Path) -> FlashConfig:
    """Load and validate an OpenOCD flash YAML config."""
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text())
    except FileNotFoundError as exc:
        raise FlashConfigError(f"flash config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise FlashConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    root = _require_mapping(data, "config")
    flash = _require_mapping(root.get("flash"), "flash")
    openocd = _require_mapping(flash.get("openocd"), "flash.openocd")
    safety_data = _require_mapping(flash.get("safety", {}), "flash.safety")

    firmware = Path(_require_string(flash, "firmware"))
    if not firmware.is_absolute():
        firmware = (config_path.parent / firmware).resolve()
    if not firmware.exists():
        raise FlashConfigError(f"firmware not found: {firmware}")

    adapter_speed = flash.get("adapter_speed_khz")
    if adapter_speed is not None:
        try:
            adapter_speed = int(adapter_speed)
        except (TypeError, ValueError) as exc:
            raise FlashConfigError("flash.adapter_speed_khz must be an integer") from exc
        if adapter_speed <= 0:
            raise FlashConfigError("flash.adapter_speed_khz must be positive")

    return FlashConfig(
        target=_require_string(flash, "target"),
        interface=_require_string(flash, "interface"),
        firmware=firmware,
        interface_cfg=_require_string(openocd, "interface_cfg"),
        target_cfg=_require_string(openocd, "target_cfg"),
        transport=str(flash.get("transport")).strip() if flash.get("transport") else None,
        format=str(flash.get("format", "elf")).strip() or "elf",
        verify=bool(flash.get("verify", True)),
        reset=bool(flash.get("reset", True)),
        adapter_speed_khz=adapter_speed,
        openocd_bin=str(openocd.get("bin", "openocd")).strip() or "openocd",
        extra_cfg=_as_tuple(openocd.get("extra_cfg")),
        pre_commands=_as_tuple(openocd.get("pre_commands")),
        post_commands=_as_tuple(openocd.get("post_commands")),
        safety=FlashSafety(
            require_unpowered_outputs=bool(safety_data.get("require_unpowered_outputs", True)),
            notes=_as_tuple(safety_data.get("notes")),
        ),
    )


def _program_command(config: FlashConfig) -> str:
    parts = ["program", config.firmware.as_posix()]
    if config.verify:
        parts.append("verify")
    if config.reset:
        parts.append("reset")
    if config.format != "elf":
        parts.append(config.format)
    return " ".join(parts)


def _openocd_commands(config: FlashConfig) -> list[str]:
    commands: list[str] = []
    if config.transport:
        commands.append(f"transport select {config.transport}")
    if config.adapter_speed_khz:
        commands.append(f"adapter speed {config.adapter_speed_khz}")
    commands.extend(config.pre_commands)
    commands.extend(["init", "reset halt", _program_command(config)])
    commands.extend(config.post_commands)
    commands.append("shutdown")
    return commands


def build_openocd_command(config: FlashConfig) -> list[str]:
    """Build the OpenOCD command for a flash config without executing it."""
    command = [
        config.openocd_bin,
        "-f",
        config.interface_cfg,
        "-f",
        config.target_cfg,
    ]
    for cfg in config.extra_cfg:
        command.extend(["-f", cfg])
    command.extend(["-c", "; ".join(_openocd_commands(config))])
    return command


def _subprocess_runner(command: list[str]) -> FlashResult:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return FlashResult(
        command=command,
        executed=True,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def flash_firmware(
    config: FlashConfig,
    *,
    execute: bool = False,
    runner: Callable[[list[str]], FlashResult | None] = _subprocess_runner,
) -> FlashResult:
    """Plan or execute firmware flashing through OpenOCD.

    Dry-run is the default because flashing changes DUT state. Callers must pass
    execute=True only after wiring and target identity have been confirmed.
    """
    command = build_openocd_command(config)
    if not execute:
        return FlashResult(command=command, executed=False, returncode=0)
    result = runner(command)
    if result is None:
        return FlashResult(command=command, executed=True, returncode=0)
    return result


def generate_flash_plan(config: FlashConfig, result: FlashResult) -> str:
    mode = "execute" if result.executed else "dry-run"
    safety_notes = list(config.safety.notes) or ["No extra safety notes provided."]
    lines = [
        "# OpenOCD Flash Plan",
        "",
        f"- Mode: {mode}",
        f"- Target: {config.target}",
        f"- Interface: {config.interface}",
        f"- Transport: {config.transport or 'OpenOCD default'}",
        f"- Firmware: {config.firmware.as_posix()}",
        f"- Verify after program: {'yes' if config.verify else 'no'}",
        f"- Reset after program: {'yes' if config.reset else 'no'}",
        f"- Require unpowered bench outputs before connect: {'yes' if config.safety.require_unpowered_outputs else 'no'}",
        "",
        "## Safety Notes",
        "",
        *[f"- {note}" for note in safety_notes],
        "",
        "## OpenOCD Command",
        "",
        "```bash",
        shlex.join(result.command),
        "```",
    ]
    if result.executed:
        lines.extend(
            [
                "",
                "## Result",
                "",
                f"- Return code: {result.returncode}",
                "",
                "### stdout",
                "",
                "```text",
                result.stdout.strip(),
                "```",
                "",
                "### stderr",
                "",
                "```text",
                result.stderr.strip(),
                "```",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or execute firmware flashing with OpenOCD")
    parser.add_argument("config", help="OpenOCD flash YAML config")
    parser.add_argument("-o", "--output", help="Write flash plan/result markdown to this path")
    parser.add_argument("--execute", action="store_true", help="Actually run OpenOCD. Default is dry-run")
    parser.add_argument(
        "--yes-i-confirm-target-wiring",
        action="store_true",
        help="Required with --execute to confirm target power/wiring/debug header are correct",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.execute and not args.yes_i_confirm_target_wiring:
        print("--execute requires --yes-i-confirm-target-wiring", flush=True)
        import sys

        print("Refusing to flash until target wiring and power state are explicitly confirmed.", file=sys.stderr)
        print("Use --yes-i-confirm-target-wiring after verifying SWD/JTAG, reset, ground, and target power.", file=sys.stderr)
        return 2

    try:
        config = load_flash_config(args.config)
        result = flash_firmware(config, execute=bool(args.execute))
    except FlashConfigError as exc:
        import sys

        print(f"flash config error: {exc}", file=sys.stderr)
        return 2

    plan = generate_flash_plan(config, result)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(plan)
    else:
        print(plan, end="")
    return result.returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
