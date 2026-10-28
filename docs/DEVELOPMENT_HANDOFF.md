# Hearth development handoff — 2026-10-28

This document is the durable checkpoint for the current repair/audit pass. It is kept in the PR branch so work can continue even when the NobaraSupervisor connection to the development PC is unavailable.

## Latest verified checkpoint — 2026-10-28 16:23 PDT

- NobaraSupervisor reconnection is healthy: service version 1.5.0, OAuth enabled, user session `roaring` (UID 1000), and no active subprocess.
- Current code HEAD is [`dc839f2`](https://github.com/Roaring1/Hearth/commit/dc839f26870c269d5bd3cea95d62bd452e925fde). CI run [#89](https://github.com/Roaring1/Hearth/actions/runs/40786455641) passed all six jobs for `a4b637e`, including the volume coalescing and first LPD8 remap-worker fixes. Run [#90](https://github.com/Roaring1/Hearth/actions/runs/40786803750) completed successfully for `dc839f2`; all six jobs passed, including lint/types, Python 3.11/3.12/3.13 tests, GTK4/Xvfb smoke, and install smoke. This commit moves the LPD8 service-status query off GTK and adds regressions for its unknown-state/pending-write behavior.
- The Setup headset-target/latency config save and routing-restart request run off the GTK click callback, disable dependent controls while pending, and report write/thread-start/restart failures separately. The latest code also coalesces bus and per-app slider values on one background worker at roughly 30 ms per batch, moves LPD8 mapping writes and service-status probes off GTK, and keeps the newest pending volume request per sink/stream.
- The local `./scripts/check.sh` gate passed on the latest combined local tree: Ruff lint and formatting, mypy (33 source files), **460 pytest tests**, legacy-module compile, desktop-entry validation, ShellCheck, and AppStream validation. AppStream still emits the existing pedantic uppercase application-ID warning while reporting successful validation.
- The correct user-session check (`XDG_RUNTIME_DIR=/run/user/1000`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`) reports “Everything is working” and all 17 monitored services healthy; LPD8 is connected and Launchpad Mini is controlled by Padfire.
- Current read-only service snapshot differs from the older checkpoint: `hearth-ui.service` is `inactive/dead` with `Result=success` (optional UI, not an audio-health failure); `lpd8-mixer.service` and `roaring-audio-routesd.service` are `active/running` with `Result=success`. No services were started, stopped, or restarted.
- The normal interactive Plasma smoke test remains unverified in this session. CI/Xvfb and automated safety tests passed, but they do not replace manually adjusting the per-app volume slider, moving a strip by its title, opening route/input popovers, and checking LPD8 LED feedback in the actual desktop session. Do not claim this test is complete until it is actually performed.
- PR #1 remains open, mergeable, and unmerged. Do not merge automatically; the handoff still recommends squash-and-merge after the maintainer reviews the live UI behavior.



## Current GitHub state

- Repository: https://github.com/Roaring1/Hearth
- Working PR: https://github.com/Roaring1/Hearth/pull/1 — Fix GTK4 packaging, health reporting, and runtime safety
- PR status: open, mergeable, **not merged**.
- Base commit: 4600ebed949107114822827056f09d7cd0780b4e (the PR base; main has not been changed by this work).
- Branch: fix/gtk4-install-and-smoke-test
- Local checkout is synchronized and clean at code commit `dc839f26870c269d5bd3cea95d62bd452e925fde` before this handoff refresh. The diagnostic bundle script is executable (`100755`) again after a GitHub contents-API edit had changed its mode.
- CI run #83 for `43e7c1c` passed lint/types, GTK4 smoke, Python 3.11/3.12/3.13 and install smoke: https://github.com/Roaring1/Hearth/actions/runs/40784116221.
- CI run #84 for `d8ad784` passed: https://github.com/Roaring1/Hearth/actions/runs/40784293718. CI run #85 for `7ab6bec` passed all jobs: https://github.com/Roaring1/Hearth/actions/runs/40784474690.
- CI run #86 for `d86b030`, run #87 for `65047a6`, run #88 for `a82b57b`, and run #89 for `a4b637e` all passed all six jobs. Run #90 for `dc839f2` completed successfully with all six jobs passing: https://github.com/Roaring1/Hearth/actions/runs/40786803750. Always use the newest branch head and workflow run as current truth.
- The PR currently contains many small commits because GitHub's file-update endpoint made one commit per file while the development PC connector was unavailable. Prefer **Squash and merge** (or consolidate history before merging) instead of preserving the entire incremental history. Do not merge without the maintainer's decision.

## CI evidence

Run #80 passed each of these jobs:

- Lint and types: success (Ruff lint, Ruff formatting, mypy, desktop-entry and metadata validation).
- GTK4 GUI import smoke under Xvfb: success; **35 tests passed**, 415 deselected for this GUI-specific job. One harmless PyGObject deprecation warning was emitted for GLib's Unix-signal API.
- Python 3.11 tests: **415 passed**, one GI-dependent test skipped because that matrix environment does not install PyGObject, one item deselected.
- Python 3.12 tests: success.
- Python 3.13 tests: success.
- Package install smoke: success.

Several intermediate commits were intentionally pushed and CI exposed issues before green runs: a missing module import in the dump writer, incomplete fake-thread test doubles, formatting differences under hosted Ruff, and the missing `threading` import in the asynchronous LPD8 remap writer. Those were corrected. The local `./scripts/check.sh` gate passed on the latest combined tree: Ruff, formatting, mypy, **460 tests**, compile checks, desktop-entry/metadata validation and ShellCheck. The AppStream uppercase-ID item remains a pedantic warning; validation is successful.

## Main findings and fixes on the PR branch

### Packaging, identity, and docs

- Corrected the GTK4 application's ID to use the package's canonical APP_ID; aligned desktop window metadata.
- Added a real GTK4 import smoke test under a virtual display, and made the CI workflow install system GTK introspection bindings where required.
- Updated stale GTK3-era README/install instructions for the actual GTK4 app.
- Marked the source-checkout hearth.py shim executable so the documented symlink-based CLI install works.

### Health/status accuracy

- An inactive hearth-ui.service (the optional mixer window) was incorrectly counted as a broken audio service by headless status, even though audio routing can work with the UI closed.
- Excluded this optional UI unit in the shared health classifier and aligned the setup window with the same logic.
- A real on-PC status check after reconnecting returned **“Everything is working”**, with all 17 monitored services healthy, LPD8 connected and Launchpad Mini controlled by Padfire. The check works when the user-session environment is set (`XDG_RUNTIME_DIR=/run/user/1000`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`). Without it, a login-shell invocation fell back to `/tmp/hearth-1000` and produced misleading audio/service errors; the correctly configured recheck was healthy.

### GTK responsiveness and audio controls

- Moved sink/source/route inventory reads into the background collector, and route mutations plus live-graph verification into a background worker.
- Route popovers use the last verified cache rather than doing synchronous Pactl calls during opening/rendering. They remain disabled until the graph is known, preserve the last verified graph on optional-query failure, refresh only when inventory changes, and defer rebuilds while a route mutation is in flight.
- Moved LPD8 MIDI port discovery and LED writes to one coalescing worker. It keeps the newest requested mute state per sink and avoids blocking the GTK thread if ALSA/amidi is slow or the controller is absent.
- Stabilized application listener rows across identical 250–500 ms snapshots instead of destroying/recreating every row on each poll.
- Added a compact per-application volume slider and percentage readout. It uses the individual sink-input command (pactl set-sink-input-volume <stream-index> <percent>), not a bus-volume command. Snapshot updates respect a short interaction hold so the slider is not pulled away while being dragged. Drag-to-move is attached to the app name, leaving the slider independently usable.
- Moved raw microphone-route config writes out of the GTK callback into the route-job worker. Those writes lock, write a temporary file, fsync and atomically replace config; the handler now reuses route-job disable/rollback/error reporting instead of blocking GTK.
- Detached volume/mute/move commands report immediate launch failures without waiting on high-frequency commands. UI callbacks restore optimistic state or show an error if a command cannot be started.

### Runtime and configuration safety

- Tightened IPC socket/PID handling, message framing, size limits, cleanup and stale-path behavior. Never unlink arbitrary/non-socket paths as though they were Hearth-owned sockets.
- Hardened numeric setting validation, loopback latency/window bounds, runtime directory fallback, ownership, permissions, settings writes and LPD8 remapping against unsafe paths and concurrent lost updates.
- Made mic-router configuration writes fail closed for invalid UTF-8 or unsafe inputs, use advisory locking and atomic replacement, and refuse unsafe target/lock paths.
- Changed the status-dump writer to use a private atomic file and made the diagnostic bundle script use umask 077. Diagnostic bundles can expose host/config details; share them deliberately.
- The branch adds docs/AUDIO_ROUTING.md and docs/LPD8_REMAP.md for operational context and safe procedures.

## NobaraSupervisor reconnection and recovered PC state

The supervisor connection recovered and returned service version `1.5.0`, OAuth enabled, user mode for `roaring` (UID 1000), no active subprocess and available execution slots. The local checkout was fetched and safely fast-forwarded from `1416b1b` to the latest GitHub branch; before clearing the interrupted work, the local diff was backed up at `/tmp/hearth-handoff/pre-sync-local-work.patch`. The interrupted edits overlapped with improved versions already on the remote branch, so that backup was preserved and the checkout was fast-forwarded rather than force-reset.

The PC state now verified after reconnection:

- Checkout was safely synced clean to `dc839f2` on branch `fix/gtk4-install-and-smoke-test` before this handoff refresh; this documentation refresh follows the code commit.
- CLI: `hearth 5.1.0`; `hearth --paths` resolves config under `~/.config/hearth`, state under `~/.local/state/hearth`, cache under `~/.cache/hearth`, and runtime under `/run/user/1000/hearth` when the session environment is set.
- Desktop integration: the existing desktop launcher and `~/bin/hearth-mixer` wrapper were preserved; `~/.local/bin/hearth` points to the executable in-tree shim.
- User-session status: `hearth --status` says **Everything is working**, all 17 monitored services are healthy, LPD8 is connected and pads/knobs are live, and Launchpad Mini is controlled by Padfire.
- At the latest check, `lpd8-mixer.service` and `roaring-audio-routesd.service` are active/running with `Result=success`. `hearth-ui.service` is now inactive/dead with `Result=success`, `ExecMainStatus=0`, and an exit timestamp of 16:08:21 PDT on 2026-10-28. It was active on an earlier check and later exited cleanly; the reason was not established. It was not stopped/restarted by this pass. The live audio graph and core routing services remain healthy.
- The initial health check without the user-session DBus environment reported PipeWire/portal down and no unit list. Repeating it with `XDG_RUNTIME_DIR=/run/user/1000` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` returned healthy status. Do not diagnose that first result as a real audio outage.

### Existing file permissions repaired on the PC

Before repair, the inspected Hearth config/state/cache directories were mode `0755`; config/settings/mic-router/mixer files and old status dumps included mode `0644`. After checking paths, ownership and symlink types, the repair removed group/other access only from regular files owned by UID 1000 and set Hearth-owned directories to `0700`. It did not delete or rewrite file contents.

Verified result:

- Directories `~/.config/hearth`, `~/.local/state/hearth`, `~/.local/state/hearth/dumps` and `~/.cache/hearth`: `0700`.
- Regular files inside those Hearth data trees (including `config.json`, `rac_settings.json`, the mic-router config and backups, `roaring_mixer.conf`, `hearth.log`, `summary.txt`, and `tracemalloc.pkl`): `0600`.
- The diagnostic bundle script's executable mode was restored to `100755` in commit `43e7c1c`.

No dump or log contents were published to GitHub. Machine-specific configuration remains on the PC only.

## Remaining work, in priority order

### P0 — remaining interactive verification

1. Run a normal Plasma-session smoke test (not just Xvfb): open/close/reopen Hearth, adjust per-app volume and mute, drag an app to another bus, toggle a route, open IN/OUT popovers while a route job is pending, and verify LPD8 LED feedback. Check that controls remain responsive if pactl/ALSA is slow or the controller is disconnected.
2. The `config.json` audit is complete: there is no in-app writer. `src/hearth/config.py` only reads/parses it, and legacy callers delegate to that read path. The on-disk file was secured to `0600`; do not copy its contents into the public repo. If a writer is added later, require atomic 0600 writes and a regression test.
3. `hearth-ui.service` last exited cleanly with status 0; do not restart it automatically. When the user next opens Hearth through their normal desktop flow, verify the UI remains responsive and exercise the new per-app volume control and Setup-save feedback.

### P1 — remaining audit/review

- Review any remaining synchronous GTK signal handler for filesystem lock/fsync, systemctl, pactl or amidi calls. Keep Setup save/restart feedback accurate and verify the per-app sliders feel smooth during high-rate snapshots.
- Consider consolidating the long PR's incremental commits before merging; a squash merge is the least invasive option.
- CI/Xvfb validates imports and mocked behavior but does not prove the mixer feels right in a live Plasma/PipeWire session.
- After the interactive smoke, append the result here. Leave PR #1 open until review/merge is deliberately requested.

## Safety boundaries

- This audit has not intentionally changed a live audio graph or restarted user routing services.
- Do not publish local status dumps, VU captures, private config contents, tokens, or full logs in public GitHub issues/PR comments. Share a redacted excerpt only when necessary.
- Do not merge PR #1 automatically. Main is unchanged by this repair branch.
