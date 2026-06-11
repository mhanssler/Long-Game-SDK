#!/bin/bash
# install_sdk.sh - Bootstraps the Long Game SDK environment
set -e

echo "--- Installing Long Game SDK dependencies ---"

# 1. Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer now defaults to ~/.local/bin (not ~/.cargo/bin).
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.local/bin/env"
    elif [ -f "$HOME/.cargo/env" ]; then
        # Backward compatibility with older installer behavior.
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
    else
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi
else
    echo "uv is already installed."
fi

# 2. Sync the project
echo "Syncing SDK environment..."
uv sync

# 3. Install optional vendor runtimes needed by detected/common lab gear.
# On Linux this may prompt for sudo once to install USB udev permissions.
if [ -x "./scripts/install_labjack_exodriver.sh" ]; then
    ./scripts/install_labjack_exodriver.sh
fi

echo "--- Installation complete! ---"
echo "Run 'uv run lg-discover' to inventory equipment."
echo "Run 'uv run lg-onboard' to ensure schemas/drivers exist."
echo "Run 'uv run lg-safe' before and after live hardware tests."
echo "Run 'uv run lg-enrich' to search manuals and enrich generated schemas."
