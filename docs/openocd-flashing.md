# OpenOCD Flashing

`lg-flash-openocd` plans or executes firmware flashing through OpenOCD.

The default mode is **dry-run**. Flashing is a hardware side effect, so actual execution requires an explicit confirmation flag.

## Example dry run

```bash
uv run lg-flash-openocd examples/openocd/stm32f4_flash.yaml \
  -o reports/openocd/stm32f4-flash-plan.md
```

This validates the YAML, checks that the firmware file exists, builds the OpenOCD command, and writes a markdown flash plan without touching hardware.

## Execute flashing

Only run after confirming target/debug wiring and safe bench state:

```bash
uv run lg-safe
uv run lg-flash-openocd examples/openocd/stm32f4_flash.yaml \
  --execute \
  --yes-i-confirm-target-wiring \
  -o reports/openocd/stm32f4-flash-result.md
uv run lg-safe
```

The confirmation flag is intentionally verbose so this does not happen by accident.

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
    extra_cfg:
      - board/custom_fixture.cfg
    pre_commands:
      - reset_config srst_only srst_nogate
    post_commands:
      - reset run
  safety:
    require_unpowered_outputs: true
    notes:
      - Confirm bench supply outputs are OFF before connecting SWD.
      - Connect GND, SWDIO, SWCLK, NRST, and VTref before flashing.
```

## Generated OpenOCD behavior

The SDK builds a command with:

1. interface config
2. target config
3. optional extra OpenOCD configs
4. optional transport selection
5. optional adapter speed
6. pre-commands
7. `init`
8. `reset halt`
9. `program <firmware> verify reset`
10. post-commands
11. `shutdown`

For non-ELF files, set `format` to the OpenOCD argument you need, for example `bin`.

## Safety model

OpenOCD support is part of guided validation, not a standalone magic flasher.

Recommended flow:

1. Run `lg-safe` to turn bench outputs/loads off where supported.
2. Confirm the DUT is powered appropriately or target-powered through the programmer as intended.
3. Confirm GND, SWDIO/JTAG data, clock, reset, and VTref/target voltage sense.
4. Dry-run `lg-flash-openocd` and review the command/report.
5. Execute with `--yes-i-confirm-target-wiring`.
6. Run `lg-safe` again before continuing with functional tests.

The LLM may explain wiring and command intent, but the SDK owns execution gating.
