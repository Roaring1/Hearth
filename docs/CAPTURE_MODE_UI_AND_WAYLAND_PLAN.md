# Capture Mode Controls and KDE Wayland/Vesktop Plan

**Design checkpoint: 2026-11-24.** This is a design and test plan only. It does not claim that the installed Vesktop build, KDE portal picker, real capture card, or live Wayland behavior has been verified.

## Product boundary

Capture Mode remains separate from the everyday Hearth mixer. Capture Controls is the operator surface and Share Output is the clean video-only window selected manually by the user in Vesktop. Hearth must never press Go Live, choose a portal source on the user's behalf, or imply that saved audio intent is a live route.

The current no-card UI already supports one-shot USB video discovery and explicit Refresh. The UI must not grow active controls until the session core has a production audio-route lease and cleanup owner. In particular, do not add buttons that are merely disabled placeholders.

## Capture Controls layout

Keep the window compact and organize it in this order:

1. **Video input** - selected physical USB capture device, supported discrete modes, and a short status. Never display volatile /dev/videoN paths as the user's device identity. If discovery is unavailable or ambiguous, say so rather than selecting the first device.
2. **Audio source** - show the verified USB-associated source when there is exactly one match. Show "Not identified" for no match, "Choose a source" for multiple matches, and "Could not check" for incomplete PipeWire inventory. A saved Vesktop selection is labelled as saved intent, not current signal or route state.
3. **Session** - show DORMANT, STARTING, RUNNING, STOPPING, or FAILED only from a real injected session snapshot. While dormant, offer Start only after the route lease, viewer startup, first-frame readiness, and rollback/cleanup path are implemented together. While running, offer Stop only when the manager owns every resource it will release.
4. **Sharing instructions** - one concise instruction to select "Hearth - Share Output" in Vesktop and start sharing manually. This is guidance, not an automation affordance.

Refresh is an explicit one-shot action. It may be disabled while a discovery request is already in flight. Opening the window must not start a watcher, poller, viewer, session, or route mutation.

## State and failure presentation

- Discovery states are distinct: ready, no compatible device, ambiguous device, and probe unavailable. Unknown is never rewritten as "none".
- A USB video device and its associated audio source are matched by stable USB identity (serial preferred, physical USB parent path as fallback), never by PipeWire object ID, ALSA card number, or /dev/videoN.
- An incomplete PipeWire inventory cannot produce a confident "no matching source" result.
- Session state and error category come from typed snapshots. Do not expose raw exception text, private cleanup details, or device paths in user-facing labels.
- A timestamp-stall warning is shown only when the session monitor confirms timestamp progress stopped. Black frames and unchanged pixels remain advisory, not proof of signal loss.
- Cleanup failure remains visible as FAILED and blocks a new start until the manager can prove prior resources are released. Never silently start a second viewer or route lease.

## KDE Wayland and Vesktop spike plan (issue #13)

### Evidence already available

Source review of current upstream Electron/Vesktop indicates that Linux Wayland screen capture is expected to use the desktop portal picker, with a single selected source passed back to Electron. That is not proof about the installed Vesktop Flatpak build. The exact local Vesktop version, portal implementation and behavior remain unverified. Xvfb tests validate GTK composition and synthetic viewer behavior only; they cannot validate KDE portal consent, compositor placement, remote capture, minimize/occlusion, or DPMS.

### Safe interactive matrix - run only in an explicitly approved window

1. Record Vesktop version, package source, KDE Plasma version, Wayland session type, and portal backend version. Do not print unrelated process environment.
2. Click Share Screen in Vesktop and record whether KDE's portal picker appears, which source types it offers, and whether the picker is owned by the desktop portal rather than a Hearth custom picker.
3. Select "Hearth - Share Output". Confirm the remote receiver sees only that video window, not the desktop or Capture Controls.
4. Cancel a picker once and confirm Hearth/Vesktop return cleanly with no stale capture session. Repeat a fresh share and determine whether the user must select the source again.
5. Compare windowed and fullscreen viewer behavior. Record compositor placement and whether the user can choose the output. Do not assume mpv command-line output flags override KWin.
6. Separately test minimized, occluded, other virtual desktop, and show-desktop behavior. Verify frame arrival at the remote receiver rather than inferring it from a running mpv process.
7. Check cursor visibility, OSC/OSD, subtitles, notifications, and other-window leakage into the selected capture.
8. Test orderly Stop, forced viewer exit, Hearth exit, and no-frame behavior. The receiver must not be left with an apparently live frozen frame.
9. Test idle inhibit, lock, and DPMS separately and only with the owner's explicit permission. Do not switch monitor inputs, lock/blank displays, or create a second Wayland session during ordinary development.

### Non-interactive work allowed before that window

Continue with source review, typed contracts, synthetic fixtures, fake backends, isolated Xvfb rendering, and documentation. No live Vesktop share, Wayland session, portal invocation, monitor switching, lock/DPMS operation, or focus manipulation is part of this work.

## Implementation order

1. Keep the existing read-only preflight and fixture-backed USB identity association.
2. Complete the production audio route adapter and transactional ownership/rollback before exposing Start/Stop.
3. Inject a typed session manager into Capture Controls; render only snapshots and explicit errors.
4. Integrate Share Output startup and first-frame readiness into the same transaction.
5. Run the approved issue #13 interactive matrix and update the documented defaults from observed behavior, not assumptions.

## Explicit non-goals

No automatic Go Live, portal automation, camera/webcam control, OBS dependency, live route migration, background device watcher, or speculative audio source selection. Full user-managed VM-SHARE routing remains separately deferred.
