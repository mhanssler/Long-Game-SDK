     1|# Independent Senior Release Review — Current Working Tree
     2|
     3|Review date: 2026-09-19
     4|Scope: Gate-A electronic-load planning package and proposed built-in SCPI schema
     5|Review mode: Read-only; no files were edited, staged, or committed.
     6|
     7|## 1. Scope examined
     8|
     9|Tracked changes:
    10|
    11|- `src/long_game_sdk/sdk/registry.py`
    12|- `tests/test_registry.py`
    13|- `tests/test_sync_expect.py`
    14|
    15|All listed untracked files:
    16|
    17|- `analysis/GA-001-preliminary-cost-and-open-items.md`
    18|- `analysis/GA-002-linear-mosfet-candidate-ledger.md`
    19|- `analysis/GA-003-preliminary-component-selection.md`
    20|- `architecture/GA-001-architecture-feasibility.md`
    21|- `reports/GA-001-sol-review-prompt.md`
    22|- `reports/GA-001-sol-review.md`
    23|- `reports/GA-001-sol-review.stderr`
    24|- `reports/GA-002-terra-candidate-research-prompt.md`
    25|- `reports/GA-002-terra-worker.stderr`
    26|- `reports/GA-002-terra-worker.stdout`
    27|- `requirements/GA-001-traceability.md`
    28|- `requirements/Long_Game_Eload_Requirements.md`
    29|- `schemas/lgt_eload500.yaml`
    30|
    31|Relevant runtime behavior was also reviewed in:
    32|
    33|- `src/long_game_sdk/sdk/universal_driver.py`
    34|- `src/long_game_sdk/sdk/safety.py`
    35|- Existing instrument schemas and safety/registry tests
    36|- `pyproject.toml` packaging configuration
    37|
    38|The requirements hash recorded at `architecture/GA-001-architecture-feasibility.md:6` was independently checked and matches the current requirements file:
    39|
    40|`e0a675e4cb2e2047ddc1106b1d26fcbfa7fc001e512a59d18d29ea25184cb628`
    41|
    42|The planning arithmetic for 43.75 W/cell, 62.5 W/cell, 4.5 kJ excess energy, and the $724 cost total is internally correct.
    43|
    44|Targeted non-hardware verification completed successfully:
    45|
    46|- `tests/test_registry.py`
    47|- `tests/test_sync_expect.py`
    48|- `tests/test_universal_driver_safety.py`
    49|
    50|Result: 66 passed.
    51|
    52|Passing tests do not resolve the release blockers below because the new tests do not exercise the proposed schema’s executable safety semantics.
    53|
    54|## 2. Release-blocking findings
    55|
    56|### P0 — The proposed built-in schema is not an executable control schema
    57|
    58|Paths/lines:
    59|
    60|- `schemas/lgt_eload500.yaml:13-27`
    61|- `src/long_game_sdk/sdk/universal_driver.py:194-235`
    62|- `src/long_game_sdk/sdk/registry.py:175-176`
    63|
    64|Every schema command is expressed using the legacy string form. `UniversalDriver` treats a legacy command without a query marker as `legacy-write` and unconditionally rejects it. In direct validation:
    65|
    66|- `input_off()` raised `SchemaSafetyError`
    67|- `set_current()` raised `SchemaSafetyError`
    68|- `set_mode()` raised `SchemaSafetyError`
    69|- No write reached the transport
    70|
    71|Therefore `input_on`, `input_off`, `reset`, `clear_status`, `set_mode`, and `set_current` are advertised by the schema but cannot execute through the declared `scpi-universal` driver.
    72|
    73|This is fail-closed, so it is not presently an unsafe write path. It is nevertheless release-blocking because the registry labels the model `built-in` while its advertised control surface is nonfunctional.
    74|
    75|Minimal fix:
    76|
    77|1. Until executable behavior is ready, classify this as an experimental/read-only draft rather than `built-in`, and remove or clearly segregate the non-executable mutating commands.
    78|2. Before enabling writes, represent each mutation with explicit `operation: write` metadata and command-local parameter bounds/enums.
    79|3. Add tests proving that:
    80|   - Mutations require trusted schema, exact manufacturer/model/serial binding, fresh identity verification, and an armed context.
    81|   - Out-of-range current, voltage, power, resistance, and invalid modes fail before transport access.
    82|   - `input_off` remains available through an independently reviewed safe-state path.
    83|   - `input_on` cannot bypass ownership, interlock, active faults, peak restrictions, or watchdog requirements.
    84|
    85|Merely converting `input_on` to an explicit write is not sufficient. Requirements `COM-005` through `COM-009`, `SAFE-001`, `SAFE-002`, and `SAFE-011` require stateful authorization semantics not supplied by the generic schema.
    86|
    87|### P0 — The schema contains a 40 A limit that contradicts the 5 A safety boundary
    88|
    89|Paths/lines:
    90|
    91|- `schemas/lgt_eload500.yaml:29-35`
    92|- `requirements/Long_Game_Eload_Requirements.md:46-49`
    93|- `requirements/Long_Game_Eload_Requirements.md:97-102`
    94|
    95|The schema declares:
    96|
    97|`current: [0.0, 40.0]`
    98|
    99|The approved operating-current ceiling is 5 A in every mode. The 40 A value appears inherited from the Rigol DL3021 schema and is inconsistent with `ELEC-002` and `ELEC-003`.
   100|
   101|The current driver does not consume the top-level `constraints` block when authorizing writes, so this erroneous limit does not presently authorize a write. It becomes hazardous if future code assumes those constraints are authoritative.
   102|
   103|Minimal fix:
   104|
   105|- Replace the copied 40 A value with a requirements-consistent representation.
   106|- Do not encode the unresolved 5 A accuracy/clipping policy as though it were settled. Separate:
   107|  - command range,
   108|  - nominal regulation target,
   109|  - measured operating ceiling,
   110|  - hardware clamp threshold and tolerance.
   111|- Enforce the approved bound in command-local metadata used by `UniversalDriver`, not only in an ignored descriptive block.
   112|- Add boundary tests at, below, and above 5 A, including NaN, infinity, booleans, and numeric strings.
   113|
   114|### P0 — The schema materially contradicts the required mode and interface scope
   115|
   116|Paths/lines:
   117|
   118|- `schemas/lgt_eload500.yaml:9-27`
   119|- `requirements/Long_Game_Eload_Requirements.md:52-54`
   120|- `requirements/Long_Game_Eload_Requirements.md:80-82`
   121|- `requirements/Long_Game_Eload_Requirements.md:240-254`
   122|- `requirements/Long_Game_Eload_Requirements.md:256-268`
   123|
   124|The schema exposes only CC mode and one current setpoint. The approved interface requires CC, CV, CP, and CR plus sense selection, ownership, watchdog/keep-alive, faults and limits, temperatures, fan status, alarms, peak status, logging, triggers, calibration status, presets, and diagnostics.
   125|
   126|A partial bring-up schema is acceptable, but it must not be represented as the built-in product schema for the requirements baseline.
   127|
   128|Minimal fix:
   129|
   130|- Rename/status it explicitly as a preliminary read-only or board-bring-up schema, or complete and review the interface specification before built-in registration.
   131|- Add a capability-completeness matrix mapping every `COM-004`, `SW-002`, `LOG`, `EVT`, `TRIG`, and safety-related operation to implemented, deferred, or blocked status.
   132|- Do not invent command syntax for unresolved ownership, keep-alive, acknowledgment, peak recovery, or trigger semantics.
   133|
   134|### P0 — Built-in registration is not integrated with the SDK safe-state subsystem
   135|
   136|Paths/lines:
   137|
   138|- `src/long_game_sdk/sdk/registry.py:175-176`
   139|- `schemas/lgt_eload500.yaml:37-43`
   140|- `src/long_game_sdk/sdk/safety.py:46-67`
   141|- `src/long_game_sdk/sdk/safety.py:82-90`
   142|- `src/long_game_sdk/sdk/safety.py:440-495`
   143|
   144|The schema contains a `safe_state` section, but `safety.py` does not load schema-defined safe-state behavior. It supports only the statically defined DP832 and DL3021 models. An LGT-ELOAD-500 is therefore classified as `UNKNOWN` by the safe-state path and reported `unverifiable`; the YAML `:INPut OFF` command is never used there.
   145|
   146|This creates a serious semantic mismatch: registry registration says “built-in,” while the SDK’s independent shutdown/verification mechanism has no known procedure for the model.
   147|
   148|Minimal fix:
   149|
   150|- Do not release the built-in registration until one of these is implemented:
   151|  1. A reviewed, exact-model LGT safe-state profile in `safety.py`; or
   152|  2. A schema-driven safe-state mechanism that preserves the same exact identity binding, fresh pre-write identity authorization, fail-closed behavior, and post-write verification used by the current static profiles.
   153|- Verify at minimum:
   154|  - load-state readback is OFF,
   155|  - measured current is below an approved safe threshold,
   156|  - query or write failure is `unverifiable`, never safe,
   157|  - identity changes block all writes,
   158|  - both `LGT-ELOAD-500` and any separately retained prototype model have explicit tests.
   159|
   160|### P1 — Model matching is substring-based across the complete identity text
   161|
   162|Paths/lines:
   163|
   164|- `src/long_game_sdk/sdk/registry.py:197-201`
   165|- `src/long_game_sdk/sdk/registry.py:175-176`
   166|- `tests/test_registry.py:73-87`
   167|
   168|`match_driver` searches for known-model substrings in the concatenation of manufacturer, model, and complete IDN. A known-model string appearing in a manufacturer name, serial, firmware field, or unrelated model can therefore select a built-in schema.
   169|
   170|The existing safety subsystem correctly selects supported models from the parsed, exact IDN model field at `src/long_game_sdk/sdk/safety.py:82-90`. Registry matching should provide equivalent resistance to false identification, especially before associating a command-capable schema.
   171|
   172|Minimal fix:
   173|
   174|- Match known VISA/SCPI models against the parsed model field, using normalized exact equality or a narrowly documented alias table.
   175|- Do not use arbitrary substring matches over the complete IDN for built-in command-capable drivers.
   176|- Add negative tests for:
   177|  - `NOT-LGT-ELOAD-500`,
   178|  - a serial containing `LGT-ELOAD-500`,
   179|  - malformed IDN responses,
   180|  - conflicting model and IDN fields,
   181|  - case/whitespace normalization,
   182|  - the `LGT-CELL-A` alias.
   183|
   184|### P1 — `LGT-CELL-A` is registered as the same built-in product without an established identity contract
   185|
   186|Paths/lines:
   187|
   188|- `src/long_game_sdk/sdk/registry.py:176`
   189|- `schemas/lgt_eload500.yaml:4-7`
   190|- `schemas/lgt_eload500.yaml:45-46`
   191|
   192|The schema metadata says its model is `LGT-ELOAD-500`, while the IDN pattern and registry also accept `LGT-CELL-A`. The planning documents describe an eight-cell instrument architecture; they do not establish `LGT-CELL-A` as an approved instrument identity or prove that a cell controller has the same command, limits, firmware, and safe-state semantics as the complete product.
   193|
   194|Minimal fix:
   195|
   196|- Remove `LGT-CELL-A` from built-in registration until its identity and command contract are documented and tested, or give it a separate schema/profile.
   197|- If it is intentionally a firmware alias, document that decision and test exact expected identity binding for each model.
   198|
   199|### P1 — The schema is not packaged with the Python distribution
   200|
   201|Paths/lines:
   202|
   203|- `src/long_game_sdk/sdk/registry.py:21-22`
   204|- `src/long_game_sdk/sdk/registry.py:283-299`
   205|- `pyproject.toml:43-48`
   206|
   207|The wheel configuration packages only `src/long_game_sdk`. Repository-root `schemas/` is not included. Inspection of the existing wheel found `registry.py` but no instrument schema files.
   208|
   209|In an installed environment, `PROJECT_ROOT / "schemas"` is not a reliable package-data location. A known model may be reported as `built-in`, and `ensure_schema` may return a path for a schema that does not exist; for built-in matches it does not generate the missing file.
   210|
   211|Minimal fix:
   212|
   213|- Move built-in schemas under the Python package or explicitly include them as package data.
   214|- Resolve paths using package resources rather than source-tree parent traversal.
   215|- Make a missing built-in schema a clear failure, not a returned nonexistent path.
   216|- Add a wheel-install test proving that registry matching can locate and load `lgt_eload500.yaml` outside the source checkout.
   217|
   218|### P1 — The new registry test verifies only metadata mapping
   219|
   220|Path/lines:
   221|
   222|- `tests/test_registry.py:73-87`
   223|
   224|The test does not verify:
   225|
   226|- Schema existence.
   227|- Schema parsing.
   228|- Driver construction.
   229|- Read/write operation classification.
   230|- Current bounds.
   231|- Mode enum handling.
   232|- Exact model matching.
   233|- Safe-state integration.
   234|- Installed-package behavior.
   235|- The `LGT-CELL-A` alias.
   236|
   237|Minimal fix:
   238|
   239|Add focused integration tests covering those behaviors before treating the model as built-in.
   240|
   241|## 3. Gate-A requirements and artifact consistency
   242|
   243|The planning package is appropriately conservative in several important respects:
   244|
   245|- `architecture/GA-001-architecture-feasibility.md:5-12` clearly says it is not approved or fabrication-released.
   246|- `architecture/GA-001-architecture-feasibility.md:105-114` correctly limits the thermal calculation to a plausibility screen.
   247|- `architecture/GA-001-architecture-feasibility.md:122-139` correctly leaves cost, DC-SOA, fault containment, and thermal feasibility blocked.
   248|- `analysis/GA-001-preliminary-cost-and-open-items.md:23-33` openly identifies the $724 planning total against the $500 requirement.
   249|- `analysis/GA-002-linear-mosfet-candidate-ledger.md:15-26` records the missing manufacturer-primary evidence rather than inferring suitability.
   250|- `analysis/GA-003-preliminary-component-selection.md:5-12` explicitly separates a development selection from a release selection.
   251|- `requirements/Long_Game_Eload_Requirements.md:361-376` correctly requires owner approval and does not treat simulation or documentation as fabrication authorization.
   252|
   253|The package is not ready for Gate-A approval. The following required evidence remains absent by the package’s own accounting:
   254|
   255|- Manufacturer-primary IXTH80N20L2 datasheet and revision-controlled DC/FBSOA evidence.
   256|- Elevated-temperature worst-case cell allocation and current-sharing analysis.
   257|- Selected thermal stack and hot-start transient model.
   258|- Exact C-LAB source/harness/fault-energy contract.
   259|- Protection-device ratings and coordination.
   260|- Isolation/withstand/separation allocation.
   261|- Preliminary physical arrangement.
   262|- Dated, source-backed quantity-one BOM.
   263|- Resolution or explicit owner disposition of the cost conflict.
   264|- 5 A clipping/tolerance policy and over-500 W arbitration.
   265|
   266|That incompleteness is acceptable for a draft planning package because it is prominently disclosed. It is not acceptable for a Gate-A approval claim.
   267|
   268|## 4. Documentation-specific issues
   269|
   270|### Existing review record is stale and internally incorrect
   271|
   272|Paths/lines:
   273|
   274|- `reports/GA-001-sol-review.md:29`
   275|- `reports/GA-001-sol-review.md:44-47`
   276|- `architecture/GA-001-architecture-feasibility.md:116-120`
   277|
   278|The review says U/D/K is undefined, but the current architecture document defines U, D, and K at lines 118-120. It also criticizes a four-cell assertion that is not present in the current reviewed architecture.
   279|
   280|Minimal fix:
   281|
   282|- Do not commit `reports/GA-001-sol-review.md` as the current independent review.
   283|- Replace it with a review against the exact current artifact set, or label it superseded and identify the reviewed artifact hashes.
   284|- The associated prompt, stdout, and empty stderr files are execution provenance, not release evidence.
   285|
   286|### Traceability matrix slightly overstates tool/document coverage
   287|
   288|Path/line:
   289|
   290|- `requirements/GA-001-traceability.md:23`
   291|
   292|The combined `TOOL/DOC/verification/release` row is marked “Documented,” although simulator/version, KiCad resources, MCU/toolchain, Onshape workspace, reproducibility package, and most release artifacts remain unresolved.
   293|
   294|Minimal fix:
   295|
   296|- Change the status to “Partially documented / planned” or split the row by requirement group.
   297|- Keep the missing tool/resource confirmation visible as required next evidence.
   298|
   299|### Generated worker artifacts should not be treated as engineering deliverables
   300|
   301|Files:
   302|
   303|- `reports/GA-001-sol-review-prompt.md`
   304|- `reports/GA-001-sol-review.stderr`
   305|- `reports/GA-002-terra-candidate-research-prompt.md`
   306|- `reports/GA-002-terra-worker.stdout`
   307|- `reports/GA-002-terra-worker.stderr`
   308|
   309|Two stderr files are empty, and the stdout file is only a worker completion summary. These do not add design evidence and should normally remain local or be placed in a clearly separated provenance/archive area if retention is required.
   310|
   311|## 5. Other working-tree observations
   312|
   313|- `tests/test_sync_expect.py` is unrelated to the Gate-A/schema change. Its CRLF/LF coverage is reasonable and the tests pass, but it should be committed separately to preserve review scope.
   314|- `git diff --check` reports a new blank line at EOF in `tests/test_registry.py:88`. This is not a safety defect but should be cleaned before committing code.
   315|- No staged changes were present.
   316|- The targeted test run and validation did not alter the reported working-tree file set.
   317|
   318|## 6. Documentation commit disposition
   319|
   320|The following can safely be committed as an explicitly draft/review-only planning package, provided the commit message and release notes state that Gate A is not approved and no fabrication, purchasing, energization, component qualification, or performance verification is authorized:
   321|
   322|- `requirements/Long_Game_Eload_Requirements.md`
   323|- `architecture/GA-001-architecture-feasibility.md`
   324|- `analysis/GA-001-preliminary-cost-and-open-items.md`
   325|- `analysis/GA-002-linear-mosfet-candidate-ledger.md`
   326|- `analysis/GA-003-preliminary-component-selection.md`
   327|- `requirements/GA-001-traceability.md`, preferably after correcting the overstated line 23 status
   328|
   329|The following should not be included in that documentation commit as current release evidence:
   330|
   331|- `reports/GA-001-sol-review.md`
   332|- The prompt/stdout/stderr worker artifacts
   333|- `schemas/lgt_eload500.yaml`
   334|- The registry and registry-test changes
   335|
   336|The SCPI schema may be retained separately as a design draft, but it should not be registered or described as a built-in executable schema until the command metadata, 5 A boundary, capability scope, exact identity matching, safe-state integration, packaging, and tests are corrected.
   337|
   338|## 7. Final verdict
   339|
   340|The Gate-A documents are suitable for version-controlled review as an explicitly unapproved planning package. The proposed built-in SCPI integration is not releasable, and the package does not contain the evidence required for Gate-A owner approval.
   341|
   342|VERDICT: approve-docs-only
   343|