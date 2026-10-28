#!/usr/bin/env python3
"""Functional check that drives the real handlers, not synthetic stand-ins.

xdotool cannot be trusted here: Xvfb has no window manager, so there is no
WM_NAME to search and a class search hands back the 1400x1000 root, where
every click lands on nothing while the test still reports success. Driving
the same gesture callbacks the pointer would fire keeps the test honest and
still ends at the real audio graph.
"""

import os
import stat
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hearth import paths
from hearth.ui.gtk4 import app as mixer
from hearth.ui.gtk4 import style as style_mod

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {label} {detail}".rstrip())
    if not ok:
        FAILURES.append(label)


def volumes(sink: str = "vm_game") -> list[int]:
    """Read every channel volume so asymmetric stereo levels can be restored."""
    try:
        result = subprocess.run(
            ["pactl", "get-sink-volume", sink],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [
        int(token[:-1])
        for token in result.stdout.split()
        if token.endswith("%") and token[:-1].isdigit()
    ]


def vol(sink: str = "vm_game") -> int:
    levels = volumes(sink)
    return levels[0] if levels else -1


def muted(sink: str = "vm_game") -> str:
    try:
        result = subprocess.run(
            ["pactl", "get-sink-mute", sink],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    if result.returncode != 0:
        return "?"
    return result.stdout.strip().split()[-1] if result.stdout.strip() else "?"


class FuncApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="co.roaring.HearthFunc2")
        self.pal = style_mod.palette()
        self.win = None
        # The functional check temporarily changes the real vm_game volume
        # and mute state. Preserve the user's values so a test never leaves
        # the machine louder or unmuted than it was before the test started.
        self._original_volumes = volumes()
        self._original_mute = muted()
        # minimise()/restore() persist their settings immediately. Snapshot
        # the file byte-for-byte as well as the sink state, so the test does
        # not unminimise channels the user had folded away or create a new
        # preferences file when none existed before the run.
        self._settings_path = paths.settings_file()
        self._settings_parent_existed = self._settings_path.parent.exists()
        try:
            self._settings_existed = self._settings_path.is_file()
            self._settings_bytes = (
                self._settings_path.read_bytes() if self._settings_existed else None
            )
            self._settings_mode = (
                stat.S_IMODE(self._settings_path.stat().st_mode)
                if self._settings_existed
                else 0o600
            )
        except OSError as exc:
            raise RuntimeError(f"cannot safely snapshot Hearth settings: {exc}") from exc

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

        # Refuse to modify the live sink unless its original state was read,
        # otherwise cleanup could only guess at the user's prior settings.
        if not self._original_volumes or self._original_mute not in ("yes", "no"):
            check(
                "original audio state can be restored",
                False,
                "could not read vm_game state; refusing to change live audio",
            )
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

        # --- minimising most of the strips must not flip the layout ---
        # Three of five folded away used to shrink the window, which made
        # it taller than it was wide, which stacked the groups, which
        # shrank it again: the UI ended up a broken vertical column.
        for sink in ("vm_music", "vm_chat", "mic_b1"):
            win.minimise(sink)
        check(
            "minimising three of five keeps the side-by-side layout",
            not win._stacked and win.body.get_orientation() == Gtk.Orientation.HORIZONTAL,
            f"stacked={win._stacked}",
        )
        for sink in ("vm_music", "vm_chat", "mic_b1"):
            win.restore(sink)

        # --- an app can be dragged from one bus to another ---
        drop_ok = win.strips["vm_game"]._on_drop(None, "app:999999", 0.0, 0.0)
        check("a dropped app is handled as a move", drop_ok is True)

        # --- a mic bus offers real capture devices ---
        mic = win.strips.get("mic_b1")
        if mic is not None and mic.in_button is not None:
            mic._fill_inputs()
            child = mic.in_popover.get_child()
            labels = []
            row = child.get_first_child() if child is not None else None
            while row is not None:
                if isinstance(row, Gtk.CheckButton):
                    labels.append(row.get_label())
                row = row.get_next_sibling()
            check(
                "mic bus can be fed from the A50 boom mic",
                "A50 boom mic" in labels,
                f"{labels}",
            )
        else:
            check("mic bus has an input chooser", False)

        # --- the fault banner knows which unit to restart ---
        win.banner_units = ()
        win._update_banner()
        check("banner button hidden when healthy", not win.banner_button.get_visible())

        GLib.timeout_add(400, self.finish)
        return False

    def restore_audio_state(self) -> None:
        """Restore the sink state captured before the test touched the graph."""
        commands: list[list[str]] = []
        if self._original_volumes:
            commands.append(
                [
                    "pactl",
                    "set-sink-volume",
                    "vm_game",
                    *[f"{level}%" for level in self._original_volumes],
                ]
            )
        if self._original_mute in ("yes", "no"):
            mute_arg = "1" if self._original_mute == "yes" else "0"
            commands.append(["pactl", "set-sink-mute", "vm_game", mute_arg])

        for command in commands:
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=3, check=False
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = f"could not restore original vm_game state: {exc}"
                print(f"FAIL: {message}")
                if message not in FAILURES:
                    FAILURES.append(message)
                continue
            if result.returncode != 0:
                detail = result.stderr.strip() or f"exit {result.returncode}"
                message = f"could not restore original vm_game state: {detail}"
                print(f"FAIL: {message}")
                if message not in FAILURES:
                    FAILURES.append(message)

    def restore_settings_state(self) -> None:
        """Restore the exact settings file state captured before the test."""
        target = self._settings_path
        temporary: Path | None = None
        try:
            if not self._settings_existed:
                target.unlink(missing_ok=True)
                if not self._settings_parent_existed:
                    with suppress(OSError):
                        target.parent.rmdir()  # another config file may occupy the directory
                return

            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.func-check-", dir=target.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(self._settings_bytes)
            temporary.chmod(self._settings_mode)
            temporary.replace(target)
        except OSError as exc:
            message = f"could not restore original Hearth settings: {exc}"
            print(f"FAIL: {message}")
            if message not in FAILURES:
                FAILURES.append(message)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def finish(self, *_a):
        try:
            if self.win is not None:
                self.win.shutdown()
        finally:
            self.quit()
        return False


def main() -> int:
    app = FuncApp()
    try:
        app.run([])
    finally:
        # Also restore on exceptions, Ctrl+C, or an ordinary window close.
        # Invalid snapshots (e.g. vm_game absent) are skipped rather than
        # applying a guessed default to a sink that may have appeared later.
        try:
            app.restore_audio_state()
        finally:
            app.restore_settings_state()

    print("FAILURES:", ", ".join(FAILURES) if FAILURES else "none")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
