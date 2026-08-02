from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, NoReturn, Sequence

import yaml


_ALLOWED_TRANSPORTS = frozenset(
    {"swd", "jtag", "hla_swd", "hla_jtag", "dapdirect_swd", "dapdirect_jtag"}
)
_ALLOWED_FORMATS = frozenset({"elf", "bin", "hex", "s19"})
_CONFIG_PATH_RE = re.compile(r"^[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*\.cfg$")
EXECUTION_UNAVAILABLE_MESSAGE = "OpenOCD execution unavailable pending hardened sandbox"


class FlashConfigError(ValueError):
    """Raised when an OpenOCD flash plan is incomplete or unsafe."""


class ExecutionUnavailableError(FlashConfigError):
    """Raised for every request to execute OpenOCD in this release."""


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
    safety: FlashSafety = field(default_factory=FlashSafety)


@dataclass(frozen=True)
class FlashResult:
    command: list[str]
    executed: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error_kind: str | None = None


def _execution_unavailable() -> NoReturn:
    raise ExecutionUnavailableError(EXECUTION_UNAVAILABLE_MESSAGE)


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


def _validated_config_path(value: str, *, prefix: str, field_name: str) -> str:
    has_traversal = any(part in {".", ".."} for part in value.split("/"))
    if has_traversal or not value.startswith(f"{prefix}/") or not _CONFIG_PATH_RE.fullmatch(value):
        raise FlashConfigError(
            f"flash.openocd.{field_name} must be a relative {prefix}/*.cfg path"
        )
    return value


def _optional_boolean(data: Mapping[str, Any], field_name: str, default: bool) -> bool:
    value = data.get(field_name, default)
    if not isinstance(value, bool):
        raise FlashConfigError(f"flash.{field_name} must be a boolean")
    return value


def load_flash_config(path: str | Path) -> FlashConfig:
    """Load and validate an OpenOCD dry-run plan config."""
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
    for unsupported_field in ("bin", "extra_cfg", "pre_commands", "post_commands"):
        if unsupported_field in openocd:
            raise FlashConfigError(
                f"flash.openocd.{unsupported_field} is not allowed; the planner emits a fixed command shape"
            )
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

    transport_value = flash.get("transport")
    transport = str(transport_value).strip() if transport_value else None
    if transport is not None and transport not in _ALLOWED_TRANSPORTS:
        raise FlashConfigError(
            f"flash.transport must be one of: {', '.join(sorted(_ALLOWED_TRANSPORTS))}"
        )

    image_format = str(flash.get("format", "elf")).strip() or "elf"
    if image_format not in _ALLOWED_FORMATS:
        raise FlashConfigError(
            f"flash.format must be one of: {', '.join(sorted(_ALLOWED_FORMATS))}"
        )

    return FlashConfig(
        target=_require_string(flash, "target"),
        interface=_require_string(flash, "interface"),
        firmware=firmware,
        interface_cfg=_validated_config_path(
            _require_string(openocd, "interface_cfg"),
            prefix="interface",
            field_name="interface_cfg",
        ),
        target_cfg=_validated_config_path(
            _require_string(openocd, "target_cfg"),
            prefix="target",
            field_name="target_cfg",
        ),
        transport=transport,
        format=image_format,
        verify=_optional_boolean(flash, "verify", True),
        reset=_optional_boolean(flash, "reset", True),
        adapter_speed_khz=adapter_speed,
        safety=FlashSafety(
            require_unpowered_outputs=_optional_boolean(
                safety_data, "require_unpowered_outputs", True
            ),
            notes=_as_tuple(safety_data.get("notes")),
        ),
    )


def _tcl_quote(value: str) -> str:
    """Return one literal Tcl word, disabling command and variable substitution."""
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise FlashConfigError("firmware path contains a forbidden control character")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
    )
    return f'"{escaped}"'


def _program_command(config: FlashConfig) -> str:
    parts = ["program", _tcl_quote(config.firmware.as_posix())]
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
    commands.extend(["init", "reset halt", _program_command(config), "shutdown"])
    return commands


def build_openocd_command(config: FlashConfig) -> list[str]:
    """Build an illustrative OpenOCD command without executing it."""
    if config.transport is not None and config.transport not in _ALLOWED_TRANSPORTS:
        raise FlashConfigError("transport is not allowlisted")
    if config.format not in _ALLOWED_FORMATS:
        raise FlashConfigError("format is not allowlisted")
    _validated_config_path(config.interface_cfg, prefix="interface", field_name="interface_cfg")
    _validated_config_path(config.target_cfg, prefix="target", field_name="target_cfg")
    if config.adapter_speed_khz is not None and (
        isinstance(config.adapter_speed_khz, bool) or config.adapter_speed_khz <= 0
    ):
        raise FlashConfigError("adapter speed must be a positive integer")
    return [
        "openocd",
        "-f",
        config.interface_cfg,
        "-f",
        config.target_cfg,
        "-c",
        "; ".join(_openocd_commands(config)),
    ]


def authorize_flash(*args: Any, **kwargs: Any) -> NoReturn:
    """Compatibility fail-closed gate; live authorization is not available."""
    _execution_unavailable()


def create_safe_state_attestation(*args: Any, **kwargs: Any) -> NoReturn:
    """Compatibility fail-closed gate; execution attestations are not accepted."""
    _execution_unavailable()


def flash_firmware(
    config: FlashConfig,
    *,
    execute: bool = False,
    authorization: object | None = None,
    timeout_seconds: float | None = None,
) -> FlashResult:
    """Generate an OpenOCD plan; reject every live-execution request before side effects."""
    if execute:
        _execution_unavailable()
    return FlashResult(command=build_openocd_command(config), executed=False, returncode=0)


def generate_flash_plan(config: FlashConfig, result: FlashResult) -> str:
    safety_notes = list(config.safety.notes) or ["No extra safety notes provided."]
    lines = [
        "# OpenOCD Flash Plan",
        "",
        "- Mode: dry-run only (not executed)",
        "- Status: planning output only; no hardware or image verification was performed",
        f"- Target config label (not identity-verified): {config.target}",
        f"- Interface label: {config.interface}",
        f"- Transport: {config.transport or 'OpenOCD default'}",
        f"- Firmware: {config.firmware.as_posix()}",
        f"- Image format: {config.format}",
        f"- Verify requested in proposed command: {'yes' if config.verify else 'no'}",
        f"- Reset requested in proposed command: {'yes' if config.reset else 'no'}",
        f"- Plan calls for unpowered bench outputs before connect: {'yes' if config.safety.require_unpowered_outputs else 'no'}",
        "",
        "## Safety Notes",
        "",
        *[f"- {note}" for note in safety_notes],
        "",
        "## Proposed OpenOCD Command (not executed)",
        "",
        "```bash",
        shlex.join(result.command),
        "```",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a dry-run OpenOCD flash plan")
    parser.add_argument("config", help="OpenOCD flash-plan YAML config")
    parser.add_argument("-o", "--output", help="Write flash-plan markdown to this path")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Unavailable: live execution is disabled pending a hardened sandbox",
    )
    # Retain old flags only so stale execution commands fail with the explicit
    # unavailable message rather than being mistaken for a parser problem.
    parser.add_argument("--timeout-seconds", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--trusted-config-root", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--openocd-executable", help=argparse.SUPPRESS)
    parser.add_argument("--safe-state-config", help=argparse.SUPPRESS)
    parser.add_argument("--unsafe-allow-unverified", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes-i-confirm-target-wiring", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.execute:
        import sys

        print(EXECUTION_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return 2

    try:
        config = load_flash_config(args.config)
        result = flash_firmware(config)
    except FlashConfigError as exc:
        import sys

        print(f"flash plan error: {exc}", file=sys.stderr)
        return 2

    plan = generate_flash_plan(config, result)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(plan)
    else:
        print(plan, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
