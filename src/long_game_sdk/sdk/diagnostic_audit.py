"""Client-facing Long Game Diagnostic Audit report generator.

This module converts the technical ``lg-preflight`` result stream into a concise
consulting deliverable: a readiness score, blocker list, quick wins, and a
30-day improvement plan that can be shared with a customer after a discovery
session or lab infrastructure audit.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from long_game_sdk.sdk.preflight.checks import CheckResult, PreflightReport, load_config, run_preflight


@dataclass(frozen=True)
class DiagnosticAudit:
    """Business-facing summary derived from a preflight report."""

    preflight: PreflightReport
    health_score: int
    readiness_band: str
    blockers: tuple[CheckResult, ...]
    quick_wins: tuple[CheckResult, ...]
    validated_controls: tuple[CheckResult, ...]

    @property
    def ready_for_client_demo(self) -> bool:
        """Return True when there are no blocking readiness failures."""

        return not self.blockers


def _readiness_band(score: int, blocker_count: int) -> str:
    if blocker_count:
        if score < 50:
            return "Critical remediation required"
        return "Not ready - blockers present"
    if score >= 90:
        return "Client-demo ready"
    if score >= 75:
        return "Operational with minor gaps"
    return "Needs cleanup before scaling"


def _score(report: PreflightReport) -> int:
    counts = report.summary_counts
    penalty = counts.get("fail", 0) * 25 + counts.get("warn", 0) * 7 + counts.get("skip", 0) * 2
    return max(0, min(100, 100 - penalty))


def build_audit(report: PreflightReport) -> DiagnosticAudit:
    """Convert a technical preflight report into a client-facing audit model."""

    blockers = tuple(item for item in report.results if item.status == "fail")
    quick_wins = tuple(item for item in report.results if item.status in {"warn", "skip"})
    validated_controls = tuple(item for item in report.results if item.status == "pass")
    score = _score(report)
    return DiagnosticAudit(
        preflight=report,
        health_score=score,
        readiness_band=_readiness_band(score, len(blockers)),
        blockers=blockers,
        quick_wins=quick_wins,
        validated_controls=validated_controls,
    )


def _clean_message(message: str) -> str:
    return " ".join(str(message).split())


def _bullet_results(results: Sequence[CheckResult], *, empty: str, limit: int | None = None) -> list[str]:
    if not results:
        return [f"- {empty}"]
    selected = results[:limit] if limit else results
    lines = [f"- **{item.category}/{item.name}** ({item.status.upper()}): {_clean_message(item.message)}" for item in selected]
    if limit and len(results) > limit:
        lines.append(f"- ...and {len(results) - limit} additional item(s).")
    return lines


def _category_counts(report: PreflightReport) -> dict[str, dict[str, int]]:
    categories: dict[str, dict[str, int]] = {}
    for item in report.results:
        bucket = categories.setdefault(item.category, {"pass": 0, "warn": 0, "fail": 0, "skip": 0})
        bucket[item.status] = bucket.get(item.status, 0) + 1
    return categories


def render_markdown(audit: DiagnosticAudit) -> str:
    """Render the diagnostic audit as customer-ready Markdown."""

    report = audit.preflight
    counts = report.summary_counts
    status = "READY FOR DISCOVERY DEMO" if audit.ready_for_client_demo else "REMEDIATION REQUIRED"
    lines = [
        "# Long Game Diagnostic Audit",
        "",
        f"- Rig: {report.rig_name}",
        f"- DUT type: {report.dut_type}",
        f"- Generated: {report.generated_at}",
        f"- Operator: {report.operator or 'Not captured'}",
        f"- DUT serial: {report.dut_serial or 'Not captured'}",
        f"- Git commit: {report.git_commit or 'Not captured'}",
        f"- Audit status: {status}",
        f"- Lab health score: {audit.health_score}/100",
        f"- Readiness band: {audit.readiness_band}",
        f"- Result mix: {counts.get('pass', 0)} pass / {counts.get('warn', 0)} warn / {counts.get('fail', 0)} fail / {counts.get('skip', 0)} skip",
        "",
        "## Executive Summary",
        "",
        "Long Game Technologies performed an automated readiness audit covering instrument connectivity, identity, safety guardrails, operator/DUT traceability, and data-path integrity. The goal is to separate real DUT failures from lab infrastructure noise before engineering teams commit time to a test campaign.",
        "",
        "## Health Score Interpretation",
        "",
        "- 90-100: Client-demo ready; maintain current controls and archive reports with test data.",
        "- 75-89: Operational with minor gaps; close warnings before scaling or handing off to new operators.",
        "- 50-74: Not ready; blockers or repeat warnings can create flaky tests and no-fault-found loops.",
        "- 0-49: Critical remediation required before live hardware work.",
        "",
        "## Blocking Risks",
        "",
        *_bullet_results(audit.blockers, empty="No blocking risks detected."),
        "",
        "## Quick Wins / Configuration Gaps",
        "",
        *_bullet_results(audit.quick_wins, empty="No warnings or skipped checks detected.", limit=8),
        "",
        "## Validated Controls",
        "",
        *_bullet_results(audit.validated_controls, empty="No controls validated yet; run preflight with reachable instruments.", limit=10),
        "",
        "## Category Breakdown",
        "",
    ]
    categories = _category_counts(report)
    if categories:
        for category, bucket in sorted(categories.items()):
            lines.append(
                f"- {category}: {bucket.get('pass', 0)} pass / {bucket.get('warn', 0)} warn / {bucket.get('fail', 0)} fail / {bucket.get('skip', 0)} skip"
            )
    else:
        lines.append("- No checks were executed.")

    lines.extend(
        [
            "",
            "## Recommended 30-Day Improvement Plan",
            "",
            "1. Resolve all blocking instrument reachability, identity, and safety-control failures before live DUT testing.",
            "2. Convert recurring warnings into explicit bench YAML fields so readiness is repeatable across operators.",
            "3. Pair this audit with `lg-hv-safety-plan` for HV/PCBA work and archive both reports beside raw test data.",
            "4. Add generated schemas/manual enrichment for placeholder or unknown instruments to reduce custom driver maintenance.",
            "5. Re-run `lg-safe`, `lg-preflight`, and `lg-audit` after remediation to prove the lab is ready for a customer-facing demo or campaign kickoff.",
            "",
            "## Consultant Notes",
            "",
            "- Primary value: reduce flaky infrastructure failures and no-fault-found investigations before they consume engineering time.",
            "- Suggested next conversation: review each blocker, assign owner/date, and define the minimum safe configuration for the next test campaign.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a client-facing Long Game Diagnostic Audit from a lab YAML config.")
    parser.add_argument("config", help="Path to lab preflight YAML config")
    parser.add_argument("--output", "-o", help="Optional Markdown audit output path")
    args = parser.parse_args()

    config_path = Path(args.config)
    report = run_preflight(load_config(config_path), repo=config_path.parent)
    audit = build_audit(report)
    markdown = render_markdown(audit)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        print(f"Wrote Long Game Diagnostic Audit: {output}")
    else:
        print(markdown)

    if not audit.ready_for_client_demo:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
