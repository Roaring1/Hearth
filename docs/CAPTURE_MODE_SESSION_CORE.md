# Capture Mode session core contract

**Checkpoint: 2026-11-10.** This is the hardware-independent lifecycle layer. It does not yet discover a real card or own a live PipeWire route.

## What is implemented

`hearth.capture_session` is toolkit-free and receives its external operations through a `CaptureRuntime` protocol. The session manager owns sequencing and state, while an eventual runtime adapter owns actual V4L2, viewer, audio-route and desktop-inhibit operations.

The persisted `CapturePlan` contains a stable USB identity, a preferred mode, and the chosen audio family (`Xbox`, `Nobara` or `Both`). It does not store `/dev/videoN`, PipeWire node IDs, numeric ALSA card IDs or link IDs. `UsbIdentity` requires a USB serial or a physical USB port path; serial is preferred and hidden from repr output. A resolved `/dev/videoN` path is obtained only during the current start operation and is not included in the UI snapshot.

`VideoMode` validates a FourCC-like pixel format, dimensions and positive frame interval. Real capabilities must still be enumerated from V4L2 before a mode is offered.

## Startup contract

Start is serialized and transactional:

1. Probe the selected stable device identity and resolve its current video node/mode.
2. Validate the chosen audio source and route safety. The real route validator must fail closed on ambiguous or unavailable graph state.
3. Start the video-only viewer and acquire ownership of its process/device lifetime.
4. Apply only session-owned audio routes and receive a lease that can remove only those exact owned routes.
5. Acquire the idle inhibitor.
6. Wait for the first decoded frame.
7. Verify the expected audio route state.
8. Only then report `RUNNING` / Ready.

Every runtime adapter must roll back its own partial work if it raises before returning a resource lease. A failure after a lease is returned triggers manager rollback.

## Stop and rollback contract

Cleanup attempts every resource in this order:

1. Stop the viewer, which must close its video-device handle.
2. Release the idle inhibitor.
3. Remove only the audio links represented by this session's route lease.

Cleanup methods must be idempotent. If a cleanup action fails, the manager retains only that failed lease and reports `FAILED`; it does not claim the session is dormant. A subsequent Stop retries remaining leases. A new Start is refused until cleanup reaches `DORMANT`.

A process crash still needs a real viewer parent-death/owner strategy and a one-shot stale session cleanup design. This module alone does not make child processes or PipeWire links crash-safe.

## Deliberately not implemented here

- No V4L2/udev discovery or actual `open()` call yet. No hardware exists to verify device identity, capabilities or busy behavior.
- No PipeWire source association, route safety, route mutation, link ownership tags or `pw-dump` verification. The full user-managed routing work remains deferred in #10.
- No mpv or other viewer adapter, idle-inhibit adapter, systemd unit, timer, watcher, polling loop or background process.
- No GTK import, UI, real process launch, device fd, audio route or machine-state change.

The tests inject failure at each startup stage and use fake viewer, route and inhibitor leases. They verify readiness gating, cleanup order, incomplete cleanup retention and retry. These prove the coordinator's contract only, not the correctness of future real adapters.

## Follow-on no-card work

The session coordinator does not replace the device-provider boundary required by #11. The next independent pieces are:

1. `discover()` returns devices by stable USB identity and display name; it never exposes `/dev/videoN` as the saved selector.
2. `probe(identity)` performs a one-shot open/capability query, enumerates real V4L2 formats, sizes and frame intervals, then closes immediately. Fake fixtures should cover metadata-only nodes and multiple video nodes per USB device.
3. The active viewer owns the actual streaming open/close lifetime. It must not race a separate long-lived probe fd against mpv. The concrete adapter decision must make one component the unambiguous owner of the streaming handle.
4. Audio association is separate from video discovery. Match through USB parent identity where provable; ambiguous/missing associations remain unknown and require user selection rather than name/card-number guessing.
5. mpv viewer, audio route and idle-inhibit adapters implement the protocol only after their individual contracts are testable. No live route adapter is supplied while #10 remains deferred.
6. Crash behavior needs an owned child process and a one-shot stale-session cleanup story. The current manager only handles orderly Start/Stop and failures delivered to the running process.

Do not add a permanent service, timer, hotplug watcher or polling loop to solve discovery. Opening Controls can perform one bounded check; active-session events belong only to an active session.
