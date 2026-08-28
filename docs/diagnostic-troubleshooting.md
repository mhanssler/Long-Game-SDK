# Diagnostic Troubleshooting

`lg-diagnose` runs a bounded, evidence-oriented diagnosis of instrument connectivity. A local language model selects among typed, read-only actions; the deterministic engine validates and executes only a fixed probe set. The model is a policy component, not a command executor.

For a representative Rigol DP832 incident, see [DP832 Ethernet incident (reconstructed)](../examples/diagnostics/dp832_ethernet_incident.md).

## Quick start

Install the project, start a local model, and run:

```bash
ollama pull qwen3:8b
ollama serve

uv run lg-diagnose \
  --identity rigol_dp832 \
  --resource 'TCPIP::192.168.1.50::INSTR' \
  --symptom 'Cannot connect over Ethernet' \
  --output reports/diagnostics/dp832-ethernet
```

The default backend is Ollama at `http://127.0.0.1:11434/api/chat`, the default model is `qwen3:8b`, the per-request timeout is 30 seconds, the conclusion threshold is `0.9`, and the evidence-turn cap is 8. `--resource` is recorded as context; it is not opened by the diagnostic CLI.

Use `uv run lg-diagnose --help` for the authoritative option list:

- `--identity`: required schema/identity name, such as `rigol_dp832`. A matching `schemas/<identity>.yaml` is supplied to the selector when present; diagnosis also works without one.
- `--resource`: required resource identifier, recorded as context only.
- `--symptom`: required operator-observed symptom.
- `--output`, `-o`: required report-bundle directory.
- `--model-endpoint`: Ollama or OpenAI-compatible local chat URL.
- `--model`: model name; default `qwen3:8b`.
- `--model-timeout`: positive per-request timeout in seconds; default 30.
- `--max-iterations`: positive evidence-gathering turn cap; default 8.
- `--confidence`: conclusion threshold from 0 through 1; default `0.9`.
- `--case-library`: local resolved-case YAML directory; default `diagnostics_cases`.

## Architecture and evidence levels

The diagnostic path separates model reasoning from execution:

1. The selector receives identity/schema context, the symptom, current hypotheses, prior findings, operator turns, similar cases, and descriptions of permitted probes.
2. It returns strict JSON for exactly one typed action: `ProbeCall`, `AskOperator`, `Conclude`, or `Exhausted`.
3. The engine independently validates the action name, exact argument keys, values, and bounds before registry lookup.
4. A permitted probe or explicit operator turn adds evidence. The selector then returns a complete, rescored list of `Hypothesis(id, description, score, status)` values.
5. The engine concludes only at or above the configured confidence threshold, or escalates at a bound or explicit exhaustion.

For Ethernet incidents, interpret evidence by layer rather than treating every failure as equivalent:

- **L1/L1.5:** cable/link indicators and local-neighbor evidence. The CLI cannot inspect an instrument's front panel, switch port, or cable electrically, so it asks the operator when that evidence matters. `arp_lookup` is sometimes described as “L1.5” evidence because a cached IP-to-MAC mapping supports local-link adjacency, but it is not a physical-link test.
- **L3:** IP address, subnet, gateway, route, and ICMP reachability. A wrong static address or DHCP/static mode mismatch can leave physical link up while making the intended address unreachable.
- **Transport/application context:** a bounded TCP connect, local socket-table inspection, and VISA resource enumeration can distinguish some higher-layer failures after the network path is plausible.

A failed ping is not proof that a host is absent: ICMP echo may be blocked or disabled. Likewise, `arp_lookup` only reads the host operating system's existing ARP/neighbor cache. It sends no ARP discovery packet. “Not found” therefore means **no complete cached neighbor answer**, not “the instrument answered an active ARP request with failure.” On routed targets, absence from the local ARP cache is expected because the host normally caches the next-hop gateway instead.

### VXI-11 and raw sockets

A VISA resource ending in `::INSTR` commonly represents a VXI-11-style instrument session. VXI-11 uses ONC/RPC: a portmapper (commonly TCP/UDP 111) can negotiate a dynamically assigned service port. A `::SOCKET` resource usually represents a direct TCP socket to an explicitly configured service port (raw SCPI sockets often use 5025, but the instrument documentation is authoritative).

The v1 diagnostic implementation does **not** perform RPC portmapper queries, discover VXI-11 dynamic ports, send SCPI, or open VISA resources. It can only enumerate VISA resource strings and perform a generic TCP connect to an explicit IP/port selected through the allowlist. Do not infer “VXI-11 is healthy” merely because port 111 or an assumed raw-socket port accepts a TCP connection.

## Fixed Tier0 probe allowlist

The allowlist is closed in code; adding a callable to an injected registry does not make it selectable. There is no free shell, dynamic command, arbitrary path, driver execution, or model-supplied callable surface.

- `ping_host(ip)`: sends one bounded ICMP echo using the platform `ping` utility. Status distinguishes reachable, unreachable, timeout, and unavailable.
- `ping_gateway()`: resolves the workstation's default gateway with a bounded OS route command, then performs the same ping check.
- `arp_lookup(ip, expected_vendor_prefix?, expected_oui_prefix?)`: reads only the local ARP/neighbor cache and may compare available MAC/vendor-prefix evidence. It does not generate discovery traffic.
- `tcp_port_probe(ip, port, timeout?)`: attempts and closes a bounded TCP connection; it does not send an application payload. Probe timeouts must be greater than 0 and at most 10 seconds.
- `visa_list_resources(expected_ip?, expected_resource?, timeout?)`: enumerates VISA addresses and reports expected-address presence. It never opens an instrument resource.
- `check_stale_socket(ip, port, timeout?)`: inspects the workstation's local TCP connection table and process metadata. It does not close sockets or terminate processes.
- `compare_to_last_known_good(identity, timeout?)`: compares `ip`, `mac`, and `resource` fields with the newest successful matching JSONL record. It never creates or updates history.

The last-known-good probe reads an explicitly configured path from `LONG_GAME_LAST_KNOWN_GOOD` or `LG_LAST_KNOWN_GOOD`; otherwise it checks, in order, `reports/transport_diagnostics.jsonl`, `reports/diagnostics.jsonl`, and `reports/diagnostic_sessions.jsonl`. This history is separate from the resolved-case library.

## Safety invariant

Diagnosis is read-only with respect to the instrument: **no instrument setting, output, setpoint, or LAN configuration is changed**. The diagnostic modules do not import or invoke `UniversalDriver`, do not open VISA resources, and expose no `--execute` option. Even a model-proposed action is inert until it passes the fixed action and argument schemas.

Some Tier0 probes necessarily produce bounded network traffic (one ICMP echo or a TCP handshake) or invoke fixed OS utilities for route/cache inspection. “Read-only” means they collect state and do not mutate instrument configuration; it does not mean zero packets.

A recommendation is not an applied repair. The operator must review and apply cable, switch, workstation, DHCP, static-IP, firewall, service, or front-panel changes. In particular, v1 does not mutate LAN settings. Re-run diagnosis or the appropriate connectivity check after operator action to confirm recovery.

## Models and endpoints

`qwen3:8b` through local Ollama is the recommended starting point: it is small enough for many engineering workstations while supporting the strict structured-JSON loop. Move to a larger locally hosted model when repeated, representative incidents produce malformed schema output, weak hypothesis discrimination, or insufficient use of a complex instrument schema—and only when the workstation has adequate memory/accelerator capacity. A larger model does not widen the allowlist or bypass engine validation.

The backend supports:

- **Ollama:** use an `/api/chat` URL, for example the default `http://127.0.0.1:11434/api/chat`.
- **OpenAI-compatible local server:** pass its chat-completions URL, commonly `http://127.0.0.1:<port>/v1/chat/completions`. URLs containing `/v1/` or ending in `/chat/completions` use the OpenAI-compatible request/response shape.

The backend sends no authentication header and is intended for a local, unauthenticated endpoint. Do not point it at a remote endpoint that requires credentials or would disclose bench/schema/incident context.

Example:

```bash
uv run lg-diagnose \
  --identity rigol_dp832 \
  --resource 'TCPIP::192.168.1.50::INSTR' \
  --symptom 'VISA resource is listed but connection fails' \
  -o reports/diagnostics/dp832-vxi11 \
  --model-endpoint 'http://127.0.0.1:8000/v1/chat/completions' \
  --model 'local-model-name' \
  --model-timeout 60
```

## Operator interaction and termination

When the selector emits `AskOperator`, the CLI prints one targeted question on stdin. Enter an explicit, non-empty observation; blank/EOF answers cannot advance the engine. Questions should capture evidence unavailable to safe automation—for example, the front-panel LAN mode/address or whether the physical link indicator is lit—not ask the operator to assume a root cause.

Evidence probes and completed operator turns count toward `--max-iterations`; rejected actions do not. The default cap is 8. Invalid/off-list actions are never executed and are re-requested until the internal default rejection cap of 3 causes escalation.

Current CLI terminal outcomes are:

- `recommended_fix_pending_operator_action`: the session status is `resolved` in the sense that a hypothesis reached threshold and a known recommendation was produced. It does **not** claim that the repair was applied or verified.
- `escalate`: the selector explicitly exhausted permitted evidence, reached an iteration/rejection bound, or a backend/runtime/model error prevented safe progress.

The command returns zero for either bounded diagnostic outcome. Invalid CLI input or inability to create/write the output bundle returns 2. Automation must inspect `session.yaml`, `events.jsonl`, or the report outcome rather than treating exit code 0 as proof of repaired hardware.

## Artifacts and case memory

Each run overwrites/creates these files under `--output`:

- `report.md`: human-readable identity/resource/symptom, final hypotheses, findings, operator turns, status, recommendation, and safety statement.
- `events.jsonl`: timestamped structured events for hypothesis seeding/updates, selected/rejected actions, findings, operator turns, errors, and final outcome.
- `session.yaml`: complete serializable session state, including hypotheses, findings, operator turns, status, and available resolution metadata.

On a threshold-reaching result, a YAML case is also written under `--case-library` as `case-<sha256>.yaml`. Persistence is content-addressed and limited to resolved sessions with root cause, outcome, and an applied or recommended fix. Retrieval is deterministic: cases must match normalized instrument class, then are ranked by normalized/stemmed symptom-keyword overlap; ties use the stable case ID. At most three similar cases are supplied to the selector by the CLI engine configuration. Similar cases are context, not proof, and do not bypass current evidence or safety checks.

## Troubleshooting

### Endpoint unavailable or connection refused

1. Confirm Ollama is running: `ollama serve`.
2. Confirm the model exists: `ollama list`; if needed, run `ollama pull qwen3:8b`.
3. Check that `--model-endpoint` is the full chat URL, not merely the server root.
4. Keep endpoint and model names paired with the serving backend.
5. Increase `--model-timeout` if local generation is healthy but slow.

The CLI intentionally turns backend, malformed-model-output, and probe runtime failures into an escalation bundle without exposing backend response details. Inspect `events.jsonl` for a `diagnostic_error` category/type and `report.md` for the safe remediation message. The current top-level message groups these failures as a local-model/runtime escalation, so reproduce with focused tests if the endpoint itself is healthy.

### Repeated malformed structured output

Use the default `qwen3:8b` first, verify the server supports JSON-schema structured output, and ensure an OpenAI-compatible URL is detected as such. If valid prompts repeatedly fail on complex incidents, try a larger local model. Do not work around validation by adding shell commands or broadening probe arguments.

### Ping fails but other connectivity works

Treat ping as one piece of L3 evidence. ICMP may be blocked. Check the explicit service port and VISA enumeration where appropriate, and use operator-confirmed front-panel network state. Do not interpret an empty ARP cache as active non-response, especially across a router.

### VISA `::INSTR` resource is absent or a port is closed

`visa_list_resources` only reports what the configured VISA backend enumerates. Verify PyVISA/backend installation with `uv run lg-check`, and distinguish a VXI-11 `::INSTR` address from a raw `::SOCKET` address. The diagnostics do not query a VXI-11 portmapper or guess negotiated ports.

### Last-known-good is missing

Set `LONG_GAME_LAST_KNOWN_GOOD` (or `LG_LAST_KNOWN_GOOD`) to an existing JSONL history file, or place history at one of the default `reports/` paths. Records need a matching identity key (`identity`, `id`, `name`, or `serial`); explicit failed records are excluded. The probe is read-only and will not bootstrap the file.

## Verification and hardware opt-in

Run the diagnostic documentation's supporting unit tests without hardware:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_transport_diagnostics.py \
  tests/test_diagnostic_session.py \
  tests/test_diagnostic_engine.py \
  tests/test_diagnostic_safety_boundary.py \
  tests/test_llm_hypothesis_selector.py \
  tests/test_diagnostic_case_library.py \
  tests/test_diagnose_cli.py
```

The repository default excludes tests marked `hardware`. The diagnostic Tier0 DP832 checks require deliberate opt-in and explicit targets:

```bash
LONG_GAME_RUN_RIGOL_DP832_TIER0=1 \
RIGOL_DP832_IP=192.168.1.50 \
RIGOL_DP832_VISA_RESOURCE='TCPIP::192.168.1.50::INSTR' \
PYTHONPATH=src uv run pytest -q -m 'hardware and rigol_dp832' \
  tests/test_diagnostic_safety_boundary.py
```

Those two opt-in checks perform only DP832 ping and VISA listing. Separately, `tests/hardware/test_dp832.py` opens live hardware and queries voltage under safe-state handling; run broader hardware tests only on an intentionally prepared safe bench as described in the [README](../README.md#development).
