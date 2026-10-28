# Hearth development handoff — 2026-10-28

This document is the durable checkpoint for the current repair/audit pass. It is kept in the PR branch so work can continue even when the NobaraSupervisor connection to the development PC is unavailable.

## Current GitHub state

- Repository: https://github.com/Roaring1/Hearth
- Working PR: https://github.com/Roaring1/Hearth/pull/1 — Fix GTK4 packaging, health reporting, and runtime safety
- PR status: open, mergeable, **not merged**.
- Base commit: 4600ebed949107114822827056f09d7cd0780b4e (the PR base; main has not been changed by this work).
- Branch: fix/gtk4-install-and-smoke-test
- Branch HEAD at this handoff: 0fa2a605ccb13d5e386e15c4ff90cfa5fc27c786.
- Last complete CI run: GitHub Actions run #80, commit 0fa2a605, **all jobs successful**: https://github.com/Roaring1/Hearth/actions/runs/40700465469
- The PR currently contains many small commits because GitHub's file-update endpoint made one commit per file while the development PC connector was unavailable. Prefer **Squash and merge** (or consolidate history before merging) instead of preserving the entire incremental history. Do not merge without the maintainer's decision.

## CI evidence

Run #80 passed each of these jobs:

- Lint and types: success (Ruff lint, Ruff formatting, mypy, desktop-entry and metadata validation).
- GTK4 GUI import smoke under Xvfb: success; **35 tests passed**, 415 deselected for this GUI-specific job. One harmless PyGObject deprecation warning was emitted for GLib's Unix-signal API.
- Python 3.11 tests: **415 passed**, one GI-dependent test skipped because that matrix environment does not install PyGObject, one item deselected.
- Python 3.12 tests: success.
- Python 3.13 tests: success.
- Package install smoke: success.

Several intermediate commits were intentionally pushed and CI exposed issues before the green run: a missing module import in the dump writer, an incomplete fake-thread test double, and formatting differences under the current hosted Ruff version. Those were corrected; the latest run above is the verified result. Do not use an older failed/cancelled run as the current status.

## Main findings and fixes on the PR branch

### Packaging, identity, and docs

- Corrected the GTK4 application's ID to use the package's canonical APP_ID; aligned desktop window metadata.
- Added a real GTK4 import smoke test under a virtual display, and made the CI workflow install system GTK introspection bindings where required.
- Updated stale GTK3-era README/install instructions for the actual GTK4 app.
- Marked the source-checkout hearth.py shim executable so the documented symlink-based CLI install works.

### Health/status accuracy

- An inactive hearth-ui.service (the optional mixer window) was incorrectly counted as a broken audio service by headless status, even though audio routing can work with the UI closed.
- Excluded this optional UI unit in the shared health classifier and aligned the setup window with the same logic.
- A real on-PC status check, before the supervisor connection was lost, returned **“Everything is working”** and reported all 17 monitored services healthy. It also reported the LPD8 controller connected and the Launchpad Mini controlled by Padfire. This is a historical observation, not a claim about current state while disconnected.

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

## NobaraSupervisor connection outage and local-only state

During this pass, NobaraSupervisor tool calls began failing with an MCP UNAVAILABLE / connection failed error for operations including shell execution, session status, path inspection, directory listing and text-file reads. Repeated attempts did not recover the connection. The outage is in the connector path as observed by the tool; do not infer whether the PC, the service, its network path, or MCP endpoint is the root cause without fresh diagnostics.

The following were observed or changed on the PC **before** the connection failed:

- Local checkout was under the user's GitHub projects directory, on the PR branch before the latest GitHub-side edits.
- A ~/.local/bin/hearth symlink was added to the executable in-tree CLI shim. hearth --version, hearth --paths and hearth --status worked at the time.
- The existing desktop launcher and its ~/bin/hearth-mixer wrapper were preserved; the launcher's WM-class metadata was aligned to the package app ID.
- hearth-ui.service was left stopped with Result=success; it had exited cleanly, not been found in a crash loop. No audio service or live routing configuration was intentionally restarted/changed during the audit.
- The most recent observed hearth --status output was healthy as recorded above.

Since the connector failed, the remote branch has received more commits through GitHub's repository API (including the per-app volume controls and off-thread raw-mic config write). **Do not assume the PC's local checkout has those latest commits.** It may be behind origin/fix/gtk4-install-and-smoke-test. The local workspace and actual current on-PC state cannot be verified until the supervisor connection returns.

## Remaining work, in priority order

### P0 — resume safely when the supervisor connection works

1. Reconnect to the PC and establish the exact state before changing anything:
   
       cd ~/Documents/GitHub/Hearth
       git status --short --branch
       git fetch origin
       git log --oneline --decorate -8

   Preserve unrelated/uncommitted user changes. If the tree is clean, switch/update to fix/gtk4-install-and-smoke-test; do not reset or discard unknown work.

2. Run the full local gate against the branch:

       ./scripts/check.sh

   Then exercise the GTK4 app under the real Plasma session (not only Xvfb): launch and close/reopen, adjust app volume and mute, move a listener to a different bus, toggle a route, open IN/OUT popovers during a route update, and verify LPD8 LED feedback. Confirm that no GTK freeze occurs if pactl/ALSA is briefly slow.

3. Repair existing artifact permissions only after inspecting the paths reported by hearth --paths and preserving symlinks/unusual ownership. Metadata observed during this audit showed existing Hearth directories/files with permissive modes (directory mode 0755 and file mode 0644 on the inspected paths). The PR code hardens new writes and selected startup paths, but a direct mode-repair pass on old artifacts remains pending while disconnected.

   Safe starting point after confirming actual XDG locations and ownership:

       hearth --paths
       find "$XDG_CONFIG_HOME/hearth" "$XDG_STATE_HOME/hearth" "$XDG_CACHE_HOME/hearth" -type d -exec chmod 700 {} +
       find "$XDG_CONFIG_HOME/hearth" "$XDG_STATE_HOME/hearth" "$XDG_CACHE_HOME/hearth" -type f -exec chmod go-rwx {} +

   Adjust unset/custom XDG variables using the exact paths printed by hearth --paths; inspect before running if any paths are symlinks or contain files not owned by the current user. The second command removes group/other permissions without stripping owner execute bits. Do not delete old dumps or logs.

4. Re-check the actual desktop launcher, command resolution, audio status, unit status and file modes after updating to the branch. Keep hearth-ui.service stopped unless a user-visible test explicitly requires launching the UI through that unit.

### P1 — remaining audit/review

- Inspect writes to the machine-specific config.json path separately from preferences and status dumps. Verify its mode/atomic-write behavior and add a regression if any writer can create a group/world-readable copy. Do not commit machine-specific contents, device serials, hostnames, addresses or captures to this public repository.
- Review every remaining synchronous GTK signal handler for filesystem lock/fsync, systemctl, pactl, or amidi calls. In particular keep Setup save/restart feedback accurate and make sure per-app sliders behave smoothly during high-rate snapshots.
- Consider consolidating the long PR's incremental commits before merging; a squash merge is the least invasive option.
- Do a normal-user interactive smoke session; CI/Xvfb validates imports and mocked behavior but does not prove the mixer feels right in a live Plasma/PipeWire session.
- Once those checks are complete, update this handoff with the real PC results, not assumptions. Leave PR #1 open until review/merge is deliberately requested.

## Safety boundaries

- This audit has not intentionally changed a live audio graph or restarted user routing services.
- Do not publish local status dumps, VU captures, private config contents, tokens, or full logs in public GitHub issues/PR comments. Share a redacted excerpt only when necessary.
- Do not merge PR #1 automatically. Main is unchanged by this repair branch.
