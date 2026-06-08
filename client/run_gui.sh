#!/usr/bin/env bash
# Launch the Geppetto settings GUI (GTK4). Needs root to read /dev/input, with
# the graphical-session env forwarded so the window shows on your desktop.
# Usage: ./run_gui.sh        (will prompt for your sudo password)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
exec sudo env \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
    DISPLAY="${DISPLAY:-:0}" \
    python3 "$DIR/geppetto_gui.py" "$@"
