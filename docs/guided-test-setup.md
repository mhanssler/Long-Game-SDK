# Guided Test Setup

`lg-guide-test` builds the first deterministic context pack for LLM-guided hardware test setup.

It combines:

- requirement/test intent
- bench instruments/connectors/safety controls
- schematic context: DUT connectors, pins, nets, test points
- approved, revision-controlled connection records with terminals, references, electrical limits, signal type, and isolation
- evidence expectations
- optional pytest target
- optional firmware flash-plan config (dry-run context only)

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

This MVP is **guide-only**. `--execute` is intentionally refused for now. Any included OpenOCD data is planning context only; OpenOCD execution is unavailable pending a hardened sandbox.

The generated guide tells the operator what can be determined from structured inputs. It does not authorize or perform flashing or tests. Before any separately implemented future hardware workflow, an operator would still need independent gates such as:

1. `lg-safe <bench/preflight-config.yaml>` before wiring changes
2. schematic-to-harness review
3. preflight pass
4. explicit wiring confirmation
5. safe-state after any separately operated flashing/tests

The LLM and SDK produce guide material only; neither proves that later hardware execution is safe or successful.

## Product chain

```text
requirements
→ schematic context
→ bench config
→ driver/flash-plan/test context
→ deterministic context pack
→ LLM/operator guide
→ operator review
→ future workflow outside this guide-only feature
```

The important design rule is that the LLM receives complete context instead of searching the repo or guessing wiring. If the context pack cannot resolve a required net/pin/test point, guided setup should stop and ask for the missing mapping.

## Explicit connection records

`lg-guide-test` never derives actionable wiring from net-name substrings such as `VIN`, `PACK`, `FAULT`, or `CELL`. The schematic context must provide a `revision` plus approved `connections`. Each record must include:

- instrument and terminal
- destination connector/pin or test point and net
- instrument reference/return terminal and complete connector/pin or test-point endpoint and net
- signal type and isolation model
- finite voltage/current limits
- `approved: true`
- `source_revision` matching `schematic_context.revision`

Missing records produce a `STOP` instruction. Unapproved, incomplete, non-finite, or revision-mismatched records reject context-pack generation. Instrument names must exist in bench inventory. Every DUT/fixture connector, pin, test point, and net is checked against its canonical schematic mapping; missing mappings and contradictions stop before an operator guide can render actionable wiring. Rendered endpoints always include `DUT` or `fixture` scope so identically named connectors such as `J1` cannot be confused.
