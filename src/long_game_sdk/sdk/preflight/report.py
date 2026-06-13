"""Markdown report rendering for lab preflight output."""

from __future__ import annotations

from collections import defaultdict

from long_game_sdk.sdk.preflight.checks import PreflightReport


def render_markdown(report: PreflightReport) -> str:
    counts = report.summary_counts
    lines = [
        "# Lab Readiness Diagnostic",
        "",
        f"- Rig: {report.rig_name}",
        f"- DUT type: {report.dut_type}",
        f"- Generated: {report.generated_at}",
        f"- Operator: {report.operator or 'Not captured'}",
        f"- DUT serial: {report.dut_serial or 'Not captured'}",
        f"- Git commit: {report.git_commit or 'Not captured'}",
        f"- Overall readiness: {'READY' if report.ready else 'NOT READY'}",
        f"- Results: {counts.get('pass', 0)} pass / {counts.get('warn', 0)} warn / {counts.get('fail', 0)} fail / {counts.get('skip', 0)} skip",
        "",
        "## Executive Summary",
        "",
        "This diagnostic verifies instrument reachability, identity, safety guardrails, environment prerequisites, and data-path integrity before a hardware test run.",
        "",
    ]
    critical = [item for item in report.results if item.status == "fail"]
    lines.extend(["## Critical Risks", ""])
    if critical:
        lines.extend(f"- **{item.category}/{item.name}**: {item.message}" for item in critical)
    else:
        lines.append("- No blocking readiness failures detected.")
    lines.append("")

    grouped = defaultdict(list)
    for item in report.results:
        grouped[item.category].append(item)
    for category in ("instrument", "safety", "environment"):
        if category not in grouped:
            continue
        title = {
            "instrument": "Instrument Inventory",
            "safety": "Safety / HV Controls",
            "environment": "Automation & Data Integrity",
        }[category]
        lines.extend([f"## {title}", ""])
        for item in grouped[category]:
            lines.append(f"- [{item.status.upper()}] {item.name}: {item.message}")
        lines.append("")

    lines.extend(
        [
            "## Recommended 30-Day Improvements",
            "",
            "- Convert recurring warnings into explicit rig config fields.",
            "- Add safe-state validation before and after every live hardware test.",
            "- Store preflight reports beside raw test data for auditability.",
            "- Add missing instrument schemas for any generated or placeholder drivers.",
            "",
        ]
    )
    return "\n".join(lines)
