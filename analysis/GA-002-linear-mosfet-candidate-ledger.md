# GA-002 — Linear MOSFET Candidate Ledger

**Revision:** 0.1-draft
**Research date:** 2026-09-19 UTC
**Gate status:** Research-only screening evidence; **NOT a component selection, approval, purchase authorization, or DC-SOA pass.**

## Scope and screening rule

This ledger implements the immediate GA-001/GA-002 evidence task for the proposed eight-cell linear sink. It was read against:

- `requirements/Long_Game_Eload_Requirements.md`, Rev. 0.1 — notably ARCH-005, PWR-001/002/009, THERM-006/007, and TOOL-002/006.
- `architecture/GA-001-architecture-feasibility.md`, Rev. 0.1-draft — notably the eight-cell topology and its requirement for manufacturer-primary, elevated-temperature DC-SOA evidence.
- `analysis/GA-001-preliminary-cost-and-open-items.md`, Rev. 0.1-draft — notably its explicit instruction not to replace a demonstrated linear-SOA device with a low-cost part having only pulsed SOA.

No candidate is credited for linear service from `RDS(on)`, maximum pulsed current, package dissipation, nominal `VDS`, a pulsed-SOA plot, or the `L2` naming suffix. A candidate becomes usable for the next analysis only after its actual manufacturer document is acquired, revision-controlled, and shows a DC/FBSOA locus that can be assessed at the allocated voltage/current and relevant temperature. This ledger performs no such pass/fail assessment.

## Evidence-access limitation

The candidate family below is Littelfuse/IXYS extended-FBSOA MOSFET material. During this bounded research session, direct HTTP retrieval of the official Littelfuse product pages and predictable official PDF locations returned **HTTP 403 Forbidden**, including the following queried official URLs:

- `https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth75n10l2`
- `https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth80n20l2`
- `https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth48n50l2`
- `https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixtk90n25l2`

The same result occurred for guessed official `~/media/electronics/datasheets/power_semiconductors/ixys_mosfets/` PDF paths. Consequently, the URLs below are manufacturer-primary *source locations to retrieve*, rather than session-verified document copies. No current distributor stock, lifecycle status, price, SPICE model, revision/date, graph detail, or thermal-curve content was independently verified in this session. These omissions are intentional rather than inferred favorable evidence.

## Candidate ledger

| Candidate | Manufacturer | Datasheet-reported headline rating / package* | Manufacturer-primary source to retrieve | Document revision/date | DC-SOA graph at relevant V/I? | Transient thermal-impedance evidence | Model availability | Lifecycle / availability evidence | Disposition |
|---|---|---|---|---|---|---|---|---|---|
| IXTH75N10L2 | Littelfuse (IXYS) | 100 V; TO-247AD | https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth75n10l2 | Not retrieved; revision/date unknown | **Not verified.** A manufacturer PDF must explicitly contain a DC/FBSOA graph and its applicable conditions; pulsed SOA is insufficient. | **Not verified.** Need the manufacturer `ZθJC` / transient thermal-impedance curve or equivalent data. | No official simulation-model download verified. | No current lifecycle or distributor availability evidence retrieved. | **INSUFFICIENT EVIDENCE** — additionally, 100 V headline `VDS` does not by itself demonstrate accommodation of the 100 V normal input plus the separate 120 V survival requirement. |
| IXTH80N20L2 | Littelfuse (IXYS) | 200 V; TO-247AD | https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth80n20l2 | Not retrieved; revision/date unknown | **Not verified.** Acquire the manufacturer DC/FBSOA graph, including temperature and time/DC interpretation. | **Not verified.** Acquire manufacturer transient `ZθJC` evidence for the 30 s thermal screen. | No official simulation-model download verified. | No current lifecycle or distributor availability evidence retrieved. | **INSUFFICIENT EVIDENCE** — voltage rating does not establish linear capability. |
| IXTH48N50L2 | Littelfuse (IXYS) | 500 V; TO-247AD | https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixth48n50l2 | Not retrieved; revision/date unknown | **Not verified.** Need the manufacturer graph and conditions at the intended DC point. | **Not verified.** Need manufacturer transient thermal-impedance data, including the applicable mounting condition. | No official simulation-model download verified. | No current lifecycle or distributor availability evidence retrieved. | **INSUFFICIENT EVIDENCE** — neither high `VDS` nor package identification is a DC-SOA result. |
| IXTK90N25L2 | Littelfuse (IXYS) | 250 V; TO-264 | https://www.littelfuse.com/products/power-semiconductors/mosfets/linear-mosfets/ixtk90n25l2 | Not retrieved; revision/date unknown | **Not verified.** Need a manufacturer DC/FBSOA graph with readable axis/conditions. | **Not verified.** Need manufacturer transient thermal-impedance evidence. | No official simulation-model download verified. | No current lifecycle or distributor availability evidence retrieved. | **INSUFFICIENT EVIDENCE** — package power handling and nominal voltage are not linear-load evidence. |

\* The headline `VDS` and package fields are provisional part-identification data pending acquisition of the revision-controlled manufacturer PDF. They are included to make the retrieval task unambiguous, not as an approved rating basis.

## Recommended next screen (explicit eight-cell allocation)

Use the existing eight-cell architecture only as a conservative, explicit candidate-screen allocation; it is not a current-sharing result or a component selection.

1. Obtain the current manufacturer PDF for each candidate, preserve its URL, revision/date, and file hash, and confirm the exact ordering suffix/package.
2. At the 100 V operating corner, screen each cell against `VDS = 100 V` and its allocated current. The explicit nominal equal-share points are:
   - 350 W continuous at 100 V / 3.5 A total: **100 V, 0.4375 A, 43.75 W per cell**.
   - 500 W, 30 s at 100 V / 5 A total: **100 V, 0.625 A, 62.5 W per cell**.
3. Do not use nominal equal sharing as a pass condition. Before any disposition advances, apply a separately justified imbalance/allocation and the relevant elevated junction/case-temperature condition to the manufacturer DC-SOA locus. If the graph is time-bounded, distinguish its DC limit from every pulse-duration curve.
4. For the 30 s condition, use the same manufacturer document's transient thermal-impedance data with the actual proposed mounting/interface/heatsink boundary. A steady-state `RθJC` value alone is not evidence for the required hot-start transient.
5. Retrieve an official SPICE/thermal model only if published by the manufacturer, record version and intended simulator, and separately verify its limits. Absence of a model must remain recorded; do not synthesize model behavior into a suitability conclusion.

The next screen must retain **INSUFFICIENT EVIDENCE** unless the above source documents are actually captured and traceably reviewed. It must not issue a component selection or claim that PWR-001, PWR-002, PWR-009, THERM-006, or THERM-007 is supported.
