# Changelog

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
