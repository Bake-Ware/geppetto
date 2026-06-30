#!/usr/bin/env python3
"""Shared config + device helpers for the Geppetto client and settings GUI.

Config lives at ~/.config/geppetto/config.json (of the *invoking* user, even when
run under sudo) and looks like:

    {
      "devices": ["056e:00fb:ELECOM TrackBall Mouse HUGE TrackBall", ...],
      "hotkey":  {"mode": "double_tap", "keys": [97]},     # 97 = KEY_RIGHTCTRL
      "keep_awake": {
        "enabled":    false,
        "interval_s": 60,
        "method":     "mouse",                             # mouse | shift | f15
        "schedule":   {"enabled": false, "days": [0,1,2,3,4],
                       "start": "09:00", "end": "17:00"}
      }
    }

- "devices": stable IDs (vendor:product:name) of the inputs to forward. Absent or
  null means "forward all keyboards + pointers" (the original behaviour).
- "hotkey":  "double_tap" of a single key, or a "chord" (all keys held at once).
- "keep_awake": periodically nudge the *target* so it doesn't sleep/lock. Only
  runs while forwarding is OFF (when forwarding, your own input keeps it awake).
  "schedule" optionally limits it to certain weekdays + a daily time window
  (days are Python weekday() indices: Mon=0 … Sun=6).
"""

import json
import os
import pwd

from evdev import ecodes as e

DEVICE_NAME = "Geppetto"      # our own USB product string — never forward it
DOUBLE_TAP_WINDOW_S = 0.4     # max gap between the two taps in double-tap mode

DEFAULT_HOTKEY = {"mode": "double_tap", "keys": [e.KEY_RIGHTCTRL]}

# ---- keep-awake -----------------------------------------------------------

DEFAULT_KEEP_AWAKE = {
    "enabled": False,
    "interval_s": 60,
    "method": "mouse",          # see KEEP_AWAKE_METHODS
    "schedule": {
        "enabled": False,
        "days": [0, 1, 2, 3, 4],   # Mon..Fri  (Python weekday(): Mon=0 .. Sun=6)
        "start": "09:00",
        "end": "17:00",
    },
}

# (key, GUI label) — order is the dropdown order. The key is what's stored.
KEEP_AWAKE_METHODS = [
    ("mouse", "Mouse jiggle (invisible)"),
    ("shift", "Tap Shift key"),
    ("f15",   "Tap F15 key"),
]

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def keep_awake_label(ka):
    """Short human summary, e.g. 'jiggle every 60s · Mon-Fri 09:00-17:00'."""
    ka = ka or DEFAULT_KEEP_AWAKE
    if not ka.get("enabled"):
        return "off"
    method = {"mouse": "jiggle", "shift": "Shift", "f15": "F15"}.get(
        ka.get("method", "mouse"), ka.get("method", "?"))
    out = f"{method} every {int(ka.get('interval_s', 60))}s"
    sched = ka.get("schedule") or {}
    if sched.get("enabled"):
        days = sched.get("days") or []
        day_str = ",".join(DAY_LABELS[d] for d in sorted(days) if 0 <= d < 7) or "no days"
        out += f" · {day_str} {sched.get('start', '00:00')}-{sched.get('end', '23:59')}"
    return out


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


# ---- runtime status (client <-> tray) -------------------------------------
# The client writes its live state here; the tray polls it for the icon.

def _runtime_dir():
    uid = pwd.getpwnam(_invoking_user()).pw_uid
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if not rt or not os.path.isdir(rt):
        rt = f"/run/user/{uid}"
    if not os.path.isdir(rt):
        rt = os.path.join(os.path.expanduser(f"~{_invoking_user()}"), ".cache", "geppetto")
        os.makedirs(rt, exist_ok=True)
    return rt


def status_path():
    return os.path.join(_runtime_dir(), "geppetto.status")


def write_status(d):
    path = status_path()
    try:
        with open(path, "w") as f:
            json.dump(d, f)
        pw = pwd.getpwnam(_invoking_user())
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (OSError, KeyError):
        pass


def read_status():
    try:
        with open(status_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clear_status():
    try:
        os.remove(status_path())
    except OSError:
        pass


# ---- one-shot commands (GUI -> client) ------------------------------------
# The GUI drops a command here and signals the client (SIGUSR2) to act on it —
# used to fire a macro at the target, since only the client holds the serial
# link. A file (not the signal alone) so we can pass the macro's steps.

def command_path():
    return os.path.join(_runtime_dir(), "geppetto.cmd")


def write_command(d):
    path = command_path()
    try:
        with open(path, "w") as f:
            json.dump(d, f)
        pw = pwd.getpwnam(_invoking_user())
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (OSError, KeyError):
        pass


def read_command():
    try:
        with open(command_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clear_command():
    try:
        os.remove(command_path())
    except OSError:
        pass


# ---- macros ---------------------------------------------------------------
# A macro is an ordered list of steps sent to the *target*:
#   {"type": "combo", "keys": [29, 56, 111]}  # evdev codes held together (CAD)
#   {"type": "text",  "text": "hunter2"}      # typed out, US layout
#   {"type": "delay", "ms": 500}              # pause between steps

def macro_step_label(step):
    t = step.get("type")
    if t == "combo":
        keys = step.get("keys", [])
        return "+".join(key_label(k) for k in keys) or "(empty combo)"
    if t == "text":
        return f'type "{step.get("text", "")}"'
    if t == "delay":
        return f"wait {int(step.get('ms', 0))} ms"
    return "(unknown step)"


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


# Media/consumer keys (play/pause, volume, …) usually sit on a separate evdev
# node with no KEY_A and no BTN_LEFT, which the keyboard/pointer filters miss.
MEDIA_CODES = frozenset(
    getattr(e, n) for n in (
        "KEY_PLAYPAUSE", "KEY_PLAY", "KEY_PAUSE", "KEY_STOPCD", "KEY_NEXTSONG",
        "KEY_PREVIOUSSONG", "KEY_FASTFORWARD", "KEY_REWIND", "KEY_MUTE",
        "KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_EJECTCD",
        "KEY_BRIGHTNESSUP", "KEY_BRIGHTNESSDOWN",
    ) if hasattr(e, n)
)


def is_consumer(dev):
    """A dedicated consumer/media-key device (media keys, but not a keyboard or
    pointer) — e.g. 'Keychron Q11 Consumer Control'."""
    caps = dev.capabilities()
    keys = set(caps.get(e.EV_KEY, []))
    return bool(MEDIA_CODES & keys) and not is_keyboard(dev) and not is_pointer(dev)


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
