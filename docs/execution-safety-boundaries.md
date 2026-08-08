# Execution Safety Boundaries

Long Game treats generated schemas, imported schematic data, and LLM output as untrusted inputs. They may describe or propose an operation, but they cannot authorize a live hardware write.

## UniversalDriver

Schema-derived commands are read-only by default. A write requires all of the following:

1. command metadata explicitly declares a write operation
2. the schema is supplied out-of-band as trusted
3. `*IDN?` matches the schema identity pattern
4. every rendered numeric parameter has finite schema bounds
5. every value is within those bounds
6. the call occurs inside `with driver.armed():`
7. the rendered command contains no separator/newline injection

Generated/manual-enriched schemas must remain untrusted until reviewed.

## Safe state

`lg-safe CONFIG.yaml` is fail-closed against expected equipment declared in the required preflight-style `rig.instruments` list. Bench-style top-level `instruments` input is rejected. Each expected instrument needs a concrete `connection` plus an exact out-of-band manufacturer, model, and serial binding before any model-specific write is permitted. A known source/load is safe only after output-disable commands succeed and every required output and measurement readback meets its configured threshold. Identity mismatch, communication errors, missing or unbound expected equipment, unknown energy sources, failed writes, and failed or incomplete readbacks are blocking; the CLI returns a nonzero status.

Running `lg-safe` without a config is **read-only discovery**. It does not issue model-specific de-energization commands because no exact out-of-band identity binding is available. Supported and unknown instruments remain unverifiable, so no-config mode is not proof that a bench is present or safe and must never be used as an expected-equipment or safe-state gate.

## Hardware smoke

`lg-smoke CONFIG.yaml` requires the same exact `rig.instruments` expected-equipment inventory. It attempts both VISA and USB safe-state operations and refuses all discovery and identity/read-only probes unless every result is positively `verified_safe` or is an explicitly validated non-energy instrument. The complete safe-state operation runs again in the outermost cleanup path, including when the initial gate, discovery, or a probe fails. An unsafe/unverifiable initial or final result is blocking and returns status 2; probe errors return status 1. There is no permissive no-config CLI mode.

## Preflight

Safety limits must be finite, non-negative typed values with canonical units. If a source is expected, its output state and configured/queried setpoints must be verifiable. Missing or malformed data fails preflight rather than degrading to a warning.

## OpenOCD

Flash configuration is planner input, not a Tcl sandbox. The SDK emits a proposed command but does not inspect OpenOCD scripts, identify hardware, execute OpenOCD, flash, or verify an image. Every library or CLI execution request fails closed before subprocess creation, attestation handling, or mutable execution-artifact staging with `OpenOCD execution unavailable pending hardened sandbox`; CLI refusal is nonzero. The generated report is review material only, including for non-ELF commands, and must not be treated as execution or verification evidence.

## Guided wiring

`lg-guide-test` never infers actionable wiring from a net name. Connection steps require approved, revision-matched records containing exact instrument terminals, DUT destinations, references/returns, signal type, isolation model, and electrical limits. Missing records produce a `STOP` instruction.

## Live tests

Physical-hardware tests use the `hardware` pytest marker and are excluded by default. Live execution must establish safe state before the test and restore safe state in a `finally` path after the test.
