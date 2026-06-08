#!/usr/bin/env bash
# Launch the Geppetto tray indicator (GTK3 + AppIndicator). Runs as your user so
# it registers on your session's system tray; reads /dev/input + serial via the
# 'input'/'uucp' groups (no sudo). See README if it doesn't appear.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/geppetto_tray.py" "$@"
