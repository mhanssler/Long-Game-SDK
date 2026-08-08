# Long Game Technologies SDK

The Long Game Technologies SDK is a modern, Python-based framework designed for hardware-agnostic test automation. It aims to replace legacy LabVIEW/NI stacks with a modular, plug-and-play architecture for test engineering.

## Core Features
- **Hardware Agnostic:** Orchestrate tests without being tied to specific instrument drivers.
- **Auto-Discovery:** Background `bus_observer` monitors LAN/USB/GPIB for new instruments.
- **Resilient Orchestration:** Built on `asyncio` for scalable concurrency with mandatory `pre_check()` health checks for every test step.
- **Observability:** Standardized JSON-L logging for CI/CD integration and post-test data analysis.

## Project Structure
- `src/long_game_sdk/sdk/`: Core orchestration logic and test framework.
- `src/long_game_sdk/sdk/observers/`: Background observers for hardware bus monitoring.
- `examples/`: Reference implementations and power rail validation scripts.

## Quick Start

1. **Install dependencies and local hardware permissions:**
   ```bash
   # On Linux/macOS. On Linux this may prompt for sudo once to install USB udev rules.
   bash scripts/install_sdk.sh

   # On Windows (PowerShell). This relaunches through UAC as Administrator when needed.
   .\scripts\install_sdk.ps1
   ```

   The installer is intentionally interactive when OS-level USB permissions are required, so users should not have to copy/paste separate LabJack or udev commands.

2. **Verify / onboard / enrich:**
   Before running tests, verify your VISA/Instrument drivers:
   ```bash
   uv run lg-check
   uv run lg-discover
   uv run lg-onboard
   uv run lg-auto-onboard --once
   uv run lg-enrich
   ```

   `lg-enrich` identifies discovered VISA/SCPI hardware, searches for likely programming/user manuals, caches the manual under `manuals/`, extracts SCPI-like commands, and merges them into the YAML schema without adding unsafe output-enabling behavior. `lg-auto-onboard` watches for newly detected VISA instruments and runs this onboard/enrichment path in-process; use `--once` for a single scan during setup or debugging.

3. **Generate readiness, safety, and verification deliverables:**
   ```bash
   uv run lg-safe examples/lab_preflight_bench_a.yaml
   uv run lg-smoke examples/lab_preflight_bench_a.yaml
   uv run lg-preflight examples/lab_preflight_bench_a.yaml -o reports/lab-readiness.md
   uv run lg-audit examples/lab_preflight_bench_a.yaml -o reports/diagnostic-audit.md
   uv run lg-hv-safety-plan examples/hv_safety_plan_bench_a.yaml -o reports/hv-safety-plan.md
   uv run lg-bench-bom examples/bms_hil_bench_architecture.yaml -o reports/bench-bom --prefix bms-hil
   uv run lg-schematic-import examples/guided_test_setup/bms_pin_map.csv --dut-name bms_controller -o reports/schematic-import/bms_pin_map_import.yaml
   uv run lg-flash-openocd examples/openocd/stm32f4_flash.yaml -o reports/openocd/stm32f4-flash-plan.md
   uv run lg-guide-test examples/hil_bms_requirements.yaml --requirement-id BMS-REQ-002 --bench-config examples/bms_hil_bench_architecture.yaml --schematic-context examples/guided_test_setup/bms_schematic_context.yaml --flash-config examples/openocd/stm32f4_flash.yaml -o reports/guided-test/bms-req-002
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md --pytest-dir tests/generated/hil_bms
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md --pytest-dir tests/generated/hil_bms_bound --bench-config examples/hv_safety_plan_bench_a.yaml
   ```

   The importer writes its raw result to `reports/schematic-import/`; it must not overwrite the reviewed example context. The following `lg-guide-test` command deliberately uses `examples/guided_test_setup/bms_schematic_context.yaml`, which includes separately reviewed revision and approved connection records that raw import does not infer.

   With a bench/preflight config, `lg-safe` fails closed when declared equipment is absent or unbound; no-config mode is discovery-only and an empty scan is not evidence that an expected bench is safe. `lg-preflight` validates typed voltage/current limits, instrument identity, reachable setpoints, and source output state before execution. `lg-audit` converts readiness checks into a client-facing Diagnostic Audit. `lg-hv-safety-plan` generates HV/PCBA safety plans. `lg-bench-bom` produces setup reports, BOMs, harness maps, and bench YAML. `lg-schematic-import` converts curated pin maps, Altium CSVs, KiCad netlists, and text/PDF notes into canonical schematic context while rejecting conflicts, invalid ratings, empty extraction, and oversized inputs. `lg-flash-openocd` is dry-run only: it writes a proposed-command plan and refuses every live execution request pending a hardened sandbox. Its report is not evidence of target identity, flashing, or verification. `lg-guide-test` only renders approved, revision-matched connection records and never infers actionable wiring from net names. `lg-test-plan` generates traceable verification plans and optional pytest bundles.

4. **Run the bus observer:**
   ```bash
   uv run python src/long_game_sdk/sdk/observers/auto_onboarder.py
   ```

## Development

This project uses `uv` and supports Python 3.11+; development targets Python 3.13.

```bash
uv sync --group dev
PYTHONPATH=src uv run pytest -q
uv run ruff check .
uv run mypy src
```

Live hardware tests are marked `hardware` and excluded from the default suite. Run them deliberately, on a safe bench, with:

```bash
PYTHONPATH=src uv run pytest -m hardware
```
