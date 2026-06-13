"""CLI entry point for lab preflight checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from long_game_sdk.sdk.preflight.checks import load_config, run_preflight
from long_game_sdk.sdk.preflight.report import render_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Long Game lab preflight checks.")
    parser.add_argument("config", help="Path to lab preflight YAML config")
    parser.add_argument("--output", "-o", help="Optional Markdown report output path")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    report = run_preflight(config, repo=config_path.parent)
    markdown = render_markdown(report)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        print(f"Wrote lab readiness report: {output}")
    else:
        print(markdown)

    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
