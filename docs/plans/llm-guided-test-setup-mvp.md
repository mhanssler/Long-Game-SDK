# LLM-Guided Test Setup MVP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build an MVP that packages all known requirement, bench, schematic, connector, driver, safety, and execution context for one test case and produces safe operator-facing connection instructions before optional execution.

**Architecture:** Add a deterministic SDK layer first: `lg-guide-test` builds a `TestContextPack` from existing requirements/test-plan inputs, DUT/fixture schematic metadata, bench config, connector map, instrument schemas, and generated pytest targets. The LLM integration comes after this context pack exists; the first version should produce structured Markdown/YAML guidance without requiring an LLM. The LLM must explain and guide, while the SDK validates, gates, and executes.

**Tech Stack:** Python 3.13+, uv, YAML schemas, pytest, existing Long Game SDK CLIs (`lg-test-plan`, `lg-bench-bom`, `lg-preflight`, `lg-safe`).

---

## Product invariant

The LLM is never the safety boundary.

The SDK must enforce:

- no execution without safe-state support
- no execution without preflight passing
- no energizing hardware without explicit operator confirmation
- no guessed wiring when connector map data is missing
- dry-run / guide-only mode by default

---

### Task 1: Define schematic context input format

**Objective:** Create a lightweight schematic metadata format that captures the details needed for guided wiring without requiring full EDA automation on day one.

**Files:**
- Create: `examples/guided_test_setup/schematic_context.yaml`
- Test: `tests/test_schematic_context.py`

**MVP input shape:**

```yaml
schematic_context:
  dut:
    name: bms_controller
    source_files:
      - docs/schematics/bms_controller.pdf
    connectors:
      J1:
        description: low-voltage harness
        pins:
          "1": { net: VIN+, max_voltage_v: 60, max_current_a: 1.0 }
          "2": { net: VIN-, max_voltage_v: 0, max_current_a: 1.0 }
          "7": { net: BMS_FAULT_N, signal_type: open_drain_logic }
    test_points:
      TP12: { net: CELL_SIM_1, description: simulated cell 1 sense node }
  fixture:
    name: bms_hil_fixture
    connectors:
      DUT_J1:
        mates_to: dut.J1
```

**Rule:** The first version can use manually curated YAML extracted from schematics. Later versions can add KiCad/Altium/PDF netlist importers.

**Verification:**

```bash
PYTHONPATH=src env -u VIRTUAL_ENV uv run pytest tests/test_schematic_context.py -q
```

---

### Task 2: Define the Test Context Pack data model

**Objective:** Create typed data structures for the context the LLM/operator needs.

**Files:**
- Create: `src/long_game_sdk/sdk/test_context_pack.py`
- Test: `tests/test_test_context_pack.py`

**Step 1: Write failing tests**

Test that a context pack includes:

- requirement ID/text
- test case ID
- required instruments
- schematic source refs
- relevant DUT/fixture nets
- relevant connector pins/test points
- bench instruments
- connector steps
- safety checks
- pytest target
- evidence artifacts

**Step 2: Implement minimal dataclasses**

Add dataclasses:

- `RequirementContext`
- `TestCaseContext`
- `SchematicContext`
- `NetContext`
- `ConnectorPinContext`
- `InstrumentContext`
- `ConnectionStep`
- `SafetyGate`
- `ExecutionContext`
- `TestContextPack`

**Step 3: Add serialization**

Implement:

```python
pack.to_dict()
pack.to_yaml()
```

**Verification:**

```bash
PYTHONPATH=src env -u VIRTUAL_ENV uv run pytest tests/test_test_context_pack.py -q
```

---

### Task 3: Build context pack from requirements, schematics, and bench config

**Objective:** Resolve a selected requirement/test case into a context pack using existing YAML artifacts.

**Files:**
- Modify: `src/long_game_sdk/sdk/test_context_pack.py`
- Test: `tests/test_test_context_pack.py`

**Inputs:**

```bash
requirements.yaml
bench_config.yaml
schematic_context.yaml
--requirement-id REQ-BMS-UV-001
```

or:

```bash
--test-case TC-BMS-UV-001
```

**Behavior:**

- Load structured requirements.
- Load schematic context.
- Find selected requirement/test case.
- Load bench config.
- Resolve relevant nets, test points, and connector pins from the requirement/test procedure.
- Match required instruments to bench instruments by role/capability.
- Include safety limits and safe-state notes.
- Fail with clear validation errors if mappings are missing.

**Verification:**

Add fixture files under:

```text
tests/fixtures/test_context_pack/
```

Run:

```bash
PYTHONPATH=src env -u VIRTUAL_ENV uv run pytest tests/test_test_context_pack.py -q
```

---

### Task 4: Generate deterministic connection instructions

**Objective:** Produce operator instructions from structured connector/harness data, not LLM guesses.

**Files:**
- Modify: `src/long_game_sdk/sdk/test_context_pack.py`
- Test: `tests/test_test_context_pack.py`

**Rules:**

- Each connection step must reference a source instrument/channel and destination DUT/fixture node.
- Include cable/current/voltage constraints if present.
- Include safety notes next to hazardous connections.
- If required connection data is missing, raise validation error.

**Example output:**

```text
1. Connect DP832 CH1 + to fixture VIN+ using banana-to-M4 lead rated >= 60 V / 1 A.
2. Connect DP832 CH1 - to fixture VIN-.
3. Connect DS1054Z CH1 probe to BMS_FAULT_N; connect probe ground to fixture logic ground.
```

**Verification:**

Tests assert exact generated instructions for example BMS bench.

---

### Task 5: Add `lg-guide-test` CLI

**Objective:** Expose the guide flow as a CLI.

**Files:**
- Modify: `pyproject.toml`
- Create/modify: `src/long_game_sdk/sdk/guide_test.py`
- Test: `tests/test_guide_test_cli.py`

**CLI:**

```bash
lg-guide-test examples/hil_bms_requirements.yaml \
  --requirement-id REQ-BMS-UV-001 \
  --bench-config examples/bms_hil_bench_architecture.yaml \
  --schematic-context examples/guided_test_setup/schematic_context.yaml \
  -o reports/guided-tests/bms-uv-guide.md
```

**Default behavior:**

- Build context pack.
- Write Markdown operator guide.
- Write YAML context pack beside it.
- Do not execute tests.

**Verification:**

```bash
PYTHONPATH=src env -u VIRTUAL_ENV uv run lg-guide-test ...
```

Expected files:

- `bms-uv-guide.md`
- `bms-uv-context-pack.yaml`

---

### Task 6: Add safety-gated execution option

**Objective:** Allow optional execution only after explicit CLI flag and operator confirmation.

**Files:**
- Modify: `src/long_game_sdk/sdk/guide_test.py`
- Test: `tests/test_guide_test_cli.py`

**CLI:**

```bash
lg-guide-test ... --execute --yes-i-confirm-wiring
```

**Required gates:**

- run safe-state before execution
- run preflight before execution
- require explicit confirmation flag
- run mapped pytest target only if preflight passes
- run safe-state after execution even on failure

**MVP can mock subprocess execution in tests.**

**Verification:**

Tests prove execution is blocked unless all gates are satisfied.

---

### Task 7: Add LLM prompt/context export

**Objective:** Produce a clean context bundle that an LLM can consume without rereading the repo.

**Files:**
- Modify: `src/long_game_sdk/sdk/guide_test.py`
- Test: `tests/test_guide_test_cli.py`

**CLI:**

```bash
lg-guide-test ... --llm-context reports/guided-tests/bms-uv-llm-context.md
```

**Content:**

- role: test setup guide
- selected requirement/test case
- relevant schematic nets, pins, connectors, and test points
- detected/declared instruments
- connector map
- safety constraints
- execution target
- hard safety rules
- allowed actions / disallowed actions

**Important:** The LLM context should say:

> Do not infer missing wiring. If a required connection is absent from the connector map, stop and ask for the missing mapping.

---

### Task 8: Update docs and examples

**Objective:** Make the workflow usable by a new user.

**Files:**
- Create: `docs/guided-test-setup.md`
- Modify: `README.md`
- Add examples under: `examples/guided_test_setup/`

**Docs should show:**

```bash
uv run lg-guide-test examples/hil_bms_requirements.yaml \
  --requirement-id REQ-BMS-UV-001 \
  --bench-config examples/bms_hil_bench_architecture.yaml \
  --schematic-context examples/guided_test_setup/schematic_context.yaml \
  -o reports/guided-tests/bms-uv-guide.md
```

Explain:

- guide-only default
- safety-gated execution
- context pack purpose
- LLM integration boundary

---

### Task 9: Full verification and commit

**Objective:** Verify the feature and preserve repo state.

**Commands:**

```bash
PYTHONPATH=src env -u VIRTUAL_ENV uv run pytest tests/test_test_context_pack.py tests/test_guide_test_cli.py -q
PYTHONPATH=src env -u VIRTUAL_ENV uv run pytest -q
PYTHONPATH=src env -u VIRTUAL_ENV uv run lg-guide-test examples/hil_bms_requirements.yaml \
  --requirement-id REQ-BMS-UV-001 \
  --bench-config examples/bms_hil_bench_architecture.yaml \
  --schematic-context examples/guided_test_setup/schematic_context.yaml \
  -o reports/guided-tests/bms-uv-guide.md
```

**Commit:**

```bash
git add src/long_game_sdk/sdk/test_context_pack.py src/long_game_sdk/sdk/guide_test.py tests/test_test_context_pack.py tests/test_guide_test_cli.py docs/guided-test-setup.md README.md examples/guided_test_setup pyproject.toml
git commit -m "feat: add guided test setup context packs"
```
