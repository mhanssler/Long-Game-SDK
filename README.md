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

1. **Install dependencies:**
   ```bash
   # Run the installation script to get uv and set up the virtual environment
   bash scripts/install_sdk.sh
   ```

2. **Verify Environment:**
   Before running tests, verify your VISA/Instrument drivers:
   ```bash
   uv run lg-check
   ```

3. **Run the bus observer:**
   ```bash
   uv run python src/long_game_sdk/sdk/observers/auto_onboarder.py
   ```

## Development
This project is built using Python 3.13+ and `uv` for dependency management.
