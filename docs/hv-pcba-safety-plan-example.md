# HV/PCBA Test Safety Plan

- Rig: bench-a
- DUT type: hv-pcba
- DUT: inverter-control-board (DUT-HV-001)
- Location: Oakland bench A
- Generated: example output from `uv run lg-hv-safety-plan examples/hv_safety_plan_bench_a.yaml`
- Operator: Not assigned
- Reviewer: Not assigned
- Plan status: READY FOR REVIEW

## DUT and Test Setup Summary

- Maximum voltage: 420 V
- Maximum current: 8 A
- Instruments:
  - main_psu: Rigol DP832 (USB0::0x1AB1::0x0E11::DP8C000000000::INSTR)
  - electronic_load: Rigol DL3021 (USB0::0x1AB1::0x0E11::DL3C000000000::INSTR)
- Energy sources:
  - 420 VDC battery simulator
  - 24 V auxiliary supply

## HV Hazard Inventory

- Stored energy in DC link capacitors
- Shock hazard above 50 VDC during probing or fixture access
- Arc flash or tool damage from incorrect probe placement

## Required PPE

- Safety glasses
- Class 0 voltage-rated gloves when exposed HV is present
- Insulated tools for HV fixture adjustments

## E-stop and Disconnect Verification

- E-stop location: Front-left mushroom switch on bench frame
- E-stop verification: Press E-stop and confirm PSU output relay opens before energizing DUT.
- Disconnects:
  - Bench DC disconnect within operator reach
  - Battery simulator contactor opened by E-stop chain

## Discharge / Bleeder-Resistor Checks

- Method: Use rated bleeder fixture across HV bus after source shutdown.
- Verification: Verify bus below 50 V with CAT III DMM before handling DUT or fixture.

## Interlock Checklist

- Fixture lid switch closed before HV enable
- Area rope line installed and observer outside boundary

## Safe-State Requirements

- All PSU outputs OFF before connect/disconnect
- Electronic load input OFF before wiring changes
- DUT contactors open before removing covers
- Run lg-safe before and after live hardware tests

## Operator Pre-Job Briefing

- Review shock boundaries and no-touch zones
- Assign one operator and one observer
- Confirm stop-work words and E-stop location

## Stop-Work Criteria

- Unexpected smell, smoke, sound, heat, or visible arcing
- Measured voltage or current exceeds configured limits
- Interlock bypass, damaged insulation, or uncertain wiring state

## Sign-Off

- Operator: ____________________  Date: __________
- Reviewer: ____________________  Date: __________
- Notes / deviations: ________________________________________________
