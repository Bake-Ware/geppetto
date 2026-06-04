#!/usr/bin/env bash
# Launch the Geppetto host client. Needs root for /dev/input (evdev grab) and
# /dev/ttyACM* (serial write).
# Usage: sudo ./run.sh                 # auto-detects the bridge Pico
#        sudo ./run.sh --port /dev/ttyACM0
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
# Prefer a local venv if one exists next to the client.
[ -x "$DIR/.venv/bin/python" ] && PY="$DIR/.venv/bin/python"
exec "$PY" -u "$DIR/geppetto.py" "$@"
