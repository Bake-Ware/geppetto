#!/usr/bin/env python3
"""Shared config + device helpers for the Geppetto client and settings GUI.

Config lives at ~/.config/geppetto/config.json (of the *invoking* user, even when
run under sudo) and looks like:

    {
      "devices": ["056e:00fb:ELECOM TrackBall Mouse HUGE TrackBall", ...],
      "hotkey":  {"mode": "double_tap", "keys": [97]}      # 97 = KEY_RIGHTCTRL
    }

- "devices": stable IDs (vendor:product:name) of the inputs to forward. Absent or
  null means "forward all keyboards + pointers" (the original behaviour).
- "hotkey":  "double_tap" of a single key, or a "chord" (all keys held at once).
"""

import json
import os
import pwd

from evdev import ecodes as e

DEVICE_NAME = "Geppetto"      # our own USB product string — never forward it
DOUBLE_TAP_WINDOW_S = 0.4     # max gap between the two taps in double-tap mode

DEFAULT_HOTKEY = {"mode": "double_tap", "keys": [e.KEY_RIGHTCTRL]}


# ---- config file ----------------------------------------------------------

def _invoking_user():
    """The real user even when we're running under sudo."""
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name


def config_path():
    home = os.path.expanduser(f"~{_invoking_user()}")
    return os.path.join(home, ".config", "geppetto", "config.json")


def load_config():
    try:
        with open(config_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    # If we're root (sudo), hand the file back to the real user.
    try:
        pw = pwd.getpwnam(_invoking_user())
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chown(os.path.dirname(path), pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError, OSError):
        pass
    return path


# ---- devices --------------------------------------------------------------

def device_id(dev):
    """Stable identity that survives reconnects / event-node renumbering."""
    info = dev.info
    return f"{info.vendor:04x}:{info.product:04x}:{dev.name}"


def is_keyboard(dev):
    caps = dev.capabilities()
    keys = set(caps.get(e.EV_KEY, []))
    return e.KEY_A in keys and e.EV_REL not in caps


def is_pointer(dev):
    caps = dev.capabilities()
    keys = set(caps.get(e.EV_KEY, []))
    return e.EV_REL in caps and e.BTN_LEFT in keys


# ---- key labels (for display + hotkey capture) ----------------------------

def key_label(code):
    """Human label for an evdev key code, e.g. 97 -> 'RIGHTCTRL'."""
    name = e.KEY.get(code) or e.BTN.get(code) or f"#{code}"
    if isinstance(name, (list, tuple)):
        name = name[0]
    if name.startswith("KEY_"):
        return name[4:]
    return name  # keep BTN_* etc. as-is


def hotkey_label(hk):
    keys = hk.get("keys", [])
    if not keys:
        return "(none)"
    combo = "+".join(key_label(k) for k in keys)
    mode = hk.get("mode")
    if mode == "double_tap":
        return f"double-tap {combo}"
    if mode == "single":
        return f"tap {combo}"
    return combo  # chord: just the held combo
