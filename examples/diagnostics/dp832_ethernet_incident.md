# Rigol DP832 Ethernet Incident — Representative Reconstruction

> **Provenance:** This is a representative, reconstructed worked transcript of the motivating failure flow. It is not a recording of a live bench, not generated evidence, and not proof that any particular DP832 had these settings. IP addresses, timestamps, scores, and results are illustrative while the action/result shapes follow the implemented diagnostic models.

## Scenario

- Instrument identity: `rigol_dp832`
- Requested VISA resource: `TCPIP::192.168.1.50::INSTR`
- Symptom: `Cannot connect to DP832 over Ethernet`
- Workstation LAN: `192.168.1.24/24`
- Intended instrument LAN: `192.168.1.50/24`
- Last-known-good history: configured separately with `LONG_GAME_LAST_KNOWN_GOOD`
- Safety mode: read-only diagnosis; no instrument output, setpoint, or LAN setting is changed

Representative invocation:

```bash
export LONG_GAME_LAST_KNOWN_GOOD="$PWD/reports/diagnostic_sessions.jsonl"

uv run lg-diagnose \
  --identity rigol_dp832 \
  --resource 'TCPIP::192.168.1.50::INSTR' \
  --symptom 'Cannot connect to DP832 over Ethernet' \
  --output reports/diagnostics/dp832-ethernet
```

The score values below are probability-like selector scores, not calibrated measurements. Each update is a complete hypothesis list in the actual model, abbreviated here to the hypotheses relevant to the incident.

## Reconstructed transcript

### 0. Seed the differential

**Hypothesis scores**

```text
Hypothesis(id="physical_link", description="Ethernet cable or link is down", score=0.35, status="open")
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.30, status="open")
Hypothesis(id="service_or_visa", description="Network path works but VXI-11/VISA service discovery fails", score=0.20, status="open")
Hypothesis(id="stale_client_socket", description="A local client socket is interfering", score=0.15, status="open")
```

### 1. Ping fails

**Action**

```text
ProbeCall(name="ping_host", args={"ip": "192.168.1.50"})
```

**Result**

```text
ProbeResult(
  probe="ping",
  target="192.168.1.50",
  ok=false,
  status="unreachable",
  details={},
  error="request timed out",
  duration_ms=2004.2
)
```

**Hypothesis scores**

```text
Hypothesis(id="physical_link", description="Ethernet cable or link is down", score=0.40, status="open")
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.46, status="open")
Hypothesis(id="service_or_visa", description="Network path works but VXI-11/VISA service discovery fails", score=0.09, status="open")
Hypothesis(id="stale_client_socket", description="A local client socket is interfering", score=0.05, status="open")
```

**Interpretation:** ICMP failure weakens a service-only explanation, but it does not prove that the instrument is absent. ICMP may be blocked.

### 2. ARP has no cached answer

**Action**

```text
ProbeCall(name="arp_lookup", args={"ip": "192.168.1.50"})
```

**Result**

```text
ProbeResult(
  probe="arp",
  target="192.168.1.50",
  ok=false,
  status="not_found",
  details={"answers": false, "mac": null, "vendor": null, "vendor_prefix_match": null},
  error="no entry",
  duration_ms=8.7
)
```

**Hypothesis scores**

```text
Hypothesis(id="physical_link", description="Ethernet cable or link is down", score=0.38, status="open")
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.55, status="open")
Hypothesis(id="service_or_visa", description="Network path works but VXI-11/VISA service discovery fails", score=0.05, status="open")
Hypothesis(id="stale_client_socket", description="A local client socket is interfering", score=0.02, status="open")
```

**Interpretation:** The “ARP no response” in this reconstruction means only that the workstation has no complete ARP/neighbor-cache entry. `arp_lookup` does not send an ARP request, so this is not evidence of an active ARP packet going unanswered. Together with failed ping it raises the L3 addressing hypothesis, but front-panel/link evidence is still needed to separate L1/L1.5 from L3.

### 3. Ask a targeted front-panel LAN question

**Action**

```text
AskOperator(question="On the DP832 front-panel LAN screen, is link shown as up, is the mode DHCP or static, and what IP address and subnet mask are displayed?")
```

**Result**

```text
OperatorTurn(
  question="On the DP832 front-panel LAN screen, is link shown as up, is the mode DHCP or static, and what IP address and subnet mask are displayed?",
  answer="Link is up. Mode is static. Address is 192.168.0.50 and mask is 255.255.255.0.",
  timestamp="2026-08-27T19:40:00+00:00"
)
```

**Hypothesis scores**

```text
Hypothesis(id="physical_link", description="Ethernet cable or link is down", score=0.03, status="ruled_out")
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.82, status="open")
Hypothesis(id="service_or_visa", description="Network path works but VXI-11/VISA service discovery fails", score=0.10, status="open")
Hypothesis(id="stale_client_socket", description="A local client socket is interfering", score=0.05, status="open")
```

**Interpretation:** Link-up evidence argues against a basic physical-link fault. The displayed `192.168.0.50/24` is not on the workstation's `192.168.1.0/24` subnet and does not match the requested resource.

### 4. Compare with last known good

**Action**

```text
ProbeCall(
  name="compare_to_last_known_good",
  args={
    "identity": {
      "identity": "rigol_dp832",
      "ip": "192.168.0.50",
      "resource": "TCPIP::192.168.0.50::INSTR"
    }
  }
)
```

**Result**

```text
ProbeResult(
  probe="last_known_good",
  target="rigol_dp832",
  ok=false,
  status="changed",
  details={
    "changes": {
      "ip": {"expected": "192.168.1.50", "actual": "192.168.0.50"},
      "resource": {
        "expected": "TCPIP::192.168.1.50::INSTR",
        "actual": "TCPIP::192.168.0.50::INSTR"
      }
    },
    "last_known_good": {
      "identity": "rigol_dp832",
      "ip": "192.168.1.50",
      "resource": "TCPIP::192.168.1.50::INSTR",
      "status": "connected"
    }
  },
  error=null,
  duration_ms=2.1
)
```

**Hypothesis scores**

```text
Hypothesis(id="physical_link", description="Ethernet cable or link is down", score=0.01, status="ruled_out")
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.96, status="confirmed")
Hypothesis(id="service_or_visa", description="Network path works but VXI-11/VISA service discovery fails", score=0.02, status="open")
Hypothesis(id="stale_client_socket", description="A local client socket is interfering", score=0.01, status="ruled_out")
```

**Interpretation:** The front-panel observation and deterministic history comparison identify the subnet mismatch: the intended/last-known-good address was `192.168.1.50/24`, while the instrument currently displays `192.168.0.50/24`.

### 5. Recommend the operator fix

**Action**

```text
Conclude(
  hypothesis_id="l3_subnet_mismatch",
  recommended_fix="After checking the site's address plan and avoiding duplicate addresses, have the operator restore the DP832 LAN configuration to static 192.168.1.50 with subnet mask 255.255.255.0 (and the approved gateway, if required), or deliberately move both endpoints to the same approved subnet. Then retry connectivity.",
  confidence=0.96
)
```

**Result**

```text
DiagnosticResult(
  outcome="recommended_fix_pending_operator_action",
  iterations=4,
  rejection_count=0,
  hypothesis_id="l3_subnet_mismatch",
  confidence=0.96
)
```

This result is intentionally **pending operator action**. The operator—not `lg-diagnose`—must validate the network plan and change the DP832 front-panel LAN settings. V1 does not mutate LAN settings, instrument settings, setpoints, or outputs.

### 6. Operator applies the fix and confirms

Outside the first diagnostic run, the authorized operator checks for address conflicts and restores the approved static address and mask. A new confirmation run is then started; v1 does not resume or silently modify the prior session.

**Operator action (external to `lg-diagnose`)**

```text
Applied approved DP832 LAN settings: 192.168.1.50 / 255.255.255.0.
```

**Confirmation action (new run)**

```text
ProbeCall(name="ping_host", args={"ip": "192.168.1.50"})
```

**Confirmation result**

```text
ProbeResult(
  probe="ping",
  target="192.168.1.50",
  ok=true,
  status="reachable",
  details={},
  error=null,
  duration_ms=1.8
)
```

**Confirmation hypothesis scores**

```text
Hypothesis(id="l3_subnet_mismatch", description="DP832 IP configuration is on the wrong subnet", score=0.02, status="ruled_out")
Hypothesis(id="connectivity_restored", description="The intended DP832 address is reachable after operator remediation", score=0.98, status="confirmed")
```

The operator should also confirm the intended VISA/application connection, because ICMP reachability alone does not prove VXI-11 or SCPI service health. The implemented Tier0 probes do not query a VXI-11 portmapper, discover RPC service ports, open the VISA resource, or send SCPI.

## Expected artifacts

The initial run writes:

```text
reports/diagnostics/dp832-ethernet/
├── events.jsonl
├── report.md
└── session.yaml
```

A threshold-reaching diagnosis also stores a deterministic content-addressed YAML case under the configured case library (default `diagnostics_cases/`). The stored recommendation remains evidence of a proposed fix, not evidence that the operator applied or verified it.

See [Diagnostic Troubleshooting](../../docs/diagnostic-troubleshooting.md) for probe semantics, model endpoint configuration, artifacts, termination behavior, and safety boundaries.
