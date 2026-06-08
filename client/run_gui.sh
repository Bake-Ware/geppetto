#!/usr/bin/env bash
# Launch the Geppetto settings GUI (GTK4). Runs as your user; reads /dev/input
# via the 'input' group (no sudo). See README if devices don't show up.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/geppetto_gui.py" "$@"
