from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "repair_windows_checkout.ps1"
GITIGNORE = REPO_ROOT / ".gitignore"


def test_windows_repair_script_exists_and_targets_stale_auto_onboarder_state():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "git fetch origin" in text
    assert "git pull --ff-only origin main" in text
    assert "src/long_game_sdk/sdk/observers/auto_onboarder.py" in text
    assert "instrument_state.json" in text
    assert "uv sync" in text
    assert "uv run lg-auto-onboard --once" in text


def test_windows_repair_script_does_not_use_blind_destructive_git_reset():
    text = SCRIPT.read_text(encoding="utf-8").lower()

    assert "git reset --hard" not in text
    assert "git clean -fd" not in text
    assert "remove-item -recurse" not in text


def test_instrument_observer_state_is_ignored():
    text = GITIGNORE.read_text(encoding="utf-8")

    assert "instrument_state.json" in text
