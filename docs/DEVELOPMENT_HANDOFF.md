# Hearth development handoff — 2026-10-28

This document is the durable checkpoint for the current repair/audit pass. It is kept in the PR branch so work can continue even when the NobaraSupervisor connection to the development PC is unavailable.

## Previous checkpoint — 2026-10-28 18:47 PDT (superseded by the later checkpoint below)

- At that checkpoint, the branch head was [`2f90830`](https://github.com/Roaring1/Hearth/commit/2f9083008f6698dcd1f83583ec2dc7a0d12861f1). The later LED capability correction below supersedes the LED-feedback claims recorded here. PR #1 remains open, mergeable and unmerged.
- Final `./scripts/check.sh` passed after the LED timeout fix and worker-level regression: Ruff lint, 85-file formatting check, mypy (33 source files), **463 pytest tests**, legacy compile, desktop-entry validation and ShellCheck passed; AppStream validation succeeds with its existing pedantic uppercase-ID warning. The external startup-sync patch also passes `bash -n` and `patch --dry-run` against the saved pre-patch script copy. A focused Xvfb run across `test_proc.py`, `test_func_check_safety.py`, `test_midi.py`, `test_meters.py`, `test_collector.py`, and `test_leds.py` passed **116 tests**, including the new case where a cached LPD8 port disappears mid-write, the worker invalidates it, and a later update re-probes rather than wedging. GitHub Actions runs [#94](https://github.com/Roaring1/Hearth/actions/runs/40788233279), [#96](https://github.com/Roaring1/Hearth/actions/runs/40793933065), [#97](https://github.com/Roaring1/Hearth/actions/runs/40794312069), [#98](https://github.com/Roaring1/Hearth/actions/runs/40794892055), [#99](https://github.com/Roaring1/Hearth/actions/runs/40795118384), [#100](https://github.com/Roaring1/Hearth/actions/runs/40795486300), [#101](https://github.com/Roaring1/Hearth/actions/runs/40800194954), [#102](https://github.com/Roaring1/Hearth/actions/runs/40800448555), [#103](https://github.com/Roaring1/Hearth/actions/runs/40800606695), and [#104](https://github.com/Roaring1/Hearth/actions/runs/40800860421) passed all six jobs for their respective heads. The newest successful code/test head is `4301d83`; #104 passed on the documentation-only head `53eee20`. Run [#105](https://github.com/Roaring1/Hearth/actions/runs/40802254980) was cancelled when the later handoff commit superseded it. Run [#106](https://github.com/Roaring1/Hearth/actions/runs/40802350618) passed all six jobs for documentation-only head `2f90830`. This checkpoint update will trigger its own workflow; always verify the newest PR-head run before merging.
- The MIDI disconnect model was exercised without unplugging hardware: a private `PATH` with successful empty `aseqdump`/`amidi` listings reported the LPD8 `connected=False` with a plug-it-in message; a failing `aseqdump` probe reported `connected=None` and `Cannot tell whether the LPD8 pad controller is connected`. A read-only listing of the actual host still showed `hw:6,0,0 LPD8 MIDI 1` and the Launchpad Mini; no MIDI write was performed by this probe test. A worker-level test also simulated a cached LPD8 port disappearing during a write: the failed write invalidated the cached address, the worker returned to idle, and the next update attempted discovery again and dropped safely when the simulated port was absent.
- The installed `~/.local/bin/hearth --status` read-only check after the timeout fix reported `Everything is working`, all 17 monitored services healthy, the LPD8 connected/working, and the Launchpad Mini controlled by Padfire. This is a service/probe health confirmation, not optical confirmation that the LPD8 pad LEDs are visibly correct.
- The initial startup patch added timeout/retry behavior, but its assumed feedback protocol was wrong for this hardware. The later capability correction below supersedes this paragraph: the classic LPD8 cannot receive incoming Note On/Off messages for pad-light control, so the host script now initializes Hearth's internal toggle state from the real mute values and explicitly disables outgoing LED feedback. The fake-device test verifies retry logic only; it does not prove hardware light support.
- Interactive tests used a temporary source copy and private config/state/runtime under `/tmp`, with a separate virtual KWin Wayland compositor and its own Xwayland `DISPLAY=:1`. All pointer events after isolation targeted only that nested display; no further pointer or keyboard events were sent to the host desktop. Test hooks and source changes existed only in the temporary copy and were never committed. The nested compositor, test app, synthetic stream and temporary files have been cleaned up.
- The per-app slider on a zero-valued synthetic stream changed from 100% to 93%, PipeWire reflected the change, and the stream was restored to 100%. Its mute/unmute UI buttons changed the stream mute state `no → yes → no`. The Game bus mute UI control also changed `no → yes → no`; Game was restored unmuted. These tests produced no audible signal.
- The custom Chat bus fader was clicked and dragged in the nested desktop; PipeWire showed the level change, and Chat was restored to its baseline 58%. For app drag-to-move, only the zero-valued `Hearth_UI_SmokeTest` stream (sink input `660058`) was dragged from `vm_game` to `vm_music`; PipeWire confirmed the sink change. The same stream was returned to `vm_game` at 100%/unmuted. The other row was not dragged, and no synthetic test stream remains.
- The Chat OUT popover was visually confirmed with `A50 game`, `A50 chat`, `Scarlett` and `Share`; `A50 chat` was checked. A separate earlier test-copy callback also enabled and disabled a `vm_chat.monitor → vm_share` route, with the worker's busy-state disabling verified.
- A fresh production-code copy on a dedicated headless Xvfb display provided the direct pointer test that was missing before. The OUT popover's initially-off `Scarlett` checkbox was clicked with the test copy's Scarlett label mapped only to the virtual null sink `hearth_route_input_test_sink`, so no physical output could receive the route. PipeWire showed the `vm_chat.monitor → hearth_route_input_test_sink` loopback appear. After polling until the actual checkbox was sensitive again following the busy worker, a second pointer click removed that same loopback; PipeWire confirmed it disappeared. The test null sink and app were removed afterwards; the real Game/Chat routes stayed intact.
- Mic B1's IN popover was opened with a pointer click in the same isolated Xvfb display. Its rendered choices were `SM7B via Carla` (checked), `SM7B (raw)`, `A50 boom mic`, `Scarlett in`, and `LifeCam`. A second pointer click on IN closed the popover, with zero source choices selected and the route graph unchanged.
- A second isolated responsiveness check wrapped only the test copy's `pactl load-module` for the null-sink route with a 1.4-second delay. During the delay, the Hearth window remained mapped and its AT-SPI tree responded; the new route had not yet appeared and the Scarlett checkbox reported `checked=True, sensitive=False`. After the wrapper released, PipeWire showed the route, the checkbox returned to `checked=True, sensitive=True`, and a pointer click disabled the route. The temporary route, null sink, app and Xvfb display were removed afterwards. This validates a realistic delayed command shorter than the subprocess timeout; it does not replace an end-to-end test with the physical controller unplugged or the actual system audio tools wedged.
- Setup-save feedback was verified through the real Save button in the isolated test copy. The worker wrote only to the private copied config; the routing-service restart was stubbed to return unavailable. The completion callback displayed `Saved*` with the expected “configuration was saved, but routing could not be restarted” tooltip. The real `config.json` checksum remained identical to its baseline, and no service was restarted. The raw MIDI port `hw:6,0,0` was detected during the earlier timeout tests; the host utility accepted writes for pads 1/2/7, but the user subsequently confirmed the physical pads stayed dark. The latest capability correction below establishes that those writes cannot set pad lights on the classic LPD8. No LPD8 mapping was changed.
- Final read-only audio check after cleanup: `vm_game` 76%, `vm_chat` 58%, `vm_music` 29%, and `vm_share` 100%, all unmuted. Exactly the original Game and Chat loopbacks to the Astro A50 chat output remain; no `Hearth_UI_*` synthetic streams remain. The temporary `vm_chat.monitor → vm_share` loopback was removed. Host volumes, mute flags and persisted config were restored/unchanged.
- `pipewire.service`, `pipewire-pulse.service`, `wireplumber.service`, `lpd8-mixer.service`, and `roaring-audio-routesd.service` remained active/running with `Result=success`; `hearth-ui.service` remained inactive/dead with `Result=success`. Only `lpd8-mixer.service` was restarted once to load and verify the startup LED retry patch. The routing service, PipeWire, PipeWire-Pulse, WirePlumber and Hearth UI unit were not restarted or otherwise changed.

## Earlier checkpoint — 2026-10-28 16:31 PDT

- NobaraSupervisor reconnection is healthy: service version 1.5.0, OAuth enabled, user session `roaring` (UID 1000), and no active subprocess.
- Current branch HEAD is [`677f443`](https://github.com/Roaring1/Hearth/commit/677f443bc4b97f3b416ca71c5abbd232207fbaa7), a documentation-only service-restart-policy update. The latest GTK changes are in [`a4b637e`](https://github.com/Roaring1/Hearth/commit/a4b637edbedf68897c1f3a8fa417d0d97a2d8dfa) and [`dc839f2`](https://github.com/Roaring1/Hearth/commit/dc839f26870c269d5bd3cea95d62bd452e925fde). CI runs [#90](https://github.com/Roaring1/Hearth/actions/runs/40786803750), [#92](https://github.com/Roaring1/Hearth/actions/runs/40787232387), and [#93](https://github.com/Roaring1/Hearth/actions/runs/40787755262) all passed all six jobs for their respective heads, including lint/types, Python 3.11/3.12/3.13 tests, GTK4/Xvfb smoke, and install smoke.
- The Setup headset-target/latency config save and routing-restart request run off the GTK click callback, disable dependent controls while pending, and report write/thread-start/restart failures separately. The latest code also coalesces bus and per-app slider values on one background worker at roughly 30 ms per batch, moves LPD8 mapping writes and service-status probes off GTK, and keeps the newest pending volume request per sink/stream.
- The local `./scripts/check.sh` gate passed on the latest combined local tree: Ruff lint and formatting, mypy (33 source files), **460 pytest tests**, legacy-module compile, desktop-entry validation, ShellCheck, and AppStream validation. AppStream still emits the existing pedantic uppercase application-ID warning while reporting successful validation.
- The correct user-session check (`XDG_RUNTIME_DIR=/run/user/1000`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`) reports “Everything is working” and all 17 monitored services healthy; LPD8 is connected and Launchpad Mini is controlled by Padfire.
- Current read-only service snapshot: `lpd8-mixer.service` and `roaring-audio-routesd.service` are `active/running` with `Result=success`; `hearth-ui.service` is `inactive/dead` with `Result=success` and `ExecMainStatus=0`. Its unit is `Type=simple`, `Restart=on-failure`, `RestartSec=3s`: the first launch at 07:08:18 on 2026-10-28 exited 1 because no display was available, systemd retried at 07:08:21, and the successful long-running instance exited 0 at 16:08:21. The journal records no specific reason for that clean exit. A clean exit is not restarted under this policy; I did not start, stop, or restart any unit.
- Real Plasma launch/render smoke completed on 2026-10-28: the current in-tree `python3 ~/bin/hearth.py --show` entry point opened the main mixer window, and `--remap` opened LPD8 Controls. The rendered windows showed the expected bus strips, per-app row, all eight pad labels and all eight knob labels. An accessibility click opened PAD 5's assignment popover; the accessible tree exposed its dropdown with `Save a diagnostic report` selected, but the expanded choice list was not visually confirmed in that captured window view. No new assignment was selected. The test instance was closed with `--quit`; no Hearth or `parec` monitor processes remained. This was a manual launch/render check; the newer deterministic Xvfb screenshot test is recorded in `docs/UI_QUALITY_AUDIT.md`. Direct live interaction on Plasma, keyboard/focus behavior, and real controller unplug/replug remain unverified. Physical LPD8 LED feedback is not an expected behavior on the attached classic device.
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

1. The stock classic LPD8 cannot mirror Hearth mute state on its pad lights through MIDI Note feedback; optical verification is no longer a pending software check for this model. A real physical unplug/replug of the controller remains unverified. The host script retains retry handling for compatible devices, while the Python worker now skips a raw MIDI port unless its label explicitly identifies an LPD8 MkII. The 1.4-second injected `pactl load-module` delay passed in the isolated GUI. MIDI absent/unknown probes and cached-port disappearance are covered with simulations; target tests also cover bounded timeouts, route-worker recovery, MIDI coalescing and meter/collector errors. Per-app volume/mute, Game bus mute, Chat fader click/drag, app drag-to-move, actual pointer-driven OUT route ON/OFF (waiting for the control to re-enable after the worker), IN popover open/close, and Setup-save feedback have been exercised in the isolated test environment.
2. The `config.json` audit is complete: there is no in-app writer. `src/hearth/config.py` only reads/parses it, and legacy callers delegate to that read path. The on-disk file was secured to `0600`; do not copy its contents into the public repo. If a writer is added later, require atomic 0600 writes and a regression test.
3. `hearth-ui.service` last exited cleanly with status 0; do not restart it automatically. Per-app volume, app mute and Setup-save feedback have been exercised in the isolated test UI. No systemd unit was started, stopped or restarted during these tests.

### P1 — remaining audit/review

- Review any remaining synchronous GTK signal handler for filesystem lock/fsync, systemctl, pactl or amidi calls. Keep Setup save/restart feedback accurate and verify the per-app sliders feel smooth during high-rate snapshots.
- Consider consolidating the long PR's incremental commits before merging; a squash merge is the least invasive option.
- CI/Xvfb now renders the actual GTK4 mixer, Setup and remap windows into screenshots and checks loading/ready/missing/fault states. It still does not prove the mixer feels right in a live Plasma/PipeWire session or replace a keyboard/focus accessibility pass.
- PR #1 remains open; do not merge unless deliberately requested. Remaining manual checks are live Plasma pointer/keyboard flows and a real controller unplug/replug. Physical LED confirmation is not a blocker for the classic LPD8 because inbound MIDI Note LED feedback is unsupported.

## Safety boundaries

- Live audio state changed only transiently for controlled tests: zero-valued synthetic streams, a temporary `vm_chat.monitor → vm_share` route, and brief Chat/Game mute/volume interactions. The app row was moved back to Game, both synthetic streams ended, the temporary route was removed, and all bus values returned to baseline. The Setup test wrote only a private temporary config copy and used a stubbed restart; the real config checksum remained unchanged. Earlier LPD8 MIDI Note writes only demonstrated host command success; the user observed all physical pads dark, and the classic hardware capability check established that inbound LED feedback is unsupported. That feedback path is now disabled. No systemd unit was started/stopped/restarted and no LPD8 mapping was edited during the isolated visual suite.
- Do not publish local status dumps, VU captures, private config contents, tokens, or full logs in public GitHub issues/PR comments. Share a redacted excerpt only when necessary.
- Do not merge PR #1 automatically. Main is unchanged by this repair branch.

## Latest checkpoint — classic LPD8 LED feedback limitation (2026-10-28 19:20 PDT)

- The live LPD8 identifies as USB `09e8:0075` (`LPD8 MIDI 1`), which is the original/classic LPD8, not the MkII. Akai's original Quickstart Guide MIDI Implementation Chart marks Note numbers as transmit-only (`000–127` under Transmit/Export and `n/a` under Recognize/Import). Source: <https://static.bhphotovideo.com/lit_files/84742.pdf>.
- The previous startup patch incorrectly treated successful host-side `amidi` writes as proof that the pad LEDs could be changed. The user reported that every pad remained dark and only the hardware `PAD` mode indicator was lit. For this hardware model, host-driven Note On/Off pad-light feedback is not supported; a retry loop cannot fix it.
- The installed `~/bin/lpd8_mixer.sh` now defaults `LED_FEEDBACK_SUPPORTED=0`, logs the hardware limitation, skips outgoing feedback notes, and still reads actual Pulse/PipeWire mute state to initialize Hearth's internal toggle state. The former `LED sync complete` message is no longer emitted for this model. A future controller must be verified to support incoming Note On/Off LED feedback before setting `LED_FEEDBACK_SUPPORTED=1`.
- The complete host-script delta is tracked in `docs/patches/lpd8-startup-led-sync.patch`. It passes `patch --dry-run -p1`, and applying it to the saved pre-change script reproduces the installed script byte-for-byte. `bash -n` and ShellCheck pass on the installed script; two pre-existing conditional/source lint findings were cleaned up in the same host script, and unsupported LED-flash writes no longer spawn delayed background work.
- The Python LED worker had the same incorrect feedback assumption. `hearth.leds.find_port()` now selects a controller for LED writes only when its raw ALSA label explicitly identifies an LPD8 MkII. Three regression tests cover classic-model rejection, MkII recognition, and `find_port()` skipping the classic controller. The `midi.LED_NOTE` and GTK remap comments now distinguish an on-screen software indicator from the physical pad lamps.
- The latest canonical local gate passed: Ruff lint/format, mypy (33 source files), 463 pytest tests, legacy compile check, desktop-entry validation, and ShellCheck. AppStream validation succeeded with its existing pedantic uppercase application-ID warning.
- Only `lpd8-mixer.service` was restarted for this change. It is active/running; Game, Chat, and Music sinks and the Focusrite microphone remain unmuted. Audio-route daemon and other units were not restarted.
- PR #1 remains open and unmerged. Physical pad LEDs cannot be made to reflect mixer mute state on this classic device through MIDI Note feedback; use a controller with documented inbound LED feedback (for example, a properly configured LPD8 MkII) if that visual requirement is mandatory.


## Latest checkpoint — ghost UI, SVG actions, icon loading and real-render CI (2026-10-29)

- Added `docs/UI_QUALITY_AUDIT.md` with the first visual/code-review findings, test boundaries and next steps. The work is on the same feature branch as PR #1; do not merge automatically.
- Before the first verified collector snapshot, mixer strips now show a dim ghost state (`checking…` / `Loading apps…`) and disable actions until a snapshot is known. Confirmed missing devices still use `no device` and fault styling, not the loading style.
- Added four original, hand-authored SVG action glyphs (`collapse`, `refresh`, `save`, `sliders`) and used icon-plus-visible-label buttons for Setup's save/reconnect/remap actions. Decorative SVG/app glyphs are marked presentation-only; minimize controls carry explicit accessible names. No AI image generation or third-party SVG assets were used.
- Cold profiling on this desktop measured `icons._file_index()` at 2.58 seconds and `_entry_index()` at 180 ms. The UI now returns an immediate letter fallback and builds the indexes on a daemon worker, updating the widget via the GTK main loop once an actual icon is found. A separate GUI regression test ensures a cold lookup does not synchronously populate either index.
- CI's GTK job now runs production GTK4 windows under a fixed 1600x1200 Xvfb display, captures ten screenshots (loading, ready, focused app-move handle, focused fader, focused reorder grip, missing device, fault banner, Setup, LPD8 Controls and focused minimized rail), and uploads `hearth-gtk-visual-audit` for 14 days. The package job checks all four SVGs after installation. `tests/test_gui_visual.py` exercises deterministic fixtures only; it does not touch PipeWire, systemd, the physical LPD8 or real user settings.
- Final local validation after these changes: `./scripts/check.sh` passed with **471 tests**, Ruff, formatting, mypy (33 source files), legacy compile, desktop entry/AppStream metadata and ShellCheck. The GTK-only capture pass separately reported **48 passed**. The remaining AppStream output is the existing pedantic uppercase application-ID warning. GTK's bundled PyGObject currently emits external deprecation warnings in its asyncio integration; no test fails on those warnings.
- Visual scope is intentionally a first slice, not a claim of full visual regression: screenshots are uploaded for human review but no approved golden baselines have been committed. Live Plasma/DPI/theme checks, real keyboard tab traversal, popover/menu flows and real unplug/replug remain outstanding. See `docs/UI_QUALITY_AUDIT.md`.


## Latest checkpoint — mute reconciliation and accessible faders (2026-10-29)

- The previous focused fix is pushed on PR #1 as [`07926e5`](https://github.com/Roaring1/Hearth/commit/07926e5523ff11d3fce7dd243909952cef9ff8f7), `Reconcile optimistic app mute state`. Its hosted [CI run #110](https://github.com/Roaring1/Hearth/actions/runs/40951167646) passed all six jobs. PR #1 remains open and unmerged.
- The next UI-quality slice adds an explicit changing accessible name to each app mute button, updates its muted-name styling with the toggle, gives the rail-dock button an action-oriented accessible label, and makes the custom-drawn mixer fader a keyboard-operable GTK slider. Up/Right change volume by 1; Down/Left decrease by 1; Page Up/Down adjust by 10; Home/End jump to 0/150. Fader value metadata is updated for accessibility consumers, and unavailable faders become insensitive and ignore keyboard/scroll input.
- Regression coverage now includes optimistic mute followed by a stale backend snapshot, dynamic app mute names, rail-dock action labels, fader focusability/slider role, keyboard updates, range endpoints, and dead-state input suppression.
- `./scripts/check.sh` passes locally with **474 tests**; Ruff lint/format, mypy (33 source files), desktop-entry validation, AppStream validation and ShellCheck also pass. Existing GTK/PyGObject deprecation warnings and the AppStream uppercase application-ID warning remain non-fatal.
- After the accessibility slice is committed/pushed, verify its hosted CI before any merge. The key handler is exercised under Xvfb, but the real Plasma/AT-SPI tree, screen-reader announcement and visible keyboard focus still need a live desktop pass. No live audio graph or systemd units were touched by these automated checks.


## Latest checkpoint — keyboard-accessible minimized rail (2026-10-29)

- Hosted CI [run #111](https://github.com/Roaring1/Hearth/actions/runs/40951877992) passed all six jobs for commit [`4e834c4`](https://github.com/Roaring1/Hearth/commit/4e834c487e94a7031703c2ce39835491944ae484), including GUI screenshots and installed SVG resource checks.
- The current follow-up makes minimized channel slivers native `Gtk.Button` widgets with a decorative drawing child, a `Restore {channel}` accessible name, and native GTK activation routed to the existing window restore method. This preserves the compact custom graphic while letting GTK provide standard keyboard and assistive-technology button actions.
- The current local full gate passes with **475 tests**; lint, formatting, mypy (33 source files), desktop entry, AppStream and ShellCheck pass. This new sliver change has not yet completed hosted CI; verify its run after pushing.
- No live audio graph or systemd unit was changed. Real Plasma focus traversal, visible focus styling and AT-SPI/screen-reader announcements still need manual confirmation.


## Latest checkpoint — visible keyboard focus artifacts (2026-10-29)

- Expanded the GTK screenshot artifact from six to eight production captures:
  `mixer-fader-focus.png` focuses the Game volume fader and
  `mixer-rail-focus.png` focuses the minimized Music restore control. Both
  tests assert that the widget actually acquired GTK focus before capturing.
  The test restores normal focus after the first capture so subsequent default
  states remain representative.
- The focused captures passed locally under Xvfb; the full `./scripts/check.sh`
  gate remains green with **475 tests**, Ruff lint/format, mypy (33 files),
  desktop-entry/AppStream validation and ShellCheck. GTK emits an increased but
  non-fatal number of existing PyGObject asyncio deprecation warnings because
  the screenshot test pumps the GTK main loop while capturing multiple states.
- This visual-evidence follow-up is pending its own hosted CI after push. CI #112
  on the preceding minimized-rail commit passed all six jobs. The screenshot
  suite still is not a pixel-diff baseline, and live Plasma/AT-SPI behavior is
  not claimed from a headless display.


## Latest checkpoint — native rail activation semantics (2026-10-29)

- Minimized channel slivers now extend `Gtk.Button`; their custom Cairo drawing
  lives in a decorative, non-targetable `Gtk.DrawingArea` child. This gives the
  control GTK's native button action and its standard keyboard/assistive-tech
  activation instead of manually imitating a button on a drawing area. The
  click signal still routes to the existing `MixerWindow.restore()` path.
- Added minimal button CSS to suppress the desktop theme's default fill/border
  while retaining the `.hearth *:focus` outline. This preserves the compact rail
  design while moving the focus/action semantics to the actual button.
- Focused fader and minimized-rail screenshot captures are in the visual artifact
  from CI #113. Their dimensions and screenshot validation passed; the fader
  focus outline causes a measurable pixel difference from the default ready
  screenshot. Run [#113](https://github.com/Roaring1/Hearth/actions/runs/40954289258)
  passed all six jobs on the previous screenshot-expansion commit.
- The current native-button refactor passes the full local gate with **475 tests**,
  including actual GTK window capture under Xvfb. The isolated AT-SPI tree probe
  did not complete reliably, so do not claim screen-reader name/action verification;
  live Plasma/AT-SPI is still outstanding. This change is pending its own hosted CI.


## Final rail-focus visual polish — 2026-10-29

- Artifact review found that the focused rail sliver did not have a sufficiently
  clear focus cue and the screenshot still carried the preceding fault/missing
  device styles. Added an explicit neutral border and focus border/outline to
  `button.sliver:focus`, and changed the screenshot setup to restore Laptop's
  present state and hide the fault banner before capturing the focused Music
  rail control.
- The screenshot regression now compares focused vs unfocused rendered frames
  and fails if the focused fader or rail button produces no visible pixel change.
  It waits for the existing window-size easing to settle before taking the
  rail reference image. The test suppresses only external `gi.*` DeprecationWarnings
  during its deterministic GTK main-loop pump so real project warnings remain
  visible.
- This changes only the compact rail presentation and deterministic Xvfb
  screenshot fixtures; no audio backend or systemd action is involved. The local
  targeted visual test passes; rerun the full gate and hosted CI before considering
  the focus artifact complete.


## Keyboard strip reordering — 2026-10-29

- The dotted strip-order grip is now focusable and exposes a descriptive GTK
  group label (`Reorder Game; press Left or Right to move`) instead of being a
  tooltip-only, nonfocusable drawing area. Its pointer drag source is preserved.
- Left/Right moves the focused strip by one visible position within its own
  group. It reuses `MixerWindow.reorder()` and therefore the same persistence
  path as pointer drag-and-drop; after a rebuild the idle callback returns focus
  to the moved strip's new handle. Arrows at a group's edge are a safe no-op.
- Regression coverage checks role/focusability, Left/Right reordering, edge
  boundaries, ignored unrelated keys and focus restoration. CI's GTK artifact
  now adds `mixer-reorder-grip-focus.png` and asserts the focused grip changes
  visible pixels compared with its unfocused reference frame.
- This is a new follow-up beyond commit `5216dfb`; rerun `./scripts/check.sh`,
  then verify the hosted CI run and nine-image screenshot artifact before calling
  the work complete. No audio or systemd operations are involved.


## Real-window strip-reorder regression — 2026-10-29

- Extended the actual production GTK screenshot test to invoke Left/Right on
  `vm_game`'s current reorder grip, check the real `MixerWindow.order`, and verify
  that a rebuild creates a new grip and returns GTK focus to it. A second arrow
  restores the original order and keeps focus, all with the collector/backend
  startup and settings save stubbed by the screenshot harness.
- This complements the fake-window regression: one covers bounds/persistence
  calls cheaply, while the production test now proves focus survives the real
  strip rebuild. The full `./scripts/check.sh` gate passes with **476 tests**;
  the next hosted CI run must validate this last test-only follow-up.


## Per-application slider accessible names — 2026-10-29

- App-specific volume scales now explicitly announce `{app name} volume` through
  the GTK accessible LABEL property, rather than relying on an adjacent visible
  app-name label or tooltip. The shared label helper accepts any `Gtk.Widget`,
  because both native scales and buttons use it.
- Extended the existing row-render regression to assert the volume scale's
  accessible name alongside the changing app-mute button label. Local full-gate
  and hosted CI checks are required after this focused change; no audio backend
  or systemd operation is involved.


## Setup latency accessible name — 2026-10-29

- The headset latency spin button now exposes `Routing latency in milliseconds`
  through the same GTK accessible-name helper used for volume scales and action
  controls. The production Setup-window test records the actual widget/name
  helper call and asserts the spin button's GTK role, so this isn't only a source
  grep or tooltip check.
- Local target/full gate and hosted CI should be rechecked after this isolated
  UI-only change. The latency range, selected value, save worker and restart
  behavior are unchanged; no audio graph or systemd unit is touched by the tests.


## LPD8 control accessible names — 2026-10-29

- Hardware-shaped pad and knob buttons now expose explicit accessible labels
  containing their physical number plus their current assignment (or `unassigned`).
  `show_job()` refreshes the label whenever the map changes, so the name cannot
  become stale after remapping. Visible layout and click/right-click behavior
  are unchanged.
- Added an Xvfb regression capturing the accessible LABEL property for initial,
  assigned and cleared pad/knob states. The test uses actual GTK buttons and
  intercepts the property update call, not the unreliable isolated AT-SPI probe.
- This is the next focused accessibility slice beyond `cff5bac`; run the targeted
  test and full gate before push, then validate the new hosted CI artifact.


## Contextual OUT/IN accessible labels — 2026-10-29

- Mixer route menu buttons keep the compact visible captions `OUT`/`IN`, but
  now expose contextual accessible names: `Choose where {channel} is sent` and
  `Choose what feeds {channel}`. Tooltips are kept in sync with these names.
- The production GTK/Xvfb test captures actual accessible LABEL property
  updates and asserts the Game OUT and microphone IN controls receive the
  channel-specific names. This supplements, rather than replaces, live
  AT-SPI/screen-reader verification.


## Per-channel mute action names — 2026-10-29

- Main mixer `MUTE` toggles now expose state-aware names such as `Mute Game`
  and `Unmute Chat`, even though the compact visible caption remains `MUTE`/
  `MUTED`. The accessible name follows initial state, every collector snapshot,
  successful toggles, and the rollback when a mute command cannot start.
- Regression checks verify the real GTK property updates on a working Game
  bus and muted Chat bus, plus the failure rollback path in the callback safety
  test. The full gate and hosted CI are required before this change is complete.


## Mixer knob-remap chip accessible name — 2026-10-29

- Each compact `K5`/`K7` binding chip now announces its action and destination,
  such as `Open LPD8 remapping for Game (knob K7)`, rather than only the short
  printed token. Its existing tooltip, visible chip and click handler are intact.
- The real GTK production-window test captures and asserts the chip's explicit
  accessible LABEL property alongside the route and mute control names.


## Contextual LPD8 assignment dropdown name — 2026-10-29

- The shared controller assignment dropdown now sets its GTK accessible LABEL
  whenever it is repopulated: `Choose assignment for LPD8 pad 3` or the matching
  knob/number. This makes the currently edited hardware control explicit even
  though a single dropdown is reused for all sixteen pad/knob buttons.
- The production GTK window test invokes the real `_fill_choice()` method for
  Pad 3 and Knob 5 and checks the emitted accessible name for each. Selection
  notifications remain blocked while repopulating; the test does not commit or
  restart an assignment.
- Targeted GTK, full local gate and hosted CI should be checked for this follow-up.
  No hardware script, audio graph or systemd unit is touched.


## Keyboard movement for per-app mixer rows — 2026-10-29

- Each app row is now focusable, role GROUP, and labeled with its movement
  action; the visible app name remains the pointer drag source. Left/Right moves
  the app to the adjacent visible mixer strip,
  using the same `_move_app_to()` function as pointer drop; movement beyond an
  edge is handled as a no-op and unrelated keys pass through.
- After a successful keyboard move, the next collector snapshot rebuilds the
  source/destination rows and restores focus to that app's row on the intended
  destination bus. A stale snapshot cannot clear the pending focus request while
  the stream is still present only on its old bus.
  Regression coverage checks key routing/bounds and the real production GTK test
  performs a synthetic Firefox move from Game to Chat and verifies post-rebuild
  focus without making a real `pactl` call.
- The visual capture suite now includes `mixer-app-move-focus.png` and asserts
  that the focused app row changes rendered pixels compared with the ready
  frame. The local full gate passes with **478 tests**; hosted CI on the pushed
  head is the remaining automated verification.


## Accessible live audio meter — 2026-10-29

- Each channel meter now exposes GTK's read-only `METER` role, a channel-specific
  accessible name such as `Game audio level`, and a numeric range from 0 to 100.
  Its current displayed level updates in 5-percentage-point increments instead
  of every animation frame, reducing unnecessary screen-reader announcements.
- The production-window GTK regression checks the actual meter role/name, its
  min/max/current initial values and a deterministic live update to `35%` after
  feeding a full-scale test level and advancing one smoothing tick. No audio
  input device or user session is contacted by this test.
- The targeted GTK test and full `./scripts/check.sh` pass with **478 tests**;
  verify hosted CI after the combined accessibility changes are pushed. This
  metadata change does not alter meter drawing, the collector or fader write path.


## Per-unit Setup restart labels — 2026-10-29

- Generated restart buttons keep the short visible caption `Restart` but now
  announce and tooltip the exact unit, e.g. `Restart roaring-vm-share.service`.
  The existing button handler still passes the same unit name to the restart
  worker; no service is started by the label-setting code.
- The real Setup-window GTK test injects a synthetic failed unit after capturing
  the normal Setup screenshot and asserts the emitted accessible name and
  tooltip, without connecting to systemd or clicking Restart.


## Mixer fault-banner Restart label — 2026-10-29

- The main repair-banner Restart action now names the exact service unit(s)
  derived from the missing-bus state, e.g. `Restart roaring-laptop-audio.service`,
  instead of only announcing the generic visible `RESTART` caption. Its tooltip
  stays synchronized; when no targets exist the hidden button is disarmed and
  carries the neutral name `Restart affected audio services`.
- The real GTK visual test simulates a previously present Laptop bus disappearing,
  checks that production banner logic picks the laptop service and exposes that
  accessible label, captures the banner, then restores fixture state. It never
  clicks Restart or invokes systemd.


## Setup health verdict semantics — 2026-10-29

- The dynamically updated Setup health verdict is now exposed with GTK's
  `STATUS` role. Its existing text and visibility behavior stays unchanged,
  but assistive technology can distinguish a service-health message from
  regular settings descriptions.
- The production GTK test injects a synthetic failed unit and checks the status
  role, `1 thing is wrong` text/visibility, and the unit-specific Restart label.
  It never activates Restart or connects to the real systemd service manager.
