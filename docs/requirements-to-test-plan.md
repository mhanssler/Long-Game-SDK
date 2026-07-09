# Requirements-to-Test-Plan Generator

`lg-test-plan` converts structured requirements YAML into a client-facing verification test plan.

## Why It Matters

Hardware teams often have requirements scattered across PRDs, safety docs, interface-control docs, and firmware tickets. The missing bridge is a traceable plan that turns those requirements into:

- verification methods
- test case IDs
- instrumentation needs
- safety/preflight controls
- required evidence artifacts
- exit criteria

## Command

```bash
# Generate a Markdown verification plan only.
uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md

# Generate the plan plus executable pytest skeletons.
uv run lg-test-plan examples/hil_bms_requirements.yaml \
  -o reports/hil-bms-test-plan.md \
  --pytest-dir tests/generated/hil_bms

# Generate skeletons bound to a YAML bench config.
uv run lg-test-plan examples/hil_bms_requirements.yaml \
  -o reports/hil-bms-test-plan.md \
  --pytest-dir tests/generated/hil_bms_bound \
  --bench-config examples/hv_safety_plan_bench_a.yaml
```

## Input Shape

```yaml
project:
  client: Example Hardware Co.
  product: 800 V BMS HiL Bench
  phase: DVT
requirements:
  - id: BMS-REQ-001
    title: Pack voltage measurement accuracy
    text: The BMS shall report pack voltage within ±0.5%.
    type: performance
    verification_method: test
    priority: high
    acceptance_criteria:
      pass_condition: Absolute voltage error is <= 0.5% at every commanded voltage point.
    instrumentation:
      - HV programmable supply
      - CAN interface
    evidence:
      - voltage_sweep_raw.csv
```

Supported verification methods:

- `inspection`
- `analysis`
- `demonstration`
- `test`

## Output

The generated Markdown report includes:

- executive summary
- project metadata
- requirement traceability matrix
- safety/preflight controls
- generated test cases named `TC-<REQ-ID>`
- data and evidence requirements
- exit criteria
- approval section

When `--pytest-dir` is provided, the command also writes one executable pytest skeleton per requirement. Each generated skeleton:

- embeds the requirement ID and generated test case ID
- uses markers such as `@pytest.mark.requirement("BMS-REQ-001")`, requirement type, priority, and `safety_critical`
- includes a safe-state fixture placeholder that must be replaced before live hardware use
- lists instrumentation, safety controls, and expected evidence artifacts
- calls `pytest.skip(...)` by default so unfinished hardware tests are safe to commit

When `--bench-config` is provided with `--pytest-dir`, the command also writes:

- `bench_config.yaml` copied beside generated tests
- `conftest.py` with shared `bench_config`, `instruments`, and `safe_state` fixtures
- test skeletons that receive those shared fixtures instead of defining a local placeholder `safe_state`

The generated `safe_state` fixture is still a placeholder until bench-specific driver calls are wired in, but it carries the safe-state controls from the bench YAML so reviewers can see exactly what must be automated before live hardware execution.

## Consulting Workflow

Use this before automation when a client asks, "How do we test this?"

1. Capture requirements from the client.
2. Normalize them into YAML.
3. Generate a reviewable verification plan.
4. Review gaps: ambiguous requirements, missing acceptance criteria, missing instrumentation, missing safety controls.
5. Convert mature test cases into executable scripts and evidence reports.

## Next Product Step

The next layer should connect the generated bench-bound `safe_state` fixture to real driver actions from YAML schemas: disable PSU outputs, turn loads off, verify DUT contactors open, and write structured evidence metadata before/after each test.
