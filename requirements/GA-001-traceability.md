# GA-001 Gate-A Requirements Traceability Matrix

**Status:** Gate-A planning traceability. Every row is UNVERIFIED unless explicitly labeled otherwise. “Documented” means captured in a design package; it is not a compliance result.

| Requirement group | Gate-A artifact / evidence | Current status | Required next evidence |
|---|---|---|---|
| GOV-001 to GOV-005 | `architecture/GA-001-architecture-feasibility.md`; this matrix | Documented | Morgan Gate-A decision; revision-controlled evidence records |
| ARCH-001 to ARCH-006 | GA-001 Sections 2–3 | Proposed architecture | Pin/domain/interface drawings and native preliminary capture after owner approval |
| ELEC-001 to ELEC-008 | GA-001 Sections 2 and 4; cost/open-items Section 3 | Partially allocated | Low-voltage resistance budget; selected-device compliance; live measurements |
| PWR-001 to PWR-009 | GA-001 Section 6 | Plausibility screen only | Selected-device DC-SOA, thermal/airflow model, hot-start bench test |
| ACC-001 to ACC-005; RESP-001 to RESP-003 | GA-001 Section 4.3; OPEN-03/04/17 | Unverified | Full error/control-loop budget and measurement-bandwidth test plan |
| SENSE-001 to SENSE-005 | GA-001 Sections 2 and 4.3 | Proposed | Sense-fault boundary, remote-sense limits and fault tests |
| ISO-001 to ISO-002; AC-001; CONN-001 to CONN-003 | GA-001 Section 3 | Proposed | Insulation/creepage/withstand allocation, PE/harness design, source/fault evidence |
| SAFE-001 to SAFE-011 | GA-001 Section 5 | Proposed fault architecture | Component-level independent latch/inhibit design and bounded fault injection |
| THERM-001 to THERM-007 | GA-001 Sections 3 and 6 | Plausibility screen only | Thermal stack model and physical thermal qualification |
| UI-001 to UI-009 | Board partition only | Unverified | Final UI state machine and implementation/bench evidence |
| COM-001 to COM-009; SW-001 to SW-002 | Isolated interface intent only | Unverified | SDK branch/interface inspection; transport and ownership specification |
| LOG/EVT requirements | Not architected beyond MCU partition | Unverified | Buffer/storage and SDK acquisition design |
| TRIG requirements | Isolated trigger intent only | Unverified | Input thresholds, isolation and timing design |
| INT requirements | GA-001 Sections 2–3 and 5 | Proposed | Relay/feedback/bypass fault matrix and test design |
| CAL/PRE/FW requirements | GA-001 Section 4.3 | Unverified | Calibration and firmware recovery specification |
| MECH/DBG requirements | Removable subassembly/PE enclosure intent | Unverified | Envelope, CAD, test-access and serviceability design |
| TOOL/DOC/verification/release requirements | Repository artifacts created; Gate-A plan in cost/open-items Section 4 | Partially documented / planned | Tool/resource confirmation; reproducible toolchain records; later Gate-B package |

## Gate-A non-claims

This matrix does not claim that a requirement has passed because an artifact exists. In particular, no MOSFET, fuse, eFuse, isolator, fan, thermal solution, insulation system, controller, or mechanical construction is selected or qualified by GA-001.
