# Hearth development handoff — 2026-10-28

This document is the durable checkpoint for the current repair/audit pass. It is kept in the PR branch so work can continue even when the NobaraSupervisor connection to the development PC is unavailable.

## Latest verified checkpoint — 2026-10-28 18:43 PDT

- The branch head entering this handoff refresh is [`53eee20`](https://github.com/Roaring1/Hearth/commit/53eee20aba128b004d344fb28ab48f8ede804d72). This checkpoint documents the host-side LPD8 startup/reconnect fix in [`docs/LPD8_STARTUP_LED_SYNC.md`](LPD8_STARTUP_LED_SYNC.md) and preserves the exact shell-script diff as [`docs/patches/lpd8-startup-led-sync.patch`](patches/lpd8-startup-led-sync.patch). The installed `~/bin/lpd8_mixer.sh` was patched in place with a private rollback copy under `~/.local/state/hearth-backups/`; only `lpd8-mixer.service` was restarted to load it. PR #1 remains open, mergeable and unmerged.
- Final `./scripts/check.sh` passed after the LED timeout fix and worker-level regression: Ruff lint, 85-file formatting check, mypy (33 source files), **463 pytest tests**, legacy compile, desktop-entry validation and ShellCheck passed; AppStream validation succeeds with its existing pedantic uppercase-ID warning. The external startup-sync patch also passes `bash -n` and `patch --dry-run` against the saved pre-patch script copy. A focused Xvfb run across `test_proc.py`, `test_func_check_safety.py`, `test_midi.py`, `test_meters.py`, `test_collector.py`, and `test_leds.py` passed **116 tests**, including the new case where a cached LPD8 port disappears mid-write, the worker invalidates it, and a later update re-probes rather than wedging. GitHub Actions runs [#94](https://github.com/Roaring1/Hearth/actions/runs/40788233279), [#96](https://github.com/Roaring1/Hearth/actions/runs/40793933065), [#97](https://github.com/Roaring1/Hearth/actions/runs/40794312069), [#98](https://github.com/Roaring1/Hearth/actions/runs/40794892055), [#99](https://github.com/Roaring1/Hearth/actions/runs/40795118384), [#100](https://github.com/Roaring1/Hearth/actions/runs/40795486300), [#101](https://github.com/Roaring1/Hearth/actions/runs/40800194954), [#102](https://github.com/Roaring1/Hearth/actions/runs/40800448555), [#103](https://github.com/Roaring1/Hearth/actions/runs/40800606695), and [#104](https://github.com/Roaring1/Hearth/actions/runs/40800860421) passed all six jobs for their respective heads. The newest successful code/test head is `4301d83`; #104 passed on the documentation-only head `53eee20`. This checkpoint adds the host-script patch artifact and will trigger its own workflow; always verify the latest PR-head run before merging.
- The MIDI disconnect model was exercised without unplugging hardware: a private `PATH` with successful empty `aseqdump`/`amidi` listings reported the LPD8 `connected=False` with a plug-it-in message; a failing `aseqdump` probe reported `connected=None` and `Cannot tell whether the LPD8 pad controller is connected`. A read-only listing of the actual host still showed `hw:6,0,0 LPD8 MIDI 1` and the Launchpad Mini; no MIDI write was performed by this probe test. A worker-level test also simulated a cached LPD8 port disappearing during a write: the failed write invalidated the cached address, the worker returned to idle, and the next update attempted discovery again and dropped safely when the simulated port was absent.
- The installed `~/.local/bin/hearth --status` read-only check after the timeout fix reported `Everything is working`, all 17 monitored services healthy, the LPD8 connected/working, and the Launchpad Mini controlled by Padfire. This is a service/probe health confirmation, not optical confirmation that the LPD8 pad LEDs are visibly correct.
- Startup LED sync was patched in the actual host script `~/bin/lpd8_mixer.sh`. The old code swallowed all `amidi` errors and marked sync complete after one attempt regardless of whether port discovery or all eight writes succeeded. The new code bounds discovery/writes, validates live mute queries, retries every second until all state-pad writes plus action-pad OFF messages succeed, and rate-limits retry logs. A fake-device test simulated an empty port listing, then a failed MIDI write, then a successful full eight-message sync. After restarting only `lpd8-mixer.service`, the live log recorded `LED sync complete: music=1 chat=1 astro=1 mic=1 game=1 (action pads dark)`; direct writes to the real `hw:6,0,0` port had also returned success for all eight messages. Physical LED appearance still cannot be confirmed through this remote session; if pads remain dark despite `LED sync complete`, check the active LPD8 preset/mode supports incoming MIDI Note feedback.
- Interactive tests used a temporary source copy and private config/state/runtime under `/tmp`, with a separate virtual KWin Wayland compositor and its own Xwayland `DISPLAY=:1`. All pointer events after isolation targeted only that nested display; no further pointer or keyboard events were sent to the host desktop. Test hooks and source changes existed only in the temporary copy and were never committed. The nested compositor, test app, synthetic stream and temporary files have been cleaned up.
- The per-app slider on a zero-valued synthetic stream changed from 100% to 93%, PipeWire reflected the change, and the stream was restored to 100%. Its mute/unmute UI buttons changed the stream mute state `no → yes → no`. The Game bus mute UI control also changed `no → yes → no`; Game was restored unmuted. These tests produced no audible signal.
- The custom Chat bus fader was clicked and dragged in the nested desktop; PipeWire showed the level change, and Chat was restored to its baseline 58%. For app drag-to-move, only the zero-valued `Hearth_UI_SmokeTest` stream (sink input `660058`) was dragged from `vm_game` to `vm_music`; PipeWire confirmed the sink change. The same stream was returned to `vm_game` at 100%/unmuted. The other row was not dragged, and no synthetic test stream remains.
- The Chat OUT popover was visually confirmed with `A50 game`, `A50 chat`, `Scarlett` and `Share`; `A50 chat` was checked. A separate earlier test-copy callback also enabled and disabled a `vm_chat.monitor → vm_share` route, with the worker's busy-state disabling verified.
- A fresh production-code copy on a dedicated headless Xvfb display provided the direct pointer test that was missing before. The OUT popover's initially-off `Scarlett` checkbox was clicked with the test copy's Scarlett label mapped only to the virtual null sink `hearth_route_input_test_sink`, so no physical output could receive the route. PipeWire showed the `vm_chat.monitor → hearth_route_input_test_sink` loopback appear. After polling until the actual checkbox was sensitive again following the busy worker, a second pointer click removed that same loopback; PipeWire confirmed it disappeared. The test null sink and app were removed afterwards; the real Game/Chat routes stayed intact.
- Mic B1's IN popover was opened with a pointer click in the same isolated Xvfb display. Its rendered choices were `SM7B via Carla` (checked), `SM7B (raw)`, `A50 boom mic`, `Scarlett in`, and `LifeCam`. A second pointer click on IN closed the popover, with zero source choices selected and the route graph unchanged.
- A second isolated responsiveness check wrapped only the test copy's `pactl load-module` for the null-sink route with a 1.4-second delay. During the delay, the Hearth window remained mapped and its AT-SPI tree responded; the new route had not yet appeared and the Scarlett checkbox reported `checked=True, sensitive=False`. After the wrapper released, PipeWire showed the route, the checkbox returned to `checked=True, sensitive=True`, and a pointer click disabled the route. The temporary route, null sink, app and Xvfb display were removed afterwards. This validates a realistic delayed command shorter than the subprocess timeout; it does not replace an end-to-end test with the physical controller unplugged or the actual system audio tools wedged.
- Setup-save feedback was verified through the real Save button in the isolated test copy. The worker wrote only to the private copied config; the routing-service restart was stubbed to return unavailable. The completion callback displayed `Saved*` with the expected “configuration was saved, but routing could not be restarted” tooltip. The real `config.json` checksum remained identical to its baseline, and no service was restarted. The LPD8 raw MIDI port `hw:6,0,0` was detected; Hearth's LED helper accepted writes for pads 1/2/7 in the expected lit state because Music, Chat and Game were unmuted. The physical LED state was not visually observed, and no LPD8 mapping was changed.
- Final read-only audio check after cleanup: `vm_game` 76%, `vm_chat` 58%, `vm_music` 29%, and `vm_share` 100%, all unmuted. Exactly the original Game and Chat loopbacks to the Astro A50 chat output remain; no `Hearth_UI_*` synthetic streams remain. The temporary `vm_chat.monitor → vm_share` loopback was removed. Host volumes, mute flags and persisted config were restored/unchanged.
- `pipewire.service`, `pipewire-pulse.service`, `wireplumber.service`, `lpd8-mixer.service`, and `roaring-audio-routesd.service` remained active/running with `Result=success`; `hearth-ui.service` remained inactive/dead with `Result=success`. No systemd unit was started, stopped or restarted during this pass.

## Earlier checkpoint — 2026-10-28 16:31 PDT

- NobaraSupervisor reconnection is healthy: service version 1.5.0, OAuth enabled, user session `roaring` (UID 1000), and no active subprocess.
- Current branch HEAD is [`677f443`](https://github.com/Roaring1/Hearth/commit/677f443bc4b97f3b416ca71c5abbd232207fbaa7), a documentation-only service-restart-policy update. The latest GTK changes are in [`a4b637e`](https://github.com/Roaring1/Hearth/commit/a4b637edbedf68897c1f3a8fa417d0d97a2d8dfa) and [`dc839f2`](https://github.com/Roaring1/Hearth/commit/dc839f26870c269d5bd3cea95d62bd452e925fde). CI runs [#90](https://github.com/Roaring1/Hearth/actions/runs/40786803750), [#92](https://github.com/Roaring1/Hearth/actions/runs/40787232387), and [#93](https://github.com/Roaring1/Hearth/actions/runs/40787755262) all passed all six jobs for their respective heads, including lint/types, Python 3.11/3.12/3.13 tests, GTK4/Xvfb smoke, and install smoke.
- The Setup headset-target/latency config save and routing-restart request run off the GTK click callback, disable dependent controls while pending, and report write/thread-start/restart failures separately. The latest code also coalesces bus and per-app slider values on one background worker at roughly 30 ms per batch, moves LPD8 mapping writes and service-status probes off GTK, and keeps the newest pending volume request per sink/stream.
- The local `./scripts/check.sh` gate passed on the latest combined local tree: Ruff lint and formatting, mypy (33 source files), **460 pytest tests**, legacy-module compile, desktop-entry validation, ShellCheck, and AppStream validation. AppStream still emits the existing pedantic uppercase application-ID warning while reporting successful validation.
- The correct user-session check (`XDG_RUNTIME_DIR=/run/user/1000`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`) reports “Everything is working” and all 17 monitored services healthy; LPD8 is connected and Launchpad Mini is controlled by Padfire.
- Current read-only service snapshot: `lpd8-mixer.service` and `roaring-audio-routesd.service` are `active/running` with `Result=success`; `hearth-ui.service` is `inactive/dead` with `Result=success` and `ExecMainStatus=0`. Its unit is `Type=simple`, `Restart=on-failure`, `RestartSec=3s`: the first launch at 07:08:18 on 2026-10-28 exited 1 because no display was available, systemd retried at 07:08:21, and the successful long-running instance exited 0 at 16:08:21. The journal records no specific reason for that clean exit. A clean exit is not restarted under this policy; I did not start, stop, or restart any unit.
- Real Plasma launch/render smoke completed on 2026-10-28: the current in-tree `python3 ~/bin/hearth.py --show` entry point opened the main mixer window, and `--remap` opened LPD8 Controls. The rendered windows showed the expected bus strips, per-app row, all eight pad labels and all eight knob labels. An accessibility click opened PAD 5's assignment popover; the accessible tree exposed its dropdown with `Save a diagnostic report` selected, but the expanded choice list was not visually confirmed in the captured window view. No new assignment was selected. The test instance was closed with `--quit`; no Hearth or `parec` monitor processes remained. `hearth-ui.service` stayed inactive/success, while `lpd8-mixer.service` and `roaring-audio-routesd.service` stayed active/running. No fader, mute, route, configuration, or hardware LED state was intentionally changed. This verifies launch/render only: direct pointer operation of volume/mute sliders, app drag-to-move, live IN/OUT route popovers, Setup-save feedback, and LPD8 LED feedback under actual user interaction remain unverified. Do not mark the full interactive smoke test complete.
- PR #1 remains open, mergeable, and unmerged. Do not merge automatically; the handoff still recommends squash-and-merge after the maintainer reviews the live UI behavior.



## Current GitHub state

- Repository: https://github.com/Roaring1/Hearth
- Working PR: https://github.com/Roaring1/Hearth/pull/1 — Fix GTK4 packaging, health reporting, and runtime safety
- PR status: open, mergeable, **not merged**.
- Base commit: 4600ebed949107114822827056f09d7cd0780b4e (the PR base; main has not been changed by this work).
- Branch: fix/gtk4-install-and-smoke-test
- At the start of the 17:22 PDT checkpoint, the audited code tree was synchronized and clean at `6d4edd655917eb3021f5a4d3440b25c055013293`; this checkpoint is a documentation-only follow-up. The diagnostic bundle script is executable (`100755`) again after a GitHub contents-API edit had changed its mode.
- CI run #83 for `43e7c1c` passed lint/types, GTK4 smoke, Python 3.11/3.12/3.13 and install smoke: https://github.com/Roaring1/Hearth/actions/runs/40784116221.
- CI run #84 for `d8ad784` passed: https://github.com/Roaring1/Hearth/actions/runs/40784293718. CI run #85 for `7ab6bec` passed all jobs: https://github.com/Roaring1/Hearth/actions/runs/40784474690.
- CI runs #86 (`d86b030`), #87 (`65047a6`), #88 (`a82b57b`), #89 (`a4b637e`), #90 (`dc839f2`), #92 (`3a39766`), #93 (`677f443`), and #94 (`6d4edd6`) all passed all six jobs. Run #95 for documentation-only head `fe0f54c` was cancelled before install smoke; lint/types, all Python versions and GTK import smoke had already succeeded. Run #94 remains the latest fully green workflow for audited code. The final local gate passed again with 460 tests. Always use the newest branch head/workflow as current truth.
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
- At the latest check, `lpd8-mixer.service` and `roaring-audio-routesd.service` are active/running with `Result=success`. `hearth-ui.service` is inactive/dead with `Result=success`, `ExecMainStatus=0`, and exit timestamp 16:08:21 PDT on 2026-10-28. Its unit uses `Restart=on-failure` / `RestartSec=3s`; the first launch failed because no display was available, systemd retried at 07:08:21, and the long-running instance later exited with status 0. No specific clean-exit reason appears in the journal. No service was started, stopped or restarted during this audit; the live audio graph and core routing services remain healthy.
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

1. Remaining interactive work is narrow: visually confirm the physical LPD8 pad LEDs after the new startup sync (the live script log and all eight MIDI command statuses are successful, but no optical readback was available) and—if a safe setup is available—verify a real controller disconnect/reconnect. The host script now retries failed initial syncs and re-runs sync per new sequencer connection; the retry path passed a fake-device test. The 1.4-second injected `pactl load-module` delay passed in the isolated GUI. MIDI absent/unknown probes and cached-port disappearance are covered with simulations; target tests also cover bounded timeouts, route-worker recovery, MIDI coalescing and meter/collector errors. Real hardware-disconnect behavior and optical LED readback remain unverified. Per-app volume/mute, Game bus mute, Chat fader click/drag, app drag-to-move, actual pointer-driven OUT route ON/OFF (waiting for the control to re-enable after the worker), IN popover open/close, and Setup-save feedback have been exercised in the isolated test environment.
2. The `config.json` audit is complete: there is no in-app writer. `src/hearth/config.py` only reads/parses it, and legacy callers delegate to that read path. The on-disk file was secured to `0600`; do not copy its contents into the public repo. If a writer is added later, require atomic 0600 writes and a regression test.
3. `hearth-ui.service` last exited cleanly with status 0; do not restart it automatically. Per-app volume, app mute and Setup-save feedback have been exercised in the isolated test UI. No systemd unit was started, stopped or restarted during these tests.

### P1 — remaining audit/review

- Review any remaining synchronous GTK signal handler for filesystem lock/fsync, systemctl, pactl or amidi calls. Keep Setup save/restart feedback accurate and verify the per-app sliders feel smooth during high-rate snapshots.
- Consider consolidating the long PR's incremental commits before merging; a squash merge is the least invasive option.
- CI/Xvfb validates imports and mocked behavior but does not prove the mixer feels right in a live Plasma/PipeWire session.
- The isolated interaction results are recorded in the latest checkpoint. Leave PR #1 open until visual LED confirmation and slow/disconnected-device resilience checks are completed and review/merge is deliberately requested.

## Safety boundaries

- Live audio state changed only transiently for controlled tests: zero-valued synthetic streams, a temporary `vm_chat.monitor → vm_share` route, and brief Chat/Game mute/volume interactions. The app row was moved back to Game, both synthetic streams ended, the temporary route was removed, and all bus values returned to baseline. The Setup test wrote only a private temporary config copy and used a stubbed restart; the real config checksum remained unchanged. LPD8 writes reasserted the expected lit state for the three unmuted mapped buses. No systemd unit was started/stopped/restarted and no LPD8 mapping was edited.
- Do not publish local status dumps, VU captures, private config contents, tokens, or full logs in public GitHub issues/PR comments. Share a redacted excerpt only when necessary.
- Do not merge PR #1 automatically. Main is unchanged by this repair branch.
