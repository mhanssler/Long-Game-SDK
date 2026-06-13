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
   uv run lg-enrich
   ```

   `lg-enrich` identifies discovered VISA/SCPI hardware, searches for likely programming/user manuals, caches the manual under `manuals/`, extracts SCPI-like commands, and merges them into the YAML schema without adding unsafe output-enabling behavior.

3. **Generate readiness and safety deliverables:**
   ```bash
   uv run lg-preflight examples/lab_preflight_bench_a.yaml -o reports/lab-readiness.md
   uv run lg-hv-safety-plan examples/hv_safety_plan_bench_a.yaml -o reports/hv-safety-plan.md
   ```

   `lg-preflight` validates bench readiness before test execution. `lg-hv-safety-plan` turns the same style of YAML bench config into a client-facing HV/PCBA test safety plan with hazards, PPE, E-stop/disconnect checks, discharge checks, interlocks, safe-state requirements, stop-work criteria, and sign-off fields.

4. **Run the bus observer:**
   ```bash
   uv run python src/long_game_sdk/sdk/observers/auto_onboarder.py
   ```

## Development
This project is built using Python 3.13+ and `uv` for dependency management.
