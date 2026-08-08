# Schematic Importers

`lg-schematic-import` converts schematic-derived exports into the canonical Long Game `schematic_context` YAML used by guided test setup.

The first goal is not perfect EDA automation. The goal is to make the connection-critical schematic facts explicit and machine-readable:

- DUT connectors
- pins
- nets
- test points
- signal types
- voltage/current limits
- source schematic/export references

This lets an LLM guide wiring from structured data instead of guessing from vague labels.

## Supported MVP import types

### Curated pin-map CSV

Best first workflow for consulting and early product use.

```bash
uv run lg-schematic-import examples/guided_test_setup/bms_pin_map.csv \
  --dut-name bms_controller \
  -o reports/schematic-import/bms_pin_map_import.yaml
```

This raw import is an inspection artifact. It intentionally does not infer a schematic revision or approved instrument-to-DUT connection records, so keep it separate from reviewed context such as `examples/guided_test_setup/bms_schematic_context.yaml`. Do not pass raw importer output to `lg-guide-test` until those records have been independently reviewed and added.

Accepted headers include:

- `connector`
- `pin`
- `net`
- `description`
- `max_voltage_v`
- `max_current_a`
- `signal_type`
- `test_point`

Rows with `connector + pin + net` become connector pinout entries.
Rows with `test_point + net` become test point entries.

### Altium pin CSV

Common Altium export headers are accepted through the CSV importer:

- `Designator`
- `Pin Number`
- `Net Name`
- `Pin Name`
- `Electrical Type`

```bash
uv run lg-schematic-import altium_pin_export.csv \
  --type altium-csv \
  --dut-name evse_controller \
  -o schematic_context.yaml
```

### KiCad generic netlist

KiCad XML/generic netlists are parsed for connector and test point nodes.
Connector references are detected by ref/value patterns like `J*`, `P*`, `CONN*`, or values containing `Conn`/`Connector`.
Test points are detected by `TP*` references or values containing `TestPoint`.

```bash
uv run lg-schematic-import bms_controller.net \
  --type kicad-netlist \
  --dut-name bms_controller \
  -o schematic_context.yaml
```

### Text / PDF text extraction

Simple text or extractable-PDF schematic notes can be parsed with patterns like:

```text
Connector J3 pin 1 net PACK+ max 400V
Connector J3 pin 2 net PACK- max 400V
TP7 net HV_SENSE divider output
```

```bash
uv run lg-schematic-import schematic_notes.txt --type text -o schematic_context.yaml
uv run lg-schematic-import schematic.pdf --type pdf -o schematic_context.yaml
```

PDF import uses `pypdf` text extraction. It is best-effort and should be reviewed before use.

## Canonical output shape

```yaml
schematic_context:
  dut:
    name: bms_controller
    source_files:
      - examples/guided_test_setup/bms_pin_map.csv
    connectors:
      J1:
        pins:
          "1":
            net: VIN+
            description: Input supply positive
            signal_type: power
            max_voltage_v: 60
            max_current_a: 1
    test_points:
      TP12:
        net: CELL_SIM_1
        description: Simulated cell 1 sense node
```

## Safety rule

Importer output is context, not permission to run hardware.

Guided execution must still require:

1. schematic context review
2. connector/harness map consistency
3. safe-state before and after execution
4. preflight pass
5. explicit operator wiring confirmation before energizing outputs or loads

If a required connection cannot be resolved from schematic context and connector maps, the guided test flow must stop and ask for the missing mapping.
