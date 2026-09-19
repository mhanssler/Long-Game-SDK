     1|# Long Game Testing — Programmable DC Electronic Load
     2|## Requirements baseline and Hermes design handoff
     3|
     4|**Document revision:** 0.1
     5|**Date:** 2026-09-18
     6|**Requirements owner / prototype evaluator:** Morgan
     7|**Design agent:** Hermes
     8|**Status:** Consolidated interview draft for review. Approved requirements below were captured from the interview; the implementation has not been designed or verified by this document. This document is not architecture approval, fabrication authorization, or a commercial-release approval.
     9|
    10|---
    11|
    12|## 1. Purpose, scope, and interpretation
    13|
    14|Develop a complete, enclosed, programmable benchtop DC electronic load for testing isolated-output bench power supplies and DC–DC converters. The eventual instrument includes electronics, firmware, front-panel controls, USB/Ethernet SCPI control, integration with the Long Game Testing SDK, mechanical design, documentation, and verification evidence.
    15|
    16|Development is staged: **prototype boards → Morgan's bench evaluation → revisions → integrated instrument**. Solar-panel testing is a stretch goal, not a Rev A acceptance requirement. Dedicated high-speed pulsed/dynamic loading is not required; normal setpoint changes and the approved external-trigger functions are required.
    17|
    18|### 1.1 Authority and status
    19|
    20|- **Approved requirement:** A user decision captured during the interview. “Shall” in Sections 3–21 expresses these requirements, not an assertion that the capability already exists.
    21|- **Derived implication:** Arithmetic or interpretation needed to reconcile approved requirements. Such material is labeled separately and does not silently change a requirement.
    22|- **Open item:** A parameter, ambiguity, or implementation decision still needing analysis or approval. Section 24 is the explicit open-item register.
    23|- **Proposed verification:** A way to demonstrate compliance. Detailed setups, tolerances, sample counts, and fault conditions remain to be agreed where not already specified.
    24|
    25|All requirements initially have verification status **UNVERIFIED**. Track design intent, analytical/simulation support, and physical verification separately. Do not label a requirement verified from a schematic, a simulation that runs, or a successful firmware build alone.
    26|
    27|The consolidated IDs in this document are the baseline IDs. Earlier conversational IDs were provisional. No component count, component rating, protection threshold, or certification status is implied unless explicitly stated.
    28|
    29|### 1.2 Development governance
    30|
    31|| ID | Approved requirement |
    32||---|---|
    33|| GOV-001 | Morgan shall evaluate the prototype hardware and review test results before progression beyond a hardware-validation stage. |
    34|| GOV-002 | Before detailed schematic capture or PCB layout, Hermes shall obtain Morgan's approval of an architecture-and-feasibility review covering topology, MOSFET selection, current sharing, safe operating area (SOA), hot-start thermal performance, isolation, protection paths, board interfaces, packaging, quantity-one hardware cost, and unresolved risks. |
    35|| GOV-003 | Hermes shall not silently relax approved requirements, conceal contradictory assumptions, or redefine operating limits to make a simulation pass. Proposed changes shall identify the affected requirements and require review. |
    36|| GOV-004 | Before Rev A fabrication, Hermes shall deliver the revision-matched release package in Section 21 and obtain Morgan's explicit fabrication approval. |
    37|| GOV-005 | Analysis and simulation shall be reproducible. Unsupported assumptions, model limitations, and unverified requirements shall remain visible. |
    38|
    39|Commercialization, target sales regions, production quantities, and the formal compliance program remain undecided. The prototype-first plan does not remove electrical-safety or protection requirements.
    40|
    41|## 2. At-a-glance target specifications
    42|
    43|| Parameter | Approved target |
    44||---|---|
    45|| Normal load input voltage | 3–100 V DC; minimum 3 V is at the instrument's power terminals |
    46|| CC current commands | 100 mA–5 A |
    47|| Maximum operating current | 5 A, with explicit limiting indication |
    48|| Continuous power | 350 W at 40 °C ambient |
    49|| Peak power | 500 W for 30 accumulated seconds above 350 W |
    50|| Required peak starting condition | Thermal equilibrium after continuous 350 W operation at 40 °C ambient |
    51|| Peak recovery | Temperature-based; no fresh allowance from a brief power reduction or OFF/ON command |
    52|| Modes | CC, CV, CP, CR |
    53|| CP programmable range | 1–500 W, subject to achievable operating conditions |
    54|| CR programmable range | 0.6 Ω–1 kΩ, subject to achievable operating conditions |
    55|| Accuracy ambient range / warm-up | 10–40 °C / no more than 15 minutes |
    56|| CC setting / current readback accuracy | ±(0.1% of setting or reading + 1 mA) |
    57|| Voltage readback / CV regulation accuracy | ±(0.1% of reading or setting + 10 mV) |
    58|| CP regulation accuracy | ±(0.5% of setting + 0.5 W) |
    59|| CR regulation accuracy | ±(0.5% of setting + 0.01 Ω) |
    60|| Setpoint settling time | ≤100 ms in all four modes under defined achievable operating conditions |
    61|| CC transient overshoot | ≤5% of the commanded current change; all operating ceilings take precedence |
    62|| OFF-state input current | ≤1 mA steady-state, auxiliary power ON or OFF, including hot operation |
    63|| Normal terminal-to-earth working voltage | Each load terminal within ±100 V DC of protective earth |
    64|| Abnormal DC survival | +120 V overvoltage and reverse polarity up to 100 V, continuously, loading inhibited |
    65|| Local interface | Approximately 4.3-inch color LCD, encoder, navigation, SET, FINE/COARSE, LOAD ON/OFF, alarm controls |
    66|| Communications | USB-C USB 2.0; RJ45 10/100BASE-T Ethernet; documented SCPI; Long Game Testing SDK |
    67|| External I/O | BNC trigger; hardware relay-contact interlock with monitored feedback |
    68|| Recording | 10 records/s to connected PC; internal diagnostic event log ≥100 events |
    69|| Mains input | 100–240 V AC nominal, 50/60 Hz; purchased internal AC/DC module |
    70|| Construction | Earthed metal chassis; forced-air cooling; serviceable power stage; front-panel daughterboards |
    71|| Size reference | Approximately the size of a Rigol 350 W electronic load; numerical envelope not yet fixed |
    72|| Hardware budget | ≤US $500 for one complete instrument, using quantity-one purchasing |
    73|
    74|Read the detailed requirements and open items together; this table is not a standalone finished datasheet.
    75|
    76|## 3. Applications, hardware budget, and architectural partition
    77|
    78|| ID | Approved requirement |
    79||---|---|
    80|| SYS-001 | Support isolated-output bench power supplies and DC–DC converters. Directly mains-connected converter testing is outside the approved application scope. |
    81|| SYS-002 | Implement CC, CV, CP, and CR modes. Dedicated pulsed-loading functionality is not mandatory. |
    82|| COST-001 | The hardware needed for one complete instrument shall cost no more than US $500 using single-build purchase quantities, not production-volume discounts. |
    83|| COST-002 | The hardware budget shall include PCBs, electronic components, internal AC/DC supply, display and controls, enclosure, cooling, connectors, wiring, and necessary hardware. Engineering, labor, test equipment, one-time tooling, and certification are tracked separately. This is not a fully burdened COGS target. |
    84|| ARCH-001 | Use a main load/control board, a display daughterboard, and a control daughterboard with ribbon-cable connections to the main board. Include these boards, cables, mating connectors, and basic functional firmware in Rev A. |
    85|| ARCH-002 | The main board shall provide the main MCU, power-stage interfaces, sensing, protection, fan/temperature control, isolated interfaces, auxiliary low-voltage distribution, USB, and Ethernet. The purchased mains converter is a separate internal supply module. |
    86|| ARCH-003 | The display daughterboard shall contain the LCD and associated display circuitry. The control daughterboard shall contain the encoder, buttons/controls, and beeper. |
    87|| ARCH-004 | Make the high-dissipation MOSFET/heatsink section serviceable or replaceable without scrapping the remaining main electronics. A removable subassembly may add to the three-board partition; exact implementation is a design decision. |
    88|| ARCH-005 | Begin from a multiple-MOSFET linear-stage architecture, with individual gate resistors and individual ballast/source resistors. Hermes shall justify exact topology, device count, device selection, and control arrangement. |
    89|| ARCH-006 | Document every board/cable interface: pinout, signals, supply requirements, reference domains, and isolation boundaries. |
    90|
    91|## 4. Voltage, current, power, and mode operating envelope
    92|
    93|Let **V_P** be voltage across the instrument power terminals, **V_S** voltage at the selected sensing point, and **I** measured load current. Reported/control power is **P = V_S × I**. These definitions distinguish cable loss from instrument dissipation.
    94|
    95|| ID | Approved requirement |
    96||---|---|
    97|| ELEC-001 | Support normal operation with 3–100 V DC at the input, with 3 V minimum explicitly defined at the power binding posts after cable loss. The exact upper-voltage boundary with remote-sense cable drop remains an open item. |
    98|| ELEC-002 | Support CC commands from 100 mA to 5 A. Enforce the approved 5 A operating-current ceiling in all modes, locally and remotely. |
    99|| ELEC-003 | If CV, CP, or CR requests more than available current, limit to 5 A, display “5 A MAX — CURRENT LIMITED,” and expose the limiting state through SCPI/logging. Also make clear that the requested setpoint is not achieved. |
   100|| ELEC-004 | Current limiting is an operating-limit condition, not by itself a latched shutdown. Resume requested regulation when achievable, subject to every other protection and operating limit. Sound one brief beep upon entry to limiting. |
   101|| ELEC-005 | Provide CP commands from 1 W to 500 W and CR commands from 0.6 Ω to 1 kΩ. These are programmable ranges, not guarantees that every value is achievable at every input voltage. |
   102|| ELEC-006 | CP and CR shall require at least 100 mA supported operating current. Before enable, reject an operating point requiring less current; during loading, a sustained below-minimum demand shall disable loading and show “BELOW MINIMUM CURRENT — LOAD OFF,” with SCPI/SDK status. Do not silently clamp upward to 100 mA. Detection tolerances shall preserve valid 100 mA operation. |
   103|| ELEC-007 | CV may remain enabled below 100 mA and decrease current toward zero. Display “CV — BELOW SPECIFIED CURRENT RANGE.” CV regulation accuracy is not guaranteed there; all protection remains active. The CC and CP/CR minimum-current rules do not change. |
   104|| ELEC-008 | OFF-state steady-state input draw from the DUT shall be ≤1 mA across 3–100 V, with auxiliary power ON or OFF, including sensing/protection draw. Verify after turn-off transients settle and after high-power heating. |
   105|
   106|### 4.1 Power definition and peak allowance
   107|
   108|| ID | Approved requirement |
   109||---|---|
   110|| PWR-001 | Sustain 350 W continuously at 40 °C ambient. |
   111|| PWR-002 | Sustain 500 W for 30 seconds at 40 °C ambient starting from stabilized internal temperatures under continuous 350 W loading, without premature thermal shutdown or power derating. |
   112|| PWR-003 | Apply the same 30-second peak allowance to every power level above 350 W through 500 W. No extra duration is granted for an intermediate level such as 400 W. Exactly 350 W is continuous operation. |
   113|| PWR-004 | Displayed power, CP regulation, the continuous/peak power limits, and peak-time accounting shall use V_S × I. With remote sensing, this includes external power-lead losses. Cable losses shall not extend the published operating envelope. |
   114|| PWR-005 | Consume the peak allowance whenever measured selected-sense power exceeds 350 W. At or below 350 W, pause accumulated time rather than clear it, unless validated thermal recovery has occurred. |
   115|| PWR-006 | At 30 seconds of accumulated above-350 W operation, disable loading and display “PEAK POWER OVERLOAD — 30 s LIMIT REACHED. LOAD OFF.” Keep display/communications powered, sound the shutdown alarm, and expose the cause through SCPI/logging. Do not silently derate to 350 W. |
   116|| PWR-007 | Restore the full peak allowance only after temperature-based, validated recovery. Display “Cooling — peak operation unavailable” while peak use is not eligible. OFF/ON commands, brief lower-power operation, and setpoint changes shall not bypass recovery. |
   117|| PWR-008 | Thermal recovery shall not restart loading. After peak shutdown, acknowledgment and deliberate re-enable are required when permitted. Recovery thresholds shall be compatible with the approved hot-start peak condition, not demand a room-temperature heatsink. |
   118|| PWR-009 | Model actual internal dissipation separately from selected-sense power and verify that local device temperatures and SOA support both continuous and peak operation. |
   119|
   120|**Derived local-sense continuous envelope:** I_max = min(5 A, 350 W / V). At 3, 24, 48, 70, and 100 V, the corresponding maximum continuous currents are 5, 5, 5, 5, and 3.5 A. The 500 W peak envelope remains bounded by 5 A and 100 V. These are arithmetic boundaries, not validated hardware performance.
   121|
   122|**Interrupted-peak example:** 20 s at 450 W, then 10 s at 300 W, leaves 10 s of peak allowance if validated recovery has not occurred. No partial recovery-credit algorithm has been approved.
   123|
   124|## 5. Accuracy, adjustment resolution, and response
   125|
   126|| ID | Approved requirement |
   127||---|---|
   128|| ACC-001 | After calibration and warm-up, meet the approved accuracy targets over 10–40 °C ambient, including self-heating from sustained 350 W operation and permitted peak operation under valid regulation conditions. |
   129|| ACC-002 | CC setting: ±(0.1% of setting + 1 mA). Current readback: ±(0.1% of reading + 1 mA). |
   130|| ACC-003 | Voltage readback: ±(0.1% of reading + 10 mV). CV regulation: ±(0.1% of setting + 10 mV), referenced to the selected sense point. |
   131|| ACC-004 | CP regulation: ±(0.5% of setting + 0.5 W). CR regulation: ±(0.5% of setting + 0.01 Ω). |
   132|| ACC-005 | Guarantee accuracy within 15 minutes of power-on. Permit use after startup checks with explicit enable, but display “Warming up — specified accuracy not yet guaranteed” until warm-up is complete. Protection remains active throughout; later high-power operation shall not require another warm-up. |
   133|| RESP-001 | For CC, CV, CP, and CR setpoint changes, enter and remain within the applicable regulation band within 100 ms from acceptance of the new setpoint, when achievable within the operating envelope under documented source/wiring conditions. |
   134|| RESP-002 | CC transient overshoot shall not exceed 5% of the commanded current change. The 5 A ceiling and other operating/protection limits take precedence. This is not 5% of full scale and does not relax steady-state accuracy. Other-mode overshoot limits remain open. |
   135|| RESP-003 | Reach ≤1 mA input current within 10 ms of a recognized front-panel OFF action, accepted SCPI OFF command, opening of the external interlock permission signal at the connector, or expiry of the remote watchdog. The watchdog detection interval is separate. Short-circuit/component-protection response timing requires separate justification. |
   136|
   137|### 5.1 Approved setpoint increments
   138|
   139|| Setting | Fine increment / SCPI granularity | Coarse front-panel increment |
   140||---|---:|---:|
   141|| CC current | 1 mA | 10 mA |
   142|| CV voltage | 10 mV | 100 mV |
   143|| CP power | 0.1 W | 1 W |
   144|| CR below 10 Ω | 0.001 Ω | 0.01 Ω |
   145|| CR from 10 Ω to below 100 Ω | 0.01 Ω | 0.1 Ω |
   146|| CR at 100 Ω and above | 0.1 Ω | 1 Ω |
   147|
   148|**RES-001:** Apply the fine increments to front-panel fine entry and SCPI. Provide explicit FINE/COARSE selection with coarse increments 10× fine increments and visible selection state. Retain digit-by-digit editing. Coarse mode does not reduce SCPI granularity or alter accuracy.
   149|
   150|Illustrative accuracy bands: 1 A CC → ±2 mA; 100 mA CC → ±1.1 mA; 48 V voltage/CV → ±58 mV; 100 W CP → ±1 W; 10 Ω CR → ±0.06 Ω. A 1 A → 2 A CC change permits at most 2.05 A transient peak and shall settle within 100 ms to 2 A ±3 mA.
   151|
   152|## 6. Local and remote voltage sensing
   153|
   154|| ID | Approved requirement |
   155||---|---|
   156|| SENSE-001 | Provide local power-terminal sensing and separate four-wire remote voltage-sense connections. Local sensing is the default; remote selection is explicit. |
   157|| SENSE-002 | In remote mode, reference voltage measurement and CV regulation to the DUT sense connections; use that selected voltage for displayed power and CP regulation. |
   158|| SENSE-003 | Accommodate up to 0.5 V drop in each power lead, 1.0 V total. Minimum power-terminal voltage remains 3 V; full allowable lead drop therefore requires at least 4 V at the DUT. |
   159|| SENSE-004 | Invalid remote-sense wiring before loading shall block enable. A detected fault while loading shall disable it and show “REMOTE SENSE FAULT — CHECK SENSE LEADS.” Require correction, acknowledgment, and explicit re-enable. Do not silently fall back to local sensing. |
   160|| SENSE-005 | Design and verify detection of open and reversed sense leads and protection against incorrect connections. Document actual diagnostic coverage and electrical fault limits. |
   161|
   162|## 7. Input isolation, mains, and connectors
   163|
   164|| ID | Approved requirement |
   165||---|---|
   166|| ISO-001 | The load input shall float: power/sense terminals shall not be intentionally bonded to protective earth or computer ground. Instrument-integrated isolation shall preserve this when USB, Ethernet, trigger, and interlock equipment are connected. |
   167|| ISO-002 | Each load terminal's normal working voltage relative to protective earth shall remain within ±100 V DC; the separate normal differential input limit is 100 V. Dielectric-withstand, transient, and fault common-mode ratings require separate specification. |
   168|| AC-001 | Accept 100–240 V AC nominal, 50/60 Hz, using a purchased internal AC/DC supply module. Include an IEC inlet, appropriate fusing, and a mains power switch. Bond the metal chassis to protective earth. |
   169|| CONN-001 | Provide two large front-panel load power binding posts (+ and −) accepting 4 mm banana plugs and two smaller remote-sense posts (S+ and S−). Clearly mark polarity and “100 V MAX / 5 A MAX,” including an unmistakable maximum-current label near the load terminals. |
   170|| CONN-002 | Place sense terminals intuitively near corresponding power terminals. Choose spacing to reduce accidental bridging; justify the final connector insulation/accessibility arrangement. |
   171|| CONN-003 | Provide a rear-panel, user-replaceable, finger-safe DC input fuse holder with the required fuse type/rating marked. DC input fusing is distinct from the AC inlet fuse. |
   172|
   173|## 8. Protection and shutdown behavior
   174|
   175|| ID | Approved requirement |
   176||---|---|
   177|| SAFE-001 | Remain OFF after power-up, processor reset, or loss/restoration of auxiliary power. Mode/setpoints may be restored; the prior ON state shall not be restored automatically. |
   178|| SAFE-002 | After a protective shutdown, require fault clearance, explicit acknowledgment, and deliberate re-enable. Cooling, reconnection, restored voltage, or cleared interlock permission alone shall not restart loading. |
   179|| SAFE-003 | Include mandatory MOSFET-based hardware reverse-polarity protection in Rev A, independent of firmware execution and effective with auxiliary power ON or OFF. |
   180|| SAFE-004 | Continuously withstand reversed input polarity up to 100 V DC without damage while connected, with loading inhibited. When powered, show “REVERSE POLARITY — CHECK CONNECTIONS. LOAD OFF,” alarm, and report the fault. |
   181|| SAFE-005 | Continuously withstand accidental positive input overvoltage up to 120 V DC without damage while connected, with loading inhibited, auxiliary power ON or OFF. When powered, show “INPUT OVERVOLTAGE — LOAD OFF,” alarm, and report the fault. This is not a normal operating range or a component-rating selection rule. |
   182|| SAFE-006 | Use hybrid input protection: a replaceable physical fuse plus electronic eFuse/hot-swap-style or equivalent fault-isolation protection. The electronic stage shall not replace the fuse. Justify voltage ratings, MOSFET SOA, fault current, transient energy, and load-loop interaction. |
   183|| SAFE-007 | Provide independent hardware shutdown for power-stage overtemperature and an independent hardware watchdog for processor malfunction. Detectable temperature-sensor failures shall prevent or disable loading. Document and physically verify independence from normal application firmware. |
   184|| SAFE-008 | If any monitored power-stage thermal zone exceeds its validated shutdown limit, disable loading even when average heatsink temperature is acceptable. Capture relevant temperatures in the event log. |
   185|| SAFE-009 | A required fan that is commanded to run but fails validated speed criteria shall prevent or disable loading, show “FAN FAULT — LOAD OFF,” alarm, and report through SCPI. A deliberately stopped fan in a permitted cool/idle mode is not a fault. |
   186|| SAFE-010 | Sustained input undervoltage below the supported range shall disable loading and show “INPUT UNDERVOLTAGE — LOAD OFF,” alarm, and report the fault. Define filtering/hysteresis without preventing valid 3.0 V operation. |
   187|| SAFE-011 | No software operation, trigger, preset, or ownership change shall bypass active hardware inhibit, latched protection, or peak-allowance restrictions. |
   188|
   189|Fault-survival voltages do not establish surge immunity, short-circuit interruption capability, or an external source fault-current rating. The interview did **not** approve a 20 A source requirement. See Section 24 before selecting a fuse or claiming fault-survival coverage.
   190|
   191|## 9. Thermal design and power-stage observability
   192|
   193|| ID | Approved requirement |
   194||---|---|
   195|| THERM-001 | Include at least three sensors across power-stage/heatsink regions plus one internal ambient or air-inlet sensor. Increase count as needed for the actual thermal layout; a reduction below the approved minimum requires review. |
   196|| THERM-002 | Every cooling fan shall provide tachometer feedback. Document startup allowance, minimum validated speed, and detection timeout. |
   197|| THERM-003 | Use temperature-based variable fan speed. Fans may be OFF/very slow when idle and cool; enforce minimum speed above validated power or temperature thresholds. Keep thermal margin below hardware shutdown. |
   198|| THERM-004 | After a high-power run ends, continue cooling until temperatures fall below validated thresholds. Make fan command/speed and temperatures available in diagnostics and SCPI/SDK. |
   199|| THERM-005 | Provide per-device or grouped source-resistor measurement access to verify MOSFET current sharing, plus probe access to individual gate/source behavior. Per-device ADC channels are not mandatory unless design analysis shows a need. |
   200|| THERM-006 | Analyze unequal current sharing, local temperature rise, and elevated-temperature SOA at worst-case operating points. Do not assume equal gate voltage guarantees equal dissipation. |
   201|| THERM-007 | Demonstrate the 350 W continuous and 500 W hot-start peak targets with the intended heatsink, mounting, airflow, and temperature monitoring. Simulation is supporting evidence, not a substitute for Morgan's hardware testing. |
   202|
   203|## 10. Front-panel UI and audible warnings
   204|
   205|| ID | Approved requirement |
   206||---|---|
   207|| UI-001 | Include an approximately 4.3-inch color LCD, push-to-select rotary encoder, navigation buttons, a dedicated LOAD ON/OFF button, SET, explicit FINE/COARSE selection, and alarm silence/acknowledgment access. A touchscreen and numeric keypad are not required. |
   208|| UI-002 | Show measured voltage/current/power, selected mode, commanded setpoint, load state, active limits/faults, control owner, and remaining peak allowance during peak operation. Distinguish commanded from measured values. |
   209|| UI-003 | Initial setup or mode changes shall follow load OFF → mode/setpoint entry → CONFIRM → explicit load enable. Confirming a value or pressing SET shall not itself enable loading. |
   210|| UI-004 | Pressing SET while loading is active shall explicitly enter live adjustment. Indicate “LIVE ADJUST.” Encoder changes then immediately update the setpoint within the current mode, using selected fine/coarse or digit editing. |
   211|| UI-005 | Changing CC/CV/CP/CR modes shall disable loading and require explicit re-enable, locally or via SCPI. Setpoint changes within the active mode may occur while loading continues. Neither change resets peak history or protection. |
   212|| UI-006 | Include the audible beeper on the first prototype. Entry into current limiting produces one brief beep; sustained limiting alone does not require repetitive beeping. |
   213|| UI-007 | Protective shutdowns, including peak timeout, produce repeated beeps until explicitly silenced. Display the reason persistently until properly resolved/acknowledged. A new shutdown event produces its own audible notification. |
   214|| UI-008 | Permit alarm silence from the front panel and SCPI. Silencing changes audio only: it shall not clear faults, hide active warnings, reset timers, or re-enable loading. |
   215|| UI-009 | The physical load button shall always be able to turn loading OFF, including remote operation. Front-panel alarm silence remains available regardless of ownership. |
   216|
   217|### 10.1 Approved diagnostic text / meaning
   218|
   219|Equivalent legible capitalization or line wrapping is acceptable; do not obscure the named cause.
   220|
   221|| Condition | Required clear indication |
   222||---|---|
   223|| Current limit | “5 A MAX — CURRENT LIMITED”; indicate setpoint not achieved |
   224|| Peak allowance exhausted | “PEAK POWER OVERLOAD — 30 s LIMIT REACHED. LOAD OFF.” |
   225|| Peak recovering | “Cooling — peak operation unavailable” |
   226|| Remote communications lost | “REMOTE COMMUNICATION LOST — LOAD OFF” |
   227|| Remote-sense fault | “REMOTE SENSE FAULT — CHECK SENSE LEADS” |
   228|| Reverse polarity | “REVERSE POLARITY — CHECK CONNECTIONS. LOAD OFF” |
   229|| Input overvoltage | “INPUT OVERVOLTAGE — LOAD OFF” |
   230|| Input undervoltage | “INPUT UNDERVOLTAGE — LOAD OFF” |
   231|| Fan fault | “FAN FAULT — LOAD OFF” |
   232|| CP/CR demand below supported current | “BELOW MINIMUM CURRENT — LOAD OFF” |
   233|| CV below guaranteed current region | “CV — BELOW SPECIFIED CURRENT RANGE” |
   234|| Warm-up in progress | “Warming up — specified accuracy not yet guaranteed” |
   235|| Interlock permission absent | “INTERLOCK OPEN — LOAD INHIBITED” |
   236|| Diagnosed interlock malfunction | “INTERLOCK FAULT — LOAD OFF,” with reason |
   237|| Armed load-start trigger | “ARMED — WAITING FOR TRIGGER” |
   238|| Capture requested without recorder | “RECORDER NOT CONNECTED” |
   239|
   240|## 11. USB, Ethernet, SCPI, and control ownership
   241|
   242|| ID | Approved requirement |
   243||---|---|
   244|| COM-001 | Provide rear-panel RJ45 10/100BASE-T Ethernet with galvanic isolation and link/activity LEDs, DHCP default, optional static IP, front-panel-visible hostname, and persistent network settings. |
   245|| COM-002 | Provide USB-C communications at USB 2.0 speeds. USB-PD functionality is not required. Preserve input isolation when connected to a computer. |
   246|| COM-003 | Expose a clean, documented SCPI interface independently usable without the SDK. The Long Game Testing SDK is the primary user-facing abstraction; exact imitation of another vendor is not required. |
   247|| COM-004 | Cover identification/status, reset/clear/operation-complete functions, mode/setpoint control and queries, ON/OFF, measurements, sense selection, faults/limits, temperatures/fan speed, alarms, peak status, logging, triggers, calibration status, presets, and applicable diagnostics. Define command semantics and error handling in the interface specification. |
   248|| COM-005 | Support one control owner at a time: LOCAL, USB REMOTE, or ETHERNET REMOTE, visibly indicated. Only the owner may change settings, arm actions, or enable loading; other connections are read-only except explicitly permitted safety/alarm actions. |
   249|| COM-006 | Ownership changes require loading OFF and cancel trigger-start arming. Taking ownership shall not itself enable loading. Do not automatically transfer ownership after controller disconnection. |
   250|| COM-007 | Local operation shall not require a computer. Disconnection of a read-only observer shall not stop the load solely because of that disconnection. |
   251|| COM-008 | During active remote control, the controlling software shall send keep-alives once per second. Five seconds without a valid keep-alive shall disable loading and report “REMOTE COMMUNICATION LOST — LOAD OFF.” Positively detected connection loss shall initiate shutdown sooner. |
   252|| COM-009 | Reconnection shall not automatically resume loading. Apply acknowledgment and explicit enable requirements. The watchdog monitors the active controller, not ordinary gaps between unrelated SCPI commands. |
   253|
   254|**Open implementation choice:** USB device class, Ethernet SCPI transport, session framing, and keep-alive command syntax have not been selected. Do not claim a specific VISA/USBTMC/socket compatibility profile before it is implemented and tested.
   255|
   256|## 12. Long Game Testing SDK and acquisition storage
   257|
   258|| ID | Approved requirement |
   259||---|---|
   260|| SW-001 | Implement the Python driver, automated control, logging, and graphical controls as part of the Long Game Testing SDK, not a separate incompatible application. Inspect its actual interfaces before implementation. |
   261|| SW-002 | Support USB and Ethernet, automatic remote keep-alive, mode/setpoint control, enable/OFF, fault/status inspection, and CSV export. Prioritize functional control and acquisition for board bring-up; graphical refinement may follow hardware testing. |
   262|| LOG-001 | Support continuous acquisition/logging at 10 complete records/s over either USB or Ethernet. This is the external record rate, not an internal control/protection bandwidth specification. |
   263|| LOG-002 | Each record shall include acquisition timestamp, voltage, current, calculated selected-sense power, mode/setpoint, load state, faults, monitored temperatures, and remaining peak allowance. Include current-limit status. |
   264|| LOG-003 | Store continuous measurement history and CSV files on the connected computer through the SDK. Rev A does not require SD-card or standalone continuous recording. |
   265|| LOG-004 | Use instrument acquisition buffers for triggered single records and pending transfers; document buffer size and overflow behavior. Do not silently imply lost data was recorded. |
   266|| LOG-005 | Continuous capture requires an active recording client. If absent, clearly report “RECORDER NOT CONNECTED.” Local load operation and triggered load starts remain possible without a computer. |
   267|| EVT-001 | Retain at least the latest 100 diagnostic events in nonvolatile memory, readable from the front panel and SCPI/SDK, with an explicit clear-log operation. |
   268|| EVT-002 | Record timestamp or uptime counter, event type, voltage/current/power, relevant temperatures, fan speeds, mode/setpoint, and local/remote control state. |
   269|
   270|**Storage boundary:** Calibration, presets, and diagnostic events are retained internally. A triggered or buffered measurement is not automatically a persistent continuous log. A battery-backed wall-clock RTC has not been required.
   271|
   272|## 13. External trigger
   273|
   274|| ID | Approved requirement |
   275||---|---|
   276|| TRIG-001 | Include a rear-panel BNC labeled EXT TRIG in Rev A. Accept 3.3 V and 5 V logic pulses, with locally/SCPI-selectable rising/falling edge and input protection. Document electrical limits and preserve floating-load isolation. 12–24 V industrial compatibility is not required. |
   277|| TRIG-002 | Provide selectable actions: armed load start, one measurement record per accepted trigger, and armed start of continuous 10-record/s capture. Selection shall be available locally and through SCPI/SDK. |
   278|| TRIG-003 | For load-start mode, explicit front-panel or SDK arming authorizes a subsequent trigger. Keep loading OFF and display the armed state while waiting. A valid edge starts the confirmed mode/setpoint only when operating/protection conditions permit. |
   279|| TRIG-004 | Additional trigger edges during that load operation shall not toggle loading or reset peak allowance. OFF, inhibit, or protective shutdown cancels trigger-start arming; another triggered start requires deliberate rearming. |
   280|| TRIG-005 | Measurement trigger functions shall not change the load's state or setpoint. Single mode returns one timestamped record per accepted trigger. Continuous mode runs at 10 records/s until stopped, without subsequent edges restarting the run. |
   281|| TRIG-006 | Recognize pulses at least 1 ms wide. Start the selected load action or acquisition within 10 ms of an accepted edge. This is not completion of acquisition or load settling. |
   282|| TRIG-007 | Support evenly spaced single-measurement triggers at 10 Hz. Report too-fast requests as trigger overruns through diagnostics/SCPI/SDK rather than silently losing them. |
   283|| TRIG-008 | Trigger actions shall never override inhibit, fault shutdown, or exhausted peak allowance. Trigger capability does not create a high-speed pulsed-load requirement. |
   284|
   285|## 14. External hardware interlock and relay diagnostics
   286|
   287|| ID | Approved requirement |
   288||---|---|
   289|| INT-001 | Include a separate rear-panel hardware interlock/INHIBIT interface. An external dry-contact permission loop is closed to permit operation and open to inhibit. Opening the loop or disconnecting the cable shall act through hardware independent of normal application/SCPI control. |
   290|| INT-002 | Use energize-to-permit behavior: a normally open relay permission contact is held closed while permission is granted. Relay de-energization or power loss opens permission and inhibits loading. |
   291|| INT-003 | Interlock opening shall cancel load-start arming and prevent/disable loading. Permission restoration shall not restart loading; deliberate enable or rearming is required. |
   292|| INT-004 | Provide a clearly labeled removable bypass plug for standalone operation. It closes the external permission loop only and shall not defeat internal protections. Feedback behavior with this plug must be resolved explicitly. |
   293|| INT-005 | Provide monitored relay-state feedback in Rev A to detect diagnosable relay/feedback malfunctions, not just an open permission loop. Document detectable failure modes and verify them using fault injection. Do not claim coverage of every relay failure. |
   294|| INT-006 | Show “INTERLOCK OPEN — LOAD INHIBITED” when permission is absent. For a diagnosed malfunction, show “INTERLOCK FAULT — LOAD OFF” with a cause; inhibit loading, sound the shutdown alarm, and report/log the fault. An interlock opening during loading also invokes shutdown annunciation. |
   295|| INT-007 | Preserve the approved floating-input isolation through the interlock interface. This approval does not by itself require a high-current relay in the DUT load path. Relay placement, feedback wiring, and contact implementation need architecture review. |
   296|
   297|## 15. Calibration, presets, and firmware servicing
   298|
   299|| ID | Approved requirement |
   300||---|---|
   301|| CAL-001 | Use software-stored two-point calibration with coefficients in nonvolatile memory; analog trim potentiometers are not required. Cover voltage/current measurement and regulation paths as applicable to the architecture. |
   302|| CAL-002 | Provide a protected calibration workflow locally and through the SDK, coefficient integrity checking such as CRC, calibration date/firmware metadata, readable calibration status, and default/recovery coefficients. Preserve calibration through normal power cycles and firmware updates. |
   303|| CAL-003 | Design the procedure around typical laboratory equipment: precision voltage source/calibrator, precision DMM, current reference such as calibrated shunt plus DMM or current meter, and a stable source capable of the required points. Dedicated automated self-calibration hardware is not required for Rev A. |
   304|| PRE-001 | Store at least 10 user presets nonvolatilely, including mode, setpoint, sense selection, fine/coarse preference, and applicable defaults. Preset recall shall not automatically enable loading. |
   305|| FW-001 | Support firmware updates over USB; Ethernet updating is deferred. Include a dedicated main-board debug/programming recovery header for Rev A. |
   306|| FW-002 | Update mode shall not permit loading. Interrupted/failed updates shall be recoverable; provide image versioning and integrity checks. Display/query firmware version. |
   307|| FW-003 | Preserve calibration, presets, and event logs through normal updates unless an explicit data migration is needed and documented. |
   308|
   309|Two-point calibration defines the correction approach, not proof of full-range linearity. Additional verification points and temperature tests are still needed to evaluate the approved accuracy envelope. Exact calibration points, reference uncertainty, range handling, and recovery restrictions remain open.
   310|
   311|## 16. Mechanical construction and serviceability
   312|
   313|| ID | Approved requirement |
   314||---|---|
   315|| MECH-001 | Target a compact size approximately comparable to a Rigol 350 W electronic load. The numerical width/height/depth and clearances shall be established at architecture review, not invented from an unverified model reference. |
   316|| MECH-002 | Use a metal chassis bonded to protective earth, with front/side intake and rear exhaust. Include replaceable/cleanable intake filtration. |
   317|| MECH-003 | Direct the coolest intake air first to the power MOSFET/heatsink region; keep display/control electronics out of the primary hot exhaust path. Minimize fan vibration transfer. |
   318|| MECH-004 | Avoid user-accessible openings that expose hazardous internal conductors. Include appropriate protective arrangements around accessible terminals, fuse access, airflow openings, and internal mains components. |
   319|| MECH-005 | Implement serviceable power-stage/heatsink hardware and rear-panel input fuse access. Include mounting and thermal-interface information in assembly documentation. |
   320|
   321|No numerical noise limit, mass limit, ingress rating, shock/vibration rating, or storage-temperature envelope has yet been approved.
   322|
   323|## 17. Rev A test access and debug provisions
   324|
   325|| ID | Approved requirement |
   326||---|---|
   327|| DBG-001 | Include actual labeled test loops on bring-up-critical nodes, not only small probe pads; use other accessible points as appropriate for signals that need different probing geometry. |
   328|| DBG-002 | Provide access to logic rails, isolated auxiliary rails, gate-drive signals, current/voltage-sense outputs, hardware shutdown, fan PWM/tach, MCU debug/UART, and temperature-sensor signals/buses. Identify expected readings/waveforms in the bring-up document. |
   329|| DBG-003 | Include removable debug-isolation jumpers or 0 Ω links to segment critical circuits: gate drive, force-shutdown path, selected sense paths, fan power/control, and selected rails for current measurement. These are prototype provisions and may be reduced later. |
   330|| DBG-004 | Support direct MOSFET sharing and individual gate/source investigation with test access around source resistors and power-stage interfaces. |
   331|| DBG-005 | Provide firmware sufficient to exercise the actual LCD, encoder, buttons, beeper, daughterboard links, measurements, and communications in the first hardware set; polished UI is not required before hardware evaluation. |
   332|
   333|**Pending safety detail:** Document reference domains and appropriate probing arrangements for every test location. Debug jumpers must have defined default states and safe behavior when links are absent; the exact arrangements require review before fabrication.
   334|
   335|## 18. Required design toolchain
   336|
   337|| ID | Approved requirement |
   338||---|---|
   339|| TOOL-001 | Use KiCad for editable schematics and PCB layout, with project-specific symbols, footprints, and associated libraries included. |
   340|| TOOL-002 | Use SPICE for circuit simulation with runnable netlists, component models, test circuits, and documented simulator/version. Verify model/simulator compatibility before relying on results. |
   341|| TOOL-003 | Use C for embedded firmware; include build configuration, dependencies, and programming/recovery instructions. |
   342|| TOOL-004 | Use Python within the Long Game Testing SDK for PC control and automation. Inspect existing SDK conventions rather than inventing an incompatible parallel framework. |
   343|| TOOL-005 | Use Onshape for editable mechanical assemblies, plus STEP exports and manufacturing drawings. |
   344|| TOOL-006 | Document and justify substitutions of tools or models. Do not silently replace a limiting device model with a simpler one merely to obtain convergence. |
   345|
   346|Specific versions, MCU family, RTOS, simulator selection, SDK repository/branch, and Onshape workspace remain implementation choices/open inputs. This document does not claim access to those project resources.
   347|
   348|## 19. Datasheet and characteristic-curve deliverables
   349|
   350|| ID | Approved requirement |
   351||---|---|
   352|| DOC-001 | Provide a datasheet stating operating limits, accuracy conditions, warm-up, sensing limits, continuous/peak behavior, fault-survival limits, interfaces, and mode-dependent restrictions. Clearly distinguish normal use from abnormal survival conditions. |
   353|| DOC-002 | Explicitly disclose continued CV operation below 100 mA without guaranteed CV regulation accuracy. Show that region separately from the guaranteed region in characteristic curves. |
   354|| DOC-003 | Label simulated versus measured curves. State test conditions, including voltage, sense configuration, ambient temperature, warm-up, and relevant loading/thermal history. Do not publish simulated characteristics as measured prototype results. |
   355|| DOC-004 | Provide bring-up, assembly, programming, calibration, SCPI/SDK, and verification documentation as part of the full design package. |
   356|
   357|**Proposed additional characterization plots for review:** operating envelope; CC/CV/CP/CR error versus operating point; hot/cold accuracy; MOSFET sharing; hot-start peak temperature trajectories; cooldown/rearm behavior; setpoint transients; OFF current; and trigger timing. These plot selections are a proposed test-plan organization, not new numerical performance guarantees.
   358|
   359|## 20. Verification strategy and stage gates
   360|
   361|### Gate A — Architecture and feasibility (approved mandatory gate)
   362|
   363|Before detailed schematic/layout work, present:
   364|
   365|1. Power topology and candidate components with sourcing/cost basis; device-level current sharing, elevated-temperature SOA, reverse protection, and electronic-disconnect reasoning.
   366|2. Thermal estimate covering continuous 350 W, the 500 W/30 s hot-start condition, enclosure airflow, filter, temperature sensors, and recovery logic.
   367|3. Isolation-domain diagram including mains module, USB/Ethernet, front-panel cables, trigger, interlock, chassis, and test access.
   368|4. Preliminary packaging and quantity-one hardware budget; explicit assumptions, conflicts, and open requirements.
   369|
   370|**Exit:** Morgan explicitly approves the architecture or approves a documented requirement change. Passing a simulation is not the exit criterion.
   371|
   372|### Gate B — Rev A fabrication release (approved mandatory gate)
   373|
   374|Deliver the complete Section 21 package, design-check reports and exceptions, cost estimate, and bring-up/verification plan.
   375|
   376|**Exit:** Morgan explicitly approves fabrication. This baseline alone does not authorize purchases, ordering, or release.
   377|
   378|### Gate C — Board bring-up and hardware evaluation
   379|
   380|Morgan tests the actual hardware. Record observations, measurements, conditions, failures, and requirement status. Iterate hardware and firmware using controlled revisions. The following sequence is a **proposed verification plan**, not a substitute for a safety-reviewed bench procedure.
   381|
   382|| Test group | Requirements covered | Proposed evidence |
   383||---|---|---|
   384|| Inspection / release audit | Architecture, cost, connectors, test access, tooling | File audit, BOM rollup, drawings, net/layout checks, physical inspection |
   385|| Auxiliary power / logic | Startup OFF, UI, rails, daughterboards, firmware recovery | Controlled bring-up results, reset/update behavior, signal/rail measurements |
   386|| Hardware protections | Watchdog, overtemperature, sensors, fans, interlock | Fault injection demonstrating shutdown without normal application firmware |
   387|| Low-power regulation | CC/CV/CP/CR, sensing, mode changes | Measured regulation, limit-state behavior, low-current exceptions |
   388|| Calibration and accuracy | ACC/CAL requirements | Two-point calibration plus independent verification points at agreed temperatures/thermal states |
   389|| Power and SOA | PWR/THERM requirements | Individual/group currents, device temperatures, continuous operation and hot-start 30 s peak |
   390|| Peak timer / recovery | PWR-003 through PWR-008 | Interrupted-peak sequences, threshold behavior, recovery/rearm observations, no bypass by OFF/ON |
   391|| Setpoint response / OFF | RESP requirements | Instrumented response/overshoot and ≤1 mA turn-off endpoint under defined wiring/source conditions |
   392|| Input/sense faults | Reverse, overvoltage, undervoltage, sense protection | Safety-reviewed fault testing with specified source limits, connections, thermal states, and powered/unpowered cases |
   393|| Remote control | COM/SW requirements | USB/Ethernet sessions, owner conflicts, keep-alive loss, observer disconnect, no auto-resume |
   394|| Triggers | TRIG requirements | Pulse/edge tests, action delay, record rate, overrun reporting, armed safety states |
   395|| Storage / persistence | LOG/EVT/PRE/CAL/FW | CSV data integrity, buffer/overflow behavior, resets/updates, retained metadata |
   396|| Integration | Enclosure, cooling, labeling, datasheet | Enclosed thermal tests, serviceability inspection, source-backed measured characteristic plots |
   397|
   398|Each completed test record should identify requirement IDs, hardware revision/serial, firmware version, calibration state, equipment and relevant uncertainty, wiring/sense arrangement, ambient/starting thermal condition, procedure, raw data, result, and unresolved anomalies.
   399|
   400|**Verification statuses:** UNVERIFIED → SUPPORTED BY ANALYSIS/SIMULATION → PHYSICALLY VERIFIED, or FAILED / BLOCKED. These are distinct statuses, not automatic progression or certification claims. Tests that cannot yet be performed remain pending.
   401|
   402|## 21. Rev A release package
   403|
   404|The following are approved deliverables for the pre-fabrication gate, with a consistent revision identifier:
   405|
   406|| Package area | Required deliverables |
   407||---|---|
   408|| Requirements | This baseline, resolved/open issues, documented changes, and requirement-to-verification matrix |
   409|| Electrical | Native KiCad project files, libraries, editable block diagrams, schematics, PCB layouts, design-check reports and exceptions |
   410|| Components / cost | BOM with manufacturer part numbers, quantity-one hardware-cost rollup, selection rationale, relevant part documentation |
   411|| Simulation / analysis | Netlists, models, test circuits, scripts/configuration, software versions, reproducible instructions, results and limitations, thermal/SOA/current-sharing evidence |
   412|| Manufacturing | Fabrication and assembly exports, assembly drawings, connector/cable definitions, and release notes |
   413|| Mechanical | Editable Onshape designs, STEP exports, manufacturing drawings, heatsink/mounting and assembly information |
   414|| Firmware / SDK | C sources/build instructions, programming/recovery instructions, prototype functional firmware, Python SDK integration sources and interface documentation |
   415|| Bring-up | Test-loop map/expected readings, jumper configurations, rail checks, staged test plan, calibration procedure, fault-injection plan |
   416|| Product documentation | Draft datasheet and user/interface documentation, with unverified specifications/curves clearly identified |
   417|
   418|**Suggested repository organization (proposal; adapt to the actual SDK/project):**
   419|
   420|```text
   421|requirements/       # baseline, decisions, open issues, traceability
   422|architecture/       # block diagrams, isolation, power/thermal budgets
   423|hardware/           # KiCad sources and project libraries
   424|simulation/         # runnable circuits, models, analysis, results
   425|firmware/           # C sources and reproducible build configuration
   426|sdk_integration/    # integration references or device module, not a duplicate SDK
   427|mechanical/         # Onshape document/version references and exports
   428|manufacturing/      # revision-specific fabrication and assembly package
   429|verification/       # procedures, raw data, logs, test reports
   430|release/            # manifests, checksums, revision notes
   431|```
   432|
   433|Do not claim manufacturing readiness while a blocking protection, SOA, isolation, or operating-envelope conflict remains unresolved.
   434|
   435|## 22. Behavioral consistency checklist
   436|
   437|The following checklist summarizes approved behavior; it is not a new feature list or a complete firmware state machine.
   438|
   439|- **Power-up/reset:** OFF. Restore configuration only, not an ON state. Validate protection before enabling; final startup-check design remains open.
   440|- **Local enable:** Requires confirmed configuration and permitted conditions. An ordinary SET/CONFIRM action is not an enable command.
   441|- **Remote enable:** Requires the active control owner, valid watchdog behavior, and permitted conditions.
   442|- **Triggered enable:** Requires deliberate load-start arming and a valid edge. The trigger cannot override protection or peak eligibility.
   443|- **Within-mode change:** Live changes are allowed. Front-panel live changes require entering SET/live-adjust mode. Existing protection and peak history remain active.
   444|- **Mode change:** OFF, confirm new configuration, then explicit enable.
   445|- **Current limit:** Continue at the current ceiling, visibly flag the unmet setpoint, give an entry beep, and resume regulation when achievable. This is not automatically a shutdown.
   446|- **CV low-current operation:** Remain enabled as current approaches zero, with the unguaranteed-accuracy indication; no exception to other protection.
   447|- **CP/CR below minimum:** Reject before enable or shut down for a sustained below-minimum demand. No silent upward clamp.
   448|- **Protection/inhibit:** OFF. Preserve visible diagnostics; obey alarm, acknowledgment, clearance, and explicit-reenable rules.
   449|- **Peak history:** Count only above 350 W, pause otherwise, restore only after validated thermal recovery, and never restart loading because recovery completes.
   450|- **Alarm silence:** Audio only. It is not fault reset, permission restoration, or load enable.
   451|- **Firmware update:** Loading disabled; recovery path remains available after an interrupted update.
   452|
   453|A final state-transition table must distinguish fault acknowledgment, alarm silence, enabling, arming, and control ownership. Do not collapse these into a single generic “reset” command.
   454|
   455|## 23. Derived operating-boundary checks
   456|
   457|These are consequences of approved requirements, not newly approved specifications.
   458|
   459|### 23.1 Mode ranges are conditional
   460|
   461|For CP, I_required = P_set / V_S. For CR, I_required = V_S / R_set. Apply the current/power/voltage limits and the CP/CR minimum-current rule to the actual operating point.
   462|
   463|- 1 W at 100 V requires 10 mA, so that requested CP point is outside the approved supported CP region.
   464|- 1 kΩ at 48 V requires 48 mA, so that requested CR point is outside the approved supported CR region.
   465|- 0.6 Ω at 3 V requires 5 A. With remote-sense cable losses, use the correct V_S rather than assuming it equals V_P.
   466|- At 100 V, 350 W corresponds to 3.5 A continuously; 500 W corresponds to the full 5 A peak corner.
   467|
   468|The datasheet should make these restrictions visible rather than show programmable ranges as an unconditional rectangular operating envelope.
   469|
   470|### 23.2 The 5 A ceiling needs a tolerance definition
   471|
   472|The approved 5 A ceiling and CC accuracy formula are both retained. At a 5 A command, the stated accuracy band is ±6 mA; an absolute never-above-5.000 A limit cannot simply be treated as identical to a nominal 5 A rating with that symmetric tolerance. The transient ceiling introduces the same boundary issue. Hermes must present a consistent clipping/tolerance policy for approval, not silently choose one.
   473|
   474|### 23.3 Hardware margins are not a blanket 20% rule
   475|
   476|The +120 V DC survival requirement provides specified headroom above the 100 V normal rating. It does not by itself select a 120 V MOSFET, set an insulation-test voltage, define transient immunity, or establish a fuse interrupt rating. Analyze the whole protected and unpowered circuit, including sensing and isolation domains.
   477|
   478|### 23.4 Hot-start capacity and reset eligibility must agree
   479|
   480|A cold-start-only 500 W demonstration does not satisfy the requirement. Peak recovery thresholds must coexist with the requirement that a thermally stabilized 350 W operating condition at 40 °C allows a full 30-second 500 W peak. Brief drops below 350 W must not masquerade as recovery. Filtering, sensor placement, thermal lag, reset eligibility, and power-cycle handling need a coherent implementation.
   481|
   482|## 24. Open-item and design-decision register
   483|
   484|These entries are deliberately not assigned invented values. Resolve safety/feasibility blockers at Gate A; resolve interface and verification details before the relevant implementation/release milestone. Only Morgan can approve a change to an existing requirement.
   485|
   486|| ID | Unresolved item | Required next action |
   487||---|---|---|
   488|| OPEN-01 | Fault-source capability and test energy. The approved 5 A rating is the load's operating limit; no 20 A source requirement was approved. | Propose a bounded fault-test source/current/energy envelope, including relevant output capacitance, wiring, and fault conditions, without silently assuming fault current is limited by normal load regulation. Obtain review before selecting final fuse interruption requirements or claiming fault coverage. |
   489|| OPEN-02 | Fuse/electronic-disconnect coordination. | Select DC voltage/interrupt capability, current/time behavior, protection MOSFET arrangement, and fault response from the approved/tested envelope. Account for hot operation and 3 V headroom. |
   490|| OPEN-03 | Exact 5 A ceiling versus accuracy/overshoot tolerances. | Resolve the Section 23.2 boundary explicitly, including measured versus commanded limits and trip/limit tolerance. |
   491|| OPEN-04 | Combined measurement/control error budgets. | Demonstrate that the two-point calibration and selected analog/control architecture meet CC/CV/CP/CR targets over voltage, current, temperature, sense configuration, and self-heating. Do not infer CP/CR compliance merely from separate V/I readback specifications. |
   492|| OPEN-05 | Thermal thresholds and equilibrium/recovery criteria. | Define sensor placement, hardware trip thresholds, sensor-fault coverage, cooling thresholds, peak eligibility, and the criterion for a stabilized 350 W starting condition. Validate with hot-start tests. |
   493|| OPEN-06 | Peak measurement/timer implementation. | Define measurement rate, filtering near 350 W, timer resolution, reset eligibility, behavior after processor/mains resets, and prevention of false allowance refresh. Preserve continuous 350 W operation and the no-bypass intent. |
   494|| OPEN-07 | Above-500 W demand in CV/CP/CR. | Specify limiting or shutdown behavior and indications when maintaining a requested point would exceed 500 W. The maximum rating is approved; detailed mode arbitration is not. |
   495|| OPEN-08 | Protection thresholds and transients. | Define OVP, UVP, overcurrent, sensor, fan, and other protection detection tolerances/filtering/response times. Separate normal OFF response from component-survival shutdown speed. |
   496|| OPEN-09 | Insulation and abnormal common-mode conditions. | Specify working/transient/withstand requirements, separation and accessible-part arrangements, interface shield/reference treatment, and how +120 V/reverse-fault survival is tested relative to earth. Do not treat ±100 V normal working rating as a dielectric test rating. |
   497|| OPEN-10 | Remote-sense limits and fault cases. | Define allowable upper sense voltage with cable drop near the 100 V limit, sense-input loading, mismatch thresholds, open/reversed/miswired cases, and fault protection. |
   498|| OPEN-11 | Relay location, feedback, and bypass compatibility. | Decide whether the monitored relay is external, internal, or an explicitly supported assembly; define feedback pins, expected/actual state comparison, switching allowance, fault coverage, and standalone bypass behavior. No safety certification/category claim is approved. |
   499|| OPEN-12 | Trigger electrical details and acquisition semantics. | Define thresholds, input impedance, electrical maximums, short-pulse behavior, acquisition aperture/channel timing, timestamp resolution, trigger busy handling, buffer capacity, and overrun reporting. Only the already-approved pulse/latency/rate targets are fixed. |
   500|| OPEN-13 | SCPI transport and remote ownership details. | Select USB class and Ethernet transport; define command tree, sessions, keep-alive/ownership handshake, error/status behavior, and permitted non-owner safety actions. Inspect the actual SDK before implementing. |
   501|| OPEN-14 | Recorder failure and buffer overflow. | Define behavior when a recording client stops while control remains valid, and how local-plus-read-only recording interacts with ownership. Do not silently equate loss of a read-only recorder with loss of the active controller. |
   502|| OPEN-15 | Calibration implementation and invalid data. | Select low/high calibration points, correction paths/ranges, reference uncertainty, verification grid, coefficient-integrity recovery policy, and behavior when only default/invalid coefficients are available. |
   503|| OPEN-16 | Detailed mode/user workflows. | Define CV setpoint endpoints, exit behavior for SET/live adjustment, preset recall while loading, enable checks when sources are absent, confirmation semantics, and startup ramp/settling. Preserve approved ON/OFF/arming constraints. |
   504|| OPEN-17 | Dynamics and source interaction. | Define test-source output impedance/capacitance, cabling, source bandwidth conditions, step sizes, settling methodology, down-step undershoot, and CV/CP/CR overshoot. Verify stability without expanding the approved feature scope. |
   505|| OPEN-18 | Numerical mechanics/environment. | Confirm Rigol size reference and exact envelope, airflow clearances, numerical mains input range around nominal ratings, enclosure construction, cooling constraints, ambient definition, and any needed environmental limits beyond those approved. |
   506|| OPEN-19 | Budget accounting and availability. | Establish a dated quantity-one costed BOM. Clarify tax/shipping, PCB minimum order charges, and treatment of successive prototype revisions. No current parts prices or availability have been verified in this document. |
   507|| OPEN-20 | Tools/resources and release reproducibility. | Identify actual SDK repository/branch, Onshape workspace, KiCad/simulator versions, MCU/toolchain, model licenses/availability, and collaboration access. Do not invent repository paths, credentials, or tool access. |
   508|| OPEN-21 | Physical verification conditions. | Agree test durations, samples/repetitions, external reference uncertainty, controlled fault-energy limits, hot/cold states, and pass/fail tolerances. Continuous survival is a design requirement; any finite qualification test needs a defined rationale. |
   509|
   510|### Scope not silently added
   511|
   512|The interview did not require solar MPPT/I–V sweep functionality, a dedicated dynamic/pulse mode, 12–24 V trigger compatibility, standalone SD logging, Ethernet firmware updates in Rev A, a touchscreen, a numeric keypad, a specific MCU, a fixed MOSFET count, a specified monolithic eFuse IC, a high-current interlock relay in the DUT path, or a commercial safety certification. Adding any of these requires explicit scope/cost review.
   513|
   514|## 25. Immediate instructions to Hermes
   515|
   516|1. Treat this document as the interview-derived Rev A requirements baseline; maintain the distinction between approved requirements, proposals, and open items.
   517|2. First resolve the feasibility-critical questions and prepare the **Gate A architecture package**. Do not begin detailed schematic capture/layout before Morgan approves it.
   518|3. Use the actual project/SDK/Onshape resources after their locations and access are established. Do not claim access or completed integration from this document alone.
   519|4. Present a feasible design with a quantity-one hardware cost against the $500 limit. Where evidence does not support a target, identify the conflict and propose alternatives for Morgan's decision without changing the baseline silently.
   520|5. After Gate A approval, execute detailed design and prepare the complete Gate B fabrication-release package. Morgan performs board testing and reviews iteration results before integration/release progression.
   521|
   522|**First deliverable expected from Hermes:** architecture, feasibility evidence, preliminary BOM/cost, protection/isolation plan, thermal/SOA assessment, and a prioritized resolution list for the open items—not a declaration that a complete instrument is finished.
   523|
   524|---
   525|
   526|### Revision history
   527|
   528|| Revision | Date | Change |
   529||---|---|---|
   530|| 0.1 | 2026-09-18 | Initial consolidated requirements and design-handoff draft from Morgan's interview approvals through the design-toolchain decision. No prototype measurements, component pricing, or design verification are claimed. |
   531|