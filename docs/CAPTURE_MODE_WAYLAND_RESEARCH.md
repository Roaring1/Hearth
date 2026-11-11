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

mpv remains the leading lightweight child-process viewer candidate. Hearth now has a pure argv builder, an explicit `MpvCaptureViewer.start()` adapter, and `proc.spawn_owned()` with a Linux parent-thread death guarantee. The adapter is not yet called by a concrete capture runtime, so Capture Mode still does not launch mpv and no real capture stream exists.

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

An isolated Xvfb smoke confirmed mpv 0.40 accepts the clean-window flags and renders a 1280x720 titled test-pattern window. A separate Xvfb check confirmed that the documented `av://v4l2` URL plus lavf mode options reach FFmpeg's V4L2 handler when given a deliberately nonexistent `/dev` path. Exact scope and caveats are recorded below; these are X11/Xvfb command-shape checks only.

The owned-process layer now provides the child-lifetime guarantee. Before Capture Mode launches the viewer, its concrete runtime must also guarantee:

- argv-only invocation through `hearth.proc`, with an explicit title and private config/input file (never user `~/.config/mpv`);
- no audio output, OSC, OSD, subtitles, menus or default hotkeys;
- correct aspect handling (letterbox rather than stretch);
- `MpvCaptureViewer.start()` is called only from an explicit session start and its handle is retained through Stop;
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

## Installed mpv command-shape smoke (isolated Xvfb only)

A no-desktop smoke test was run on 2026-11-10 with installed **mpv v0.40.0** and ffmpeg 7.1.2. ffmpeg generated a temporary 1280x720/30fps test pattern under `/tmp`; mpv ran under a private Xvfb display with:

```text
--no-config
--vo=x11 --hwdec=no
--title="Hearth - Share Output"
--osc=no --osd-level=0
--cursor-autohide=always
--input-builtin-bindings=no
--ao=null
--loop-file=inf --force-window=yes
```

Observed in that isolated X11 test only:

- Window title was exactly `Hearth - Share Output`.
- Window geometry was 1280x720 on the 1600x1200 Xvfb screen.
- A PNG screenshot of the window was captured.
- mpv's playback log advanced through the synthetic four-second file.
- The player was interrupted and waited on by the smoke script; the temporary video, screenshot and log were removed by its cleanup trap.

This confirms that mpv 0.40 accepts the candidate flag set and can produce a titled video window in Xvfb. The test explicitly used `--vo=x11`, so it does **not** prove the `gpu`/Wayland VO, KDE placement/fullscreen, portal selection, minimize/occlusion rendering, remote Vesktop capture or idle-inhibit behavior. No real video device, audio source, logged-in Wayland window, desktop focus, monitor, service or audio route was used. Treat the arguments as a prototype, not a committed viewer command.


## V4L2 mpv input command research

The mpv stable manual documents libavdevice input as `av://type:options`, with this explicit example:

```text
mpv av://v4l2:/dev/video0 --profile=low-latency --untimed
```

The manual describes `--untimed` as a hack to output each captured frame immediately instead of respecting input timestamps. Do **not** add it by default; frame cadence and A/V synchronization must be measured first. See [mpv av:// input documentation](https://mpv.io/manual/stable/#protocols).

The mpv manual also documents `--demuxer-lavf-o=key=value,...` for passing AVOptions to libavformat. FFmpeg's V4L2 input options include `video_size`, `framerate`, and `input_format`; the latter accepts a codec name for compressed video or a pixel format for raw video. See [mpv demuxer-lavf options](https://mpv.io/manual/stable/#demuxer) and [FFmpeg V4L2 options](https://www.ffmpeg.org/ffmpeg-devices.html#video4linux2_002c-v4l2).

Hearth's pure `build_mpv_viewer_argv()` builder has an allow-list mapping for common FourCCs (`MJPG`, `JPEG`, `H264`, `HEVC`, `YUYV`, `UYVY`, `NV12`, `YU12`, `RGB3`, `BGR3`). YV12 and unknown FourCC values fail closed until a correct FFmpeg mapping is verified. The builder sets `--audio=no`, disables OSC/OSD/subtitles/built-in keys, pins the selected size/rate/input format, and uses the resolved ephemeral node path. `MpvCaptureViewer.start()` delegates the argv to `proc.spawn_owned`; no production runtime calls that method yet. The fullscreen request is configurable; the default asks for fullscreen, but the Wayland compositor still controls actual output placement.

The installed mpv 0.40.0 + FFmpeg build was tested with a deliberately nonexistent path under Xvfb using the exact `av://v4l2` form and explicit lavf options from the builder. mpv reported `Cannot open video device /dev/hearth-no-such-video-device`, proving that the URL reached FFmpeg's V4L2 input handler rather than failing as an unknown protocol. A follow-on `Failed to recognize file format` is mpv's terminal response after the demuxer's expected missing-node failure. This did not open any real node. The build's `ffmpeg -devices` lists `video4linux2,v4l2` as an input device.

The command builder, adapter and owned process primitive are implemented, and the viewer now has a bounded first-frame IPC check. No actual capture stream has been opened and no input-format mapping has been verified against the target card. `proc.spawn_owned()` waits for the PDEATHSIG/exec handshake and keeps the creating thread alive through child exit; its synthetic tests cover owner-thread death, the pre-prctl race, setup failure and bounded Stop. The remaining work is to wire the adapter and first-frame check into the session runtime and monitor frame loss/stalls after readiness.


## mpv libavdevice command-construction smoke (Xvfb)

A second isolated check used the mpv v0.40.0 `av://` input handler with `av://lavfi:testsrc=size=1280x720:rate=30`, running under private Xvfb and `--vo=x11`. The window title was `Hearth - Share Output`, geometry 1280x720, with audio, OSC, OSD, subtitles and built-in bindings disabled. The player and Xvfb session were stopped by the test wrapper. This validates only the libavdevice URL path and presentation shell with a synthetic lavfi source. It is not a V4L2 device test and not a Wayland/portal test.

The candidate viewer argv is now assembled in `src/hearth/capture_viewer.py`. It uses mpv's documented `av://v4l2:/dev/videoN` form and `--demuxer-lavf-o` for exact input options. `docs/CAPTURE_MODE_SESSION_CORE.md` records the current option policy and remaining process-ownership requirements. The actual viewer adapter has not started mpv against a real `/dev/videoN`.


## First-frame readiness check (synthetic Xvfb)

The mpv viewer wrapper now creates a unique IPC socket under a private 0700 directory in `XDG_RUNTIME_DIR`. It does not use a shared `/tmp` socket. After launch, `wait_for_first_frame()` opens a bounded local JSON IPC connection and queries `video-frame-info/picture-type`, `video-params` and `vo-configured`. It only returns Ready when a current decoded frame has type I/P/B, dimensions are positive, and the VO is configured. A child exit or deadline becomes a failure; the socket directory is removed only after Stop reaps the child.

This was exercised with mpv 0.40.0 under Xvfb using the synthetic `av://lavfi:testsrc=size=640x360:rate=30` source: the readiness result was 640x360, picture type I; the child was stopped and the private runtime directory removed. No real V4L2 device or Wayland session was used. This confirms the first-frame IPC mechanics only, not capture-card mode correctness or ongoing stream health.
