# Screen Share and Capture Mode - UI plan

**Checkpoint: 2026-11-10.** This describes the first no-card UI slice, not a claim that capture is operational.

## Where the UI lives

Hearth keeps the main mixer as the everyday audio surface. Capture Mode is a related but separate workflow, so it opens in its own lightweight **Screen Share** overview window rather than turning the mixer into a general settings dashboard.

There are two visible ways to open the same overview:

1. A persistent **SCREEN SHARE** button/entry in the main mixer. It remains present when the Screen Share strip is collapsed, so a new user does not need to discover a hidden strip, keyboard shortcut or context menu.
2. A **SET UP** button on the Screen Share strip itself, next to the place the user already adjusts its level and mute state.

Both paths reuse one lazily-created window. Closing it hides the window; reopening it presents the same instance. It performs no capture-device probe and starts no background activity. The current default-collapsed preference for the Screen Share strip is preserved.

## Overview content

The overview should answer four questions in order, using user language rather than internal PipeWire terminology. On KDE Wayland, the expected source-selection UI is the desktop capture portal opened by Vesktop, not necessarily a custom grid drawn inside Vesktop; see [the Wayland source research](CAPTURE_MODE_WAYLAND_RESEARCH.md). The installed Flatpak version and local dialog remain unverified.

- **What video is being shared?** Capture will create a separate video-only window titled `Hearth - Share Output`. This is the window to choose in Vesktop, not the whole desktop. Device discovery is not available in the current overview; nothing is opened merely by looking at this page.
- **What audio will friends hear?** Show the saved Vesktop audio selection as saved intent, not as proof of live signal. At this checkpoint the machine's saved selection is Game + Music directly into Vesktop. The Screen Share master strip is not yet connected to that route. The overview says this plainly and does not imply its fader changes the audio currently sent to friends.
- **What does Hearth do?** Hearth will check the capture device and start/stop the local capture session. This overview does not probe the device and contains no fake or inert Start/Stop controls.
- **What does Vesktop do?** The owner manually picks `Hearth - Share Output`, enables Share Audio in the dialog, checks the preview and manually starts sharing. Hearth never presses Go Live.

The three-step user flow is: start capture in Hearth; choose the video-only window in Vesktop; enable audio and start sharing manually.

## Window separation after the session core exists

The eventual active experience has two independent windows:

- **Capture Controls:** an ordinary movable window, kept outside the shared picture. Its first read-only slice now shows USB video inputs/modes, saved Vesktop audio intent, and explicit Refresh. The eventual active window will add Start/Stop, audio mode, mute, and cleanup feedback only when those operations have real backing adapters.
- **Hearth - Share Output:** a clean video-only viewer that Vesktop captures. No controls, menus, OSD, branding or status text over the picture. A plain **Video stopped updating** card is reserved for the confirmed timestamp-stall path; mostly-black or unchanged pixel observations are advisory and never prove a lost HDMI signal.

The overview is not a replacement for either active window. It is the novice-friendly starting point and workflow explanation. The main mixer remains available to adjust everyday audio buses.

## What is intentionally not implemented in this first slice

- The overview itself performs no capture-device discovery, file-descriptor open, viewer start, active session, graph watcher, timer or service. The separate Capture Controls preflight does one bounded read-only V4L2 inventory when explicitly opened or refreshed.
- No Start Capture or Stop Capture button until the session core can implement those actions transactionally. A disabled-looking button that cannot do anything would mislead users.
- No source-selection editor or live route migration. Full user-managed VM-SHARE routing remains deferred in [issue #10](https://github.com/Roaring1/Hearth/issues/10).
- No automatic Vesktop share/Go Live action, Discord microphone selector, webcam controls, OBS dependency or monitor input switching.
- No claim that a saved selection is a live PipeWire route or that the Screen Share fader controls Vesktop today.

## Work that can proceed without the card

1. Build and test this overview with synthetic saved selection data.
2. Review and document the intended Share Output window behavior and mpv/V4L2 viewer constraints.
3. Build the toolkit-free capture provider and fake-device/session lifecycle tests, with no live hardware requirement for ordinary development.
4. Complete the Capture Controls/Share Output UI shell, wiring only actions that have a real core implementation.
5. Refresh the README and handoff after each product boundary changes.

The real Wayland portal/Vesktop, monitor-input, lock/DPMS and remote-share matrix is not reproducible in Xvfb. While the user's normal desktop is in use, do not launch a second Wayland session, create a competing desktop, switch monitor inputs, lock/blank displays or start a real share. Use isolated Xvfb for GTK composition and wait for a separately agreed safe interactive test window for the compositor-specific matrix in [issue #13](https://github.com/Roaring1/Hearth/issues/13).

## Current issue order

- [#16](https://github.com/Roaring1/Hearth/issues/16): novice-facing overview and in-mixer discovery entry points - implemented.
- [#13](https://github.com/Roaring1/Hearth/issues/13): upstream research and fixture/documentation work; interactive portal tests deferred until safe.
- [#11](https://github.com/Roaring1/Hearth/issues/11): transactional session core and resource ownership; production route adapter remains missing.
- [#12](https://github.com/Roaring1/Hearth/issues/12): Capture Controls preflight and an optional injected session-snapshot UI consumer are implemented. The production app does not yet construct/inject a session manager; transactional Start/Stop and Share Output integration remain blocked on route ownership.
- [#10](https://github.com/Roaring1/Hearth/issues/10): full user-managed VM-SHARE routing editor/migration remains deferred.
- [#14](https://github.com/Roaring1/Hearth/issues/14): post-V1 parking lot.
- [#10](https://github.com/Roaring1/Hearth/issues/10): configurable source editor and one-path migration - explicitly deferred for now.
- [#14](https://github.com/Roaring1/Hearth/issues/14): post-V1 parking lot.

## Follow-up status - 2026-11-10

The first UI slice is implemented in draft PR #17 (`a19d163`) and hosted CI passed all six jobs. The main mixer has a persistent **SCREEN SHARE** entry and the Screen Share strip has a clear **SET UP** action; both open the same lazy overview. A separate desktop shortcut adds a third, familiar entry point without requiring a terminal or CLI.
The separate **Use Capture Card** desktop launcher now opens the independent **Hearth - Capture Controls** window. It uses the existing control socket or the single-instance `Gio.Application` command-line route. Capture Controls runs one bounded V4L2 inventory check when opened and another only when Refresh is selected; there is no watcher, polling, or background service. It shows verified USB video inputs and enumerated modes, plus the saved Vesktop audio selection as saved intent. It does not query PipeWire, open video frames, or change audio routes. Start/Stop are deliberately absent until the production audio-route owner and cleanup path exist. The mixer SCREEN SHARE entry and strip SET UP action still open this same lazy overview.
The Screen Share strip's **SET UP** action carries a small hand-authored pop-out glyph, and its tooltip/accessibility name explicitly identify that it opens a separate window. The persistent **SCREEN SHARE** entry remains visible when the strip is collapsed.

The no-card session-core work is in draft PR #18. It now has fixture-backed device/route resolution, transactional session ownership, an owned mpv Share Output process with first-frame readiness, timestamp-stall monitoring, private frame-health metadata, a lazy logind inhibitor adapter and structured mpv input-open errors. The mpv child is the V4L2 FD owner; no second Hearth-side device FD is opened. Because mpv IPC may return only `loading failed`, a mode-0600 log lives in the private session runtime directory and is read only on failure; it is removed on Stop. A synthetic `/dev/video999` attempt confirmed real mpv's missing-device error maps to `device_unavailable`. These are testable software contracts, not proof of GUV302G hardware behavior.

The eventual two-window contract remains unchanged:

- **Capture Controls** is a separate movable Hearth window, never part of the shared picture. Its first read-only slice is implemented: one-shot USB video inventory, discrete driver modes, the saved Vesktop audio selection as saved intent, and explicit Refresh. The **Use Capture Card** desktop shortcut opens it through the single-instance path. The in-mixer SCREEN SHARE and SET UP entry points continue to open the novice overview. Session Start/Stop, audio mode, mute and cleanup feedback remain unavailable until their backing adapters and real lifecycle path exist.
- **Hearth - Share Output** is a clean fullscreen video-only window. It has no controls, menus, OSD, branding or status text. A plain **Video stopped updating** card is reserved for the confirmed timestamp-stall path; mostly-black or unchanged pixel observations are advisory and never prove a lost HDMI signal.

There is still no production Start/Stop path, no real CaptureAudioAdapter or route mutation, and no production CaptureSessionManager injected into Controls. The Controls widget can consume injected snapshots and display typed failures/stall state, but the running app does not yet supply them. No active GUV302G or live Wayland/Vesktop portal test has been performed. The Share Output window itself has the timestamp-stall overlay. Do not add inert controls. Full user-managed VM-SHARE editing/migration remains deferred in #10, and the interactive KDE/Vesktop matrix remains deferred in #13 until a safe test window is explicitly arranged.
