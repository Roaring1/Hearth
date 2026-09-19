#!/usr/bin/env python3
"""Functional check that drives the real handlers, not synthetic stand-ins.

xdotool cannot be trusted here: Xvfb has no window manager, so there is no
WM_NAME to search and a class search hands back the 1400x1000 root, where
every click lands on nothing while the test still reports success. Driving
the same gesture callbacks the pointer would fire keeps the test honest and
still ends at the real audio graph.
"""

import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

sys.path.insert(0, "/home/roaring/Documents/GitHub/Hearth/src")

from hearth.ui.gtk4 import app as mixer
from hearth.ui.gtk4 import style as style_mod

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {label} {detail}".rstrip())
    if not ok:
        FAILURES.append(label)


def vol(sink: str = "vm_game") -> int:
    out = subprocess.run(["pactl", "get-sink-volume", sink], capture_output=True, text=True).stdout
    for token in out.split():
        if token.endswith("%"):
            return int(token[:-1])
    return -1


def muted(sink: str = "vm_game") -> str:
    out = subprocess.run(["pactl", "get-sink-mute", sink], capture_output=True, text=True).stdout
    return out.strip().split()[-1] if out.strip() else "?"


class FuncApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="co.roaring.HearthFunc2")
        self.pal = style_mod.palette()
        self.win = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        mixer.load_css(provider, style_mod.CSS(self.pal))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self):
        self.win = mixer.MixerWindow(self)
        self.win.present()
        GLib.timeout_add(2500, self.run_checks)

    def run_checks(self):
        win = self.win
        strip = win.strips.get("vm_game")
        if strip is None:
            check("game strip exists", False)
            return self.finish()

        # --- single press somewhere down the fader lowers the volume ---
        subprocess.run(["pactl", "set-sink-volume", "vm_game", "100%"], check=False)
        height = max(1, strip.fader.get_height())
        strip.fader._on_press(None, 1, 0.0, height * 0.6)
        GLib.timeout_add(700, self.after_press, strip, height)
        return False

    def after_press(self, strip, height):
        v = vol()
        check("single press moves the fader", 25 <= v <= 60, f"-> {v}%")

        # --- double press resets to the default ---
        strip.fader._on_press(None, 2, 0.0, height * 0.6)
        GLib.timeout_add(700, self.after_reset, strip)
        return False

    def after_reset(self, strip):
        v = vol()
        check("double press resets", v == 100, f"-> {v}%")

        before = muted()
        strip.mute.set_active(not strip.mute.get_active())
        GLib.timeout_add(700, self.after_mute, strip, before)
        return False

    def after_mute(self, strip, before):
        now = muted()
        check("mute toggles the sink", now != before, f"{before} -> {now}")
        strip.mute.set_active(not strip.mute.get_active())

        win = self.win
        win.minimise("vm_game")
        gone = "vm_game" not in win.strips and "vm_game" in win.slivers
        check("minimise moves the strip to the rail", gone)
        win.restore("vm_game")
        back = "vm_game" in win.strips and "vm_game" not in win.slivers
        check("rail click restores the strip", back)

        # --- the fault banner knows which unit to restart ---
        win.banner_units = ()
        win._update_banner()
        check("banner button hidden when healthy", not win.banner_button.get_visible())

        GLib.timeout_add(400, self.finish)
        return False

    def finish(self, *_a):
        subprocess.run(["pactl", "set-sink-volume", "vm_game", "100%"], check=False)
        subprocess.run(["pactl", "set-sink-mute", "vm_game", "0"], check=False)
        self.win.shutdown()
        self.quit()
        return False


FuncApp().run([])
print("FAILURES:", ", ".join(FAILURES) if FAILURES else "none")
raise SystemExit(1 if FAILURES else 0)
