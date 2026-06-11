#!/bin/bash
# install_labjack_exodriver.sh - user-local LabJack Exodriver bootstrap
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
WORKDIR="${TMPDIR:-/tmp}/long-game-exodriver"
EXO_URL="https://github.com/labjack/exodriver/archive/refs/heads/master.zip"

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

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    echo "Installing LabJack udev rules via sudo..."
    sudo cp "$RULE_SOURCE" /etc/udev/rules.d/90-labjack.rules
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger || true
else
    echo "LabJack udev rules require sudo. If U3 access fails, run:"
    echo "  sudo cp $RULE_SOURCE /etc/udev/rules.d/90-labjack.rules"
    echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
fi

echo "LabJack Exodriver bootstrap complete."
echo "Library: $PREFIX/lib/liblabjackusb.so"
