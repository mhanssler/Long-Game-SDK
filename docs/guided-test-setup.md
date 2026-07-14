# Guided Test Setup

`lg-guide-test` builds the first deterministic context pack for LLM-guided hardware test setup.

It combines:

- requirement/test intent
- bench instruments/connectors/safety controls
- schematic context: DUT connectors, pins, nets, test points
- evidence expectations
- optional pytest target
- optional firmware flash config

The command emits two artifacts:

- `test-context-pack.yaml` — machine/LLM-ready structured context
- `operator-guide.md` — human-facing setup and safety guide

## Example

```bash
uv run lg-guide-test examples/hil_bms_requirements.yaml \
  --requirement-id BMS-REQ-002 \
  --bench-config examples/bms_hil_bench_architecture.yaml \
  --schematic-context examples/guided_test_setup/bms_schematic_context.yaml \
  --flash-config examples/openocd/stm32f4_flash.yaml \
  --pytest-target tests/generated/hil_bms/test_bms_req_002.py \
  -o reports/guided-test/bms-req-002
```

## Safety model

This MVP is **guide-only**. `--execute` is intentionally refused for now.

The generated guide tells the operator what can be determined from structured inputs, but execution still requires later gates:

1. `lg-safe` before wiring changes
2. schematic-to-harness review
3. preflight pass
4. explicit wiring confirmation
5. safe-state after flashing/tests

The LLM may explain the guide, but the SDK owns execution gating.

## Product chain

```text
requirements
→ schematic context
→ bench config
→ driver/flash/test capabilities
→ deterministic context pack
→ LLM/operator guide
→ gated execution
→ evidence
```

The important design rule is that the LLM receives complete context instead of searching the repo or guessing wiring. If the context pack cannot resolve a required net/pin/test point, guided setup should stop and ask for the missing mapping.

## Current deterministic connection inference

The MVP resolves obvious setup steps from schematic nets and available bench instruments:

- `FAULT` nets → logic analyzer or DAQ
- `CELL` test points → cell simulator
- `VIN` / `PACK` nets → power source / HV supply

This is intentionally conservative. It is enough to generate a useful first operator guide while keeping ambiguity visible.
