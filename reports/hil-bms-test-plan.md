# Verification Test Plan: 800 V BMS HiL Bench

## Executive Summary

This test plan translates 2 requirement(s) into verification activities, traceability records, and evidence expectations.

## Project Metadata

- Client: Example Hardware Co.
- Product / subsystem: 800 V BMS HiL Bench
- Program phase: DVT
- Requirements source: examples/hil_bms_requirements.yaml

## Scope and Assumptions

- This plan covers requirements included in the supplied YAML document.
- Test execution requires calibrated instrumentation and bench preflight before collecting evidence.
- Each generated test case should be reviewed by engineering and safety owners before live hardware use.

## Requirement Traceability Matrix

- **BMS-REQ-001** — Pack voltage measurement accuracy
  - Type: performance
  - Verification method: test
  - Test case ID: TC-BMS-REQ-001
  - Acceptance criteria: Absolute voltage error is <= 0.5% at every commanded voltage point.
  - Evidence artifact: voltage_sweep_raw.csv, voltage_accuracy_summary.md
- **BMS-REQ-002** — Over-voltage fault response
  - Type: safety
  - Verification method: test
  - Test case ID: TC-BMS-REQ-002
  - Acceptance criteria: Fault asserted and contactor-open command observed within 100 ms.
  - Evidence artifact: fault_timing_capture.csv, can_fault_log.asc

## Safety / Preflight Controls

- **BMS-REQ-002** requires explicit safe-state/preflight review.
  - Verify simulator limits before test.
  - Force safe-state before and after test.

## Test Cases

### Test Case: `TC-BMS-REQ-001`

- Related requirement: `BMS-REQ-001`
- Objective: Verify Pack voltage measurement accuracy.
- Method: test
- Requirement text: The BMS shall report pack voltage within ±0.5% of calibrated reference measurement from 100 VDC to 800 VDC.
- Preconditions:
- Define operating conditions before execution.
- Instrumentation:
- HV programmable supply or simulator
- Calibrated DMM / DAQ voltage reference
- CAN interface
- Procedure:
  1. Put the DUT and bench into the required starting state for `BMS-REQ-001`.
  2. Verify preflight checks and instrument readiness.
  3. Execute the test activity for Pack voltage measurement accuracy.
  4. Capture required data and metadata.
  5. Return the DUT and bench to the defined safe state.
- Acceptance criteria: Absolute voltage error is <= 0.5% at every commanded voltage point.
- Expected evidence:
- voltage_sweep_raw.csv
- voltage_accuracy_summary.md
- Failure triage:
  - DUT failure indicators: measured behavior violates acceptance criteria with valid bench setup.
  - Bench/setup failure indicators: preflight, instrument, fixture, or calibration check fails.
  - Automation failure indicators: script/runtime/data-path failure prevents valid evidence capture.

### Test Case: `TC-BMS-REQ-002`

- Related requirement: `BMS-REQ-002`
- Objective: Verify Over-voltage fault response.
- Method: test
- Requirement text: The BMS shall assert an over-voltage fault and command contactor open within 100 ms when simulated cell voltage exceeds configured threshold.
- Preconditions:
- Define operating conditions before execution.
- Instrumentation:
- Cell simulator
- Logic analyzer or DAQ
- CAN interface
- Procedure:
  1. Put the DUT and bench into the required starting state for `BMS-REQ-002`.
  2. Verify preflight checks and instrument readiness.
  3. Execute the test activity for Over-voltage fault response.
  4. Capture required data and metadata.
  5. Return the DUT and bench to the defined safe state.
- Acceptance criteria: Fault asserted and contactor-open command observed within 100 ms.
- Expected evidence:
- fault_timing_capture.csv
- can_fault_log.asc
- Failure triage:
  - DUT failure indicators: measured behavior violates acceptance criteria with valid bench setup.
  - Bench/setup failure indicators: preflight, instrument, fixture, or calibration check fails.
  - Automation failure indicators: script/runtime/data-path failure prevents valid evidence capture.
- Safety notes:
  - Verify simulator limits before test.
  - Force safe-state before and after test.

## Data and Evidence Requirements

- Capture DUT serial, hardware revision, firmware version, operator, fixture ID, instrument calibration status, test script version, and git commit where applicable.
- Archive raw data beside the generated report and link each evidence artifact back to requirement ID and test case ID.

## Exit Criteria

- Every requirement has an approved verification method and test case.
- Every executed test has raw data and reviewable evidence.
- Any failure is triaged as DUT, bench/setup, automation, or requirement ambiguity.
- Safety-critical tests include documented safe-state behavior before and after execution.

## Approval

- Prepared by: ____________________ Date: __________
- Engineering review: _____________ Date: __________
- Safety review: __________________ Date: __________
- Client approval: ________________ Date: __________
