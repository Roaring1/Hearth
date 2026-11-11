# Capture Mode session and adapter contract

**Checkpoint: 2026-11-11.** This is the current no-card engineering checkpoint. The transaction coordinator, fixture parsers, one-shot inventory adapters, V4L2 Start-time resolver, composed mpv runtime, owned viewer process, private IPC first-frame check and active-session timestamp monitor exist. No real capture session has been started. The one-shot providers, resolver and viewer adapter have not been invoked against a capture card or the live PipeWire graph.

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
- `hearth.capture_runtime.V4L2CaptureResolver.probe_device()` is the Start-time seam: it takes exactly one inventory snapshot, selects by saved stable USB identity, requires one eligible capture node and the exact enumerated preferred mode. An issue with unknown USB identity or the selected device's identity fails closed; a probe issue positively tied to a different USB device does not block the selected card. Ambiguous node selection still requires explicit choice. It returns only the current ephemeral `/dev/videoN` to the active operation and does not persist it.
- Each `v4l2-ctl` ioctl command transiently opens its node and closes on process exit. Hearth does not open/hold a streaming handle in this inventory/resolution path. The provider/resolver have not been invoked on the user's machine; only fake inventory/process-runner tests exist. The absent GUV302G's node list, permissions, busy behavior and modes remain unknown.

### PipeWire audio inventory and USB association

- `hearth.capture_pipewire.PipeWireAudioProvider` performs one bounded `pw-dump --no-colors` snapshot on explicit request. It does not use `--monitor`, start a watcher or mutate links.
- `parse_pw_dump_audio_inventory()` joins Node `device.id` to Device `id` only inside the same snapshot and discards those numeric IDs. It derives USB identity from explicit bus/vendor/product/serial/parent-path properties; `node.name` is retained as a nonnumeric current source selector and a separate label is for display.
- Video/audio association prefers matching USB serial + VID/PID. When serial is missing on either side, it uses normalized physical USB parent path. Friendly names and numeric ALSA/PipeWire IDs are not identity.
- `hearth.capture_runtime.PipeWireCaptureAudioResolver` consumes one bounded snapshot and applies that matcher. A unique match returns the nonnumeric `node.name` selector; no match reports `NONE`, an incomplete query reports `UNKNOWN`, and multiple matches report `AMBIGUOUS` with candidate labels for a future user choice. It never picks the first match.
- `CaptureSessionError.cause` preserves the original typed adapter exception for the caller. An ambiguous audio match therefore remains available as `CaptureAudioAssociationError` with candidate objects/selectors, rather than becoming display text only. `SessionSnapshot` still contains only safe text/state and does not expose runtime candidates or device handles.
- The default `V4L2CaptureResolver` used by `MpvCaptureRuntime` invokes this audio resolver during Start-time probing. The resulting selector is ephemeral in `ResolvedCaptureDevice`; it is not persisted and is not itself a route. The Xbox/Nobara/Both `AudioMode` is a separate mix policy, not a way to guess the hardware source.
- Both parser/provider and identity-resolver paths have synthetic JSON/fake-inventory tests. The provider has not been run against the user's live graph. The actual GUV302G audio source topology and whether its video/audio functions expose the same USB identity remain unknown.

### Composed session runtime: `hearth.capture_runtime.MpvCaptureRuntime`

`MpvCaptureRuntime` now implements the `CaptureRuntime` protocol by composing `V4L2CaptureResolver`, `MpvCaptureViewer`, and two mandatory injected interfaces: `CaptureAudioAdapter` (validate, acquire/remove a session route lease, verify) and `CaptureIdleInhibitorAdapter` (acquire/release a lease). Its default V4L2 resolver also performs one `PipeWireCaptureAudioResolver` snapshot and fails unless the matching card audio endpoint is uniquely identified. It delegates first-frame readiness to `MpvViewerProcess.wait_for_first_frame()`. Construction is inert; only `CaptureSessionManager.start()` invokes the resolver and then the adapters in the defined transaction order.

The composition is exercised with fake inventory, viewer, route and inhibitor adapters, including first-frame failure rollback. The PipeWire identity resolver separately tests unique, none, unknown, ambiguous and provider-failure outcomes. The GTK-layer logind idle/sleep inhibitor lease is implemented separately (see below), but it is not yet wired into this runtime or an active Controls/Start path. Live audio-route creation/removal and post-route verification remain unimplemented, so this is not an operational capture runtime.

### systemd-logind idle/sleep inhibitor lease

`hearth.ui.gtk4.capture_inhibit.Login1IdleInhibitorAdapter` now implements the injected idle-inhibitor protocol without adding a core dependency on GTK/Gio. Construction is inert. Only an explicit `acquire()` opens a private system-bus connection and requests logind's `idle:sleep` block inhibitor for Hearth. The adapter duplicates and validates the returned Unix FD, marks it close-on-exec, closes its private D-Bus connection, and returns a lease whose lifetime is the FD alone. Stop closes that FD; Hearth process exit closes it automatically, so there is no helper process, permanent service, timer, or capture-specific watcher.

Release is idempotent. The lease clears its owned-FD slot before calling `close()` and never retries the same descriptor number after an error: Linux releases the descriptor early, so it may already have been reused by another thread. `EBADF`/`EINTR` are also treated as released. A reported close error is surfaced once; the manager's subsequent Stop retry is a no-op rather than a second `close()` on a potentially reused FD. The adapter fails closed if the system bus, logind call, returned handle, or FD validation fails. Seven fake-FD tests cover headless construction without PyGObject, lazy acquisition, the exact `idle:sleep`/`block` request, private bus connection cleanup, close-on-exec, failure cleanup, idempotent release and no-retry-on-close-error. No live system-bus call has been made, and the adapter is not yet wired into a GTK Start action. See [Linux close(2)](https://man7.org/linux/man-pages/man2/close.2.html) for the descriptor-reuse caveat.

### V4L2 stream ownership and first-frame open errors

The V4L2 device FD is intentionally opened and held by the owned mpv child, not by a second Hearth-side FD. `MpvViewerProcess` is the lifetime lease: it owns the child-process handle, mpv opens the selected `av://v4l2:` input, and Stop reaps mpv before deleting the session IPC directory. This avoids double-opening a driver that may enforce exclusive access. First-frame readiness requires an actual I/P/B decoded picture type, positive `video-params` dimensions, and `vo-configured=true`; a PID or input-open alone is not considered a successful stream. The one-shot inventory probe has already closed its device handles before this viewer start.

During first-frame readiness, mpv `end-file` IPC events with `reason=error` are mapped into stable categories: `device_busy`, `permission_denied`, `device_unavailable`, `unsupported_mode`, `io_error`, or generic `open_failed`. Since mpv's IPC `file_error` is often only the generic text `loading failed`, Start also creates a mode-0600 `mpv.log` in the private session directory and passes it with `--log-file`. On failure only, `MpvViewerProcess` reads at most the last 8 KiB and extracts `[e]`/`[fatal]` lines to enrich the category; the log is removed with the runtime directory on Stop. Fake-IPC tests cover every category. An actual mpv 0.40/Xvfb attempt to open the nonexistent `/dev/video999` produced the generic IPC event plus `No such file or directory` in the private log, and the full viewer path correctly surfaced `device_unavailable`. No physical GUV302G stream has been opened, so actual driver strings and exclusive-open behavior remain hardware validation items.

### sampled-frame content health probe

`MpvCaptureViewer.start()` now writes a private `frame-health.lua` into the same 0700 session runtime directory as mpv's IPC socket and passes it through `--script`. The Lua probe observes mpv `time-pos` changes and, at most once every two seconds, samples a 32x18 grid from the in-memory `screenshot-raw` video frame. It hashes only sampled RGB bytes and publishes a small `user-data/hearth/frame-health` object; it never writes screenshots or raw pixel data to disk and never creates a timer. The script exists only for the active owned mpv process and is removed with the IPC directory on Stop.

The monitor exposes three advisory states: `changing` when sampled content changes, `mostly_black` after six seconds where at least 98% of sample points are near-black, and `unchanged` after ten seconds with an identical sampled fingerprint. These states do not fail or stop the session. A paused menu or static game scene can be valid, so `unchanged` is explicitly a warning signal, not proof of a broken capture. `mostly_black` is also only a black-frame heuristic, not proof of a disconnected cable or missing HDMI signal. If the mpv build rejects the optional user-data property, timestamp liveness continues unchanged. A timestamp stall now displays the centered **Video stopped updating** label in the Share Output window while keeping the session RUNNING; it does not stop audio routes, release the idle inhibitor, or close the video window. This wording describes the observed failure (no advancing video timestamps), not a definitive HDMI/cable-presence diagnosis. Only mpv process exit or an explicit lifecycle failure triggers normal cleanup.

The synthetic mpv/Xvfb integration was exercised against three 320x180 sources: changing `testsrc` remained `changing`; a black source became `mostly_black` after approximately six seconds; and a static red source became `unchanged` after approximately ten seconds. The monitor retained first-frame readiness and exited/cleaned up normally in all cases. A separate real-mpv/Xvfb rendering test toggles the no-signal property over IPC and confirms the centered label appears and clears over a synthetic video. These tests prove the mpv Lua/IPC/rendering mechanism only; they do not prove GUV302G signal behavior or real V4L2 recovery.

`MpvViewerProcess.set_no_signal_callback()` emits only actual Boolean edges (`False -> True` on stall/input error and `True -> False` on resumed timestamps). `CaptureSessionManager` binds that callback to its `subscribe_no_signal()` API and publishes a fresh `SessionSnapshot` with an incremented revision. Subscribers receive the initial snapshot by default and get an unsubscribe closure. The initial callback is serialized with monitor transitions; later notifications run on the active viewer-monitor thread. Keep observers non-blocking, and marshal GTK updates to the main context (for example `GLib.idle_add`). Callback exceptions are isolated from the viewer monitor and session cleanup. The manager detaches the viewer callback before Stop/rollback begins, so a late monitor edge cannot retain the manager or publish stale UI state. This API adds no polling, watcher, timer or thread. Start/Stop lifecycle snapshots remain synchronous return values. The subscription is a core contract for the future Controls window; it is not yet connected to a production UI.

### mpv command construction and owned process

`hearth.capture_viewer.build_mpv_viewer_argv()` is a pure argv builder. It takes one freshly resolved node/mode, uses `av://v4l2:/dev/videoN`, passes the selected discrete `input_format`, `video_size` and rational `framerate`, and uses an explicit FourCC allow-list. Unknown formats raise `UnsupportedCaptureFormat`. The command disables audio, OSC, OSD, subtitles and built-in keys, uses no user mpv config, hides the cursor, sets the exact `Hearth - Share Output` title and can request fullscreen.

`MpvCaptureViewer.start()` creates a unique private 0700 directory under the current user's `XDG_RUNTIME_DIR`, adds `--input-ipc-server=<private socket>` to the command, starts the child through `proc.spawn_owned()`, and returns an `MpvViewerProcess`. If startup fails, its private directory is removed. Immediately before creating a new session directory (therefore only on explicit Start), `create_private_ipc_directory()` performs one bounded stale-artifact sweep: it considers only direct-child `hearth-capture-*` directories owned by the user, mode 0700, and at least 60 seconds old. A live `mpv.sock` connection preserves the directory; absent/refused sockets permit removal only when every entry is one of Hearth's known artifacts (`frame-health.lua`, `mpv.log`, or `mpv.sock`) with the expected owner/type/private mode. Unknown files, nested directories, symlinks, unexpected owners/modes, unknown socket errors, permissive directories and untrusted runtime roots are preserved. It never kills processes or touches devices/audio routes, and there is no launch-time sweep, daemon or periodic cleanup.

`hearth.proc.spawn_owned()` and `hearth.proc.OwnedProcess` provide the process-ownership layer. All `Popen` creation remains in `hearth.proc`. It starts a small standard-library exec wrapper, waits for a status-pipe handshake proving that Linux `PR_SET_PDEATHSIG` was installed and the requested executable successfully replaced the wrapper, then keeps the creating daemon thread blocked in `wait()` for the entire child lifetime. The wrapper uses SIGTERM on parent-thread death and checks the parent PID to cover the process-death-before-prctl race. Explicit Stop is bounded and escalates to SIGKILL before reaping.

The creation-thread requirement is verified by a synthetic integration test: the test creates a sleeping Python child from a dedicated thread, exits that thread while the test process remains alive, and confirms the child receives SIGTERM. Other tests cover normal Stop/reap, unexpected exit and exec failure. These tests launch only short-lived synthetic Python children, not mpv, a video device, GTK or audio.

`MpvViewerProcess.wait_for_first_frame(timeout_s)` uses bounded local JSON IPC requests. It requires all three conditions: `video-frame-info/picture-type` is `I`, `P` or `B`; `video-params` reports positive decoded width/height; and `vo-configured` is true. A still-running PID or an open V4L2 input is not sufficient. It reports timeout and early process exit distinctly.

After first-frame readiness, `MpvViewerProcess.monitor_until_exit(stall_timeout_s)` opens one IPC connection and subscribes to mpv's `time-pos` property with `observe_property`. The existing session waiter uses this method while RUNNING, so frame-progress monitoring exists only while a capture session is active. When timestamps stop changing for the configured interval (2 seconds by default), it sets `user-data/hearth/no-signal`; the private Lua script draws a centered white **Video stopped updating** label over the video. The mpv command uses `--force-window=yes`, `--idle=yes` and `--keep-open=yes`, so the Share Output window remains available after input loss. The monitor then blocks on the existing IPC socket (no polling); resumed `time-pos` clears the label and `SessionSnapshot.no_signal` becomes false. The session remains RUNNING throughout. Process exit still triggers FAILED and normal cleanup. Stop reaps mpv before deleting the private socket directory.

The IPC socket is powerful and unauthenticated by design. It is therefore local-only and protected by a 0700 directory inside the verified, owner-controlled `XDG_RUNTIME_DIR`; the implementation refuses a shared `/tmp` fallback. mpv's [JSON IPC documentation](https://github.com/mpv-player/mpv/blob/master/DOCS/man/ipc.rst) describes this interface and its trust boundary. The readiness properties are documented in the [mpv property list](https://mpv.io/manual/stable/#property-list).

Four isolated Xvfb checks have been performed:

- mpv 0.40.0 played a generated 1280x720 test file with the clean-window flags, opened the exact title and yielded a screenshot. This was X11/Xvfb, not Wayland.
- The installed mpv/FFmpeg build accepted the `av://v4l2` + lavf option shape with a deliberately nonexistent `/dev` path and reached FFmpeg's V4L2 handler, which returned the expected missing-node error. No actual video node was opened.
- `proc.spawn_owned()` launched mpv against a synthetic `av://lavfi:testsrc` source under Xvfb. The private IPC check returned a decoded 640x360 I-frame; Stop reaped the child and removed its IPC directory. A second synthetic end-to-end run then monitored `time-pos` events, sent an orderly quit, observed exit code 0 and removed the private IPC directory. These prove the IPC/readiness/progress mechanics with a synthetic source only.

These checks prove command shape, local IPC first-frame/progress behavior with synthetic sources, and compiled input support only. They do not verify the FourCC mapping against the target card, actual card frames, real-card cadence, pixel-level freeze/no-signal detection, fullscreen placement, portal capture or remote Vesktop video. `time-pos` proves stream timestamps advance; it does not prove that changing image content is visible. `--untimed` is not included by default; its timing impact needs measurement.

## Not implemented yet

- Real `CaptureAudioAdapter` implementation: route-guard validation against live PipeWire metadata, safe session-scoped VM-SHARE lease creation/removal, owner metadata and post-route verification. USB audio identity discovery now exists as a fixture-backed, one-shot selector only. Full user-managed VM-SHARE source editing and one-path migration remain explicitly deferred in #10; this work has not changed live audio routes.
- Wire the tested `Login1IdleInhibitorAdapter` into the future GTK Start path. The adapter itself is implemented, but no live logind call has been made and no active Controls window uses it yet.
- Connect the composed `MpvCaptureRuntime` to the Hearth GTK Controls/Start action. No UI path constructs or starts it yet.
- Real GUV302G streaming-open/exclusive-access behavior remains unverified. The ownership model is the owned mpv child process; fake IPC now classifies common mpv `end-file` open errors, but actual driver strings and busy-device behavior need the card. Inventory ioctls must close before the viewer starts.
- Wire `MpvViewerProcess.frame_health` and `SessionSnapshot.no_signal` into the future Controls/Share Output UI. `mostly_black` and `unchanged` remain advisory only; the shared picture uses **Video stopped updating** only for the confirmed timestamp-stall or mpv input-error path.
- Active Capture Controls/Share Output UI integration. Do not show Start/Stop until the live audio, inhibitor, device and UI paths exist.
- Real GUV302G discovery, audio association, modes, USB speed, signal, A/V sync and long-run soak results.

## Next no-card work

1. Keep the real `CaptureAudioAdapter` behind #10's single-path ownership contract. Full user-managed VM-SHARE editing/migration remains deferred; do not mutate live routes or guess the route model.
2. Wire the validated frame-health advisory state and structured input-open categories into the future Controls/Share Output UI. Keep `unchanged` advisory, and do not claim black input proves cable disconnection.
3. When the GUV302G is available, verify exclusive-open behavior, actual driver error strings, supported format/size/FPS and signal loss. Do not simulate these as passed without the hardware.
4. Wire the tested login1 inhibitor and composed `MpvCaptureRuntime` into a lazy Hearth Controls action only after the real route adapter and all Start prerequisites exist. Controls may run one bounded discovery; app startup must remain dormant.
5. Continue #13 documentation/fixtures only. Real KDE portal, monitor, lock/DPMS and remote Vesktop tests remain deferred until the user explicitly grants a safe interactive window.
6. Wire active controls and Share Output in #12 only after the owned viewer/session operations work. No inert Start/Stop buttons.

No permanent service, timer, polling daemon or idle graph watcher is added. Capture-specific activity should exist only while a user explicitly starts a session.


The no-signal overlay is also verified in real mpv 0.40/Xvfb: a synthetic video is captured, the private property is toggled over IPC, and the centered label is confirmed to appear and clear.
