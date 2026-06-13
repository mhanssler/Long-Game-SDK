"""Shared helpers for preflight checks."""

from __future__ import annotations

from typing import Any


def result(name: str, category: str, status: str, message: str, *, severity: str = "medium", evidence: dict[str, Any] | None = None):
    # Local import avoids a circular import between checks.py and the check modules.
    from long_game_sdk.sdk.preflight.checks import CheckResult

    if status not in {"pass", "warn", "fail", "skip"}:
        raise ValueError(f"Invalid preflight status: {status}")
    return CheckResult(name=name, category=category, status=status, message=message, severity=severity, evidence=evidence or {})
