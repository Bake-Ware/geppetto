#!/usr/bin/env bash
# Launch the Geppetto tray indicator (GTK3 + AppIndicator). Needs root to read
# /dev/input and control the bridge client; forwards the graphical session env
# (display + session D-Bus) so the tray icon registers on your panel.
# Usage: ./run_tray.sh        (prompts for your sudo password)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
UID_="$(id -u)"
: "${XDG_RUNTIME_DIR:=/run/user/$UID_}"
: "${DBUS_SESSION_BUS_ADDRESS:=unix:path=$XDG_RUNTIME_DIR/bus}"
exec sudo env \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
    DISPLAY="${DISPLAY:-:0}" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
    python3 "$DIR/geppetto_tray.py" "$@"
