# Bench BOM and Test Setup Architecture Generator

`lg-bench-bom` converts a test setup architecture YAML into the missing middle layer between requirements and execution:

**Requirement → Test case → Setup architecture → Equipment BOM → Connector map → Bench config YAML → Execution → Evidence**

## Example

```bash
uv run lg-bench-bom examples/bms_hil_bench_architecture.yaml \
  -o reports/bench-bom \
  --prefix bms-hil
```

Generated outputs:

- `bms-hil-setup-report.md` — client-facing setup architecture report.
- `bms-hil-equipment-bom.csv` — procurement/calibration equipment BOM.
- `bms-hil-connector-map.csv` — connector and harness map.
- `bms-hil-bench-config.yaml` — machine-readable bench config for later preflight/test execution flows.

## Input Shape

```yaml
project:
  client: Example Hardware Co.
  project: 800 V BMS HiL Bench
  phase: DVT

bench:
  name: bms_hil_bench_a
  dut: 800 V BMS controller
  evidence_root: reports/bms-hil
  automation_host: Linux laptop

requirements:
  - BMS-REQ-001

instruments:
  - name: hv_supply
    category: power
    role: pack_voltage_source
    purpose: Simulate pack voltage.
    required_optional: required
    quantity: 1
    manufacturer: EA Elektro-Automatik / equivalent
    model: TBD
    control_interface: ethernet
    calibration_required: true
    selection_criteria:
      - Voltage/current range covers requirements with margin.
      - API supports output enable/disable.
    safety_notes:
      - Output OFF before wiring changes.
    estimated_cost_usd: TBD
    lead_time: TBD
    replacement_option: Lower-voltage supply for early firmware tests.

connectors:
  - name: lv_control_header
    location: DUT LV connector
    family: Molex Micro-Fit / equivalent
    mating_connector: TBD
    pin_count: 12
    keying: polarized
    voltage_rating_v: 60
    current_rating_a: 8
    signal_type: LV power, CAN, enables
    cable_type: shielded multi-conductor
    shielding_grounding: shield drain to fixture ground
    strain_relief: boot and cable tie
    required_optional: required
    risk_notes: Prevent swapped CAN pins.

safety_controls:
  - All outputs OFF before connect/disconnect.
```

## Why This Matters

Most hardware validation delays are not caused by lack of test scripts alone. They come from the unstructured middle:

- Which instruments should be bought or reused?
- Which connectors/cables make the setup repeatable and safe?
- Which measurements require calibration traceability?
- What are the safe-state controls?
- How does the physical bench become machine-readable test infrastructure?

`lg-bench-bom` makes those choices explicit, reviewable, buildable, and automatable.

## Relationship to Other CLIs

- `lg-test-plan` maps requirements to test cases and evidence expectations.
- `lg-bench-bom` maps the physical setup to BOM, connectors, and bench YAML.
- `lg-preflight` can later consume bench config style data to validate readiness.
- `lg-test-plan --bench-config` can use generated bench YAML to bind pytest skeletons to fixtures.
