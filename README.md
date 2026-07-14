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
   uv run lg-preflight examples/lab_preflight_bench_a.yaml -o reports/lab-readiness.md
   uv run lg-audit examples/lab_preflight_bench_a.yaml -o reports/diagnostic-audit.md
   uv run lg-hv-safety-plan examples/hv_safety_plan_bench_a.yaml -o reports/hv-safety-plan.md
   uv run lg-bench-bom examples/bms_hil_bench_architecture.yaml -o reports/bench-bom --prefix bms-hil
   uv run lg-schematic-import examples/guided_test_setup/bms_pin_map.csv --dut-name bms_controller -o examples/guided_test_setup/bms_schematic_context.yaml
   uv run lg-flash-openocd examples/openocd/stm32f4_flash.yaml -o reports/openocd/stm32f4-flash-plan.md
   uv run lg-guide-test examples/hil_bms_requirements.yaml --requirement-id BMS-REQ-002 --bench-config examples/bms_hil_bench_architecture.yaml --schematic-context examples/guided_test_setup/bms_schematic_context.yaml --flash-config examples/openocd/stm32f4_flash.yaml -o reports/guided-test/bms-req-002
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md --pytest-dir tests/generated/hil_bms
   uv run lg-test-plan examples/hil_bms_requirements.yaml -o reports/hil-bms-test-plan.md --pytest-dir tests/generated/hil_bms_bound --bench-config examples/hv_safety_plan_bench_a.yaml
   ```

   `lg-preflight` validates bench readiness before test execution. `lg-audit` converts those readiness checks into a client-facing Diagnostic Audit with a health score, blockers, quick wins, and a 30-day improvement plan. `lg-hv-safety-plan` turns the same style of YAML bench config into a client-facing HV/PCBA test safety plan with hazards, PPE, E-stop/disconnect checks, discharge checks, interlocks, safe-state requirements, stop-work criteria, and sign-off fields. `lg-bench-bom` turns a test setup architecture YAML into a setup report, equipment BOM CSV, connector/harness map CSV, and generated bench config YAML. `lg-schematic-import` converts curated pin maps, Altium CSVs, KiCad netlists, and text/PDF schematic notes into canonical `schematic_context` YAML for guided wiring. `lg-flash-openocd` validates OpenOCD flash configs and writes a dry-run flash plan by default; actual flashing requires `--execute --yes-i-confirm-target-wiring`. `lg-guide-test` combines requirements, bench config, schematic context, optional flash config, and optional pytest target into a deterministic `test-context-pack.yaml` plus an operator wiring/safety guide. `lg-test-plan` converts structured requirements YAML into a verification test plan with requirement traceability, generated test case IDs, safety/preflight notes, evidence expectations, optional pytest skeletons via `--pytest-dir`, and bench-bound fixture bundles via `--bench-config`.

4. **Run the bus observer:**
   ```bash
   uv run python src/long_game_sdk/sdk/observers/auto_onboarder.py
   ```

## Development
This project is built using Python 3.13+ and `uv` for dependency management.
