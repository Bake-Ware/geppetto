#!/usr/bin/env python3
"""Geppetto settings GUI (GTK4).

Pick which input devices get forwarded and capture the toggle hotkey, then save
to ~/.config/geppetto/config.json (read by geppetto.py). Needs root to read
/dev/input, so launch it via run_gui.sh (which sudo's with the display env).

Hotkey capture: click "Capture", press the combo you want, release. A single
key becomes a double-tap; multiple keys held together become a chord.
"""

import os
import signal
import sys
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

import evdev  # noqa: E402
from evdev import ecodes as e  # noqa: E402

from geppetto_config import (  # noqa: E402
    DEFAULT_HOTKEY, load_config, save_config, config_path,
    device_id, is_keyboard, is_pointer, is_consumer, hotkey_label, key_label,
)


def list_devices():
    """(device_id, label, is_kbd) for every forwardable input, minus our dongle."""
    out = []
    seen = set()
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            if "Geppetto" in d.name:
                continue
            if is_keyboard(d):
                kind, rank = "keyboard", 0
            elif is_consumer(d):
                kind, rank = "media keys", 1
            elif is_pointer(d):
                kind, rank = "pointer", 2
            else:
                continue
            did = device_id(d)
            if did in seen:
                continue
            seen.add(did)
            out.append((did, f"{d.name}   ({kind})", rank))
        finally:
            d.close()
    out.sort(key=lambda r: (r[2], r[1].lower()))  # keyboards, media, pointers
    return out


def signal_running_clients():
    """SIGHUP every running geppetto.py client so it re-applies config live."""
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except OSError:
            continue
        if "geppetto.py" in cmd and "geppetto_gui.py" not in cmd:
            try:
                os.kill(int(pid), signal.SIGHUP)
                n += 1
            except OSError:
                pass
    return n


def capture_combo(timeout=8.0):
    """Block reading all keyboards + pointers; return a hotkey spec for the held
    combo. Mouse buttons (BTN_*) count too, so a side button can be the hotkey."""
    kbds = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if "Geppetto" not in d.name and (is_keyboard(d) or is_pointer(d)):
            kbds.append(d)
        else:
            d.close()

    import select
    import time
    held, max_set, started = set(), set(), False
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < timeout:
            r, _, _ = select.select([d.fd for d in kbds], [], [], 0.2)
            fdmap = {d.fd: d for d in kbds}
            for fd in r:
                try:
                    for ev in fdmap[fd].read():
                        if ev.type != e.EV_KEY:
                            continue
                        if ev.value == 1:
                            held.add(ev.code)
                            started = True
                            if len(held) > len(max_set):
                                max_set = set(held)
                        elif ev.value == 0:
                            held.discard(ev.code)
                except OSError:
                    pass
            if started and not held:
                break
    finally:
        for d in kbds:
            d.close()
    if not max_set:
        return None
    keys = sorted(max_set)
    return {"mode": "double_tap" if len(keys) == 1 else "chord", "keys": keys}


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Geppetto Settings")
        self.set_default_size(420, 520)

        cfg = load_config()
        selected = cfg.get("devices")            # None => everything checked
        self.hotkey = cfg.get("hotkey") or dict(DEFAULT_HOTKEY)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(14)
        self.set_child(root)

        root.append(Gtk.Label(label="<b>Forward these devices</b>", use_markup=True,
                              xalign=0))

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        devbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.set_child(devbox)
        root.append(scroller)

        self.rows = []  # (checkbutton, device_id)
        devices = list_devices()
        if not devices:
            devbox.append(Gtk.Label(label="No input devices found.", xalign=0))
        for did, label, _kbd in devices:
            cb = Gtk.CheckButton(label=label)
            cb.set_active(selected is None or did in selected)
            devbox.append(cb)
            self.rows.append((cb, did))

        # ---- hotkey ----
        root.append(Gtk.Separator())
        root.append(Gtk.Label(label="<b>Toggle hotkey</b>", use_markup=True, xalign=0))
        hk_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.hk_label = Gtk.Label(label=hotkey_label(self.hotkey), xalign=0, hexpand=True)
        self.hk_label.set_selectable(True)
        capture_btn = Gtk.Button(label="Capture")
        capture_btn.connect("clicked", self.on_capture)
        hk_row.append(self.hk_label)
        hk_row.append(capture_btn)
        root.append(hk_row)

        # Single vs double tap (only meaningful for a single key/button; a
        # multi-key combo is always a "chord" — hold all at once).
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_row.append(Gtk.Label(label="Require double-tap", xalign=0, hexpand=True))
        self.dbl_switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        self.dbl_switch.connect("state-set", self.on_mode_toggle)
        mode_row.append(self.dbl_switch)
        root.append(mode_row)

        self.hint = Gtk.Label(
            label="Capture a single key/button, then pick tap vs double-tap. "
                  "Multiple keys held together = chord.",
            xalign=0, wrap=True)
        self.hint.add_css_class("dim-label")
        root.append(self.hint)

        self._sync_mode_switch()  # set switch from loaded config

        # ---- actions ----
        root.append(Gtk.Separator())
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                       halign=Gtk.Align.END)
        self.status = Gtk.Label(label="", xalign=0, hexpand=True)
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self.on_save)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: self.close())
        btns.append(self.status)
        btns.append(close_btn)
        btns.append(save_btn)
        root.append(btns)

        self._capture_btn = capture_btn

    # ---- hotkey capture (off the UI thread) ----
    def on_capture(self, btn):
        btn.set_sensitive(False)
        self.hk_label.set_label("press your hotkey, then release…")

        def worker():
            spec = capture_combo()
            GLib.idle_add(self._capture_done, spec)

        threading.Thread(target=worker, daemon=True).start()

    def _capture_done(self, spec):
        if spec:
            self.hotkey = spec
            self._sync_mode_switch()  # chord -> lock; single key -> honor switch
            self._apply_mode()
            self.status.set_label("hotkey captured (not saved yet)")
        else:
            self.hk_label.set_label(hotkey_label(self.hotkey))
            self.status.set_label("capture timed out — nothing pressed")
        self._capture_btn.set_sensitive(True)
        return False

    # ---- single / double-tap toggle ----
    def _is_chord(self):
        return len(self.hotkey.get("keys", [])) > 1

    def _sync_mode_switch(self):
        """Set the switch from the current hotkey, disabling it for chords."""
        chord = self._is_chord()
        self.dbl_switch.set_sensitive(not chord)
        # block our own handler while we set the initial state
        self.dbl_switch.handler_block_by_func(self.on_mode_toggle)
        self.dbl_switch.set_active(self.hotkey.get("mode") != "single")
        self.dbl_switch.handler_unblock_by_func(self.on_mode_toggle)

    def _apply_mode(self):
        if self._is_chord():
            self.hotkey["mode"] = "chord"
        else:
            self.hotkey["mode"] = "double_tap" if self.dbl_switch.get_active() else "single"
        self.hk_label.set_label(hotkey_label(self.hotkey))

    def on_mode_toggle(self, _switch, _state):
        # _state is the requested value; apply it then let the switch update.
        if not self._is_chord():
            self.hotkey["mode"] = "double_tap" if _state else "single"
            self.hk_label.set_label(hotkey_label(self.hotkey))
        return False

    # ---- save ----
    def on_save(self, _btn):
        devices = [did for cb, did in self.rows if cb.get_active()]
        cfg = {"devices": devices, "hotkey": self.hotkey}
        path = save_config(cfg)
        n = signal_running_clients()
        if n:
            self.status.set_label(f"saved · applied live to {n} client" + ("s" if n != 1 else ""))
        else:
            self.status.set_label("saved · will apply when the client starts")
        print(f"saved {len(devices)} device(s), hotkey {hotkey_label(self.hotkey)} "
              f"-> {path}; signaled {n} client(s)")


def main():
    app = Gtk.Application(application_id="dev.bakeware.geppetto.settings")
    app.connect("activate", lambda a: SettingsWindow(a).present())
    return app.run(None)


if __name__ == "__main__":
    sys.exit(main())
