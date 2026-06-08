#!/usr/bin/env bash
# Launch the Geppetto host client. Reads /dev/input + the serial port via your
# group membership (input, uucp) — no sudo needed. If it can't find devices,
# you're probably not in those groups yet: see README (usermod -aG input,uucp).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 -u "$DIR/geppetto.py" "$@"
