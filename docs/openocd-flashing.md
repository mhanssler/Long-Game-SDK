# OpenOCD Flash Planning (Dry Run Only)

`lg-flash-openocd` is a **strictly dry-run-only planner**. It validates flash-plan YAML, checks that the referenced firmware file exists, renders a proposed OpenOCD argument vector, and optionally writes a markdown report.

**This release cannot execute OpenOCD or flash hardware.** Library and CLI execution requests fail closed with `OpenOCD execution unavailable pending hardened sandbox`. The refusal occurs before subprocess creation, safe-state/attestation handling, or mutable execution-artifact staging. A CLI refusal returns a nonzero status.

## Generate a plan

```bash
uv run lg-flash-openocd examples/openocd/stm32f4_flash.yaml \
  -o reports/openocd/stm32f4-flash-plan.md
```

The command shown in the report is review material, not verified execution output. Do not treat the report as evidence that OpenOCD is installed, config scripts are trusted or loadable, the target matches its label, wiring is correct, firmware is compatible, programming occurred, or verification passed. This SDK release provides no supported path to copy the proposed command into live execution.

## Disabled execution interface

`--execute` remains recognized only to fail stale scripts with a clear diagnostic:

```text
OpenOCD execution unavailable pending hardened sandbox
```

Former authorization and safe-state-attestation helpers are fail-closed compatibility gates; they do not create tokens or accept attestations. Options from older execution examples may still parse so the CLI can return the same deliberate refusal, but they grant no capability and are not supported configuration.

## Config shape

```yaml
flash:
  target: stm32f407
  interface: stlink
  transport: hla_swd
  firmware: firmware/bms_controller.elf
  format: elf
  verify: true
  reset: true
  adapter_speed_khz: 4000
  openocd:
    interface_cfg: interface/stlink.cfg
    target_cfg: target/stm32f4x.cfg
  safety:
    require_unpowered_outputs: true
    notes:
      - Confirm bench supply outputs are OFF before connecting SWD.
      - Connect GND, SWDIO, SWCLK, NRST, and VTref before flashing.
```

The safety fields and command flags describe intended operator review points only. They are not measured or attested by this planner.

## Accepted planning values

The YAML is data, not an arbitrary OpenOCD scripting interface:

- `openocd.bin`, `openocd.pre_commands`, `openocd.post_commands`, and `openocd.extra_cfg` are rejected.
- Transport is allowlisted: `swd`, `jtag`, `hla_swd`, `hla_jtag`, `dapdirect_swd`, or `dapdirect_jtag`.
- Planned image format is allowlisted: `elf`, `bin`, `hex`, or `s19`.
- Interface and target config labels must use relative `interface/*.cfg` and `target/*.cfg` forms.
- Firmware paths are escaped as Tcl words in the proposed command.
- Boolean fields must be YAML booleans (`true` or `false`), not strings.

These checks make report generation deterministic; they are **not a Tcl sandbox or execution security boundary**. The planner does not open or inspect OpenOCD config scripts. For every format, including non-ELF formats, `verify` and `reset` indicate words requested in the proposed command and never claim successful or verified execution.

The proposed script selects the optional transport and adapter speed, then lists `init`, `reset halt`, `program <quoted-firmware>`, optional `verify`/`reset` words, the non-ELF format word where applicable, and `shutdown`.

## Library API

```python
from long_game_sdk.sdk.openocd_flash import (
    flash_firmware,
    generate_flash_plan,
    load_flash_config,
)

config = load_flash_config("examples/openocd/stm32f4_flash.yaml")
result = flash_firmware(config)  # dry run only
document = generate_flash_plan(config, result)
assert result.executed is False
```

Passing `execute=True` raises `ExecutionUnavailableError` immediately. There is no supported execution authorization or attestation workflow in this release.

## Operator boundary

The planner can help review intent, but an operator must independently establish any future flashing procedure, target identity, electrical safety, OpenOCD installation/config provenance, image suitability, and resulting evidence outside this release. `target` and `target_cfg` are labels only. The planner neither reads silicon identity nor runs OpenOCD `verify`.
