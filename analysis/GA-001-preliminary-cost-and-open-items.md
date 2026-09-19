     1|# GA-001 — Preliminary Cost Screen and Open-Item Resolution List
     2|
     3|**Revision:** 0.1-draft
     4|**Date:** 2026-09-19
     5|**Gate status:** Planning evidence only. No vendor quotes, availability checks, or purchase authorization.
     6|
     7|## 1. Cost screen
     8|
     9|The following is an engineering allocation, not a priced BOM. It intentionally uses a linear-rated eight-device power stage rather than low-cost switching MOSFETs with no demonstrated DC-SOA suitability. Amounts include neither tax nor shipping and must not be represented as current distributor prices.
    10|
    11|| Assembly / function | Planning allocation (USD) | Basis / consequence |
    12||---|---:|---|
    13|| Eight-device linear-SOA MOSFET bank | 144 | Cost-sensitive; cannot substitute ordinary low-RDS(on) parts until DC-SOA is proven. |
    14|| Heatsink, fans, thermal interfaces | 94 | Low-thermal-resistance forced-air assembly; 350 W/40 C and 500 W hot-start are not yet proven. |
    15|| Main PCB plus two UI PCBs | 76 | Low-volume PCB fabrication, assembly consumables, and board connectors. |
    16|| Metal chassis, filter, hardware | 78 | PE-bonded enclosure, mounting, intake filter, fasteners and service hardware. |
    17|| Internal AC/DC, IEC, switch, fusing | 42 | Purchased isolated internal supply and mains entry components. |
    18|| LCD, controls, daughterboard cables | 46 | 4.3-inch-class display, encoder/buttons/beeper and ribbon system. |
    19|| MCU, Ethernet, USB, isolation | 64 | Includes isolated interfaces; no USB-PD allocation. |
    20|| Precision sensing, control, protection | 88 | Shunt/analogue front end/reference, hardware latch, temperature/fan monitoring, reverse/OV boundary. |
    21|| Load/sense/trigger/interlock connectors | 54 | Binding posts, BNC, rear interlock and related panel hardware. |
    22|| Wiring, buswork, assembly miscellaneous | 38 | High-current copper, harnesses, thermal/mechanical consumables. |
    23|| **Planning total** | **724** | **$224 over COST-001 before tax/shipping.** |
    24|
    25|### Finding
    26|
    27|At this stage, the requirements are **not cost-feasible by evidence** against the US$500 quantity-one ceiling. This is not a recommendation to weaken the safety, thermal, or isolation requirements. It identifies an explicit Gate-A decision:
    28|
    29|1. obtain dated quotes and seek a verified reduction without reducing the required envelope;
    30|2. amend COST-001; or
    31|3. define a staged prototype where expensive reusable fixtures/subassemblies are tracked separately only if Morgan explicitly changes the budget accounting rule.
    32|
    33|The first cost-reduction work must preserve DC-SOA, thermal performance, isolation, and independent protection. A lower-cost MOSFET with only a pulsed SOA graph is not an acceptable budget reduction.
    34|
    35|## 2. Priority resolution register
    36|
    37|| Priority | Open items | Why it blocks / bounded output required |
    38||---|---|---|
    39|| P0 — source and fault containment | OPEN-01, OPEN-02, OPEN-08, OPEN-09 | Owner-approved C-LAB U/D/K source/fault-energy record; preliminary fault responsibility matrix; protected-boundary ratings. No fuse, eFuse, physical interruption, or survival claim is selected before this. |
    40|| P0 — linear-stage feasibility | OPEN-04, OPEN-05, OPEN-17 | Manufacturer-primary DC-SOA candidate comparison at hot case, an eight-cell sharing allocation, transient thermal screen, airflow/thermal interface assumptions, and a reduced-stress validation plan. |
    41|| P0 — envelope arbitration | OPEN-03, OPEN-06, OPEN-07, OPEN-10 | 5 A clipping/tolerance policy; peak measurement/time state machine; over-500-W request behavior; remote-sense upper-bound/fault policy. |
    42|| P0 — cost | OPEN-19 | Dated quantity-one BOM/quote roll-up including shipping/tax policy. Must disclose the exact price source and availability date. |
    43|| P1 — safety interfaces | OPEN-11, OPEN-12, OPEN-13, OPEN-14 | Interlock topology/feedback/bypass contract; trigger electrical contract; SCPI transport/ownership state machine; recorder-loss behavior. |
    44|| P1 — measurement and calibration | OPEN-15 | Calibration points, reference uncertainty, range strategy, invalid-calibration behavior and independent accuracy-verification grid. |
    45|| P1 — implementation/tool context | OPEN-18, OPEN-20 | Numerical mechanical envelope and airflow clearances; confirmed project/SDK branch, Onshape location, KiCad/simulator/MCU toolchain. |
    46|| P2 — test execution detail | OPEN-16, OPEN-21 | UI state table, finite test source/energy limits, samples, tolerances, equipment uncertainty and physical qualification procedures. |
    47|
    48|## 3. Required decision records before any part selection
    49|
    50|### C-LAB source/fault envelope
    51|
    52|A source record must state the exact source identity and configuration—not merely “bench supply”—and include all fields below or label them `TBD-BLOCKING`:
    53|
    54|- normal voltage/current/power, polarity, and permitted source-control mode;
    55|- prospective short-circuit current vs. time and CV/CC/foldback/hiccup behavior;
    56|- output capacitance, ESR, cable inductance/resistance, and stored energy;
    57|- output-off/restart/down-programming waveform and latency;
    58|- remote-sense use, common-mode/PE relation, backfeed/reverse behavior;
    59|- fixture/harness identity; U/D/K physical boundary; and permitted energy states;
    60|- required current-zero, residual-voltage, post-trip-energy and manual-rearm conditions.
    61|
    62|### 5 A policy
    63|
    64|The design must separately define:
    65|
    66|- requested setpoint range;
    67|- nominal regulation target and calibrated measurement uncertainty;
    68|- hardware current-limit threshold and its tolerance;
    69|- transient measurement bandwidth and allowed peak interpretation;
    70|- behavior at a request that mathematically needs more than 5 A; and
    71|- whether the published “5 A” limit is a command limit, a measured normal-operation maximum, or both.
    72|
    73|The policy must meet ELEC-002/003, ACC-002, and RESP-002 together without calling a tolerance excursion a hidden new operating range.
    74|
    75|## 4. Near-term Gate-A evidence plan
    76|
    77|This plan is deliberately analysis-only and non-energized until its source/part boundaries are approved:
    78|
    79|1. Create a manufacturer-primary candidate ledger for linear MOSFETs with DC-SOA curves, package/thermal data, and model availability.
    80|2. Build a source-envelope record for the first qualified C-LAB configuration.
    81|3. Create a fixed thermal stack model (heatsink, fans, flow path, interfaces, enclosure and sensor positions) and use selected-device transient thermal data for the 350 W hot equilibrium → 500 W/30 s condition.
    82|4. Produce a dated, distributor-backed BOM and identify any budget deltas honestly.
    83|5. Submit the resulting package for independent senior review; only then request Morgan's Gate-A architecture decision.
    84|
    85|No requirement change is proposed by this document.
    86|