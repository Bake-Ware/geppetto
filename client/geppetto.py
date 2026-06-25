#!/usr/bin/env python3
"""Geppetto — host client for the twin-Pico HID bridge.

Captures this machine's keyboard + pointer (e.g. an Elecom HUGE trackball) via
evdev, translates events into USB HID reports, and writes them — as framed
bytes — to the *bridge* Pico's USB serial port. That Pico relays the frames over
its UART to its soldered partner Pico, which is plugged into the target machine
and replays them as a real USB keyboard/mouse. No network, no encryption: it's a
wire. The target needs no software and is unaffected by VPNs or its OS.

Toggle forwarding with a DOUBLE-TAP of RIGHT CTRL:
  - ON  -> grabs the input devices (EVIOCGRAB) so this PC stops seeing them.
  - OFF -> releases the grab; input is local again.
On each toggle an "all released" report is sent so nothing sticks down.

Wire frame (matches firmware src/main.cpp):
    0xAB | type | len | payload[len] | crc8(type, len, payload)
    type 1 = keyboard (8 bytes), 2 = mouse (7 bytes), 4 = consumer (2 bytes)

Deps:  pip install evdev pyserial
Run :  sudo python3 geppetto.py             # auto-detects the bridge Pico
       sudo python3 geppetto.py --port /dev/ttyACM0
"""

import argparse
import datetime
import os
import selectors
import signal
import struct
import sys
import time

import evdev
from evdev import ecodes as e
import serial
from serial.tools import list_ports

from geppetto_config import (
    DEVICE_NAME, DOUBLE_TAP_WINDOW_S, DEFAULT_HOTKEY, DEFAULT_KEEP_AWAKE,
    load_config, device_id, is_keyboard, is_pointer, is_consumer, hotkey_label,
    keep_awake_label, write_status, clear_status,
)

REPORT_ID_KEYBOARD = 1
REPORT_ID_MOUSE = 2
REPORT_ID_CONSUMER = 4

FRAME_SYNC = 0xAB

# keep-awake nudges (raw USB HID, not via evdev — sent straight to the target)
HID_MOD_LSHIFT = 0x02   # left-shift bit in the keyboard report's modifier byte
HID_KEY_F15 = 0x6A      # HID Keyboard/Keypad usage for F15 (apps rarely map it)

# evdev media key -> USB HID Consumer Page (0x0C) usage. The firmware emits these
# as report ID 4. Press sends the usage; release sends 0.
CONSUMER_MAP = {}
for _name, _usage in (
    ("KEY_PLAYPAUSE", 0x00CD), ("KEY_PLAY", 0x00B0), ("KEY_PAUSE", 0x00B1),
    ("KEY_STOPCD", 0x00B7), ("KEY_NEXTSONG", 0x00B5), ("KEY_PREVIOUSSONG", 0x00B6),
    ("KEY_FASTFORWARD", 0x00B3), ("KEY_REWIND", 0x00B4),
    ("KEY_MUTE", 0x00E2), ("KEY_VOLUMEUP", 0x00E9), ("KEY_VOLUMEDOWN", 0x00EA),
    ("KEY_EJECTCD", 0x00B8), ("KEY_BRIGHTNESSUP", 0x006F), ("KEY_BRIGHTNESSDOWN", 0x0070),
):
    if hasattr(e, _name):
        CONSUMER_MAP[getattr(e, _name)] = _usage

# ---- framing (must match firmware crc8 / parser) --------------------------

def crc8(data: bytes) -> int:
    """CRC-8, poly 0x07, init 0 — over (type, len, payload)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def frame(report_id: int, body: bytes) -> bytes:
    head = bytes([report_id, len(body)]) + body
    return bytes([FRAME_SYNC]) + head + bytes([crc8(head)])

# ---- evdev -> USB HID translation -----------------------------------------

MODIFIERS = {
    e.KEY_LEFTCTRL: 0x01, e.KEY_LEFTSHIFT: 0x02,
    e.KEY_LEFTALT: 0x04, e.KEY_LEFTMETA: 0x08,
    e.KEY_RIGHTCTRL: 0x10, e.KEY_RIGHTSHIFT: 0x20,
    e.KEY_RIGHTALT: 0x40, e.KEY_RIGHTMETA: 0x80,
}

KEYMAP = {
    e.KEY_A: 0x04, e.KEY_B: 0x05, e.KEY_C: 0x06, e.KEY_D: 0x07,
    e.KEY_E: 0x08, e.KEY_F: 0x09, e.KEY_G: 0x0A, e.KEY_H: 0x0B,
    e.KEY_I: 0x0C, e.KEY_J: 0x0D, e.KEY_K: 0x0E, e.KEY_L: 0x0F,
    e.KEY_M: 0x10, e.KEY_N: 0x11, e.KEY_O: 0x12, e.KEY_P: 0x13,
    e.KEY_Q: 0x14, e.KEY_R: 0x15, e.KEY_S: 0x16, e.KEY_T: 0x17,
    e.KEY_U: 0x18, e.KEY_V: 0x19, e.KEY_W: 0x1A, e.KEY_X: 0x1B,
    e.KEY_Y: 0x1C, e.KEY_Z: 0x1D,
    e.KEY_1: 0x1E, e.KEY_2: 0x1F, e.KEY_3: 0x20, e.KEY_4: 0x21,
    e.KEY_5: 0x22, e.KEY_6: 0x23, e.KEY_7: 0x24, e.KEY_8: 0x25,
    e.KEY_9: 0x26, e.KEY_0: 0x27,
    e.KEY_ENTER: 0x28, e.KEY_ESC: 0x29, e.KEY_BACKSPACE: 0x2A,
    e.KEY_TAB: 0x2B, e.KEY_SPACE: 0x2C,
    e.KEY_MINUS: 0x2D, e.KEY_EQUAL: 0x2E, e.KEY_LEFTBRACE: 0x2F,
    e.KEY_RIGHTBRACE: 0x30, e.KEY_BACKSLASH: 0x31, e.KEY_SEMICOLON: 0x33,
    e.KEY_APOSTROPHE: 0x34, e.KEY_GRAVE: 0x35, e.KEY_COMMA: 0x36,
    e.KEY_DOT: 0x37, e.KEY_SLASH: 0x38, e.KEY_CAPSLOCK: 0x39,
    e.KEY_F1: 0x3A, e.KEY_F2: 0x3B, e.KEY_F3: 0x3C, e.KEY_F4: 0x3D,
    e.KEY_F5: 0x3E, e.KEY_F6: 0x3F, e.KEY_F7: 0x40, e.KEY_F8: 0x41,
    e.KEY_F9: 0x42, e.KEY_F10: 0x43, e.KEY_F11: 0x44, e.KEY_F12: 0x45,
    e.KEY_SYSRQ: 0x46, e.KEY_SCROLLLOCK: 0x47, e.KEY_PAUSE: 0x48,
    e.KEY_INSERT: 0x49, e.KEY_HOME: 0x4A, e.KEY_PAGEUP: 0x4B,
    e.KEY_DELETE: 0x4C, e.KEY_END: 0x4D, e.KEY_PAGEDOWN: 0x4E,
    e.KEY_RIGHT: 0x4F, e.KEY_LEFT: 0x50, e.KEY_DOWN: 0x51, e.KEY_UP: 0x52,
    e.KEY_NUMLOCK: 0x53, e.KEY_KPSLASH: 0x54, e.KEY_KPASTERISK: 0x55,
    e.KEY_KPMINUS: 0x56, e.KEY_KPPLUS: 0x57, e.KEY_KPENTER: 0x58,
    e.KEY_KP1: 0x59, e.KEY_KP2: 0x5A, e.KEY_KP3: 0x5B, e.KEY_KP4: 0x5C,
    e.KEY_KP5: 0x5D, e.KEY_KP6: 0x5E, e.KEY_KP7: 0x5F, e.KEY_KP8: 0x60,
    e.KEY_KP9: 0x61, e.KEY_KP0: 0x62, e.KEY_KPDOT: 0x63,
    e.KEY_COMPOSE: 0x65,
}

# Many trackballs (e.g. the Elecom HUGE) have 8 buttons; map each to a bit (0..7).
BUTTONMAP = {
    e.BTN_LEFT: 0, e.BTN_RIGHT: 1, e.BTN_MIDDLE: 2,
    e.BTN_SIDE: 3, e.BTN_EXTRA: 4, e.BTN_FORWARD: 5,
    e.BTN_BACK: 6, e.BTN_TASK: 7,
}


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Sender:
    """Writes framed HID reports to the bridge Pico's serial port."""

    def __init__(self, port):
        self.ser = serial.Serial(port, baudrate=115200, timeout=0,
                                 write_timeout=0.2)

    def send(self, report_id, body: bytes):
        try:
            self.ser.write(frame(report_id, body))
        except serial.SerialException as ex:
            print(f"serial write failed: {ex}", file=sys.stderr)


class Forwarder:
    def __init__(self, sender):
        self.s = sender
        self.mods = 0
        self.keys = []  # ordered list of held HID usages (max 6 in report)
        self.btn_mask = 0
        self.dx = self.dy = self.wheel = self.pan = 0
        self.mouse_dirty = False

    # ---- keyboard ----
    def kbd_event(self, code, value):
        """value: 1=down, 0=up, 2=autorepeat (ignored — target repeats)."""
        if value == 2:
            return
        if code in MODIFIERS:
            bit = MODIFIERS[code]
            if value:
                self.mods |= bit
            else:
                self.mods &= ~bit
            self.send_kbd()
            return
        usage = KEYMAP.get(code)
        if usage is None:
            return
        if value:
            if usage not in self.keys:
                self.keys.append(usage)
        else:
            if usage in self.keys:
                self.keys.remove(usage)
        self.send_kbd()

    def send_kbd(self):
        keys = self.keys[:6]
        report = bytes([self.mods, 0] + keys + [0] * (6 - len(keys)))
        self.s.send(REPORT_ID_KEYBOARD, report)

    # ---- consumer / media keys ----
    def consumer_event(self, code, value):
        usage = CONSUMER_MAP.get(code)
        if usage is None or value == 2:   # ignore autorepeat
            return
        self.s.send(REPORT_ID_CONSUMER, struct.pack("<H", usage if value else 0))

    # ---- mouse ----
    def mouse_btn(self, code, value):
        bit = BUTTONMAP.get(code)
        if bit is None:
            return
        if value:
            self.btn_mask |= (1 << bit)
        else:
            self.btn_mask &= ~(1 << bit)
        self.mouse_dirty = True

    def mouse_rel(self, code, value):
        if code == e.REL_X:
            self.dx += value
        elif code == e.REL_Y:
            self.dy += value
        elif code == e.REL_WHEEL:
            self.wheel += value
        elif code == e.REL_HWHEEL:
            self.pan += value
        else:
            return
        self.mouse_dirty = True

    def mouse_flush(self):
        if not self.mouse_dirty:
            return
        body = struct.pack(
            "<Bhhbb",
            self.btn_mask,
            clamp(self.dx, -32767, 32767),
            clamp(self.dy, -32767, 32767),
            clamp(self.wheel, -127, 127),
            clamp(self.pan, -127, 127),
        )
        self.s.send(REPORT_ID_MOUSE, body)
        self.dx = self.dy = self.wheel = self.pan = 0
        self.mouse_dirty = False

    # ---- keep-awake: a tiny, target-only nudge so it doesn't sleep/lock ----
    def nudge(self, method):
        """Send a single harmless activity event to the target. Called only while
        forwarding is OFF, so it never collides with the user's own input."""
        if method == "shift":
            # Tap and release Left-Shift (a modifier — types nothing on its own).
            self.s.send(REPORT_ID_KEYBOARD, bytes([HID_MOD_LSHIFT, 0, 0, 0, 0, 0, 0, 0]))
            self.s.send(REPORT_ID_KEYBOARD, bytes(8))
        elif method == "f15":
            self.s.send(REPORT_ID_KEYBOARD, bytes([0, 0, HID_KEY_F15, 0, 0, 0, 0, 0]))
            self.s.send(REPORT_ID_KEYBOARD, bytes(8))
        else:  # "mouse" — net-zero jiggle: +1px then -1px leaves the cursor put.
            self.s.send(REPORT_ID_MOUSE, struct.pack("<Bhhbb", 0, 1, 0, 0, 0))
            self.s.send(REPORT_ID_MOUSE, struct.pack("<Bhhbb", 0, -1, 0, 0, 0))

    # ---- toggle: release everything so nothing sticks down ----
    def release_all(self):
        self.mods = 0
        self.keys = []
        self.btn_mask = 0
        self.dx = self.dy = self.wheel = self.pan = 0
        self.s.send(REPORT_ID_KEYBOARD, bytes(8))
        self.s.send(REPORT_ID_MOUSE, struct.pack("<Bhhbb", 0, 0, 0, 0, 0))
        self.s.send(REPORT_ID_CONSUMER, struct.pack("<H", 0))


def _parse_hhmm(s):
    """'HH:MM' -> minutes since midnight; falls back to 0 on garbage."""
    try:
        h, m = str(s).split(":")
        return (int(h) % 24) * 60 + (int(m) % 60)
    except (ValueError, AttributeError):
        return 0


def in_schedule(sched, now):
    """True if keep-awake is allowed at `now` (a datetime). No/disabled schedule
    means always allowed. Handles windows that wrap past midnight."""
    if not sched or not sched.get("enabled"):
        return True
    days = sched.get("days")
    if days is not None and now.weekday() not in days:
        return False
    start = _parse_hhmm(sched.get("start", "00:00"))
    end = _parse_hhmm(sched.get("end", "23:59"))
    if start == end:
        return True  # degenerate window = the whole day
    cur = now.hour * 60 + now.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # overnight, e.g. 22:00–06:00


def autodetect_port():
    """Find the bridge Pico's CDC port. Prefer our product string, fall back to
    any Raspberry Pi RP2040 (VID 0x2E8A), else the first ttyACM."""
    acm = [p for p in list_ports.comports() if "ACM" in p.device]
    for p in acm:
        prod = (p.product or "") + (p.manufacturer or "")
        if DEVICE_NAME in prod:
            return p.device
    for p in acm:
        if p.vid == 0x2E8A:
            return p.device
    return acm[0].device if acm else None


class Hotkey:
    """Toggle detector. Two shapes, both configured via the GUI:
       - double_tap: one key tapped twice within DOUBLE_TAP_WINDOW_S
       - chord:      a set of keys all held down at the same time
    """

    def __init__(self, spec):
        spec = spec or DEFAULT_HOTKEY
        self.mode = spec.get("mode", "double_tap")
        self.keys = set(spec.get("keys") or DEFAULT_HOTKEY["keys"])
        self._last_tap = 0.0
        self._satisfied = False

    def feed(self, held, code, value):
        """Call on every keyboard key event, with `held` already updated to the
        current set of pressed keys. Returns True when the hotkey fires."""
        if self.mode == "chord":
            now = self.keys.issubset(held)
            fired = now and not self._satisfied
            self._satisfied = now
            return fired
        if self.mode == "single":
            return code in self.keys and value == 1
        # double_tap
        key = next(iter(self.keys))
        if code == key and value == 1:
            t = time.monotonic()
            if t - self._last_tap <= DOUBLE_TAP_WINDOW_S:
                self._last_tap = 0.0
                return True
            self._last_tap = t
        return False


def find_devices():
    """All keyboards + pointers on the system, minus our own dongle's phantom
    HID interface (grabbing that is pointless and risks a feedback loop)."""
    devs = []
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if DEVICE_NAME in d.name:
            d.close()
            continue
        if is_keyboard(d) or is_pointer(d) or is_consumer(d):
            devs.append(d)
        else:
            d.close()
    return devs


def main():
    ap = argparse.ArgumentParser(description="Geppetto twin-Pico HID-bridge client")
    ap.add_argument("--port", help="bridge Pico serial port (auto-detect if omitted)")
    ap.add_argument("--all", action="store_true",
                    help="forward every device, ignoring the saved selection")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        print("no bridge Pico serial port found (looked for /dev/ttyACM*)",
              file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    selected = None if args.all else cfg.get("devices")  # None => forward all
    hotkey = Hotkey(cfg.get("hotkey"))
    keep_awake = cfg.get("keep_awake") or dict(DEFAULT_KEEP_AWAKE)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    sender = Sender(port)
    fwd = Forwarder(sender)

    all_devs = find_devices()
    if not all_devs:
        print("no input devices found (need root or 'input' group)", file=sys.stderr)
        sys.exit(1)

    sel_set = set(selected) if selected is not None else None

    def wanted(d):
        return sel_set is None or device_id(d) in sel_set

    # Forwarded = exactly what's selected (consumer/media devices included — they
    # show as their own checkbox in the GUI, so play/pause/volume are opt-in).
    forwarded = [d for d in all_devs if wanted(d)]
    fwd_fds = {d.fd for d in forwarded}
    mouse_fds = {d.fd for d in all_devs if is_pointer(d)}

    # Read every device (so the hotkey works regardless of what's forwarded —
    # including mouse-button hotkeys). Only `forwarded` devices are grabbed/sent.
    watched = all_devs

    print(f"bridge Pico   : {port}")
    print(f"hotkey        : {hotkey_label(cfg.get('hotkey') or DEFAULT_HOTKEY)}")
    print(f"keep-awake    : {keep_awake_label(keep_awake)}")
    for d in watched:
        tag = "forward" if d.fd in fwd_fds else "watch  "
        kind = "kbd" if is_keyboard(d) else "mda" if is_consumer(d) else "ptr"
        print(f"  [{tag}] {kind}  {d.name}")
    if sel_set is not None and not forwarded:
        print("  (warning: none of the selected devices are present)")
    print("Edit devices/hotkey with the GUI (run_gui.sh). Ctrl-C to quit.")

    sel = selectors.DefaultSelector()
    for d in watched:
        sel.register(d.fd, selectors.EVENT_READ, d)

    forwarding = False
    grabbed = False
    held = {}  # keycode -> press count, aggregated across all keyboards

    def set_grab(on):
        nonlocal grabbed
        if on and not grabbed:
            for d in forwarded:
                try:
                    d.grab()
                except OSError:
                    pass
            grabbed = True
        elif not on and grabbed:
            for d in forwarded:
                try:
                    d.ungrab()
                except OSError:
                    pass
            grabbed = False

    hk_label_str = hotkey_label(cfg.get("hotkey") or DEFAULT_HOTKEY)

    def publish():
        write_status({"pid": os.getpid(), "forwarding": forwarding,
                      "devices": len(forwarded), "hotkey": hk_label_str,
                      "keep_awake": keep_awake_label(keep_awake)})

    def toggle():
        nonlocal forwarding
        forwarding = not forwarding
        set_grab(forwarding)
        fwd.release_all()
        print(f"[forwarding {'ON' if forwarding else 'OFF'}]")
        publish()

    publish()  # advertise initial (off) state for the tray

    # SIGHUP (GUI Save) => re-exec to apply new config live.
    # SIGUSR1 (tray)    => toggle forwarding, same as the hotkey.
    reload_req = {"v": False}
    toggle_req = {"v": False}
    signal.signal(signal.SIGHUP, lambda *_: reload_req.__setitem__("v", True))
    signal.signal(signal.SIGUSR1, lambda *_: toggle_req.__setitem__("v", True))

    # Keep-awake timer. interval clamped to >=5s so a bad config can't spam.
    ka_interval = max(5, int(keep_awake.get("interval_s", 60) or 60))
    next_nudge = time.monotonic() + ka_interval

    HEARTBEAT_S = 0.025
    try:
        while True:
            # keep-awake: nudge the target on its own schedule, but never while
            # forwarding (the user's input already keeps it awake then).
            if keep_awake.get("enabled") and not forwarding:
                now_mono = time.monotonic()
                if now_mono >= next_nudge:
                    if in_schedule(keep_awake.get("schedule"), datetime.datetime.now()):
                        fwd.nudge(keep_awake.get("method", "mouse"))
                    next_nudge = now_mono + ka_interval

            if reload_req["v"]:
                set_grab(False)
                fwd.release_all()
                print("[reloading config…]", flush=True)
                os.environ["PYTHONUNBUFFERED"] = "1"  # keep stdout live after re-exec
                os.execv(sys.executable,
                         [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:])
            if toggle_req["v"]:
                toggle_req["v"] = False
                toggle()
            ready = sel.select(timeout=HEARTBEAT_S)
            for sk, _ in ready:
                dev = sk.data
                do_forward = dev.fd in fwd_fds
                is_mouse = dev.fd in mouse_fds
                try:
                    events = list(dev.read())
                except BlockingIOError:
                    continue
                for ev in events:
                    # ---- hotkey: track held keys/buttons across every device ----
                    if ev.type == e.EV_KEY:
                        if ev.value == 1:
                            held[ev.code] = held.get(ev.code, 0) + 1
                        elif ev.value == 0 and held.get(ev.code, 0) > 0:
                            held[ev.code] -= 1
                            if held[ev.code] == 0:
                                del held[ev.code]
                        if hotkey.feed(set(held), ev.code, ev.value):
                            toggle()
                            continue  # don't also forward the triggering press

                    if not forwarding or not do_forward:
                        continue

                    if ev.type == e.EV_KEY:
                        if ev.code in CONSUMER_MAP:
                            fwd.consumer_event(ev.code, ev.value)
                        elif is_mouse and ev.code in BUTTONMAP:
                            fwd.mouse_btn(ev.code, ev.value)
                        elif not is_mouse:
                            fwd.kbd_event(ev.code, ev.value)
                    elif ev.type == e.EV_REL and is_mouse:
                        fwd.mouse_rel(ev.code, ev.value)
                    elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT and is_mouse:
                        fwd.mouse_flush()
    except KeyboardInterrupt:
        pass
    finally:
        set_grab(False)
        fwd.release_all()
        clear_status()
        print("\nstopped, devices released.")


if __name__ == "__main__":
    main()
