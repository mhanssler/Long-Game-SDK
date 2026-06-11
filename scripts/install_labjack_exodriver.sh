#!/bin/bash
# install_labjack_exodriver.sh - user-local LabJack Exodriver bootstrap
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
WORKDIR="${TMPDIR:-/tmp}/long-game-exodriver"
EXO_URL="https://github.com/labjack/exodriver/archive/refs/heads/master.zip"

if [ "$(uname -s)" != "Linux" ]; then
    echo "LabJack Exodriver/udev bootstrap is Linux-specific; skipping on $(uname -s)."
    exit 0
fi

if [ -f "$PREFIX/lib/liblabjackusb.so" ]; then
    echo "LabJack Exodriver already present at $PREFIX/lib/liblabjackusb.so"
else
    echo "Installing LabJack Exodriver locally into $PREFIX..."
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    python3 - <<PY
import urllib.request
urllib.request.urlretrieve("$EXO_URL", "$WORKDIR/exodriver.zip")
PY
    unzip -q "$WORKDIR/exodriver.zip" -d "$WORKDIR"
    make -C "$WORKDIR/exodriver-master/liblabjackusb" clean
    make -C "$WORKDIR/exodriver-master/liblabjackusb"
    make -C "$WORKDIR/exodriver-master/liblabjackusb" install PREFIX="$PREFIX" LINK_SO=1 RUN_LDCONFIG=0
fi

RULE_SOURCE="$WORKDIR/exodriver-master/90-labjack.rules"
if [ ! -f "$RULE_SOURCE" ]; then
    mkdir -p "$WORKDIR"
    python3 - <<PY
import urllib.request
urllib.request.urlretrieve("$EXO_URL", "$WORKDIR/exodriver.zip")
PY
    unzip -oq "$WORKDIR/exodriver.zip" -d "$WORKDIR"
fi

RULE_DEST="/etc/udev/rules.d/90-labjack.rules"
install_udev_rule() {
    local sudo_cmd=()
    if [ "$(id -u)" -ne 0 ]; then
        if ! command -v sudo >/dev/null 2>&1; then
            echo "ERROR: LabJack udev rules require root, but sudo is not installed." >&2
            return 1
        fi
        sudo_cmd=(sudo)
    fi

    echo "Installing LabJack udev rules into $RULE_DEST..."
    "${sudo_cmd[@]}" cp "$RULE_SOURCE" "$RULE_DEST"
    "${sudo_cmd[@]}" udevadm control --reload-rules || true
    "${sudo_cmd[@]}" udevadm trigger || true
}

if [ "$(uname -s)" = "Linux" ]; then
    if [ -f "$RULE_DEST" ] && cmp -s "$RULE_SOURCE" "$RULE_DEST"; then
        echo "LabJack udev rules are already installed at $RULE_DEST"
    elif [ -t 0 ]; then
        echo "LabJack U3 USB access needs a one-time udev permissions install."
        echo "You may be prompted for your sudo password."
        install_udev_rule
        echo "If a LabJack is already plugged in, unplug/replug it so Linux reapplies permissions."
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        install_udev_rule
    else
        echo "ERROR: LabJack udev rules are missing and this installer is not interactive." >&2
        echo "Re-run from a terminal so sudo can prompt for your password:" >&2
        echo "  bash scripts/install_sdk.sh" >&2
        exit 1
    fi
fi

echo "LabJack Exodriver bootstrap complete."
echo "Library: $PREFIX/lib/liblabjackusb.so"
