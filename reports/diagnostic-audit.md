# Long Game Diagnostic Audit

- Rig: bench-a
- DUT type: pcba
- Generated: 2026-06-14T22:52:49.341709+00:00
- Operator: Not captured
- DUT serial: Not captured
- Git commit: 2f3eca8
- Audit status: REMEDIATION REQUIRED
- Lab health score: 0/100
- Readiness band: Critical remediation required
- Result mix: 6 pass / 3 warn / 4 fail / 0 skip

## Executive Summary

Long Game Technologies performed an automated readiness audit covering instrument connectivity, identity, safety guardrails, operator/DUT traceability, and data-path integrity. The goal is to separate real DUT failures from lab infrastructure noise before engineering teams commit time to a test campaign.

## Health Score Interpretation

- 90-100: Client-demo ready; maintain current controls and archive reports with test data.
- 75-89: Operational with minor gaps; close warnings before scaling or handing off to new operators.
- 50-74: Not ready; blockers or repeat warnings can create flaky tests and no-fault-found loops.
- 0-49: Critical remediation required before live hardware work.

## Blocking Risks

- **environment/required_env** (FAIL): LG_OPERATOR: missing.
- **environment/required_env** (FAIL): LG_DUT_SERIAL: missing.
- **instrument/instrument_reachable** (FAIL): main_psu: PyUSB does not seem to be properly installed. Please refer to the PyUSB documentation and install a suitable backend like libusb 0.1, libusb 1.0, libusbx, libusb-win32 or OpenUSB. If you do not have administrator/root privileges, you may try installing the "libusb-package" Python package to provide the necessary backend. No module named 'libusb_package' No backend available
- **instrument/instrument_reachable** (FAIL): scope: PyUSB does not seem to be properly installed. Please refer to the PyUSB documentation and install a suitable backend like libusb 0.1, libusb 1.0, libusbx, libusb-win32 or OpenUSB. If you do not have administrator/root privileges, you may try installing the "libusb-package" Python package to provide the necessary backend. No module named 'libusb_package' No backend available

## Quick Wins / Configuration Gaps

- **environment/operator_captured** (WARN): Operator name not supplied; set runtime.operator or LG_OPERATOR.
- **environment/dut_serial_captured** (WARN): DUT serial not supplied; set runtime.dut_serial or LG_DUT_SERIAL.
- **safety/output_disabled_on_start** (WARN): main_psu: output-state query not configured/injected.

## Validated Controls

- **environment/data_output_writable** (PASS): Output directory writable: examples/reports/preflight
- **environment/git_commit_captured** (PASS): Git commit captured: 2f3eca8
- **safety/calibration_date** (PASS): main_psu: calibration due 2027-06-01.
- **safety/voltage_limit** (PASS): main_psu: voltage_limit configured as CH1 <= 5.50 V, CH2 <= 15.0 V, CH3 <= 15.0 V.
- **safety/current_limit** (PASS): main_psu: current_limit configured as CH1 <= 1.0 A, CH2 <= 0.5 A, CH3 <= 0.5 A.
- **safety/calibration_date** (PASS): scope: calibration due 2027-06-01.

## Category Breakdown

- environment: 2 pass / 2 warn / 2 fail / 0 skip
- instrument: 0 pass / 0 warn / 2 fail / 0 skip
- safety: 4 pass / 1 warn / 0 fail / 0 skip

## Recommended 30-Day Improvement Plan

1. Resolve all blocking instrument reachability, identity, and safety-control failures before live DUT testing.
2. Convert recurring warnings into explicit bench YAML fields so readiness is repeatable across operators.
3. Pair this audit with `lg-hv-safety-plan` for HV/PCBA work and archive both reports beside raw test data.
4. Add generated schemas/manual enrichment for placeholder or unknown instruments to reduce custom driver maintenance.
5. Re-run `lg-safe`, `lg-preflight`, and `lg-audit` after remediation to prove the lab is ready for a customer-facing demo or campaign kickoff.

## Consultant Notes

- Primary value: reduce flaky infrastructure failures and no-fault-found investigations before they consume engineering time.
- Suggested next conversation: review each blocker, assign owner/date, and define the minimum safe configuration for the next test campaign.
