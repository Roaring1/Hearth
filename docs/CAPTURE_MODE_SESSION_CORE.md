# Capture Mode session core contract

**Checkpoint: 2026-11-10.** This is the hardware-independent lifecycle layer. It does not yet discover a real card or own a live PipeWire route.

## What is implemented

`hearth.capture_session` is toolkit-free and receives its external operations through a `CaptureRuntime` protocol. The session manager owns sequencing and state, while an eventual runtime adapter owns actual V4L2, viewer, audio-route and desktop-inhibit operations. `hearth.capture_devices` contains pure fixture parsers and identity matching that the live provider can reuse.

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

- The read-only V4L2 inventory provider is implemented but has **not** been invoked on the target machine. No explicit `open()` call or stream handle is opened by Hearth yet. Hardware identity, capability output, permissions and busy behavior remain unverified.
- No live PipeWire audio inventory or route safety/mutation, link ownership tags or `pw-dump` verification. `associate_audio_source()` only tests USB-parent identity against fixture candidates; it does not inspect the PipeWire graph. The full user-managed routing work remains deferred in #10.
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

## V4L2 discovery parsing (fixture-backed, not a live provider)

`hearth.capture_devices` now handles the parser/selection layer independently of command execution:

- Parses `v4l2-ctl --list-devices` into `/dev/videoN` candidates and ignores `/dev/mediaN` nodes.
- Parses udev properties for vendor/product plus USB serial (preferred) or physical USB path fallback. A node without a stable identity is rejected.
- Requires the per-node `Device Caps` block; aggregate card `Capabilities` alone are insufficient to choose a node safely. Metadata-only nodes are not capture-eligible.
- Parses only discrete pixel format, frame size and FPS combinations from `v4l2-ctl --list-formats-ext`. Stepwise/continuous ranges are not guessed or expanded.
- Groups nodes by stable USB key. It chooses a capture node only when exactly one eligible node exists; multiple eligible nodes require an explicit path selection.

The parser functions accept strings and mappings only; the provider above gathers those values through bounded read-only commands. Neither parser nor provider opens `/dev/videoN`. Provider tests use synthetic command output for a card with video and metadata nodes, not a record from the absent GUV302G. The provider itself has not been invoked on the user's desktop.

## USB audio-source association (fixture-backed only)

The device module provides `parse_udev_usb_identity()` and `associate_audio_source()` for a future PipeWire inventory adapter:

- Udev must explicitly classify each endpoint as `ID_BUS=usb`; vendor/product IDs alone do not prove the bus type.
- USB serial is authoritative when both video and audio functions expose it, with vendor/product IDs also required to match.
- If either side lacks a serial, matching falls back to the normalized physical USB parent path. Composite interface suffixes such as `:1.0` and `:1.3` are removed so functions on the same physical USB device can match.
- A successful audio inventory with no matching USB identity reports `none`; an unavailable inventory reports `unknown`; one match reports `matched`; more than one endpoint reports `ambiguous` and requires user selection.
- Friendly names, ALSA card numbers and numeric PipeWire object IDs are never used to associate endpoints. The future adapter supplies a stable source selector for a matched endpoint; obvious volatile numeric selectors are rejected.

This is pure matching logic over udev-property fixtures and fake source candidates. It does not parse a live `pw-dump`, `pactl` or ALSA inventory and does not open or mutate an audio device. The future adapter must establish that PipeWire source metadata carries the matching udev parent identity before this association can be trusted on the target system. Real GUV302G audio topology is still unknown and must be measured when the card is available.

## One-shot live V4L2 provider (implemented, not invoked on this machine)

`hearth.capture_provider.V4L2InventoryProvider` now gathers the parser inputs with the existing `hearth.proc.run` argv-only subprocess seam:

1. `v4l2-ctl --list-devices` lists current video nodes.
2. `udevadm info --query=property --name=/dev/videoN` resolves USB parent identity for each listed node.
3. `v4l2-ctl --device=/dev/videoN --all` reads the per-node `Device Caps` block.
4. Only capture-capable nodes receive `v4l2-ctl --device=/dev/videoN --list-formats-ext`.

Every command has a configurable positive timeout (3 seconds by default). A top-level listing failure raises `CaptureDiscoveryError`. A per-node failure is returned as a `CaptureProbeIssue` alongside the other discovered nodes; it does not cause another node to be silently substituted. The inventory is a one-shot snapshot and creates no watcher, timer, service, device fd or persistent process.

Provider tests inject a fake `proc` runner and assert the exact commands, timeout and output handling. The provider has **not** been called on the target machine, so command availability, udev property shape, device permissions, busy behavior and the GUV302G's actual nodes/modes remain unverified.
