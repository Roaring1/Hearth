# Capture Mode viewer and Wayland portal research

**Source review checkpoint: 2026-11-10.** This is upstream documentation/source inspection only. The actual Nobara KDE Wayland + Vesktop window-capture matrix has **not** been run. Do not turn any item below into a claim that the user's desktop behaves this way until tested in a safe interactive session.

## 1. What the user sees when choosing a window in Vesktop

Current Electron documentation says `desktopCapturer.getSources()` returns **one selected source** on Linux when using PipeWire. When an application asks for both `window` and `screen`, that single selected source is returned as a window capture. Electron's documentation also shows that cancelling the PipeWire picker can make `getSources()` reject. See [Electron desktopCapturer - Linux caveats](https://www.electronjs.org/docs/latest/api/desktop-capturer/#linux).

The current Vesktop `main` branch source (`src/main/screenShare.ts`, inspected 2026-11-10) does the following:

- Detects Wayland when Linux has `XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY`.
- Calls Electron `desktopCapturer.getSources()` with both `window` and `screen` types.
- On Wayland, takes the single returned source, passes it to Vesktop's renderer picker with `skipPicker: true`, then gives that same source to Electron. Its own custom multi-source picker is used in the non-Wayland branch.

See [Vesktop's current screenShare.ts](https://github.com/Vencord/Vesktop/blob/main/src/main/screenShare.ts), particularly `isWayland` and `setDisplayMediaRequestHandler`.

**Implication for our instructions:** for current upstream Wayland code, the expected flow is likely: click Share Screen in Vesktop, then choose **Hearth - Share Output** in the desktop/system portal picker (KDE's chooser), not in a second custom grid inside Vesktop. The portal docs define WINDOW and MONITOR source types. See [XDG ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.impl.portal.ScreenCast.html).

This is source-level evidence, not proof about the exact Vesktop Flatpak build currently installed on Nobara. The real version and portal behavior still need the interactive matrix. Do not assume that current `main` exactly matches the installed build.

The current Vesktop Wayland code inspected here does not pass Electron's persistent source restore token options; plan for the user to choose the window again on a new share unless the installed version's behavior proves otherwise. Do not try to automate or retain screen-capture consent in Hearth. The user remains the one who initiates sharing in Vesktop.

The Flathub Vesktop README states that its Flatpak runs through Wayland by default with X11 fallback and says screen sharing should work out of the box. This is package guidance, not a local installation check: [Flathub Vesktop README](https://github.com/flathub/dev.vencord.Vesktop/blob/master/README.md#wayland).

## 2. mpv candidate behavior and Wayland constraints

mpv remains the leading lightweight child-process viewer candidate, but no final command line or viewer choice is accepted yet.

Documented controls useful for a video-only candidate:

- `--title="Hearth - Share Output"` sets the video window title.
- `--osc=no` disables mpv's on-screen controller.
- `--osd-level=0` disables OSD.
- `--cursor-autohide=always` keeps the cursor hidden.
- `--input-builtin-bindings=no` disables mpv's built-in key bindings at startup. This is preferable to letting keyboard input in the shared picture window seek, pause, or expose controls.
- `--ao=null` produces no audio output while maintaining video playback speed. It is a candidate for ensuring the viewer does not become a second audio path.
- `--fullscreen` requests fullscreen.

References: [mpv Window options](https://mpv.io/manual/stable/#window), [mpv Input options](https://mpv.io/manual/stable/#input), [mpv OSD options](https://mpv.io/manual/stable/#osd), [mpv Audio output drivers](https://mpv.io/manual/stable/#audio-output-drivers).

Important Wayland limitations from the current mpv manual:

- `--screen` does **not** place a window on a chosen Wayland output. It only influences mpv's initial output guess for initial sizing options. The compositor owns placement.
- `--window-minimized` may minimize a window but Wayland may not support unminimizing it. Runtime support depends on the VO and compositor. Do not design session recovery around minimizing and then unminimizing the viewer.
- `--force-render` can force rendering while a window is not visible on the Wayland VO, but it has a performance cost and is not a substitute for testing what Vesktop actually receives. Keep it off by default until occlusion/minimize testing proves it necessary.
- Fullscreen and the target output are compositor requests. We cannot promise monitor placement or an always-rendering surface from command-line flags alone.
- mpv's `--stop-screensaver` uses the Wayland idle-inhibit protocol where the compositor supports it. This must be tested against the owner's lock/DPMS policy; do not assume a player option replaces a session-lifecycle inhibitor.

## 3. Preliminary viewer recommendation

Keep an adapter around the viewer and prototype **mpv as a child process** first. It isolates frame decode/render from GTK's mixer loop and avoids adding a GStreamer/Python media stack before a need is demonstrated.

The initial test command should be derived from the options above only after the viewer adapter can guarantee:

- argv-only invocation through `hearth.proc`, with an explicit title and private config/input file (never user `~/.config/mpv`);
- no audio output, OSC, OSD, subtitles, menus or default hotkeys;
- correct aspect handling (letterbox rather than stretch);
- child lifetime tied to Hearth, with bounded stop and no orphan process;
- visible failure/no-signal state in Capture Controls rather than a frozen last frame being reported as ready.

Do not yet decide whether the viewer should always start fullscreen. A reliable windowed-first path may be necessary to let the user select the correct display through the compositor, followed by a fullscreen request. The issue #13 matrix must settle this.

## 4. Test matrix changes

When an interactive session is explicitly safe, add these precise checks to #13:

1. Confirm the actual Vesktop build and session type. Click Share Screen and determine whether the system portal picker appears and whether it offers application windows, monitors or both.
2. Select `Hearth - Share Output` in the system picker. Confirm the remote receiver sees that window and not the desktop. Cancel once and confirm the operation cancels cleanly without stale capture.
3. Repeat the flow on a new share. Since current Vesktop Wayland code does not request a persistent restore token, assume a new picker is expected unless the installed build proves otherwise.
4. Compare windowed and fullscreen mpv starts. Establish which output the compositor chooses, whether fullscreen placement can be user-selected, and whether a window survives an input switch that removes an output from KWin.
5. Test minimized, occluded, other virtual desktop and show-desktop behavior separately. Record frame arrival on the remote receiver; a locally visible mpv process is not proof of continuous capture.
6. Verify cursor, mpv OSC/OSD, notifications and other windows do not enter the selected window capture.
7. Test mpv child termination, Hearth termination and no-frame conditions. Friends must not be left watching a misleading frozen frame.
8. Verify idle inhibit on the actual KDE version; report lock and DPMS separately. Do not change monitor inputs or lock the user's desktop as part of an unscheduled test.

## 5. Safety boundary for current work

Until the user explicitly provides a safe interactive window, do only code/source review, fixture design, fake-device tests and isolated Xvfb rendering. Do not create a second Wayland session, start a competing desktop, launch Vesktop sharing, switch inputs, lock/blank/DPMS displays, or manipulate the active focus. Xvfb proves GTK composition only; it cannot prove portal source selection, compositor behavior, live rendering cadence, remote capture or actual lock/DPMS behavior.
