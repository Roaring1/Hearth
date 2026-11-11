# Capture Mode session and adapter contract

**Checkpoint: 2026-11-11.** This is the current no-card engineering checkpoint. The transaction coordinator, fixture parsers, one-shot inventory adapters, owned viewer process, private mpv IPC first-frame check and active-session exit watcher exist. No real capture session has been started. The one-shot providers and viewer adapter have not been invoked against a capture card or the live PipeWire graph.

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

While RUNNING, an active-session waiter observes the owned viewer handle. If it exits unexpectedly, it calls `CaptureSessionManager.viewer_exited(returncode)`. The manager cleans up remaining resources in the normal order, records the exit and enters `FAILED` even if cleanup succeeds. This prevents the mixer from continuing to show a ready-looking state while the share window is gone. An explicit Stop acknowledges the failure and returns to `DORMANT`; if cleanup failed, Stop retries only the retained leases. The waiter exists only while the session is active. Stale waiter completion after an intentional Stop is ignored internally; a direct external exit report outside RUNNING is rejected.

On failure it attempts cleanup in reverse ownership order: viewer/device handle, idle inhibitor, session routes. A failed cleanup lease remains retained and the manager reports `FAILED`; Stop retries it. New Start is refused until all leases are cleaned and the state returns to `DORMANT`. The tests inject failure at every startup phase and exercise rollback/retry.

`CapturePlan` stores stable USB identity, an enumerated mode and `AudioMode` (`Xbox` default, `Nobara`, `Both`). It does not store `/dev/videoN`, PipeWire object IDs, ALSA card numbers or runtime link IDs. `UsbIdentity` requires USB serial or physical port path, preferring serial. `ResolvedCaptureDevice` carries the current node path only for a running operation.

### V4L2 discovery parsing and one-shot provider

- `hearth.capture_devices` parses `v4l2-ctl --list-devices`, udev identity, per-node `Device Caps` and discrete formats/sizes/FPS.
- It requires `ID_BUS=usb` and per-node Device Caps. Aggregate card capabilities alone are insufficient. Metadata-only nodes are excluded. Stepwise/continuous mode ranges are not guessed.
- Nodes are grouped by USB identity. A single eligible capture node can be selected automatically; multiple eligible nodes require explicit user choice.
- `hearth.capture_provider.V4L2InventoryProvider` performs one bounded sequence when explicitly called: list nodes, resolve each node's udev properties, query per-node caps, then enumerate formats only for capture-capable nodes. A top-level listing failure aborts; per-node failures are returned as issues without substituting another node.
- `hearth.capture_runtime.V4L2CaptureResolver.probe_device()` is the Start-time seam: it takes exactly one inventory snapshot, selects by saved stable USB identity, requires one eligible capture node and the exact enumerated preferred mode. Any incomplete inventory or ambiguous node selection fails closed. It returns only the current ephemeral `/dev/videoN` to the active operation and does not persist it.
- Each `v4l2-ctl` ioctl command transiently opens its node and closes on process exit. Hearth does not open/hold a streaming handle in this inventory/resolution path. The provider/resolver have not been invoked on the user's machine; only fake inventory/process-runner tests exist. The absent GUV302G's node list, permissions, busy behavior and modes remain unknown.

### PipeWire audio inventory and USB association

- `hearth.capture_pipewire.PipeWireAudioProvider` performs one bounded `pw-dump --no-colors` snapshot on explicit request. It does not use `--monitor`, start a watcher or mutate links.
- `parse_pw_dump_audio_inventory()` joins Node `device.id` to Device `id` only inside the same snapshot and discards those numeric IDs. It derives USB identity from explicit bus/vendor/product/serial/parent-path properties; `node.name` is retained as a nonnumeric current source selector and a separate label is for display.
- Video/audio association prefers matching USB serial + VID/PID. When serial is missing on either side, it uses normalized physical USB parent path. Friendly names and numeric ALSA/PipeWire IDs are not identity.
- Outcomes are `unknown`, `none`, `matched`, and `ambiguous`. Missing/unverifiable metadata makes the inventory incomplete/unknown; multiple matching audio endpoints require user choice.
- Both parser and provider have synthetic JSON/fake-runner tests. The provider has not been run against the user's live graph. The actual GUV302G audio source topology and whether its video/audio functions expose the same udev identity remain unknown.

### mpv command construction and owned process

`hearth.capture_viewer.build_mpv_viewer_argv()` is a pure argv builder. It takes one freshly resolved node/mode, uses `av://v4l2:/dev/videoN`, passes the selected discrete `input_format`, `video_size` and rational `framerate`, and uses an explicit FourCC allow-list. Unknown formats raise `UnsupportedCaptureFormat`. The command disables audio, OSC, OSD, subtitles and built-in keys, uses no user mpv config, hides the cursor, sets the exact `Hearth - Share Output` title and can request fullscreen.

`MpvCaptureViewer.start()` creates a unique private 0700 directory under the current user's `XDG_RUNTIME_DIR`, adds `--input-ipc-server=<private socket>` to the command, starts the child through `proc.spawn_owned()`, and returns an `MpvViewerProcess`. If startup fails, its private directory is removed.

`hearth.proc.spawn_owned()` and `hearth.proc.OwnedProcess` provide the process-ownership layer. All `Popen` creation remains in `hearth.proc`. It starts a small standard-library exec wrapper, waits for a status-pipe handshake proving that Linux `PR_SET_PDEATHSIG` was installed and the requested executable successfully replaced the wrapper, then keeps the creating daemon thread blocked in `wait()` for the entire child lifetime. The wrapper uses SIGTERM on parent-thread death and checks the parent PID to cover the process-death-before-prctl race. Explicit Stop is bounded and escalates to SIGKILL before reaping.

The creation-thread requirement is verified by a synthetic integration test: the test creates a sleeping Python child from a dedicated thread, exits that thread while the test process remains alive, and confirms the child receives SIGTERM. Other tests cover normal Stop/reap, unexpected exit and exec failure. These tests launch only short-lived synthetic Python children, not mpv, a video device, GTK or audio.

`MpvViewerProcess.wait_for_first_frame(timeout_s)` uses bounded local JSON IPC requests. It requires all three conditions: `video-frame-info/picture-type` is `I`, `P` or `B`; `video-params` reports positive decoded width/height; and `vo-configured` is true. A still-running PID or an open V4L2 input is not sufficient. It reports timeout and early process exit distinctly.

After first-frame readiness, `MpvViewerProcess.monitor_until_exit(stall_timeout_s)` opens one IPC connection and subscribes to mpv's `time-pos` property with `observe_property`. The existing session waiter uses this method while RUNNING, so frame-progress monitoring exists only while a capture session is active. When timestamps stop changing for the configured interval (2 seconds by default), it raises `ViewerStreamStalled`; the session manager transitions to FAILED and runs its normal cleanup. This is event-driven, not timer polling. Stop reaps mpv before deleting the private socket directory.

The IPC socket is powerful and unauthenticated by design. It is therefore local-only and protected by a 0700 directory inside the verified, owner-controlled `XDG_RUNTIME_DIR`; the implementation refuses a shared `/tmp` fallback. mpv's [JSON IPC documentation](https://github.com/mpv-player/mpv/blob/master/DOCS/man/ipc.rst) describes this interface and its trust boundary. The readiness properties are documented in the [mpv property list](https://mpv.io/manual/stable/#property-list).

Four isolated Xvfb checks have been performed:

- mpv 0.40.0 played a generated 1280x720 test file with the clean-window flags, opened the exact title and yielded a screenshot. This was X11/Xvfb, not Wayland.
- The installed mpv/FFmpeg build accepted the `av://v4l2` + lavf option shape with a deliberately nonexistent `/dev` path and reached FFmpeg's V4L2 handler, which returned the expected missing-node error. No actual video node was opened.
- `proc.spawn_owned()` launched mpv against a synthetic `av://lavfi:testsrc` source under Xvfb. The private IPC check returned a decoded 640x360 I-frame; Stop reaped the child and removed its IPC directory. A second synthetic end-to-end run then monitored `time-pos` events, sent an orderly quit, observed exit code 0 and removed the private IPC directory. These prove the IPC/readiness/progress mechanics with a synthetic source only.

These checks prove command shape, local IPC first-frame/progress behavior with synthetic sources, and compiled input support only. They do not verify the FourCC mapping against the target card, actual card frames, real-card cadence, pixel-level freeze/no-signal detection, fullscreen placement, portal capture or remote Vesktop video. `time-pos` proves stream timestamps advance; it does not prove that changing image content is visible. `--untimed` is not included by default; its timing impact needs measurement.

## Not implemented yet

- Connect `MpvCaptureViewer.start()` to a concrete `CaptureRuntime.start_viewer()` implementation and `CaptureSessionManager`. The adapter already delegates to `proc.spawn_owned`, but no runtime currently calls it and no mpv process has been started.
- The active viewer/streaming device open and No signal UI integration. First-frame and active-session timestamp-stall monitoring now exist in `MpvViewerProcess`, but no concrete `CaptureRuntime` calls the viewer adapter yet. Inventory ioctls must close before the viewer takes the streaming open; one component must clearly own the active device handle.
- A real idle-inhibit lease adapter and its Stop/crash cleanup.
- A live audio safety/route adapter, owner metadata or `pw-dump` route verification. Full user-managed VM-SHARE source editing and single-path migration remain explicitly deferred in #10. No live audio routes are changed by this work.
- Active Capture Controls/Share Output UI integration. Do not show Start/Stop until the underlying operations exist.
- Real GUV302G discovery, audio association, modes, USB speed, signal, A/V sync and long-run soak results.

## Next no-card work

1. Connect `MpvCaptureViewer.start()` and `MpvViewerProcess.wait_for_first_frame()` to a concrete `CaptureRuntime` and the session manager. First-frame gating exists, but remains unwired at the runtime layer.
2. Wire the existing event-driven `time-pos` stall monitor into the concrete runtime, and add clean No signal feedback; do not leave a frozen last frame looking live.
3. Make the UI-safe wrapper around the one-shot V4L2/PipeWire inventory adapters explicit: no automatic open on app launch, one bounded check on entering Capture Controls, and clear `unknown` vs. `not found` results.
4. Continue #13 documentation/fixtures only. Real KDE portal, monitor, lock/DPMS and remote Vesktop tests remain deferred until the user explicitly grants a safe interactive window.
5. Keep #10 deferred. All session route operations remain abstract; no live route adapter is supplied in this tranche.
6. Wire active controls and Share Output in #12 only after the owned viewer/session operations work. No inert Start/Stop buttons.

No permanent service, timer, polling daemon or idle graph watcher is added. Capture-specific activity should exist only while a user explicitly starts a session.
