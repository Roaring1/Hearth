# Hearth Windows Port - Working Record

- **Status:** early porting work; local-only, not committed or pushed
- **Windows checkout:** `%USERPROFILE%\\Documents\\GitHub\\Hearth-Windows`
- **Local branch:** `windows-port`
- **Base:** PR #6 branch `test/gtk4-accessibility-tab-order`, commit `dbdc98ee0cf5b0ebae3b9c5182da8887a9ad9970`
- **Original Linux checkout:** untouched
- **Audio drivers, routing, RoaringMic, and Padfire:** untouched

This file is a working handoff. Update it as the port progresses.

## 1. Porting strategy

Preserve the production GTK4 interface and port the operating-system/audio infrastructure underneath it.

GTK4, PyGObject, and Cairo have been installed in MSYS2 UCRT64 on the Windows laptop. The real imports succeeded:

- Python 3.14.9
- GTK 4.24.1
- PyGObject 3.58.1
- Cairo 1.29.2

This avoids rewriting the existing GTK interface in Qt or WPF. The UI is already substantial: `src/hearth/ui/gtk4/app.py` is about 2,485 lines, with separate Setup and Remap modules, custom faders/meters, routing popovers, accessibility work, and visual tests.

The current Linux implementation is not Windows-compatible merely because GTK loads. Its backend is built around PipeWire/PulseAudio, systemd, ALSA, Unix filesystem semantics, and external companion scripts. The Windows port therefore needs explicit platform adapters and a real audio engine.

## 2. GitHub/source state

- Hearth is version 5.1.
- The main implementation is in PR #1; PR #6 is a draft accessibility/tab-order improvement on top of it.
- Both PR heads were checked and their latest CI runs were green across six jobs.
- Both PRs remain unmerged. Do not merge or push changes as part of this local port without a separate decision.
- The Linux checkout's `check.sh` passed: 506 tests, Ruff, mypy, and build.
- The current Windows clone is based on the PR #6 head, not a stale main branch.
- The Windows branch is local and separate. Its first changes are uncommitted.

## 3. What Hearth actually contains

The current production GTK4 mixer has six principal channels:

1. Game
2. Chat
3. Music
4. Laptop
5. Mic B1
6. Mic B2

The interface also presents application streams, per-stream volume/mute, routing controls, meters, health/status information, Setup, and LPD8 remapping. Some older or companion surfaces are deliberately not part of the current main window.

Important architecture:

- `collector.py`: immutable snapshot/data contract consumed by the UI.
- `audio/pactl.py` and `audio/links.py`: PipeWire/PulseAudio control and graph inspection.
- `meters.py`: live peak capture using Linux audio processes/pipes.
- `health.py`, `services.py`, `units.py`: service and health aggregation based on systemd.
- `chain.py`: Carla/plugin-chain and graph health.
- `mic.py`, `mic_routes.py`: microphone diagnostics and route configuration.
- `share.py`: PipeWire/screen-share routing.
- `midi.py`, `lpd8map.py`, `control.py`, `leds.py`: MIDI/controller integration.
- `paths.py`, `settings.py`, `logging_setup.py`: filesystem, preference, and log safety.
- `ipc/`: single-instance/control protocol. Padfire polls the control API (notably `get_sinks`); desktop actions and CLI commands also use it.
- `ui/gtk4/`: the production GTK4 UI. Keep UI logic separate from OS-specific audio/service operations.

The project intentionally separates the GTK UI from the core, but some UI flows still import legacy configuration and Linux route modules. Those seams need to be cleaned up during the port.

The repository does not contain all of the machine's companion scripts. The README and development handoff describe additional scripts/services in the user's home directories and systemd user units. Porting the GitHub repository alone will not reproduce the entire deployed Linux audio rig.

## 4. Linux reference system (read-only observations)

The Linux Hearth instance reported 18 monitored services healthy. Voice effects were active for the Stream route; screen-share readiness was reported; microphone diagnostics had no recordings yet; the LPD8 was reported connected and the Launchpad was handled by Padfire.

The live Carla project contains an approximately 11-plugin voice chain, including RNNoise, LSP Gate, an eight-band LSP parametric EQ, Calf compressor/de-esser, LSP limiter, Calf stereo tools/exciter, a bypassed Calf bass enhancer, and an LSP filter. This is a deployed external dependency/configuration, not a self-contained Hearth repository feature.

The Linux-to-Windows arrangement is distributed. Companion scripts send audio to the laptop and receive Windows-originated commands. Preserve this topology until the intended Windows role is settled.

## 5. Existing Windows components — preserve them

### RoaringMic

A running Windows companion exists at `%LOCALAPPDATA%\\RoaringMic\\RoaringMic.ps1`. It is an approximately 838-line PowerShell tray application with embedded C# WinMM audio playback.

Observed behavior:

- It owns the live UDP listener on port 46001.
- It receives 48 kHz mono PCM and writes it to the VB-Audio Cable playback endpoint.
- It contains a packet jitter buffer, playback-queue recovery, and level monitoring.
- It sends commands to the Linux command receiver on UDP 46002 (including source swap/mute operations).
- It is registered as the `RoaringMicReceiver` scheduled task.
- The receiver process was running during this audit.

Do not stop, replace, or modify it. It is already doing real work.

### Padfire

Padfire is installed per-user as a separate Windows-native application and was running during the current audit. A Launchpad Mini PnP device was visible, but successful MIDI communication has not been verified in this session. Treat Padfire as the existing device owner and do not open the Launchpad from Hearth until there is an explicit ownership/coordination design.

The checked-out Linux Padfire repository is an older v2.3 branch; the Windows-native development work is separate. No Hearth integration has been verified. Keep the two applications independent for now.

### Audio drivers and endpoints

The laptop has VB-Audio Cable and Voicemeeter VAIO virtual endpoints/drivers, alongside Realtek, NVIDIA, Astro A50, and Steam Streaming endpoints.

Important correction: the Voicemeeter virtual drivers are present, but the full Voicemeeter console application/Remote API was not found running or installed in its expected application location. Do not assume a functioning Voicemeeter mixer/API. Do not install or update audio drivers as part of the GTK proof of concept.

The Windows audio endpoint list has not been validated by playing or capturing test audio.

## 6. Windows runtime and experiments

Installed on the Windows laptop:

- MSYS2 (WinGet package)
- UCRT64 GTK4, Python, PyGObject packages

The following tests passed:

- GTK4/PyGObject/Cairo imports.
- A local named-pipe request/response using Python's `multiprocessing.connection.Listener/Client` with `AF_PIPE` and an authentication key.
- MSVCRT byte-range locking across separate processes.
- SID-based `icacls` ACL recipe on a temporary directory and child file; the test directory was removed.

The Windows Python capability audit found:

- `os.getuid`: unavailable.
- `os.O_NOFOLLOW`: unavailable.
- `socket.AF_UNIX`: unavailable in this MSYS2 Python.
- `os.fchmod`: present, but Windows mode bits are not a substitute for a private DACL.
- `msvcrt` and named pipes: available and tested.

The actual Hearth app, Setup, and Remap modules now import successfully on Windows after a small local-only patch added `hearth.platform.locking` and replaced POSIX `fcntl.flock` imports in four modules. The application has **not** been launched.

### Windows clone modifications

The local branch currently contains:

- New `src/hearth/platform/locking.py`:
  - POSIX retains `fcntl.flock`.
  - Windows uses MSVCRT byte-range locking on byte zero.
- Updated `ipc/path_lock.py`, `lpd8map.py`, `mic_routes.py`, and `legacy_core.py` to use that abstraction.

These edits are a first compatibility step, not a complete Windows implementation. Review the diff and add Windows-specific tests before committing.

## 7. Security and IPC requirements

The Linux implementation carefully validates ownership, regular files, hard-link counts, symlinks, private directories, and atomic settings writes. Preserve the intent of these checks.

The Windows `%LOCALAPPDATA%` parent directory has an inherited `CodexSandboxUsers` read/execute ACE. Therefore Hearth must not blindly inherit the parent DACL for private state.

A temporary test proved this SID-based ACL pattern works:

- Disable inherited ACLs on the Hearth root.
- Grant Full Control to the current user's SID, SYSTEM (`S-1-5-18`), and Administrators (`S-1-5-32-544`).
- Child files inherit only those entries.

Derive the current SID at runtime (do not hardcode this laptop's SID). Apply the protected DACL only to Hearth's own root, not to the entire AppData tree or other applications.

MSYS2 Python has no `socket.AF_UNIX`. A named-pipe transport using `multiprocessing.connection` with `AF_PIPE` was tested successfully. A Windows IPC adapter should preserve the current request/response API and use a per-user pipe name plus authentication. The app's single-instance lifecycle, PID fallback, Padfire `get_sinks` polling, CLI `show/quit`, and shutdown behavior must be preserved.

## 8. Initial Windows porting gaps identified before implementation (historical)

1. **Paths/security:** XDG defaults and `os.getuid` remain in `paths.py`; config/state/cache/runtime should move beneath a private LocalAppData Hearth root.
2. **IPC:** current server/client use Unix-domain sockets; implement named pipes and preserve the protocol.
3. **Settings/logging:** replace POSIX owner/mode assumptions with the protected Windows root ACL; retain atomic replacement and hard-link/reparse-point checks.
4. **Audio engine:** replace `pactl`, `pw-link`, `pw-dump`, `parec`, and null-sink assumptions with a Windows backend.
5. **Application streams:** preserve per-app session volume/mute and route changes. Windows per-session endpoint reassignment is not a direct public Core Audio equivalent of `pactl move-sink-input`; investigate a documented/maintainable implementation before promising parity.
6. **Virtual buses:** determine how to provide the six logical channels. Existing VB-Audio endpoints and the RoaringMic cable are not proof that the complete bus topology is available.
7. **Meters:** replace Linux pipe/select capture with WASAPI/Core Audio capture or session/endpoint meters.
8. **Microphone/effects:** decide how to reproduce the Carla chain on Windows (Carla Windows, VST host, or equivalent plugins) and preserve the deployed parameters.
9. **MIDI/controller:** add Windows MIDI discovery/control without competing with Padfire for the Launchpad; LPD8 support needs a separate device mapping/backend.
10. **Services/health:** replace systemd units and Linux process probes with Windows Audio services, app-managed workers, scheduled tasks, and endpoint/process health.
11. **Share/screen audio:** replace PipeWire graph/portal assumptions with a Windows virtual-device/OBS-compatible route.
12. **Packaging/CI:** add a reproducible MSYS2 UCRT64 launcher/installer and Windows CI; do not ship a one-off machine-specific environment as the final product.

## 9. Safe next sequence

1. Finish a read-only audit of the UI/backend contracts and existing companion protocols.
2. Implement Windows path roots and protected ACL handling.
3. Implement named-pipe IPC with the existing command protocol.
4. Add Windows tests for locking, paths, ACL setup, IPC, and lifecycle.
5. Build a Windows audio-backend interface and a read-only endpoint/session inventory first.
6. Only after inventory works, design volume/mute/routing and virtual buses.
7. Add meters, microphone processing, MIDI, health, and setup workflows.
8. Test with virtual/test audio before any real device routing changes.
9. Keep RoaringMic and Padfire untouched and document all integration points.
10. Add Windows CI and packaging after the local implementation is stable.

## 10. Decision still needed

The Windows laptop currently behaves as a secondary endpoint in a distributed Linux/Windows audio arrangement, while the request is to recreate Hearth inside Windows.

Before installing a full mixer/driver stack or changing audio routing, clarify whether Windows Hearth should be:

- a **standalone local mixer/host** managing Windows applications and devices, or
- a **Windows control/companion client** for the existing Linux Hearth host, preserving the current network audio and RoaringMic arrangement.

The GTK/UI and filesystem/IPC work can proceed independently of that decision. No audio-driver or routing changes have been made.

## 11. Verified progress — 2026-10-29 late session

The Windows path/persistence layer has advanced beyond the earlier checkpoint:

- `paths.py` now selects `%LOCALAPPDATA%\\Hearth` as the Windows data root, with `config`, `state`, `cache`, `runtime`, and `state\\dumps` beneath it. `%APPDATA%` remains available as the roaming configuration base for legacy/theme lookups.
- The real `%LOCALAPPDATA%\\Hearth` directory was confirmed absent before creation, then created by `paths.ensure_dirs()`.
- Its DACL was inspected with `icacls`: only the current user SID, SYSTEM, and Administrators have access. The inherited `CodexSandboxUsers` ACE is absent. The `config` child inherits the protected DACL.
- `windows_fs.py` contains SID discovery, reparse-point detection, protected root ACL setup, private-file ACL handling, and private file-descriptor validation. It currently has duplicate `ensure_private_file` definitions; consolidate them and add an outside-root rejection test before considering the helper complete.
- Settings persistence was exercised against a disposable file inside the private config directory: two consecutive saves and a load round-trip succeeded; the file had one hard link; the temporary file was removed.
- File logging was initialized successfully, handlers were closed, and the temporary log was removed.
- The real `python hearth.py --paths` CLI entry point passed and printed the Windows locations.
- `paths.ensure_dirs()`, default settings loading, `logging_setup.configure(to_file=False)`, and imports of the production Mixer, Setup, and Remap modules passed.
- `git diff --check` passed. The Windows port branch remains local and uncommitted.

Additional Windows audio inventory clarification:

- `C:\\Program Files\\VB` contains VB-Audio Cable and Voicemeeter VAIO driver/control-panel folders, including `VBCABLE_ControlPanel.exe` and `VBVoicemeeterVAIO_ControlPanel.exe`.
- This is driver/control-panel evidence, not evidence of the full Voicemeeter mixer console or Remote API. No Voicemeeter mixer was installed or started.

Still not complete:

- The production GUI has not been launched.
- The current IPC implementation still needs a Windows named-pipe server/client adapter.
- The Windows audio backend and logical virtual-bus topology are not implemented.
- No audio drivers, endpoints, routing, RoaringMic, or Padfire were modified.

The next safe technical gate is to consolidate and test the private-file helper, then implement and test Windows IPC while keeping all audio operations read-only.

## 12. Verified Windows port state — 2026-10-30

This section supersedes the earlier "known gaps" and "still not complete" statements above where they conflict. Historical notes are retained to show how the port progressed.

### Runtime and checkout

- Windows checkout remains %USERPROFILE%\Documents\GitHub\Hearth-Windows, branch windows-port, based on dbdc98e (test/gtk4-accessibility-tab-order).
- The branch tracks origin but is still local, uncommitted, and unpushed. The original Linux checkout has not been modified.
- Windows runtime is MSYS2 UCRT64 Python 3.14.9 with GTK 4.24.1, PyGObject 3.58.1, and Cairo 1.29.2.
- A local .venv-win uses --system-site-packages so it can see GTK/PyGObject installed by MSYS2. Pycaw 20260927 and sounddevice 0.5.6 were installed in this venv. Pycaw is used; sounddevice currently cannot import its _sounddevice_data helper on this runtime and is not part of the active implementation.
- Project metadata remains Linux-oriented (Operating System :: POSIX :: Linux, PipeWire description, no runtime dependencies). The venv is a development setup, not a distributable Windows installation.

### Windows filesystem and locking

- paths.py selects %LOCALAPPDATA%\Hearth by default, with config, state, cache, runtime, and diagnostic dumps beneath it. Absolute XDG overrides are honored for test/caller isolation.
- windows_fs.py derives the current user SID at runtime, protects the Hearth root with a DACL for that SID, SYSTEM, and Administrators, and validates regular files, reparse points, hard-link counts, root containment, and open handles.
- The actual root was confirmed absent before creation. Its DACL and inherited child DACL were inspected; the parent CodexSandboxUsers ACE is not inherited into Hearth's private tree.
- Settings writes use a process-local reentrant mutex plus a persistent cross-process lock file, then atomic replacement. This was added after a real concurrent-writer WinError 5 surfaced on Windows.
- Logging, PID files, diagnostic dumps, legacy config, mic-route config, and LPD8 file paths have Windows-aware private-file handling. POSIX mode/UID assumptions remain in Linux branches.
- Thread serialization was added around settings, microphone-route, and LPD8 read/modify/write transactions after concurrency tests exposed same-process races.

### IPC

- Windows has src/hearth/ipc/windows_pipe.py, using multiprocessing.connection with AF_PIPE, a per-user pipe name derived from the current SID, and a random 256-bit authentication key stored in the private runtime directory.
- A non-blocking MSVCRT byte-range lock protects single-instance ownership. Linux retains its Unix-socket implementation.
- The existing line-framed UTF-8 command protocol is retained. Round-trip, large reply, handler-error recovery, second-instance rejection, and shutdown cleanup were exercised on Windows.
- The production IPC adapter is wired into ipc.server and ipc.client; the hidden GTK smoke harness deliberately disabled production IPC so it would not claim the real application's instance slot.

### Native audio — read/control layer only

- windows_audio.py uses Pycaw/Core Audio. It reads endpoint state, app audio sessions, endpoint peak meters, and the Windows default render endpoint.
- The current logical strip mapping is:
  - vm_game → Voicemeeter Input (VB-Audio Voicemeeter VAIO)
  - vm_chat → Voicemeeter AUX Input (VB-Audio Voicemeeter VAIO)
  - vm_music → Voicemeeter VAIO3 Input (VB-Audio Voicemeeter VAIO)
  - laptop_audio → the current Windows default playback endpoint
  - mic_b1 → Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)
  - mic_b2 → Voicemeeter Out B2 (VB-Audio Voicemeeter VAIO)
- The Laptop mapping follows the live default playback endpoint rather than assuming Realtek. It was observed as the Steam Streaming Speakers endpoint during the read-only inventory.
- Windows endpoint volume/mute and per-app session volume/mute are exposed through the pactl compatibility layer. Session IDs include the endpoint ID and the Audio Session Instance Identifier, so the correct endpoint/session is located again when a user changes an app control.
- Windows per-app session volume is intentionally limited to 0–100%: ISimpleAudioVolume uses a 0.0–1.0 scalar. App rows do not advertise cross-endpoint drag/keyboard moves because a safe native reassignment layer is not implemented.
- PID-0 Windows System Sounds pseudo-sessions are filtered rather than displayed as application rows.
- Endpoint/session meters use IAudioMeterInformation.GetPeakValue(); no capture stream is opened. The Linux parec worker remains unchanged on Linux.
- collector.py now delegates to this native snapshot source on Windows. The GTK mixer and meter worker consume the same existing Snapshot/StreamState contracts.
- The following remain deliberately unsupported and fail closed on Windows: PipeWire module graph inspection, loopback creation/removal, default-endpoint mutation, and moving an app session between endpoints. The route popovers show this limitation rather than reporting a fake empty route graph.
- The six logical endpoint mappings are based on the installed VB-Audio virtual endpoint names. They are not proof of a functioning Voicemeeter mix engine. The full Voicemeeter console/Remote API was not verified as installed or running, and no audio playback/capture/routing was tested.

### Setup, Remap, and controller ownership

- Setup and Remap now construct successfully with GTK 4 on Windows.
- Setup explicitly identifies the native Core Audio backend and disables Linux-only systemd, PipeWire/Carla, ALSA headset, and Moonlight router actions. It does not start the Linux unit-scan polling loop on Windows.
- The Remap window retains its eight-pad/eight-knob drawing but is inert on Windows and explains that Padfire owns the LPD8 connection. It does not open MIDI, read/write the Linux ~/bin/lpd8_mixer.sh mapping, query systemd, or restart the LPD8 unit.
- The main mixer service-restart banner also fails closed on Windows. The services.py wrapper returns unknown for Linux units and never spawns systemctl.
- RoaringMic and Padfire remain untouched. No drivers, audio endpoints, device ownership, routing, or existing companion processes were changed.

### GUI verification — hidden only

- The production MixerWindow was constructed in the existing interactive desktop session and allowed to consume several Core Audio snapshots. It was never presented; the harness disabled IPC, then quit and verified collector and meter thread shutdown.
- The main mixer test reported GTK 4, all expected endpoint states, no absent logical channels, a verified capture-device inventory, and routes=None (unknown/unavailable rather than empty).
- A separate hidden test constructed both SetupWindow and RemapWindow, exercised their Windows-guarded entrypoints, intercepted service calls, and verified no window became visible, no Setup poll/scan began, all Linux-only controls stayed disabled, all 16 LPD8 controls stayed inert, and no mapping-write callback ran.
- These are hidden widget-tree/backend integration tests, not a visible user-facing launch or visual acceptance test.
- The extended hidden MixerWindow test also verified the 100% app-session slider maximum, disabled app-move affordances, disabled LPD8 bind chip, explicit unavailable route hints, and clean shutdown. Only synthetic app-session rows were injected; no real session was changed.

### Test evidence

- Dedicated filesystem/IPC/persistence Windows regressions passed, including protected ACL checks, hard-link rejection, cross-process locks, named-pipe lifecycle, PID lifecycle, and atomic settings save/load.
- Dedicated filesystem/IPC/persistence regressions and native Core Audio tests are included in the complete Windows test suite.
- The Windows app-session slider is capped at 100% because Core Audio's ISimpleAudioVolume is a 0.0–1.0 scalar. The final setter clamps independently; a mocked boundary test verifies 150% becomes 1.0 and negative input becomes 0.0.
- Windows app rows no longer expose drag or keyboard moves between endpoints. The internal move handler fails closed. Per-app volume/mute remains available; the visible UI communicates that endpoint reassignment is not implemented.
- Full non-GUI test suite after all current changes: **383 passed, 85 skipped, 56 deselected in 40.77 seconds**. Skips are POSIX-only PulseAudio, Unix socket, systemd, Linux shell, and UID/mode-bit expectations replaced by Windows-native coverage.
- Focused Windows Core Audio suite: **8 passed**, including endpoint/session state, inventory, peak meter worker, fail-closed routing, and the session-volume scalar boundary.
- Static checks: **Ruff clean** across src/tests; **mypy clean** (Success: no issues found in 39 source files); Python compileall clean across src/tests.
- Packaging: python -m build successfully produced hearth-5.1.0.tar.gz and hearth-5.1.0-py3-none-any.whl using Hatchling 1.33.0. This confirms the build backend works; it does not constitute a tested Windows installer. Project classifiers, runtime dependencies, companion components, and installation lifecycle still need a Windows-specific design.
- Final git diff --check passes. The branch remains local and uncommitted/unpushed.

### Remaining work, in priority order

1. Run the full non-GUI suite again after the service and Setup/Remap changes; run lint/type/build checks in the UCRT64 environment where supported.
2. Inspect/review all Windows diffs for edge cases, especially session volume range (Windows Core Audio scalar is 0–1), default endpoint refresh, COM apartment lifetimes, and stale meter samples when sources disappear.
3. Fix the sounddevice packaging issue only if a capture backend is actually needed; Core Audio endpoint meters currently work without it.
4. Decide whether Windows is intended to be a standalone mix host or a companion client for the Linux host. The current work is a native endpoint/session read/control layer, not a complete routing engine.
5. Design and implement an actual Windows virtual-bus/routing layer before advertising per-app moves, mic sends, loopbacks, or share audio. Keep all route actions disabled until tested.
6. Port the external Carla/plugin voice chain or define its Windows equivalent; document plugin versions and exact parameters before migrating.
7. Define the Windows health/service model (Windows Audio service, companion processes, endpoint presence, worker status) without pretending systemd units exist.
8. Resolve Windows packaging and delivery: a reproducible MSYS2 installer/launcher, dependency pinning, user data migration/backup, app icon/desktop entry, upgrade/uninstall, and Windows CI. The current .venv-win is not a deployable package.
9. Only after read/control and routing designs are complete, perform user-approved real playback/capture and visible UI acceptance testing. Do not interfere with the currently running RoaringMic or Padfire applications.

**Current safety boundary:** no visible Hearth window has been opened, no real audio setter has been invoked by the smoke tests, no playback/capture test has been performed, and no device routing or driver changes have been made. Synthetic test rows and mocked setters are used for control-boundary coverage.

## 13. Final Windows verification checkpoint — 2026-10-30

This section supersedes any earlier test count or static-check status in section 12.

- Full Windows non-GUI test suite: **386 passed, 85 skipped, 56 deselected in 35.21 seconds**. A bounded supervisor job captured exit code 0 and the complete pytest summary.
- Strict mypy: **Success: no issues found in 39 source files**.
- Focused final Windows Core Audio/filesystem/IPC/service suite: **26 passed, 4 POSIX-only skips**. Gdk 4.0 is explicitly required before importing Gdk in the GTK widget module; the PyGI version warning is gone.
- Ruff: **All checks passed** across src and tests.
- compileall -q src tests: passed.
- git diff --check: passed.
- Hatchling build: both hearth-5.1.0.tar.gz and hearth-5.1.0-py3-none-any.whl built successfully after the final code changes.
- Hidden GTK mixer smoke test passed against the actual production widget tree: all six logical strip states were present; the endpoint fader maximum and synthetic app-row slider upper bound were both 100%; a synthetic 142% value was clamped to 100%; cross-endpoint movement returned False; no app-move handle was created; the Game LPD8 bind chip was disabled; output/input route popovers explained the unsupported Windows routing graph; collector and meter workers stopped cleanly.
- The Core Audio session-volume mock test verified requested values above 100% are passed to ISimpleAudioVolume as 1.0, and negative values as 0.0. No real app session was changed.
- The earlier hidden Setup/Remap smoke test also passed: no window presented; Setup polling/scanning stayed off; Linux-only controls were disabled; all eight pads and eight knobs remained inert; Padfire ownership was clearly explained; no systemd, MIDI, mapping-write, or audio actions occurred.
- Ruff and mypy fixes preserve Linux behavior: POSIX-only getuid, fchmod, and AF_UNIX use narrow attr-defined annotations inside Linux branches; Windows uses its SID/DACL, MSVCRT, and AF_PIPE implementations.
- Four obsolete test process trees from earlier timed-out PID test invocations, plus one associated trace helper, were confirmed by command line and creation time and then cleaned up by exact PID. No user applications or companion audio processes were targeted.

### Final implementation boundary

The branch still represents a native Windows endpoint/session mixer foundation, not a complete replacement for Linux Hearth. Endpoint/session readback, endpoint/session level and mute setters, peak metering, filesystem security, named-pipe IPC, and GTK presentation are implemented and tested. Cross-endpoint app movement, PipeWire route-graph operations, virtual-bus creation, microphone sends/effects, share audio, full health/service management, visible UI acceptance, real playback/capture validation, and a reproducible end-user installer remain open.

The branch is still windows-port, local, uncommitted, and unpushed. Do not claim the wheel is a finished Windows installer; the project metadata still describes the Linux/PipeWire application and needs a deliberate platform/packaging pass before public Windows distribution.

## 14. Resumed Windows verification and routing checkpoint - 2026-10-30

This resumed-session entry supersedes earlier test counts and formatting/build status where the checks below were repeated later.

### Repeated verification

- Full Windows non-GUI suite after the final Ruff formatting changes: **386 passed, 85 skipped, 56 deselected in 34.11 seconds**. The bounded supervisor job captured a real process exit code of 0.
- Test execution used the checkout's MSYS2 UCRT64 Python 3.14.9 environment and the explicit src/ import path. The repository-root hearth.py compatibility shim must not shadow src/hearth during pytest or isolated imports.
- Ruff lint: **All checks passed** for src and tests.
- Ruff format: **85 files already formatted**. Eight staged Python files were mechanically formatted and restaged.
- Strict mypy: **Success: no issues found in 39 source files**.
- Python compileall: passed for src and tests.
- git diff --cached --check: passed.
- Fresh Hatchling wheel and source distribution builds passed: hearth-5.1.0-py3-none-any.whl (188,772 bytes) and hearth-5.1.0.tar.gz (5,266,119 bytes).

### Hidden GUI acceptance

- Repeated the Mixer smoke through the interactive-user scheduled-task wrapper, not directly in the SSH service desktop. The wrapper reported TASK_LAST_RESULT=0, its outer supervisor job exited 0, and the temporary scheduled task was returned to Ready/unregistered by its finally block.
- The actual production MixerWindow was constructed/refreshed while remaining invisible (visible_window=false). The six logical states were read; endpoint fader maximum and synthetic application slider maximum were 100; synthetic 142% was clamped to 100; cross-endpoint move returned false; no app-move controls were exposed; the Game LPD8 binding remained disabled; route/capture hints accurately reported unsupported functionality; collector and meter workers shut down.
- Only read-only endpoint/session inventory and peak reads were used. A synthetic app-session row drove the slider boundary assertion. No real endpoint/session setter, playback, capture, or routing action was invoked.
- The earlier hidden Setup and Remap construction checks remain recorded in section 13. Their Windows guards and Padfire/LPD8 inertness were not changed during this resumed session.
- A direct SSH invocation of GTK was deliberately rejected as an acceptance method: the non-interactive SSH desktop produced native GTK access violations even for a tiny standalone Gtk.Window/Application. The established interactive hidden-task path completed successfully. This was a test-context distinction, not evidence of a Core Audio regression. Separate read-only Core Audio probes (import, single/repeated peaks, single/repeated inventory collection, and standalone meter worker) all exited 0.

### Routing architecture decision record

- Added docs/WINDOWS_ROUTING_DESIGN.md. It documents why the current Core Audio session/endpoint interfaces cannot reassign another process's already-open stream and why Windows routing remains fail-closed.
- The leading standalone option for read-only investigation is the official Voicemeeter Remote API, conditional on verifying that the full engine and architecture-matched API are actually installed. Existing VB-Audio endpoint names alone do not prove that.
- The alternative is a Windows control client for the existing Linux Hearth routing host. A custom virtual audio driver is a separate long-term driver/engine project, not an incremental Python change.
- No Voicemeeter installation/start/reconfiguration, driver work, default-endpoint change, loopback, stream capture, or live audio mutation was performed. The design record is research only.

### Current Git state

- Branch: windows-port; HEAD remains dbdc98ee0cf5b0ebae3b9c5182da8887a9ad9970.
- 38 files staged: 2,546 insertions and 134 deletions. This includes the Windows port, the updated Windows port handoff, and docs/WINDOWS_ROUTING_DESIGN.md.
- No unstaged changes. Staged whitespace check passed.
- No commit, push, or merge. The Windows branch remains local-only; the original Linux checkout is untouched.

### Remaining substantial work

1. Complete the read-only Voicemeeter engine/API installation and capability discovery, without starting or reconfiguring it.
2. Decide between standalone Windows routing and a Windows companion for the Linux host.
3. Define a provider capability contract and mock routing model before enabling route UI.
4. Continue health monitoring, packaging/installer design, and visible UI acceptance only after a separate safe test plan.
5. Keep real playback/capture, endpoint/session changes, drivers, RoaringMic, Padfire, and MIDI out of scope until explicitly approved.

- Read-only Voicemeeter inventory update: the inspected 64-bit vendor tree is C:\Program Files\VB and contains only CABLE and VBVoicemeeterVAIOs. Its recursive filename scan found VBVoicemeeterVAIO_ControlPanel.exe and VBVoicemeeterVAIO_Setup_x64.exe, but no full Voicemeeter mixer executable or VoicemeeterRemote*.dll. The conventional 32-bit VB tree and common Voicemeeter engine/API paths checked were absent. This is scoped to the inspected vendor tree and common paths, not an exhaustive scan of every disk. Do not run the VAIO setup utility as a substitute for the full mixer engine.

## 15. Explicit Windows ACL grant repair - 2026-10-30

A focused temporary-directory reproduction found a security gap in the previous ACL-repair implementation: `icacls /inheritance:r /grant:r` removed inherited sandbox access and replaced grants for the three expected principals, but left a pre-existing explicit `Everyone:F` ACE untouched. The test used a disposable directory and cleaned it up; no existing Hearth user data or live machine directory was changed by the reproduction.

The Windows filesystem helper now calls a checked `icacls /reset` step before applying the SID allow-list:
- App-owned root repair uses recursive reset and then recursively applies the protected DACL to its existing tree.
- Explicit caller/test directories outside the app root are reset and resecured at that directory only, preserving the documented boundary against recursively changing caller-owned trees.
- Individual private files have their old ACL reset before the non-inheriting user/SYSTEM/Administrators file DACL is applied.
- A failure to reset the ACL raises `PermissionError`; the helper does not continue as though the file or directory were secured.

Added Windows regression `test_private_directory_and_file_remove_explicit_everyone_aces`. It gives a temporary app root an inheritable Everyone full-control grant, gives an existing descendant its own explicit Everyone grant, then calls the recursive root repair and checks both ACLs. It separately injects an explicit Everyone grant on a standalone file and checks the single-file repair path. All three targets lose Everyone access; the root and descendant also lose inherited CodexSandboxUsers access. The expanded test and all other Windows-only port regressions pass: **11 passed**.

### Post-hardening verification

- Full Windows non-GUI suite: **387 passed, 85 skipped, 56 deselected in 37.75 seconds**, actual process exit code 0.
- Ruff lint: all checks passed.
- Ruff format: 85 files already formatted.
- Strict mypy: no issues in 39 source files.
- Python compileall: passed.
- Staged diff whitespace check: passed.
- Fresh Hatchling distributions built successfully: `hearth-5.1.0-py3-none-any.whl` and `hearth-5.1.0.tar.gz`. Their exact byte sizes are recorded in the final local build output.

The ACL change is staged alongside its regression. The branch remains local, uncommitted, and unpushed. No visible window, real audio setter, capture/playback, driver, endpoint routing, Voicemeeter configuration, RoaringMic, Padfire, or MIDI action was performed during this fix.

## 16. Windows-local read-only health reporting - 2026-10-30

The CLI previously routed `hearth --status` through the Linux/PipeWire health composer even on Windows. That path asked Linux-specific systemd and desktop-portal questions, producing unknown results instead of describing what the Windows port actually knows.

Added `src/hearth/windows_status.py` and routed the Windows `--status` branch to it:
- Reads `Audiosrv` (Windows Audio) and `AudioEndpointBuilder` using the Service Control Manager's query-only `OpenServiceW(SERVICE_QUERY_STATUS)` and `QueryServiceStatusEx` APIs. No service start/stop/change API is called.
- Reads the existing Core Audio snapshot for default playback endpoint, mapped Hearth playback endpoints, active capture endpoint inventory, and application-session inventory.
- Reports unavailable probes as `unknown` and includes the error; it does not turn a failed probe into a healthy result.
- Separates local Windows endpoint health from the unimplemented routing graph, virtual mixer engine, app-to-bus reassignment, microphone processing, and actual playback/capture signal verification.
- Linux retains its original `hearth.status` path; only the Windows CLI branch is redirected.

### Read-only result from the Windows laptop

The actual `python -m hearth --status` command exited 0 and reported:
- Windows Audio service: running.
- Audio Endpoint Builder: running.
- Default playback: Laptop (Windows default).
- Capture endpoint inventory: 11 endpoints enumerated.
- Application audio sessions: 0 at the moment of the probe.
- Mapped Hearth playback endpoints: 4/4.
- Virtual mixer engine: explicitly not verified by endpoint inventory.
- Routing graph and app-to-bus reassignment: unavailable/not implemented.
- Playback/capture signal testing: not performed.

This is intentionally a local Windows inventory report. It does **not** claim that the VB-Audio endpoint drivers are backed by a running Voicemeeter mix engine, that the microphone carries signal, or that application routing is functional.

### Regression coverage

- Added a platform-independent CLI dispatch test proving Windows `--status` does not call the Linux composer.
- Added Windows-only synthetic-inventory tests for the successful report shape and for service/Core Audio probe failures degrading to unknown.
- Focused CLI plus Windows platform tests: **36 passed, 2 POSIX-only skipped**.
- Full Windows non-GUI suite including the new health-report work: **390 passed, 85 skipped, 56 deselected in 38.28 seconds**, actual process exit code 0.
- Post-change strict mypy: **no issues in 52 source files**; Ruff lint and formatting clean; compileall passed.
- Final Hatchling wheel and source distribution rebuilt; archive inspection confirmed `hearth/windows_status.py` is present in both.
- Ruff check: clean.
- Ruff format: **86 files already formatted**.
- Strict mypy: **no issues in 52 source files**.
- Python compileall: passed.

No endpoint/session setters, device routing, Windows service mutations, playback, or capture were invoked by this feature.

## 17. Recursive Windows ACL tree-safety hardening - 2026-10-30

A second disposable reproduction found that recursive `icacls /reset /T` could change an unrelated external file's ACL when a hard link to that file was placed beneath the Hearth app root. The prior single-file validator already rejected hard links, but recursive root repair did not inspect descendants before traversing them.

The Windows filesystem helper now validates the entire app-owned tree before any recursive ACL operation:
- Walks with `os.scandir` without following directory links.
- Rejects all Windows reparse points (including directory junctions) before recursion.
- Uses `Path.lstat()` to check file link counts and rejects files with more than one hard link.
- Rejects non-regular entries rather than changing ACLs on special objects.
- Performs no recursive ACL reset or grant if any descendant fails validation.
- Caller-supplied directories outside the app root now receive a directory-only ACL update; Hearth does not recursively rewrite caller-owned trees.

A portability detail surfaced during the new work: this MSYS2 Python build reports `st_nlink=0` from `DirEntry.stat(follow_symlinks=False)` even on ordinary files. `Path.lstat()` returns the correct Windows link count and is used for the safety check.

Added Windows regressions:
- `test_private_root_rejects_hardlinked_descendants_without_changing_external_acl` creates an external file with an explicit Everyone grant, hard-links it under a disposable root, and verifies repair refuses the tree while the external ACL remains unchanged.
- `test_private_root_rejects_reparse_point_descendants` creates a real directory junction and verifies repair refuses it before recursion.

The Windows platform/security/IPC module now passes **15 tests**, including both new cases. The full Windows non-GUI suite passes **392 passed, 85 skipped, 56 deselected in 37.30 seconds**, with process exit code 0.

No existing user data, Hearth log, virtual device, service, or live audio configuration was changed by the reproductions; all unsafe-path cases used disposable temporary directories and cleaned them up.

## 18. Windows runtime dependency and MSYS2 packaging contract - 2026-10-30

A packaging audit found that the wheel could build while the project declared `dependencies = []`, despite the native Windows Core Audio provider importing Pycaw at runtime. The installed development venv happened to have Pycaw already, masking this clean-install gap.

Updated `pyproject.toml`:
- Declares `pycaw>=20260927; sys_platform == 'win32'`.
- Leaves Linux pip dependencies unchanged.
- Pycaw's installed metadata declares `comtypes>=1.4.8` and `psutil>=5.9.0` transitively.
- Adds the Microsoft Windows OS classifier and changes the package description to identify Windows as an experimental Core Audio backend, not a complete Linux feature replacement.

Added a Windows regression that asserts the project metadata contains exactly the conditional Pycaw requirement. The wheel archive was inspected directly:
- `Requires-Dist: pycaw>=20260927; sys_platform == 'win32'`
- PEP 508 marker evaluates true for `sys_platform=win32` and false for `sys_platform=linux`.
- The source archive contains the same marker in its pyproject.

### MSYS2 runtime boundary

The actual `.venv-win/pyvenv.cfg` uses the MSYS2 UCRT64 interpreter and `include-system-site-packages = true`. That is deliberate: PyGObject/GTK4, GLib, GDK Pixbuf, GObject Introspection, Cairo, COM bindings, and psutil are provided by the MSYS2 UCRT64 package set; Pycaw is installed into the project venv using pip. The shared site packages satisfy Pycaw's transitive comtypes/psutil requirements.

Added `docs/WINDOWS_PACKAGING.md` with the observed MSYS2 package names/versions, this venv topology, the PEP 508 dependency, and the remaining clean-machine/installer work. The inventory is observational, not yet a pinned bootstrap manifest. The wheel remains a Python package and is not a standalone installer.

### Verification

- Focused Windows platform tests after adding the metadata regression: **16 passed**.
- Full Windows non-GUI suite: **393 passed, 85 skipped, 56 deselected in 37.65 seconds**, actual process exit code 0.
- Ruff lint clean; Ruff format: **86 files already formatted**.
- Strict mypy clean across **52 source files**.
- Wheel and source distribution rebuilt successfully.
- Archive inspection confirmed the Windows marker in wheel metadata and sdist project metadata, and evaluated the marker for Windows and Linux.
- Fresh temporary UCRT64 venv install: pip installed the built Hearth wheel normally, fetched Pycaw 20260927, reused system-provided comtypes 1.4.17 and psutil 7.2.2, and successfully ran the installed python -m hearth --status command (exit 0). The temporary venv was removed.

No audio devices, services, endpoints, driver state, or playback/capture paths were changed by this packaging work.

## 19. MSYS2 UCRT64 Windows bootstrap helper - 2026-10-30

Added two scripts under `scripts/`:

- `windows_bootstrap.ps1` checks the installed MSYS2 UCRT64 package set, validates the venv's base interpreter and `include-system-site-packages = true`, creates the venv only when missing, installs the latest local Hearth wheel using pip, and calls the import probe.
- `windows_runtime_probe.py` imports GTK4/PyGObject, Pycaw, COM, psutil, and Hearth's Windows status module. It does not enumerate endpoints, start services, open streams, or do audio I/O.

The default PowerShell behavior is fail-closed for missing packages: it lists the missing UCRT64 package requirements and stops. Installing them with pacman requires the explicit `-InstallRuntimePackages` switch. `-CheckOnly` is read-only: it skips venv creation and pip installation, and uses the repository's `src/` directory only so a source checkout can be import-checked.

The helper now configures its child-process environment from `MsysRoot` before the first UCRT64 import: it sets `MSYSTEM=UCRT64`, prepends the UCRT64 and MSYS2 `usr/bin` directories to `PATH`, and sets `PYGI_DLL_PATH` to both DLL directories. This makes the documented entry point work from ordinary Windows PowerShell without relying on an already-initialized MSYS2 terminal. These environment changes are process-local and do not persist in the user's Windows environment.

### Bootstrap validation performed on the Windows laptop

- `windows_bootstrap.ps1 -CheckOnly` exited 0, validated the MSYS2 package set and UCRT64 venv configuration, then imported Python 3.14.9, GTK 4.24, Pycaw 20260927, comtypes 1.4.17, psutil 7.2.2, and the Windows status module.
- Repeated `-CheckOnly` from ordinary PowerShell with the inherited MSYS2 `PATH` entries removed and `PYGI_DLL_PATH` unset. The helper configured both itself and its child Python process, imported the same runtime successfully, and did not query an audio endpoint.
- Normal install mode was tested with a dedicated temporary venv under `%LOCALAPPDATA%\Temp`. The script created it with `--system-site-packages`, installed the built Hearth wheel with pip, fetched Pycaw via the Windows marker, and completed the import probe successfully.
- The installed-wheel check reported Hearth 5.1.0 and Pycaw 20260927; it did not query or change any audio devices.
- The dedicated temporary test venv was removed and its absence verified. The active project `.venv-win` was not overwritten.
- `-InstallRuntimePackages` was not exercised because the required MSYS2 packages were already installed.

The bootstrap is a reproducible **development bootstrap/check helper**, not yet a signed or supported end-user installer. The MSYS2 package set remains observational and must be pinned and validated on a clean machine before release.

## 20. Windows GitHub Actions validation - 2026-10-30

Added `.github/workflows/windows.yml` as a separate Windows workflow. It uses `windows-latest` with the MSYS2 UCRT64 environment and the same GTK/PyGObject, GLib, GDK Pixbuf, Cairo, COM, and psutil runtime packages required by the local bootstrap helper.

The job:
- Verifies the UCRT64 Python, GTK 4, PyGObject, COM, and psutil imports.
- Creates an isolated venv that inherits the MSYS2 runtime packages, installs Hearth editable, and installs only the CI tools actually used (Ruff, MyPy, pytest/coverage, and build). It intentionally omits pre-commit because its PyYAML dependency does not provide a wheel for this MSYS2 ABI.
- Runs Ruff lint/format checks from the MSYS2 UCRT64 package, strict MyPy 1.18.2 from the venv, and the non-GUI pytest suite with Python safe-path mode.
- Builds both wheel and source distribution.
- Invokes `windows_bootstrap.ps1` against the built wheel using a separate temporary venv, then runs the installed CLI version check.
- Removes the temporary wheel-test venv after the smoke check.

The wheel smoke invokes the bootstrap from native PowerShell without pre-setting `MSYSTEM`, `PATH`, or `PYGI_DLL_PATH`, so it also validates that entry point's environment setup. The helper compares normalized full paths from `pyvenv.cfg`, because MSYS2 may write the interpreter home with forward slashes while PowerShell supplies backslashes. Python `-P` is also used for the pytest and installed-wheel CLI commands so the repository's top-level `hearth.py` compatibility shim cannot shadow the actual `src/hearth` package. The junction regression converts `C:/...` paths to native backslashes before invoking `cmd.exe`/`mklink`, avoiding `/Users` being interpreted as a switch. The import probe does not enumerate audio endpoints, start services, open GTK windows, or perform playback/capture/routing. GUI tests remain covered by the separate hidden-window tests already performed on the Windows laptop; they are not run on the headless hosted runner.

The workflow YAML parsed successfully in a local YAML parser. The complete local CI rehearsal on the Windows laptop exited 0:
- Fresh UCRT64 development venv created with inherited MSYS2 packages; pip installed the editable Hearth project, MyPy 1.18.2, pytest-cov, and its pure-Python dependencies without requiring a compiler.
- Ruff lint and format passed; Ruff reported 103 Python files already formatted. Strict MyPy passed across 40 source files.
- Non-GUI Windows suite: **393 passed, 85 skipped, 56 deselected in 41.82 seconds**. The real directory-junction ACL-safety regression ran and passed after the test normalized MSYS2's forward-slash paths for `cmd.exe`.
- Wheel and source distribution both built successfully.
- A second fresh UCRT64 venv was created by `windows_bootstrap.ps1`; it installed the just-built Hearth 5.1.0 wheel and Pycaw 20260927, imported GTK 4.24/COM/psutil/Hearth successfully, and passed `python -P -m hearth --version`.
- Both temporary venvs were removed, and their absence was confirmed.

The Windows job deliberately gets Ruff from `mingw-w64-ucrt-x86_64-ruff`. It pins only its CI MyPy install to 1.18.2 because later MyPy versions pull in `librt`; the current MSYS2 Python ABI cannot use the package's standard CPython `win_amd64` wheels. It omits `pre-commit`, which is not invoked by the job and would add a PyYAML source-build requirement. The project's general `dev` extra is unchanged.

The hosted GitHub Actions run has **not** yet executed because this `windows-port` branch and its workflow remain local-only; no commit or push was made.


## 21. Per-user Windows installation and workflow rehearsal - 2026-10-30

A local per-user pilot installation was created from the rebuilt hearth-5.1.0-py3-none-any.whl on the MSYS2-equipped Windows laptop. The installation is separate from the repository's development venv:

- Runtime: %LOCALAPPDATA%\Programs\Hearth\venv
- Launcher: %LOCALAPPDATA%\Programs\Hearth\Launch-Hearth.ps1
- Start Menu shortcut: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Hearth.lnk
- User data remains below %LOCALAPPDATA%\Hearth (config, state, cache and runtime); it is not stored inside the program venv.
- The installation directory ACL grants full access only to the current user, SYSTEM and local Administrators. The existing %LOCALAPPDATA%\Hearth data tree was preserved.

The install uses the existing Windows bootstrap helper and inherits the already provisioned MSYS2 UCRT64 runtime packages. The installed distribution reports Hearth 5.1.0. pip check found no broken requirements. The installed CLI version and read-only status commands were exercised. Windows Audio and Audio Endpoint Builder report running; the inventory reports four of four mapped playback endpoints and eleven capture endpoints.

### Wheel artwork correction

The first pilot wheel exposed a packaging omission: the source-tree GTK module looked for assets/art above the installed package and therefore fell back to theme speaker icons. The GTK module now first resolves assets/art alongside itself and falls back to the repository-root art directory only for a source checkout. Hatch's wheel force-include maps the root assets/art directory to hearth/ui/gtk4/assets/art.

The rebuilt 5.1.0 wheel was inspected independently. Both app-muted@2x.png and app-unmuted@2x.png are present, their PNG signatures are valid, and the installed module resolves ART_DIR inside the installed venv's site-packages. The refreshed installed package's asset probe passed. The Windows Actions workflow now also verifies the two wheel entries and their PNG signatures immediately after its build step; the workflow YAML and step ordering were parsed and asserted locally. Hosted Actions remains unrun while the branch is local-only.

### Installed UI workflow and regression results

- The hidden installed-wheel Mixer workflow refreshed five configured strips (laptop_audio, mic_b1, vm_chat, vm_game, vm_music) with no missing strips. It loaded the installed package from site-packages, confirmed both packaged speaker icons, kept the window hidden, verified 100% endpoint/app slider upper bounds and clamping of an over-range synthetic value, and confirmed unsupported app-to-bus reassignment/LPD8 binding stay disabled. Collector and meter workers both stopped cleanly.
- The hidden installed-wheel Setup/Remap workflow passed. It did not start Linux-only polling or service queries, did not enable Moonlight or LPD8 actions, and recorded zero mapping-write side effects.
- Both GUI workflows used a disposable _installed_smoke data subtree and temporary interactive scheduled tasks. The task wrappers unregister themselves and remove the isolated smoke data.
- Final source checks after the artwork fix: Ruff lint passed; Ruff format reported 86 files already formatted; strict MyPy passed for 40 source files; Windows non-GUI pytest reported 393 passed, 85 skipped, 56 deselected in 40.32 seconds; compileall and both Git whitespace checks passed.

### Scope and remaining release work

This was a same-user pilot install on the existing MSYS2-equipped laptop. It is not a clean-machine acceptance test, signed installer, or supported standalone Windows distribution. The UCRT64 runtime package manifest is still not pinned, and an official install/update/uninstall and backup policy remains future work. The installed Start Menu link and launcher were created and their target, arguments and working directory validated; no visible-window/visual-acceptance session was performed.

The Windows backend remains intentionally limited: the status report is read-only, the virtual mixer/routing graph and playback/capture signal paths have not been verified, and app reassignment, capture routing, and Linux-specific Setup/Remap workflows are not implemented on Windows. No audio endpoint level/mute setters, default-device changes, driver changes, service configuration changes, or playback/capture signal tests were performed. Padfire and the existing RoaringMic setup were left untouched.

The source/packaging changes remain local to windows-port. No commit, push, merge, or hosted GitHub Actions run was performed.


## 12. Verified Windows pilot status - 2026-10-30

This section supersedes the initial gap list above where the implementation below is now present.

### Implemented and tested

- Windows LocalAppData config/state/cache/runtime paths, protected SID-derived ACLs, atomic writes, safe reparse-point handling, and MSVCRT locking.
- Authenticated per-user named-pipe IPC with single-instance locking, clean shutdown, CLI control, and Windows-safe runtime paths.
- Windows Core Audio endpoint and application-session inventory, endpoint/session volume and mute setters, capture peak queries, and a truthful read-only health report.
- Windows dark/light palette and system accent-color reader, plus a native DWM caption integration that is applied after the GTK window maps.
- Windows bootstrap/runtime probe, packaging metadata, and a Windows Actions workflow. The pilot remains an MSYS2 UCRT64 development installation, not a standalone installer.

### Verification

- Windows Python 3.14 full suite: **397 passed, 141 skipped**. Platform-specific POSIX/Linux tests and display-dependent GUI tests account for the skips.
- Focused Windows theme/Core Audio/ACL/IPC suite: **47 passed**; Ruff clean; MyPy clean for 40 source files.
- Linux full suite under Xvfb: **506 passed**.
- The laptop's Windows registry reports dark app mode, accent #e90082, and accent-on-title-bars enabled. The Core Audio adapter successfully accepted a same-value endpoint write and read back the unchanged value.

### Remaining functional boundaries

- Windows per-application routing between Hearth buses is not implemented; non-empty move_sink_input requests fail closed.
- The Windows endpoint inventory is not a substitute for the complete six-bus virtual audio engine or for a verified Windows mic/effects chain.
- No external LPD8 MIDI device was present in the Windows Plug-and-Play inventory during this audit. Native LPD8 MIDI discovery/control remains unimplemented; Linux currently has an active LPD8 service, while the Launchpad remains owned by Padfire.
- A clean-machine installer, pinned MSYS2 runtime manifest, signed distribution, and full Windows signal-path/visible-GUI acceptance remain open work.

The test for named-pipe lifecycle uses a process-scoped test pipe so it can run while the real desktop Hearth application owns its stable per-user pipe. This preserves the production single-instance contract and avoids disrupting the live application during tests.
