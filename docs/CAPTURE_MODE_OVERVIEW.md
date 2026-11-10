# Screen Share and Capture Mode - UI plan

**Checkpoint: 2026-11-10.** This describes the first no-card UI slice, not a claim that capture is operational.

## Where the UI lives

Hearth keeps the main mixer as the everyday audio surface. Capture Mode is a related but separate workflow, so it opens in its own lightweight **Screen Share** overview window rather than turning the mixer into a general settings dashboard.

There are two visible ways to open the same overview:

1. A persistent **SCREEN SHARE** button/entry in the main mixer. It remains present when the Screen Share strip is collapsed, so a new user does not need to discover a hidden strip, keyboard shortcut or context menu.
2. A **SET UP** button on the Screen Share strip itself, next to the place the user already adjusts its level and mute state.

Both paths reuse one lazily-created window. Closing it hides the window; reopening it presents the same instance. It performs no capture-device probe and starts no background activity. The current default-collapsed preference for the Screen Share strip is preserved.

## Overview content

The overview should answer four questions in order, using user language rather than internal PipeWire terminology:

- **What video is being shared?** Capture will create a separate video-only window titled `Hearth - Share Output`. This is the window to choose in Vesktop, not the whole desktop. Device discovery is not available in the current overview; nothing is opened merely by looking at this page.
- **What audio will friends hear?** Show the saved Vesktop audio selection as saved intent, not as proof of live signal. At this checkpoint the machine's saved selection is Game + Music directly into Vesktop. The Screen Share master strip is not yet connected to that route. The overview says this plainly and does not imply its fader changes the audio currently sent to friends.
- **What does Hearth do?** Hearth will check the capture device and start/stop the local capture session. This overview does not probe the device and contains no fake or inert Start/Stop controls.
- **What does Vesktop do?** The owner manually picks `Hearth - Share Output`, enables Share Audio in the dialog, checks the preview and manually starts sharing. Hearth never presses Go Live.

The three-step user flow is: start capture in Hearth; choose the video-only window in Vesktop; enable audio and start sharing manually.

## Window separation after the session core exists

The eventual active experience has two independent windows:

- **Capture Controls:** ordinary movable controls, kept outside the shared picture. It owns device status, Start/Stop, audio mode, mute, and cleanup feedback.
- **Hearth - Share Output:** a clean video-only viewer that Vesktop captures. No controls, menus, OSD, branding or status text over the picture; only a plain No signal card is allowed when frames stop.

The overview is not a replacement for either active window. It is the novice-friendly starting point and workflow explanation. The main mixer remains available to adjust everyday audio buses.

## What is intentionally not implemented in this first slice

- No capture device discovery, file descriptor, viewer process, active session, graph watcher, timer or service.
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

- [#16](https://github.com/Roaring1/Hearth/issues/16): overview and discovery UI - current implementation slice.
- [#13](https://github.com/Roaring1/Hearth/issues/13): design/fixture work can proceed; interactive portal tests deferred until safe.
- [#11](https://github.com/Roaring1/Hearth/issues/11): fake-device and transactional session core - later.
- [#12](https://github.com/Roaring1/Hearth/issues/12): active controls and clean viewer - later.
- [#10](https://github.com/Roaring1/Hearth/issues/10): configurable source editor and one-path migration - explicitly deferred for now.
- [#14](https://github.com/Roaring1/Hearth/issues/14): post-V1 parking lot.
