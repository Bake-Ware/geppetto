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
    DEFAULT_HOTKEY, DEFAULT_KEEP_AWAKE, KEEP_AWAKE_METHODS, DAY_LABELS,
    load_config, save_config, config_path, write_command, macro_step_label,
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


def signal_running_clients(sig=signal.SIGHUP):
    """Signal every running geppetto.py client (default SIGHUP = reload config)."""
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
                os.kill(int(pid), sig)
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
        self.set_default_size(480, 720)

        cfg = load_config()
        selected = cfg.get("devices")            # None => everything checked
        self.hotkey = cfg.get("hotkey") or dict(DEFAULT_HOTKEY)
        ka = cfg.get("keep_awake") or {}
        ka_sched = ka.get("schedule") or {}

        # Window = a notebook (tabbed pages) above a shared actions row.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{m}")(12)
        self.set_child(outer)
        self.outer = outer
        self.notebook = Gtk.Notebook(vexpand=True)
        outer.append(self.notebook)

        # ---- Settings tab ----
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(10)
        self.notebook.append_page(root, Gtk.Label(label="Settings"))

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

        # ---- keep target awake ----
        root.append(Gtk.Separator())
        ka_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ka_head.append(Gtk.Label(label="<b>Keep target awake</b>", use_markup=True,
                                 xalign=0, hexpand=True))
        self.ka_switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        self.ka_switch.set_active(ka.get("enabled", DEFAULT_KEEP_AWAKE["enabled"]))
        self.ka_switch.connect("notify::active", lambda *_: self._sync_ka_sensitivity())
        ka_head.append(self.ka_switch)
        root.append(ka_head)

        # method + interval
        self.ka_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.append(self.ka_body)

        method_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        method_row.append(Gtk.Label(label="Nudge", xalign=0))
        self.ka_method = Gtk.DropDown.new_from_strings([lbl for _k, lbl in KEEP_AWAKE_METHODS])
        cur_method = ka.get("method", DEFAULT_KEEP_AWAKE["method"])
        keys = [k for k, _lbl in KEEP_AWAKE_METHODS]
        self.ka_method.set_selected(keys.index(cur_method) if cur_method in keys else 0)
        method_row.append(self.ka_method)
        method_row.append(Gtk.Label(label="every", xalign=0))
        self.ka_interval = Gtk.SpinButton.new_with_range(5, 3600, 5)
        self.ka_interval.set_value(int(ka.get("interval_s", DEFAULT_KEEP_AWAKE["interval_s"])))
        method_row.append(self.ka_interval)
        method_row.append(Gtk.Label(label="seconds", xalign=0, hexpand=True))
        self.ka_body.append(method_row)

        # schedule toggle
        sched_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sched_row.append(Gtk.Label(label="Only on a schedule", xalign=0, hexpand=True))
        self.ka_sched_switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        self.ka_sched_switch.set_active(ka_sched.get("enabled", False))
        self.ka_sched_switch.connect("notify::active", lambda *_: self._sync_ka_sensitivity())
        sched_row.append(self.ka_sched_switch)
        self.ka_body.append(sched_row)

        # day checkboxes
        self.ka_sched_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.ka_body.append(self.ka_sched_box)
        days_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sel_days = ka_sched.get("days", DEFAULT_KEEP_AWAKE["schedule"]["days"]) or []
        self.day_checks = []
        for idx, name in enumerate(DAY_LABELS):
            cb = Gtk.CheckButton(label=name)
            cb.set_active(idx in sel_days)
            days_row.append(cb)
            self.day_checks.append(cb)
        self.ka_sched_box.append(days_row)

        # start / end times
        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        time_row.append(Gtk.Label(label="From", xalign=0))
        self.ka_start = Gtk.Entry(max_width_chars=5, width_chars=5)
        self.ka_start.set_text(ka_sched.get("start", DEFAULT_KEEP_AWAKE["schedule"]["start"]))
        time_row.append(self.ka_start)
        time_row.append(Gtk.Label(label="to", xalign=0))
        self.ka_end = Gtk.Entry(max_width_chars=5, width_chars=5)
        self.ka_end.set_text(ka_sched.get("end", DEFAULT_KEEP_AWAKE["schedule"]["end"]))
        time_row.append(self.ka_end)
        time_row.append(Gtk.Label(label="(24h, HH:MM)", xalign=0, hexpand=True))
        self.ka_sched_box.append(time_row)

        self._sync_ka_sensitivity()

        # ---- Macros tab ----
        self._build_macros_page(cfg)

        # ---- actions (shared across tabs) ----
        outer.append(Gtk.Separator())
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
        outer.append(btns)

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

    # ---- keep-awake sensitivity ----
    def _sync_ka_sensitivity(self):
        on = self.ka_switch.get_active()
        self.ka_body.set_sensitive(on)
        self.ka_sched_box.set_sensitive(on and self.ka_sched_switch.get_active())

    def _collect_keep_awake(self):
        keys = [k for k, _lbl in KEEP_AWAKE_METHODS]
        idx = self.ka_method.get_selected()
        method = keys[idx] if 0 <= idx < len(keys) else keys[0]
        days = [i for i, cb in enumerate(self.day_checks) if cb.get_active()]
        return {
            "enabled": self.ka_switch.get_active(),
            "interval_s": int(self.ka_interval.get_value()),
            "method": method,
            "schedule": {
                "enabled": self.ka_sched_switch.get_active(),
                "days": days,
                "start": self.ka_start.get_text().strip() or "00:00",
                "end": self.ka_end.get_text().strip() or "23:59",
            },
        }

    # ---- macros tab ----
    def _build_macros_page(self, cfg):
        self._macro_suppress = False
        self.macros = [{"name": mc.get("name", "macro"),
                        "steps": list(mc.get("steps") or [])}
                       for mc in (cfg.get("macros") or [])]
        self.cur_macro = 0 if self.macros else None

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(page, f"set_margin_{m}")(10)
        self.notebook.append_page(page, Gtk.Label(label="Macros"))

        page.append(Gtk.Label(label="<b>Macros</b>", use_markup=True, xalign=0))

        # macro list + New/Delete
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        msc = Gtk.ScrolledWindow(hexpand=True)
        msc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        msc.set_min_content_height(96)
        self.macros_listbox = Gtk.ListBox()
        self.macros_listbox.connect("row-selected", self._on_macro_row_selected)
        msc.set_child(self.macros_listbox)
        top.append(msc)
        mbtns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        valign=Gtk.Align.START)
        new_btn = Gtk.Button(label="New")
        new_btn.connect("clicked", self.on_macro_new)
        del_btn = Gtk.Button(label="Delete")
        del_btn.connect("clicked", self.on_macro_delete)
        mbtns.append(new_btn)
        mbtns.append(del_btn)
        top.append(mbtns)
        page.append(top)

        # name of selected macro
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_row.append(Gtk.Label(label="Name", xalign=0))
        self.macro_name_entry = Gtk.Entry(hexpand=True)
        self.macro_name_entry.connect("changed", self.on_macro_name_changed)
        name_row.append(self.macro_name_entry)
        page.append(name_row)

        # steps of selected macro
        page.append(Gtk.Label(label="<b>Steps</b> (run top to bottom)",
                              use_markup=True, xalign=0))
        ssc = Gtk.ScrolledWindow(vexpand=True)
        ssc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.steps_listbox = Gtk.ListBox()
        ssc.set_child(self.steps_listbox)
        page.append(ssc)

        add1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cad_btn = Gtk.Button(label="+ Ctrl+Alt+Del")
        cad_btn.connect("clicked", self.on_add_cad)
        self._combo_btn = Gtk.Button(label="Capture combo…")
        self._combo_btn.connect("clicked", self.on_add_combo)
        rm_btn = Gtk.Button(label="Remove step")
        rm_btn.connect("clicked", self.on_remove_step)
        add1.append(cad_btn)
        add1.append(self._combo_btn)
        add1.append(rm_btn)
        page.append(add1)

        add2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add2.append(Gtk.Label(label="text", xalign=0))
        self.macro_text_entry = Gtk.Entry(hexpand=True)
        self.macro_text_entry.connect("activate", self.on_add_text)
        add2.append(self.macro_text_entry)
        text_btn = Gtk.Button(label="+ Text")
        text_btn.connect("clicked", self.on_add_text)
        add2.append(text_btn)
        page.append(add2)

        add3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add3.append(Gtk.Label(label="delay", xalign=0))
        self.macro_delay = Gtk.SpinButton.new_with_range(0, 10000, 50)
        self.macro_delay.set_value(500)
        add3.append(self.macro_delay)
        add3.append(Gtk.Label(label="ms", xalign=0))
        delay_btn = Gtk.Button(label="+ Delay")
        delay_btn.connect("clicked", self.on_add_delay)
        add3.append(delay_btn)
        page.append(add3)

        send_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        send_btn = Gtk.Button(label="Send to target")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self.on_macro_send)
        send_row.append(send_btn)
        note = Gtk.Label(label="Sends the selected macro live to the running "
                               "bridge client (Save to keep it).",
                         xalign=0, hexpand=True, wrap=True)
        note.add_css_class("dim-label")
        send_row.append(note)
        page.append(send_row)

        self._reload_macro_list()

    def _cur_macro(self):
        if self.cur_macro is None or not (0 <= self.cur_macro < len(self.macros)):
            return None
        return self.macros[self.cur_macro]

    def _clear_listbox(self, lb):
        row = lb.get_row_at_index(0)
        while row is not None:
            lb.remove(row)
            row = lb.get_row_at_index(0)

    @staticmethod
    def _list_row(text):
        r = Gtk.ListBoxRow()
        r.set_child(Gtk.Label(label=text, xalign=0, margin_start=6, margin_end=6,
                              margin_top=3, margin_bottom=3))
        return r

    def _reload_macro_list(self):
        self._macro_suppress = True
        self._clear_listbox(self.macros_listbox)
        for mc in self.macros:
            self.macros_listbox.append(self._list_row(mc["name"] or "(unnamed)"))
        if self.cur_macro is not None:
            self.macros_listbox.select_row(
                self.macros_listbox.get_row_at_index(self.cur_macro))
        self._macro_suppress = False
        self._populate_macro_editor()

    def _populate_macro_editor(self):
        mc = self._cur_macro()
        self._macro_suppress = True
        self.macro_name_entry.set_text(mc["name"] if mc else "")
        self._macro_suppress = False
        self._reload_steps()

    def _reload_steps(self):
        self._clear_listbox(self.steps_listbox)
        mc = self._cur_macro()
        if mc is None:
            return
        for st in mc["steps"]:
            self.steps_listbox.append(self._list_row(macro_step_label(st)))

    def _on_macro_row_selected(self, _lb, row):
        if self._macro_suppress:
            return
        self.cur_macro = row.get_index() if row is not None else None
        self._populate_macro_editor()

    def on_macro_new(self, _btn):
        self.macros.append({"name": f"Macro {len(self.macros) + 1}", "steps": []})
        self.cur_macro = len(self.macros) - 1
        self._reload_macro_list()
        self.macro_name_entry.grab_focus()

    def on_macro_delete(self, _btn):
        if self._cur_macro() is None:
            return
        del self.macros[self.cur_macro]
        self.cur_macro = None if not self.macros else max(0, self.cur_macro - 1)
        self._reload_macro_list()

    def on_macro_name_changed(self, entry):
        if self._macro_suppress:
            return
        mc = self._cur_macro()
        if mc is None:
            return
        mc["name"] = entry.get_text()
        row = self.macros_listbox.get_row_at_index(self.cur_macro)
        if row is not None:
            row.get_child().set_label(mc["name"] or "(unnamed)")

    def _append_step(self, step):
        if self._cur_macro() is None:
            self.on_macro_new(None)
        self._cur_macro()["steps"].append(step)
        self._reload_steps()
        self.status.set_label("step added (not saved yet)")

    def on_add_cad(self, _btn):
        self._append_step({"type": "combo",
                           "keys": [e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_DELETE]})

    def on_add_text(self, _btn):
        txt = self.macro_text_entry.get_text()
        if not txt:
            return
        self._append_step({"type": "text", "text": txt})
        self.macro_text_entry.set_text("")

    def on_add_delay(self, _btn):
        self._append_step({"type": "delay", "ms": int(self.macro_delay.get_value())})

    def on_add_combo(self, btn):
        if self._cur_macro() is None:
            self.on_macro_new(None)
        btn.set_sensitive(False)
        self.status.set_label("press the keys for this step, then release…")

        def worker():
            spec = capture_combo()
            GLib.idle_add(self._combo_captured, spec)

        threading.Thread(target=worker, daemon=True).start()

    def _combo_captured(self, spec):
        self._combo_btn.set_sensitive(True)
        if spec and spec.get("keys"):
            self._append_step({"type": "combo", "keys": spec["keys"]})
        else:
            self.status.set_label("capture timed out — nothing pressed")
        return False

    def on_remove_step(self, _btn):
        mc = self._cur_macro()
        if mc is None:
            return
        row = self.steps_listbox.get_selected_row()
        if row is None:
            return
        idx = row.get_index()
        if 0 <= idx < len(mc["steps"]):
            del mc["steps"][idx]
            self._reload_steps()

    def on_macro_send(self, _btn):
        mc = self._cur_macro()
        if mc is None or not mc["steps"]:
            self.status.set_label("nothing to send — add steps first")
            return
        write_command({"name": mc["name"], "macro": mc["steps"]})
        n = signal_running_clients(signal.SIGUSR2)
        if n:
            self.status.set_label(f"sent “{mc['name']}” to {n} client"
                                  + ("s" if n != 1 else ""))
        else:
            self.status.set_label("no running client — start the bridge client first")

    # ---- save ----
    def on_save(self, _btn):
        devices = [did for cb, did in self.rows if cb.get_active()]
        cfg = {"devices": devices, "hotkey": self.hotkey,
               "keep_awake": self._collect_keep_awake(),
               "macros": self.macros}
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
