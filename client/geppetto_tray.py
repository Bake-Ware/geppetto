#!/usr/bin/env python3
"""Geppetto system-tray indicator (GTK3 + Ayatana AppIndicator).

A little marionette in your tray that shows forwarding state and controls the
bridge:
    grey  = bridge client not running
    white = client running, forwarding OFF
    green = forwarding ON

Menu: open Settings, toggle forwarding, start/stop the bridge client, quit.
Middle-clicking the icon toggles forwarding.

Runs as root (to read /dev/input and signal the root client) — launch via
run_tray.sh, which forwards the Wayland/X display and the session D-Bus.

AppIndicator is GTK3; the settings window is GTK4, so it's launched as a
separate process (you can't load both GTK versions in one process).
"""

import os
import signal
import subprocess
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator  # noqa: E402

from geppetto_config import read_status  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CLIENT = os.path.join(HERE, "geppetto.py")
GUI = os.path.join(HERE, "geppetto_gui.py")


def pid_alive(pid):
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"geppetto.py" in f.read()
    except OSError:
        return False


class Tray:
    RESTART_BACKOFF_S = 3.0  # min gap between (re)starts, so a crash can't spam

    def __init__(self):
        self.ind = AppIndicator.Indicator.new(
            "geppetto", "geppetto-idle",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_icon_theme_path(ICON_DIR)
        self.ind.set_title("Geppetto")
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        self.gui_proc = None
        self.client_proc = None      # the client process we launched (supervised)
        self.want_client = True      # intent: keep a client alive
        self._last_spawn = 0.0       # for restart backoff
        self._suppress = False       # guard programmatic checkbox updates
        self._icon = None

        m = Gtk.Menu()
        self.i_settings = Gtk.MenuItem.new_with_label("Open Settings…")
        self.i_settings.connect("activate", self.on_settings)
        self.i_fwd = Gtk.CheckMenuItem.new_with_label("Forwarding")
        self.i_fwd.connect("toggled", self.on_fwd)
        self.i_client = Gtk.MenuItem.new_with_label("Start bridge client")
        self.i_client.connect("activate", self.on_client)
        self.i_status = Gtk.MenuItem.new_with_label("")
        self.i_status.set_sensitive(False)
        i_quit = Gtk.MenuItem.new_with_label("Quit")
        i_quit.connect("activate", self.on_quit)
        for it in (self.i_settings, Gtk.SeparatorMenuItem(), self.i_fwd,
                   self.i_client, Gtk.SeparatorMenuItem(), self.i_status,
                   Gtk.SeparatorMenuItem(), i_quit):
            m.append(it)
        m.show_all()
        self.ind.set_menu(m)
        self.ind.set_secondary_activate_target(self.i_fwd)  # middle-click toggles

        GLib.timeout_add(1000, self._tick)
        # Adopt an already-running client, or start one now so the hotkey works
        # right after login. From here on _supervise() keeps it alive.
        self._supervise()
        self.refresh()

    # ---- state ----
    def client_pid(self):
        st = read_status()
        pid = st.get("pid")
        return pid if pid_alive(pid) else None

    # ---- client supervision ----
    # The tray owns the bridge client: it adopts an existing one, restarts it if
    # it dies, and stops it on quit — so the two never drift apart.
    def _proc_alive(self):
        return self.client_proc is not None and self.client_proc.poll() is None

    def client_running(self):
        """True if a client is up (one we launched, or any that's published
        status) — _proc_alive covers the gap before the status file appears."""
        return self.client_pid() is not None or self._proc_alive()

    def _start_client(self):
        self._last_spawn = time.monotonic()
        self.client_proc = subprocess.Popen([sys.executable, "-u", CLIENT])

    def _stop_client(self):
        pid = self.client_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if self._proc_alive():
            self.client_proc.terminate()
        self.client_proc = None

    def _supervise(self):
        """Keep exactly one client alive while we want one."""
        if not self.want_client or self.client_running():
            return
        if time.monotonic() - self._last_spawn < self.RESTART_BACKOFF_S:
            return  # crash-loop backoff
        self._start_client()

    def _set_icon(self, name):
        if name != self._icon:
            self.ind.set_icon_full(name, "Geppetto")
            self._icon = name

    def refresh(self):
        st = read_status()
        pid = self.client_pid()
        running = pid is not None
        forwarding = bool(st.get("forwarding")) if running else False

        self._set_icon("geppetto-on" if forwarding else
                       "geppetto-off" if running else "geppetto-idle")

        self._suppress = True
        self.i_fwd.set_active(forwarding)
        self._suppress = False
        self.i_fwd.set_sensitive(running)
        self.i_client.set_label("Stop bridge client" if running
                                else "Start bridge client")
        if running:
            self.i_status.set_label(f"forwarding {'ON' if forwarding else 'off'} · "
                                    f"hotkey: {st.get('hotkey', '?')} · "
                                    f"keep-awake: {st.get('keep_awake', 'off')}")
        else:
            self.i_status.set_label("bridge client not running")

    def _tick(self):
        self._supervise()   # restart the client if it died while we want it up
        self.refresh()
        return True  # keep polling

    # ---- actions ----
    def on_settings(self, _it):
        if self.gui_proc and self.gui_proc.poll() is None:
            return  # already open
        self.gui_proc = subprocess.Popen([sys.executable, GUI])

    def on_fwd(self, _it):
        if self._suppress:
            return
        pid = self.client_pid()
        if pid:
            os.kill(pid, signal.SIGUSR1)
        self.refresh()

    def on_client(self, _it):
        # Toggle the *intent*; the supervisor honours it (and won't fight us).
        if self.client_running():
            self.want_client = False
            self._stop_client()
        else:
            self.want_client = True
            self._last_spawn = 0.0   # user asked now — skip the backoff wait
            self._supervise()
        GLib.timeout_add(400, lambda: (self.refresh(), False)[1])

    def on_quit(self, _it):
        # The tray owns the client, so take it down with us.
        self.want_client = False
        self._stop_client()
        Gtk.main_quit()


def main():
    Tray()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
