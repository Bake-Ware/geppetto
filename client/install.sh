#!/usr/bin/env bash
# One-shot installer for the Geppetto host tools (client + settings GUI + tray).
#   1. installs dependencies (Arch/pacman; lists them otherwise)
#   2. adds you to the input + uucp groups (device access without sudo)
#   3. installs an app-menu launcher (+ autostart with --autostart)
#
# Run as your NORMAL user (it calls sudo only where needed):
#   ./install.sh [--autostart]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART=""
[ "${1:-}" = "--autostart" ] && AUTOSTART="--autostart"

echo "== Geppetto installer =="

# ---- 1. dependencies ----
DEPS=(python-gobject gtk3 gtk4 libayatana-appindicator python-evdev python-pyserial)
if command -v pacman >/dev/null 2>&1; then
    missing=()
    for p in "${DEPS[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || missing+=("$p"); done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "installing: ${missing[*]}"
        sudo pacman -S --needed --noconfirm "${missing[@]}"
    else
        echo "deps: all present"
    fi
else
    echo "non-Arch system — install these with your package manager:"
    echo "  ${DEPS[*]}"
    echo "  (PyGObject, GTK3, GTK4, libayatana-appindicator, python-evdev, pyserial)"
fi

# ---- 2. device-access groups ----
member_of() { getent group "$1" | awk -F: '{print $4}' | tr ',' '\n' | grep -qx "$2"; }
need_relogin=0
for g in input uucp; do
    if member_of "$g" "$USER"; then
        echo "group $g: already a member"
    else
        echo "group $g: adding $USER"
        sudo usermod -aG "$g" "$USER"
        need_relogin=1
    fi
done

# ---- 3. launcher / autostart ----
"$DIR/install-desktop.sh" $AUTOSTART

echo
echo "== done =="
if [ "$need_relogin" = "1" ]; then
    echo "!! Log out and back in for the input/uucp groups to take effect."
fi
echo "Launch:  $DIR/run_tray.sh   (or pick 'Geppetto' from your app menu)"
