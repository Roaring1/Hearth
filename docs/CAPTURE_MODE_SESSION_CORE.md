# Capture Mode session and adapter contract

**Checkpoint: 2026-11-11.** This is the current no-card engineering checkpoint. The transaction coordinator, fixture parsers, one-shot inventory adapters and pure viewer argv builder exist. No real capture session has been started and no adapter has been invoked against a capture card or the live PipeWire graph.

## Implemented

### Session transaction: `hearth.capture_session`

The module is toolkit-free and owns lifecycle order/state, not the operating-system adapters. `CaptureRuntime` injects device probe, audio safety validation, viewer start, session-route acquisition, idle inhibit, first-frame wait and route verification.

`CaptureSessionManager.start()` proceeds in this order:

1. Resolve the current node/mode from a stable USB identity.
2. Validate audio association and safety. Unknown/ambiguous graph state must fail closed.
3. Start a viewer and receive an owned handle.
4. Apply only session-scoped audio routes and receive a cleanup lease.
5. Acquire an idle-inhibit lease.
6. Wait for the first frame.
7. Verify the expected audio-route state.
8. Only then report `RUNNING` / Ready.

On failure it attempts cleanup in reverse ownership order: viewer/device handle, idle inhibitor, session routes. A failed cleanup lease remains retained and the manager reports `FAILED`; Stop retries it. New Start is refused until all leases are cleaned and the state returns to `DORMANT`. The tests inject failure at every startup phase and exercise rollback/retry.

`CapturePlan` stores stable USB identity, an enumerated mode and `AudioMode` (`Xbox` default, `Nobara`, `Both`). It does not store `/dev/videoN`, PipeWire object IDs, ALSA card numbers or runtime link IDs. `UsbIdentity` requires USB serial or physical port path, preferring serial. `ResolvedCaptureDevice` carries the current node path only for a running operation.

### V4L2 discovery parsing and one-shot provider

- `hearth.capture_devices` parses `v4l2-ctl --list-devices`, udev identity, per-node `Device Caps` and discrete formats/sizes/FPS.
- It requires `ID_BUS=usb` and per-node Device Caps. Aggregate card capabilities alone are insufficient. Metadata-only nodes are excluded. Stepwise/continuous mode ranges are not guessed.
- Nodes are grouped by USB identity. A single eligible capture node can be selected automatically; multiple eligible nodes require explicit user choice.
- `hearth.capture_provider.V4L2InventoryProvider` performs one bounded sequence when explicitly called: list nodes, resolve each node's udev properties, query per-node caps, then enumerate formats only for capture-capable nodes. A top-level listing failure aborts; per-node failures are returned as issues without substituting another node.
- Each `v4l2-ctl` ioctl command transiently opens its node and closes on process exit. Hearth does not open/hold a streaming handle in this inventory path. The provider has not been invoked on the user's machine; only fake process-runner tests exist. The absent GUV302G's node list, permissions, busy behavior and modes remain unknown.

### PipeWire audio inventory and USB association

- `hearth.capture_pipewire.PipeWireAudioProvider` performs one bounded `pw-dump --no-colors` snapshot on explicit request. It does not use `--monitor`, start a watcher or mutate links.
- `parse_pw_dump_audio_inventory()` joins Node `device.id` to Device `id` only inside the same snapshot and discards those numeric IDs. It derives USB identity from explicit bus/vendor/product/serial/parent-path properties; `node.name` is retained as a nonnumeric current source selector and a separate label is for display.
- Video/audio association prefers matching USB serial + VID/PID. When serial is missing on either side, it uses normalized physical USB parent path. Friendly names and numeric ALSA/PipeWire IDs are not identity.
- Outcomes are `unknown`, `none`, `matched`, and `ambiguous`. Missing/unverifiable metadata makes the inventory incomplete/unknown; multiple matching audio endpoints require user choice.
- Both parser and provider have synthetic JSON/fake-runner tests. The provider has not been run against the user's live graph. The actual GUV302G audio source topology and whether its video/audio functions expose the same udev identity remain unknown.

### mpv command construction

`hearth.capture_viewer.build_mpv_viewer_argv()` is a pure argv builder, not a process launcher. It takes one freshly resolved node/mode, uses `av://v4l2:/dev/videoN`, passes the selected discrete `input_format`, `video_size` and rational `framerate`, and uses an explicit FourCC allow-list. Unknown formats raise `UnsupportedCaptureFormat`. The command disables audio, OSC, OSD, subtitles and built-in keys, uses no user mpv config, hides the cursor, sets the exact `Hearth - Share Output` title and can request fullscreen.

Two isolated Xvfb checks have been performed:

- mpv 0.40.0 played a generated 1280x720 test file with the clean-window flags, opened the exact title and yielded a screenshot. This was X11/Xvfb, not Wayland.
- The installed mpv/FFmpeg build accepted the `av://v4l2` + lavf option shape with a deliberately nonexistent `/dev` path and reached FFmpeg's V4L2 handler, which returned the expected missing-node error. No actual video node was opened.

These checks prove command shape/compiled input support only. They do not verify the FourCC mapping against the target card, actual frames, frame cadence, fullscreen placement, portal capture or remote Vesktop video. `--untimed` is not included by default; its timing impact needs measurement.

## Not implemented yet

- A parent-death-safe owned viewer process supervisor with stable creation-thread lifetime, PID race checking, bounded Stop and verified exit. `hearth.proc.spawn()` detaches children into a new session; a fire-and-forget launch could leave a frozen viewer after Hearth exits. Do not launch the argv builder until an owned-process boundary exists.
- The active viewer/streaming device open, frame/stall monitor and No signal integration. Inventory ioctls must close before the viewer takes the streaming open; one component must clearly own the active device handle.
- A real idle-inhibit lease adapter and its Stop/crash cleanup.
- A live audio safety/route adapter, owner metadata or `pw-dump` route verification. Full user-managed VM-SHARE source editing and single-path migration remain explicitly deferred in #10. No live audio routes are changed by this work.
- Active Capture Controls/Share Output UI integration. Do not show Start/Stop until the underlying operations exist.
- Real GUV302G discovery, audio association, modes, USB speed, signal, A/V sync and long-run soak results.

## Next no-card work

1. Implement the owned viewer-process supervisor and synthetic-child tests before any viewer launch. Linux parent-death signaling is tied to the creating thread, so the owner thread must live for the entire active session; also cover the parent-death race before the child installs its signal.
2. Make the UI-safe wrapper around the one-shot V4L2/PipeWire inventory adapters explicit: no automatic open on app launch, one bounded check on entering Capture Controls, and clear `unknown` vs. `not found` results.
3. Add no-card tests for transactional cleanup when the viewer exits unexpectedly, first-frame timeout, and session state after child termination.
4. Continue #13 documentation/fixtures only. Real KDE portal, monitor, lock/DPMS and remote Vesktop tests remain deferred until the user explicitly grants a safe interactive window.
5. Keep #10 deferred. All session route operations remain abstract; no live route adapter is supplied in this tranche.
6. Wire active controls and Share Output in #12 only after the owned viewer/session operations work. No inert Start/Stop buttons.

No permanent service, timer, polling daemon or idle graph watcher is added. Capture-specific activity should exist only while a user explicitly starts a session.
