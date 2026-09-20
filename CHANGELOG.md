# Changelog

## Unreleased — round 31 (2026-09-20)

### Changed
- The LPD8 window now shows the rig, not just the map. Each knob's notch sits at the volume that knob last wrote to its bus, with a thin accent arc for the travel; a knob whose position nothing can vouch for still points straight up and draws no arc.
- Pads glow orange on the outline while the bus they watch is audible, matching the controller's own lamps. Pads that fire an action rather than hold a state (headset source, save report, desk speakers) never glow.
- Live state is pushed into the LPD8 window from the snapshot the mixer already takes, twice a second, and only while the window is on screen. It polls nothing of its own, and a knob whose volume has not moved does not cost a redraw.
- The mixer's redraw loop now idles at 15 Hz instead of running flat out, and jumps to 60 Hz for one second whenever a fader is touched, dragged or scrolled. Dragging feels attached to the pointer; an untouched window costs a quarter of what it used to.
- Hearth Setup is boxed by job: headset, mic path, Moonlight sends and LPD8 each sit in their own bordered section, with the borders doing the separating so the column can sit tight.
- Hearth Setup says less. The "The rig is up" line is gone — the verdict line now appears only when something is actually wrong. The A50 endpoint explanation moved from a paragraph to a tooltip, "Save and restart routing" is now "Save", and "Loopback latency" is now "Latency".

## Unreleased — round 30 (2026-09-20)

### Changed
- The LPD8 window is now only the drawn controller. The hint line, the shared detail row underneath it and the status-and-Restart row at the bottom are gone; the window is the picture and nothing else.
- Changing a control happens on the control. Right-clicking a pad, a knob, or the slab around them opens a dropdown over that control listing what it can do; picking an entry writes the script and closes the menu. A plain click opens the same menu, so the mouse and the keyboard (Tab, then Enter) reach it too.
- Right-clicking the gap between controls hands the click to the nearest pad or knob, so the space around a control counts as the control.
- The service state no longer takes a permanent line. A single line appears under the picture only when something is actually wrong: a clash, a missing script, a failed write, or a service known to be stopped. When everything is fine the window says nothing.
- Restarting is no longer a button. Every edit still restarts `lpd8-mixer` on the existing 900 ms delay, and closing the window with an edit pending restarts immediately.

## Unreleased — round 29 (2026-09-20)

### Changed
- The LPD8 window is now a picture of the LPD8. Eight pad boxes in two rows of four, eight drawn knobs in two rows of four beside them, in the order printed on the hardware (pads 5-8 above 1-4, K1-K4 above K5-K8). Each box carries the job that pad or knob currently does, so the map is read off the shape of the device instead of off thirteen stacked dropdowns.
- Clicking any pad or knob selects it and one row underneath changes what it does. Thirteen dropdowns became one, the picture stays a picture, and every control is still reachable by Tab and Enter. Escape closes the window.
- The map is inverted on read (control → job instead of job → control) because that is the direction a person looking at the hardware asks the question in. Unused pads are drawn as empty dashed boxes and unused knobs are drawn dark: the hardware has eight of each whether or not the script uses them.

## Unreleased — round 28 (2026-09-20)

### Added
- A standalone **LPD8 Controls** window: every knob and every pad on one page, each one a dropdown that writes the map and restarts the service. Reachable four ways — the `K5`-style chip on a fader, right-click → *LPD8 controls…*, Ctrl+L, and a *Remap…* button in Setup beside *Reconnect* — plus `hearth --remap` and a `remap` verb on the control socket. Like Setup, it is imported only when first opened, kept rather than rebuilt, and reads nothing while it is closed.
- Pad remapping in `lpd8map`: `PAD_SLOTS`, `pad_to_note` / `note_to_pad` / `pad_to_cc` / `pad_to_prog`, `read_pad_map`, `set_pad`, `pad_conflicts`. A pad's slot appears in three tables in `lpd8_mixer.sh` (`NOTE_PAD*`, `CC_PAD*`, `PROG_*`), one per hardware mode, so `set_pad` rewrites all three in a single atomic write — moving only the note table would move the pad in PAD mode and nowhere else. Twelve tests cover it.

### Changed
- The bind chip was a menu button whose popover offered that one fader's knob and nothing else, so the eight pads — half the controller — had no UI at all and no two rows could be compared. The chip now opens the window; the popover, `_fill_binds` and `_on_bind` are gone.
- Consecutive edits coalesce into one `lpd8-mixer` restart (900 ms), and a restart queued when the window is closed still fires.

### Fixed
- The new window reads the unit's raw state rather than `is_active()`, which folds "systemd did not answer" into "not running" — the same confident lie round 27 removed from Setup.

## Unreleased — round 27 (2026-09-20)

### Fixed
- Setup claimed "lpd8 mixer is not installed" whenever the unit scan came back empty — including when systemd simply did not answer, which the verdict line two inches above already said. The pad was plugged in and its unit enabled the whole time. An empty scan now reads "unknown — systemd did not answer".

### Verified
- The Setup entry points, which had only ever been rendered, are now exercised: Ctrl+comma opens it (and Ctrl+A and a bare comma do not), the right-click popover carries the Setup item, both routes reuse one lazily-built window, and the Moonlight radios are a real group with exactly one selection. Driven offscreen on a throwaway display, runtime dir and session bus, so the live mixer's socket and the user's windows were never touched.

## Unreleased — round 26 (2026-09-20)

### Removed
- `legacy_core`'s duplicate `Collector` and `PeakPoller` (426 lines). They shadowed `hearth.collector` / `hearth.meters` and their only consumer was the GTK3 window deleted in round 24. Eight imports went with them, and the `F401` lint exemption on the file — it existed for re-exports the deleted star-import consumed — is retired, so unused imports there are errors again.

### Fixed
- `theme.Theme.css()` emitted `:root { --token: … }`, which is browser CSS. GTK3 rejects it outright (`Invalid name of pseudo-class`, verified against the real parser) and GTK4 parses it but has no `var()`, so the tokens were inert — the one function bridging the desktop palette to a stylesheet could not be used by either toolkit. It now emits `@define-color`, verified clean against real GTK3 and GTK4 providers, so colours resolve as `@window-bg`.

## Unreleased — round 25 (2026-09-20)

### Fixed
- Every `threading.Thread` subclass kept its shutdown flag in `self._stop`, which shadows the real `Thread._stop` method. `join()` calls that method on CPython 3.11 and 3.12, so five tests died with `TypeError: 'Event' object is not callable` — the CI failure that has been red on every run for weeks. The flag is now `_stopping` in `collector.py`, `meters.py`, `reporter.py`, and `legacy_core.py` (and, for consistency, in `ipc/server.py`, which is not a Thread). 3.13+ happens not to take that path and the dev box runs 3.14, which is why it never reproduced locally.

### Added
- `tests/test_thread_hygiene.py`: a static scan that fails if any Thread subclass assigns a name belonging to `Thread`. It carries an explicit list of private internals rather than trusting `dir(threading.Thread)`, because 3.14 has already dropped `_stop` from the class — a purely dynamic check would pass on the dev box and miss the bug on CI all over again.

## Unreleased — round 24 (2026-09-20)

### Removed
- The GTK3 window (`ui/app.py`, 2096 lines) and its settings dialog. Everything in it worth keeping was folded into the GTK4 mixer and its Setup window in rounds 19–23; the rest duplicated a strip. `~/bin/roaring_audio_control.py` and the `Roaring Audio Control` desktop entry are retired with it.
- `--vu-dump`, which only ever reached the GTK3 peak poller.

### Added
- `hearth.control`: the control socket's command language, with no toolkit attached, so `show` / `quit` / `unmute` / `dump` / `get_sinks` survived the window that used to answer them.
- The GTK4 app serves that socket and writes the pid file. `get_sinks` is answered from the snapshot the window already holds, so Padfire's three-second poll no longer costs ten `pactl` subprocesses.

## Unreleased — sessions 1–5 (2026-09-19)

All of the below is in the working tree only; HEAD is still `286f0c6`.

### Added
- `scripts/check.sh` single-command gate (ruff, ruff-format, mypy, pytest) plus `pyproject.toml`, `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes`, `LICENSE`, `CONTRIBUTING.md`, `.github/`, and a `tests/` suite (249 tests).
- Packaging moved to XDG layout: `data/applications/`, `data/icons/hicolor/{48,64,128,256}`, `data/metainfo/`, `data/examples/`; `docs/INSTALL.md`.
- `src/` package split extracted from the monolith (`collector.py`, `meters.py`, and friends) with `ui/app.py` as the front end.

### Fixed
- `ui/app.py` no longer relies on `from hearth.legacy_core import *` for stdlib names; the 12 inherited stdlib imports (`json`, `math`, `os`, `queue`, `re`, `shlex`, `signal`, `socket`, `subprocess`, `threading`, `time`, `Path`) are explicit. 43 real `legacy_core` API names remain for Phase 4 to rehome.
- `WIN_KEY` used `os.path.expanduser`; now `Path(...).expanduser()`, verified byte-identical for `~/…`, absolute, empty, and `~user/…` inputs.
- The SSH argv expanded `WIN_KEY` a second time after it was already expanded. Redundant call removed.
- Removed a stale `# noqa: F403`.

### Known gaps
- `legacy_core` still carries duplicate `Collector` / `PeakPoller` shadowing `collector.py` / `meters.py`. Deferred: the only consumer is `ui/app.py`, which Phase 4 rewrites.
- Pre-commit only sees tracked files; 72 files in the repo are still untracked.

## 5.1 — 2026-08-19

### Added
- **No Noise Room-Correction Integration**: Integrated control toggle and status reconciliation for the ~95Hz room resonance notch filter (`no_noise_ctl.sh`) on physical Scarlett speaker output. Decoupled from Carla and managed via PipeWire filter-chain.
- **Hardened IPC Server**: Isolated per-connection command execution in `ipc_serve` within robust exception guards, preventing client disconnects or malformed commands from killing the background IPC thread.
- **Safe Regex Configuration Writer**: Hardened `write_conf_key` to use literal lambda replacements, preventing escape interpretation of backslashes and group references in configuration values.

## 5.0 — 2026-06-21

### Added
- Desktop notifications now actually fire. The "notify on failures" setting existed but was never wired up; Hearth now sends a `notify-send` alert when a core service transitions into the failed state, and when the Astro A50 hardware sink gets muted (the "press U" situation). Honors the existing notify toggle.
- New "Mute All Buses" action in the tray and hamburger menu — a one-click way to silence GAME/CHAT/MUSIC/LAPTOP when stepping away. "Unmute All" / `[U]` restores everything.
- New "Open Config Folder" menu item that opens `~/.config/hearth` in the file manager (where the SSH config and debug dumps live).
- Window size is now remembered across sessions and restored on launch.

### Removed
- Dropped the unused `svc_state()` helper (superseded by the batched `svc_states_batch()`; nothing called it).
- Removed two hidden Astro/Scarlett buttons that were constructed and signal-connected but never added to any panel (the keybinds, tray, and menu already drive those actions).
- Removed the per-strip status labels that were built and updated every refresh tick but never displayed (the VU meter color already conveys running/idle), trimming needless work from the refresh loop.

### Verified
- `python3 -m py_compile hearth.py` and `compileall -q .`
- `pyflakes` clean; `ruff` (remaining hits are the project's intentional style patterns).

## 4.9 — 2026-06-21

### Changed
- Removed an unused precomputed sink-name list in the status poller (dead code; the per-index parse below it is the real path).
- Dropped a stray f-string prefix on a tracemalloc debug header line that had no interpolation.

### Verified
- ruff, pyflakes, pylint (W/E), vulture: no new runtime defects (remaining hits are intentional broad-except / encoding / local-import patterns).
- `python3 -m py_compile hearth.py` and `compileall -q .`
- mypy run (informational; codebase is untyped + relies on GObject introspection).

## 4.8 — 2026-06-21

### Fixed
- Fixed mic monitor loopback cleanup so turning the B1/B2 monitor button off unloads the matching PipeWire loopback modules instead of leaving stale monitor routes behind.
- Fixed per-app stream controls so mute/volume changes are reflected in the popover even when the app list itself does not change.
- Fixed LPD8 auto-reconnect so Hearth starts watching immediately when the service is already failed or inactive at launch, not only after a later state transition.
- Hardened helper-script and SSH launcher command quoting so spaces or shell-sensitive characters in paths/settings cannot break the launched command.
- Cleared stale VU peak readings when a `parec` monitor process exits or a source is removed, preventing dead streams from retaining old levels.

### Verified
- `python3 -m py_compile hearth.py`
- `python3 -m compileall -q .`
- AST parse of all Python files
- Output zip structure and exclusions checked
