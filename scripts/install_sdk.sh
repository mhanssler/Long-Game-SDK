#!/bin/bash
# install_sdk.sh - Bootstraps the Long Game SDK environment
set -e

echo "--- Installing Long Game SDK dependencies ---"

# 1. Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
else
    echo "uv is already installed."
fi

# 2. Sync the project
echo "Syncing SDK environment..."
uv sync

echo "--- Installation complete! ---"
echo "Run 'uv run lg-check' to verify your VISA/Instrument environment."
