     1|# GA-003 — Preliminary Development Component Selection Register
     2|
     3|**Revision:** 0.1-draft
     4|**Date:** 2026-09-19
     5|**Authority:** Morgan authorized preliminary component selections on 2026-09-19.
     6|**State:** **Development selection only — NOT a released BOM, purchase authorization, fabrication release, or safety qualification.**
     7|
     8|## Selection semantics
     9|
    10|A **development selection** fixes the part targeted for the next analysis and prototype architecture work. It may be replaced if manufacturer-primary evidence, source/fault analysis, thermal/DC-SOA analysis, cost, availability, or independent review fails. A **release selection** requires the Gate-A/Gate-B evidence defined by the requirements baseline.
    11|
    12|This distinction preserves GOV-002 through GOV-005. It is not a refusal to select components: the selections below are now the working design basis.
    13|
    14|## 1. Power-stage development selection
    15|
    16|| Function | Development selection | Status | Rationale | Required promotion evidence |
    17||---|---|---|---|---|
    18|| Linear sink MOSFET | **Littelfuse/IXYS IXTH80N20L2**, eight nominal positions, TO-247AD | Development selected; blocked from release | 200 V-class member of the Littelfuse/IXYS linear-MOSFET family. This gives a materially more credible voltage starting point than a 100 V device for a 100 V normal input plus a separate 120 V survival requirement. It is selected for the next evidence screen, not credited as suitable. | Revision-controlled manufacturer datasheet; readable elevated-temperature DC/FBSOA locus at worst allocated VDS/ID; transient thermal data; genuine/available sourcing; mounting/current-sharing/thermal analysis. |
    19|| Cell current-sharing element | One Kelvin source ballast resistor per MOSFET, value/package **TBD after DC-SOA/control allocation** | Architecture selected; exact part blocked | Required by ARCH-005 and for observable sharing. A resistance/value chosen before gate-loop and thermal allocation would be invented. | Cell imbalance, loss, pulse/continuous rating, Kelvin layout, and current-sharing analysis. |
    20|| Gate network | One individual gate resistor and gate-source clamp per MOSFET; exact values/parts TBD | Architecture selected; exact part blocked | Required by ARCH-005 and protection intent. | Gate-loop model, clamp energy/fault path, turn-off/inhibit response and layout. |
    21|| Thermal monitoring | Minimum three power-stage sensors plus inlet/ambient sensor, each with independent shutdown-path consideration | Architecture selected; exact sensor TBD | Directly required by THERM-001 and SAFE-007/008. | Sensor range/accuracy/fault coverage, placement, hardware-trip topology and wiring. |
    22|
    23|## 2. Components deliberately not selected yet
    24|
    25|| Category | Why it remains blocked |
    26||---|---|
    27|| DC input fuse, high-side electronic disconnect, reverse-polarity MOSFETs, clamps/snubber | OPEN-01/02/08: no approved source fault-energy/current-time contract. Selecting these now would invent interruption and energy duty. |
    28|| Mains AC/DC module, mains fuse/switch/EMI arrangement | Exact auxiliary rail demand, enclosure/PE scheme, safety class and physical packaging are not closed. |
    29|| Isolators, Ethernet PHY/magnetics, USB isolator, trigger/interlock isolators | OPEN-09 requires working/transient/withstand/common-mode and reference/shield allocation. |
    30|| Current shunt, reference, ADC/DAC, control amplifiers | OPEN-03/04/17: accuracy, bandwidth, control architecture, 5 A clipping and dissipation budgets are not allocated. |
    31|| Heatsink, fans, enclosure | OPEN-05/18: selected MOSFET thermal model, mechanical envelope, pressure-drop and airflow analysis are still required. |
    32|| MCU/display/control components | They can be selected after the safety/measurement and isolated-interface boundaries constrain I/O, memory, rails and physical interface requirements. |
    33|
    34|## 3. Immediate component-evidence task
    35|
    36|The next task is not generic research: obtain and freeze the official IXTH80N20L2 datasheet/model evidence, then execute the explicit eight-cell hot-case DC-SOA and thermal screen. The candidate does not become a released component if that task passes; it merely earns a supported analysis disposition.
    37|
    38|## 4. No silent requirement changes
    39|
    40|No approved operating, safety, cost, source, or verification requirement changed in making this development selection. The $500 budget remains unsupported, and the admitted source scope remains an owner decision.
    41|