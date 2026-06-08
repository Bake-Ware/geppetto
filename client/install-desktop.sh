#!/usr/bin/env bash
# Install a desktop launcher (and optional autostart entry) for the Geppetto
# tray. Runs as your normal user. Usage: ./install-desktop.sh [--autostart]
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
DESKTOP="$APPS/geppetto.desktop"
mkdir -p "$APPS"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Geppetto
Comment=USB HID bridge — tray indicator & settings
Exec=$DIR/run_tray.sh
Icon=$DIR/icons/geppetto.svg
Terminal=false
Categories=Utility;System;
StartupNotify=false
EOF
echo "installed $DESKTOP"

if [ "${1:-}" = "--autostart" ]; then
    AUTO="$HOME/.config/autostart"
    mkdir -p "$AUTO"
    cp "$DESKTOP" "$AUTO/geppetto.desktop"
    echo "installed autostart entry $AUTO/geppetto.desktop"
fi
