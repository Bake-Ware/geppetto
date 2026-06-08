#!/usr/bin/env bash
# Install a desktop launcher (and optional autostart entry) for the Geppetto
# tray. Run as your normal user (NOT sudo): ./install-desktop.sh [--autostart]
#
# The launcher runs run_tray.sh, which needs root — so it opens in a terminal to
# prompt for your sudo password. For seamless (no-prompt) start, see the note at
# the end about passwordless sudo.
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
Terminal=true
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

cat <<'NOTE'

The launcher opens a terminal to ask for your sudo password (the tray needs root
for /dev/input). To start it with no prompt (e.g. for autostart), allow the run
scripts without a password — `sudo visudo -f /etc/sudoers.d/geppetto` and add:

    <youruser> ALL=(root) NOPASSWD: /full/path/to/client/run_tray.sh, /full/path/to/client/run_gui.sh

then set Terminal=false in the .desktop.
NOTE
