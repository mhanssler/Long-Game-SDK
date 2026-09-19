     1|# GA-001 — LGT-ELOAD-500 Architecture and Feasibility Package
     2|
     3|**Revision:** 0.1-draft
     4|**Date:** 2026-09-19
     5|**Status:** Gate A package — **NOT APPROVED / NOT FABRICATION RELEASED**
     6|**Requirements baseline:** `requirements/Long_Game_Eload_Requirements.md`, Rev 0.1, SHA-256 `e0a675e4cb2e2047ddc1106b1d26fcbfa7fc001e512a59d18d29ea25184cb628`
     7|
     8|## 1. Gate-A decision requested
     9|
    10|Approve, reject, or amend the following **development architecture** for bounded analysis and unpopulated/preliminary native capture only. This decision does **not** authorize purchasing, fabrication, energization, or a claim that the 350 W / 500 W requirements are physically verified.
    11|
    12|**Proposed architecture:** an eight-cell, source-referenced, linear MOSFET electronic-load stage; isolated host/UI interfaces; a PE-bonded metal chassis; a qualified, finite C-LAB source configuration; and independent hardware inhibit/trip paths. The architecture retains the approved multiple-linear-MOSFET starting point in ARCH-005.
    13|
    14|## 2. Theory of operation
    15|
    16|### Normal operation
    17|
    18|The DUT positive terminal enters a protected high-side input boundary. It feeds a positive force bus and eight paralleled linear sink cells. Each cell contains a linear-rated N-channel MOSFET, individual gate resistance, a Kelvin-connected source ballast resistor, gate clamp, and temperature/current-sharing test access. The MOSFET drains connect to the positive force bus; their source-ballast returns join at the negative force return.
    19|
    20|A control loop senses selected local or remote voltage and summed load current. It generates a bounded analogue command for the cell gate-drive distribution. Firmware provides commands, mode arithmetic, monitoring, display, SCPI/SDK, logging, and sequencing. Firmware is not credited for primary load disable.
    21|
    22|At 3 V / 5 A, the entire resistance budget implied by the terminal requirement is at most 0.6 ohm. Allocation of that budget among the in-instrument force path and the regulation elements is unresolved and must be completed before a compliance claim. Four-wire remote sense affects the selected regulation/measurement point; it is never part of the high-current return.
    23|
    24|### Default-off and permit state
    25|
    26|On mains startup, auxiliary-rail loss, reset, watchdog expiry, gate-drive loss, interlock opening, hardware overtemperature, or a validated input fault, an analogue hardware trip latch removes the gate-drive permit and discharges/clamps every MOSFET gate to its source. This action is dominant over MCU DAC/PWM and SCPI commands. The normal load state is OFF. A cleared fault never re-enables loading; firmware requires acknowledgment and deliberate enable.
    27|
    28|### Fault model boundary
    29|
    30|The internal stage is not credited with unlimited interruption ability. Gate inhibit limits controlled MOSFET current but cannot alone clear an arbitrary external battery/low-ESR bus through a failed-short power MOSFET. Before a source-dependent part selection or fault-survival claim, the product must have an owner-approved C-LAB source and U/D/K fault-energy contract (OPEN-01/02). Direct batteries, PV, parallel buses, and regenerative sources remain outside Rev A source admission unless separately qualified.
    31|
    32|## 3. Partition and interfaces
    33|
    34|```text
    35| AC mains ─ IEC/fuse/switch ─ purchased medically/industrial isolated AC/DC ──┐
    36| PE ───────────────────────────── metal chassis ───── shields / mechanical   │
    37|                                                                              ▼
    38|                      isolated low-voltage auxiliary supplies
    39|                                  │
    40|   host PC ─ USB-C isolator ─┐    ├── isolated Ethernet PHY/magnetics
    41|                             ├────┤
    42|   display/control boards ───┘    ├── MCU / measurement / mode logic
    43|                                  │
    44| DUT + ─ DC fuse ─ reverse/OV boundary ─ positive force bus ─ drains (8 cells)
    45| DUT S+ ─ protected high-Z sense ────────────────────────────────────────────┐
    46| DUT S− ─ protected high-Z sense ────────────────────────────────────────────┼─ selected-sense ADC
    47| DUT − ─ negative force return ─ source ballast (each cell) ─ sources ───────┘
    48|
    49| EXT TRIG ─ galvanically isolated receiver ─ MCU capture
    50| INT interlock ─ isolated monitored permit circuit ─ dominant hardware gate-inhibit latch
    51|```
    52|
    53|**Isolation intent:** DUT force/sense remain floating from PE and computer ground. USB, Ethernet, trigger, and interlock must cross the boundary through selected isolation components or an equivalently justified system boundary. The chassis remains bonded to PE; no intentional DUT-to-PE connection is introduced.
    54|
    55|**Three-board partition:**
    56|- **Main board:** MCU, measurement, control, isolated host interfaces, auxiliary distribution, protection latch, fan/temperature functions, and interfaces to the removable power-stage subassembly.
    57|- **Display board:** 4.3-inch class LCD and display-interface components only.
    58|- **Control board:** encoder, switches, beeper, and local-control signals only.
    59|- **Removable subassembly:** MOSFET bank, source ballasts, heatsink, fans, thermal sensors, and short high-current buswork. The main board is replaceable independently.
    60|
    61|## 4. Proposed load-cell architecture
    62|
    63|### 4.1 Why linear is retained for Gate A
    64|
    65|The required 3 V / 5 A corner, normal CC/CV/CP/CR behavior, 100 ms setting response, and no dedicated pulse-load requirement favor a linear sink for a first bounded prototype. A switching dissipative or regenerative architecture remains a legitimate future alternative, but it adds an energy-destination, low-voltage start/backfeed, EMI, and control-loop program not justified before the linear feasibility screen is complete.
    66|
    67|Linear operation is **not yet feasible by assertion**. Every candidate MOSFET must pass a manufacturer-primary, elevated-temperature DC-SOA screen at its allocated voltage/current/dissipation. A pulsed SOA point, package power number, or low RDS(on) is not a DC-SOA pass.
    68|
    69|### 4.2 Initial allocation for screening only
    70|
    71|- Eight nominally identical linear-rated N-MOSFET cells.
    72|- Nominal continuous allocation: 43.75 W/cell at 350 W total.
    73|- Nominal peak allocation: 62.5 W/cell at 500 W total.
    74|- The 30 s excess-energy increment from 350 W to 500 W is 4.5 kJ.
    75|- Individual cell source resistors provide negative feedback and observable current-sharing access. Final resistance/value/power and the exact current-sharing acceptance band are OPEN-04/05 design outcomes.
    76|- Per-cell gate resistors, gate-to-source clamps, and a non-firmware dominant gate-pull-down are required.
    77|
    78|The allocation is a topology screen, not an equal-sharing assumption. Validate imbalance from VGS variation, source-resistor tolerance/TCR, thermal gradients, gate distribution, and the actual DC-SOA locus.
    79|
    80|### 4.3 Measurement and regulation
    81|
    82|- Precision Kelvin shunt in the negative force path; separate force and sense landings.
    83|- Independent selected-voltage divider/ADC path for local and remote sense.
    84|- Precision voltage reference and low-offset analogue front end; calibration is software-stored and CRC-protected.
    85|- An analogue current-limit/kill comparator is separate from the normal MCU control output and must win on a single fault.
    86|- Firmware computes CP and CR setpoints and supplies a bounded current command. It cannot request more than the hardware current clamp permits.
    87|
    88|This decomposition is proposed so that a future analogue current loop can be designed to retain a bounded response when the firmware is delayed, while preserving digital modes and traceability. Stability, compensation, ADC/DAC architecture, and the 5 A tolerance policy are unresolved and require evidence.
    89|
    90|## 5. Protection architecture — required independent actions
    91|
    92|| Fault / condition | Required primary response | Independent path / evidence still needed |
    93||---|---|---|
    94|| MCU fault, reset, watchdog expiry | Gate permit removed; load OFF | Hardware watchdog and dominant analogue inhibit, physically verified without application firmware |
    95|| Power-stage thermal zone high | Gate permit removed; load OFF | Independent comparator/latch per thermal-zone scheme; sensor-open/short coverage |
    96|| Fan demanded but failed | Inhibit/disable; latched fault | Tach monitoring plus timeout; prove at temperature and during startup |
    97|| Interlock opens or cable removed | Hardware inhibit, cancel arming | Isolated, de-energize-to-inhibit permit and monitored relay-feedback diagnosis |
    98|| Reverse input ≤100 V | No loading, no damage | MOSFET-based reverse block that works with auxiliary rails absent; test at bounded source energy |
    99|| Positive input >100 V to 120 V | No loading, no damage | High-side fault isolation/OV comparator; 120 V is survival only, not normal operation |
   100|| Controlled load OFF | ≤1 mA DUT input after 10 ms | Gate inhibition plus a verified low-leakage input/sense architecture |
   101|| Failed-short control MOSFET / high-energy source | Not closed by this package | C-LAB U/D/K energy contract, series interruption/conditioner analysis, physical fault tests |
   102|
   103|A fuse is retained as a physical, replaceable layer but is not credited as the fast or sole interrupter. Its voltage/interrupt rating and coordination are BLOCKED on OPEN-01/02.
   104|
   105|## 6. Preliminary thermal/SOA feasibility screen
   106|
   107|A simple eight-cell screen uses a provisional 0.08 C/W forced-air heatsink-to-ambient value and 0.60 C/W combined junction-to-heatsink path per device (0.40 C/W junction-to-case plus 0.20 C/W interface). At 40 C ambient it predicts:
   108|
   109|| Condition | Heatsink screen | Per-cell power | Junction screen |
   110||---|---:|---:|---:|
   111|| 350 W continuous | 68 C | 43.75 W | 94.25 C |
   112|| 500 W equilibrium comparison only | 80 C | 62.5 W | 117.5 C |
   113|
   114|This does **not** prove the required 30-second hot-start case; it omits transient thermal impedance, heat spreading, airflow distribution, filter pressure drop, contact/interface variability, sensor lag, device imbalance, enclosure recirculation, and production tolerance. This only creates a **hypothesis to test**: an eight-cell scale with a low-thermal-resistance forced-air sink may be worth a selected-device feasibility study. It provides no comparative evidence for any other cell count. Gate-A feasibility remains conditional on selected-device DC-SOA curves at hot-case VDS/ID; a transient thermal model for the 30-second increment; an enclosure airflow model; and a physical hot-start test plan.
   115|
   116|## 7. Source admission and C-LAB development boundary
   117|
   118|For **development safety analysis**, the first test configuration is proposed to be one specified isolated laboratory source and exact harness. This is not yet a Rev A supported-use limitation. Morgan must decide whether C-LAB is only a development configuration that still leads to SYS-001 support as written, or an explicit staged-scope amendment; this package makes no unilateral scope change. The first configuration must state source model/revision, output-C/ESR, control/foldback behavior, maximum current-versus-time, cable L/R, remote-sense state, output-off/restart behavior, common-mode relation to PE, and permitted operating states.
   119|
   120|For this package, **U** is the raw source-side boundary, **D** is the finite conditioned/instrument-input boundary that a later source contract must define, and **K** is the DUT force/Kelvin measurement plane. The boundaries are bookkeeping for fault-energy and performance allocation only; no conditioner is selected or credited. Until the record is approved, the following are explicitly **not admitted** for a protection claim: batteries, PV arrays, parallel supplies, low-ESR capacitor banks, sources with regeneration, or arbitrary laboratory supplies. This is a source-qualification boundary, not a claim that the instrument cannot later be qualified with them.
   121|
   122|## 8. Cost finding
   123|
   124|The complete quantity-one cost target of US$500 is **not yet shown feasible**. The preliminary cost screen in `analysis/GA-001-preliminary-cost-and-open-items.md` identifies the cost-sensitive items: linear-SOA MOSFET bank, forced-air thermal assembly, enclosure, isolated communications, high-voltage protection, and PCB fabrication. A dated, purchasable BOM is required before Gate-A approval can claim COST-001 support.
   125|
   126|## 9. Gate-A disposition
   127|
   128|| Gate-A evidence element | Disposition |
   129||---|---|
   130|| Principal theory of operation | Drafted in this package |
   131|| Topology selection | Conditional linear prototype architecture proposed |
   132|| Isolation-domain definition | Drafted; ratings/creepage/withstand remain BLOCKED |
   133|| Power/thermal feasibility | Plausibility screen only; not closed |
   134|| DC-SOA evidence | BLOCKED pending selected-device manufacturer data and hot-case analysis |
   135|| Fault interruption / source admission | BLOCKED pending owner-approved C-LAB U/D/K contract |
   136|| Quantity-one cost | BLOCKED pending dated BOM/quotes |
   137|| Requirement changes | None proposed or applied |
   138|
   139|**Conclusion:** this is a credible Gate-A starting architecture, but not an approval recommendation yet. The mandatory closure items are the selected-MOSFET DC-SOA screen, source/fault-energy contract, detailed thermal stack/airflow analysis, 5 A arbitration policy, and dated costed BOM.
   140|