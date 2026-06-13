# Lab Readiness Diagnostic Report Template

## Executive Summary

- Client:
- Site / bench:
- DUT family:
- Diagnostic date:
- Overall readiness: Ready / Conditional / Not Ready
- Top business risk:

## Critical Risks

- Risk:
  - Evidence:
  - Impact:
  - Recommended mitigation:
  - Owner:
  - Target date:

## Instrument Inventory

- Instrument:
  - Role:
  - Manufacturer / model:
  - Serial:
  - Firmware:
  - Connection:
  - Calibration status:
  - Automation readiness:

## Automation Readiness

- Test runner / framework:
- Driver coverage:
- Required environment variables:
- Version capture:
- Operator/DUT metadata capture:
- Failure visibility/logging:

## Safety / HV Controls

- Energy sources and loads:
- Safe-state behavior before test:
- Safe-state behavior after test:
- Interlocks / E-stops:
- Output limits:
- Known gaps:

## Data Integrity Risks

- Output data path:
- Permissions / backup:
- Raw data format:
- Metadata completeness:
- Traceability to git commit / test version:

## Recommended 30-Day Improvements

1. Stabilize preflight checks that block invalid test runs.
2. Add missing instrument schemas/drivers for critical bench equipment.
3. Enforce safe-state before and after every live hardware test.
4. Store readiness reports next to raw data for review and audits.
5. Convert tribal lab setup knowledge into version-controlled rig configs.

## Fixed-Scope Follow-On Offer

Long Game Technologies can convert this diagnostic into a one-week implementation sprint:

- Day 1: Bench inventory and risk review.
- Day 2-3: Driver/schema setup for priority instruments.
- Day 4: Preflight and safe-state automation.
- Day 5: Documentation, handoff, and 30-day roadmap.
