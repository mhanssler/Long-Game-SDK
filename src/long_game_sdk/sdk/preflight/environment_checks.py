"""Environment and data-integrity preflight checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from long_game_sdk.sdk.preflight.results import result


def run_environment_checks(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    repo: str | Path | None = None,
    git_commit: str | None = None,
):
    runtime = dict(config.get("runtime") or {})
    checks = []

    for variable in runtime.get("required_env", []) or []:
        status = "pass" if env.get(str(variable)) else "fail"
        checks.append(result("required_env", "environment", status, f"{variable}: {'present' if status == 'pass' else 'missing'}."))

    output_dir = runtime.get("output_dir")
    if output_dir:
        path = Path(str(output_dir)).expanduser()
        if not path.is_absolute() and repo:
            path = Path(repo) / path
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".long_game_preflight_write_test"
            probe.write_text("ok")
            probe.unlink(missing_ok=True)
            checks.append(result("data_output_writable", "environment", "pass", f"Output directory writable: {path}"))
        except Exception as exc:  # noqa: BLE001 - report readiness issue
            checks.append(result("data_output_writable", "environment", "fail", f"Output directory not writable: {path}: {exc}"))
    else:
        checks.append(result("data_output_writable", "environment", "warn", "No runtime.output_dir configured."))

    checks.append(
        result(
            "git_commit_captured",
            "environment",
            "pass" if git_commit else "warn",
            f"Git commit captured: {git_commit}" if git_commit else "Git commit unavailable; run from a git checkout or set runtime.git_commit.",
            evidence={"git_commit": git_commit},
        )
    )
    return checks
