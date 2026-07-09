# 800 V BMS HiL Bench — Test Setup Architecture

## Executive Summary

Long Game translated the validation setup for **800 V BMS HiL Bench** into a reviewable, buildable, and automatable bench package.

## Project Metadata

- Client: Example Hardware Co.
- Phase: DVT
- Bench: bms_hil_bench_a
- DUT: 800 V BMS controller
- Automation host: macOS or Linux laptop running long-game-sdk
- Evidence root: reports/bms-hil

## Requirements Covered

- BMS-REQ-001
- BMS-REQ-002

## Test Setup Diagram Blocks

- DUT / subsystem under test
- Fixture, breakout, or harness
- Power supplies and loads
- Measurement instruments
- Communication interfaces
- Safety controls and E-stop chain
- Automation host and evidence storage

## Equipment BOM

### hv_supply — pack_voltage_source

- Category: power
- Purpose: Simulate battery pack voltage during BMS tests.
- Required/optional: required
- Quantity: 1
- Manufacturer/model: EA Elektro-Automatik / Magna-Power / Chroma / equivalent / TBD
- Control interface: ethernet
- Calibration required: yes
- Selection criteria:
- Voltage range covers 0-850 V with margin.
- Remote API supports output enable/disable and OVP/OCP programming.
- Output defaults off after power cycle or communication loss.
- Safety notes:
- Set OVP/OCP before enabling output.
- Output OFF before wiring changes.

### electronic_load — downstream_load

- Category: load
- Purpose: Sink controlled current for contactor/fault response checks.
- Required/optional: required
- Quantity: 1
- Manufacturer/model: Chroma / Rigol / BK Precision / equivalent / TBD
- Control interface: ethernet_or_usb
- Calibration required: yes
- Selection criteria:
- Dynamic load mode supports fault transients.
- Remote API supports input off command.
- Safety notes:
- Input OFF before connect/disconnect.

### can_interface — bms_telemetry

- Category: comms
- Purpose: Log and command BMS CAN traffic.
- Required/optional: required
- Quantity: 1
- Manufacturer/model: Kvaser / PEAK / Vector / TBD
- Control interface: usb
- Calibration required: no
- Selection criteria:
- Good timestamp quality.
- Python driver support.
- Isolation preferred.
- Safety notes:
- Prefer isolated adapter on HV bench.

## Connector / Harness Map

### hv_positive

- Location: DUT HV input
- Family: Amphenol SurLok / equivalent
- Mating connector: TBD
- Pin count: 1
- Voltage/current rating: 1000 V / 120 A
- Signal type: HV power
- Cable type: orange HV cable
- Shielding/grounding: chassis bonding per safety review
- Strain relief: fixture clamp
- Risk notes: Verify touch-safe cover and creepage/clearance.

### lv_control_header

- Location: DUT LV connector
- Family: Molex Micro-Fit / equivalent
- Mating connector: TBD
- Pin count: 12
- Voltage/current rating: 60 V / 8 A
- Signal type: LV power, CAN, enables, interlocks
- Cable type: shielded multi-conductor
- Shielding/grounding: shield drain to fixture ground
- Strain relief: boot and cable tie
- Risk notes: Prevent swapped CAN and enable pins.

## Safety Controls

- All HV sources output OFF before connect/disconnect.
- Electronic load input OFF before wiring changes.
- E-stop path verified before energizing DUT.
- Discharge verification completed before touch.

## Generated Artifacts

- Setup architecture report: human-readable design review package.
- Equipment BOM CSV: procurement and calibration planning.
- Connector/harness map CSV: fixture/cable build guidance.
- Bench config YAML: machine-readable input for preflight and test execution.

## Next Step

Review this package with hardware, firmware, safety, and test owners before procurement or live hardware execution.
