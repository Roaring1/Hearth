# Changelog

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
