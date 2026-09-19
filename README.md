# Hearth

A GTK3 control panel for a PipeWire audio rig on Linux: virtual mixer buses,
mic routing, per-app volume, live VU metering, and health/control for the
systemd user services that hold the whole thing together.

It is the GUI head of a Voicemeeter-style setup:

- five virtual output buses — **GAME**, **CHAT**, **MUSIC**, **SHARE**
  (screen-share audio, deliberately excluding chat so the far end never
  hears itself) and **LAPTOP**
- two mic buses — B1 (stream/OBS) and B2 (Discord/Moonlight)
- an Astro A50 headset with independent `stereo-chat` / `stereo-game` targets
- a Focusrite interface for monitor speakers, with optional room correction
- an AKAI LPD8 as a hardware control surface
- network audio to a second machine over RTP
- Carla VST host session management
- Moonlight game-streaming mic routing

Developed and run on **Nobara Linux (Fedora-based) with KDE Plasma on
Wayland**. Nothing is Nobara-specific; any PipeWire desktop should work.

---

## What it does

- **Mixer strip** — per-bus fader (0–150%), mute, live stereo VU meters, and
  the list of applications feeding each bus with per-app volume and
  move-to-another-bus.
- **Services panel** — systemd user unit health for every audio daemon, with
  per-service restart/stop, plus soft and hard restart of the whole stack.
- **Hardware panel** — Astro A50 target toggle, monitor-speaker toggle, room
  correction toggle, Carla instance count with start/stop/dedup.
- **Mic routing** — per-bus source assignment, monitor loopback toggles, and
  the Moonlight mic source selector.
- **LPD8 auto-reconnect** — a watcher restarts the `lpd8-mixer` service when
  the controller is unplugged and plugged back in.
- **Single instance + IPC** — a second launch focuses the running window
  instead of starting a rival process.
- **Status report** — `hearth --status` prints a plain-language report on the
  whole rig: what is wrong, how to fix it, and one line each for the voice
  effects, screen-share audio, microphone and control surfaces. Needs no
  display, so it works over SSH.
- **Debug dump** — `SIGUSR1` or `hearth --dump` writes a tracemalloc
  snapshot, thread list, and memory figures to the state directory
  (`hearth --paths` prints the exact location).

---

## Requirements

- Python 3.11+
- GTK3 with PyGObject
- `pycairo` — VU meter gradients (falls back to solid blocks without it)
- `libappindicator` / StatusNotifier support — tray icon, optional
- `pactl` and `parec` from PipeWire's PulseAudio compatibility tools

```bash
# Fedora / Nobara
sudo dnf install python3-gobject gtk3 python3-cairo \
                 libappindicator-gtk3 pipewire-utils pulseaudio-utils

# Arch
sudo pacman -S python-gobject gtk3 python-cairo \
               libappindicator-gtk3 libpulse

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 python3-cairo \
                 gir1.2-ayatanaappindicator3-0.1 pulseaudio-utils
```

---

## Install

```bash
git clone https://github.com/Roaring1/Hearth.git
cd Hearth

# recommended: an isolated install that puts `hearth` on PATH
pipx install .

# or run straight from the checkout, without installing
python3 hearth.py

# then install the launcher and icon
install -Dm644 data/icons/hicolor/256x256/apps/io.github.roaring1.Hearth.png \
  ~/.local/share/icons/hicolor/256x256/apps/io.github.roaring1.Hearth.png
install -Dm644 data/applications/io.github.roaring1.Hearth.desktop \
  ~/.local/share/applications/io.github.roaring1.Hearth.desktop
update-desktop-database ~/.local/share/applications
```

---

## Configuration

Machine-specific details live in `~/.config/hearth/config.json`, never in the
source. Every key is optional; see `data/examples/config.json`.

| Key | Meaning |
| --- | --- |
| `astro_chat_sink`, `astro_game_sink` | PulseAudio sink names for the headset's two targets |
| `monitor_sink` | Sink name for the monitor interface. Leave blank to autodetect |
| `monitor_sink_match` | Substring used for that autodetect (default `Focusrite`) |
| `laptop_host`, `laptop_rtp_port` | Labels for the network-audio target shown in the UI |
| `ssh_host`, `ssh_user`, `ssh_key` | Target for the Ctrl+L "SSH to laptop" shortcut |

UI preferences (refresh rate, VU speed, window size, memory thresholds) are
written by the app to `~/.config/hearth/rac_settings.json`; there is an
example in `data/examples/rac_settings.json`.

The bus topology itself (`VM_SINKS`, `SERVICES`, `MIC_OPTS`) is still defined
at the top of `src/hearth/legacy_core.py`. Adapting Hearth to a different rig
means editing those lists. (`hearth.py` in the repository root is a 26-line
shim kept so existing symlinks and launchers keep working.)

---

## Usage

```bash
hearth                # launch, or focus the existing instance
hearth --status       # plain-language report on the whole rig
hearth --show         # bring the window to the front
hearth --unmute       # unmute every bus, headless (panic button)
hearth --dump         # write a debug snapshot
hearth --vu-dump      # log per-source RMS/peak to a gzipped CSV
hearth --paths        # print every location Hearth resolves
hearth --quit         # quit the running instance
hearth --version      # print version
```

`--status` answers "is my audio fine?" without a display, and is the first
thing worth pasting into a bug report:

```text
Everything is working

Services        All 16 audio services are running
Voice effects   Voice effects on for Stream
Screen share    Ready to share Game, Music when you start a screen share
Microphone      No microphone recordings yet - start the recorder to check your mic
Controls        LPD8 pad controller connected - knobs and pads are live
```

**Keybinds:** `T` Astro target · `S` monitor speakers · `U` unmute all ·
`M` collapse mixer · `F5` soft restart · `Ctrl+R` hard restart ·
`Ctrl+D` debug dump · `Ctrl+L` SSH to laptop · `Ctrl+,` settings ·
`Esc` / `Ctrl+Q` hide to tray.

---

## Architecture

A GTK3 window on top of a toolkit-free core. One rule shapes the whole
tree: **only `hearth.ui` may import `gi`**, enforced by a lint rule rather
than by convention. Everything else is testable with no display, no sound
card and no PipeWire daemon running.

```
hearth/
  cli.py                  entry point and every flag
  paths.py  config.py     XDG locations, machine-specific settings
  proc.py                 the only place a child process is spawned
  audio/pactl.py          sink and module parsing, argv-only commands
  audio/links.py          the PipeWire graph, read from pw-link
  services.py  units.py   systemd, judged by unit kind (see below)
  collector.py            mixer snapshots, frozen dataclasses
  meters.py               parec lifecycle and RMS
  health.py  chain.py     is the rig fine; are the voice effects on
  share.py  mic.py        screen-share audio; microphone analysis
  midi.py  tools.py       control surfaces; the companion scripts
  status.py               all of the above, composed into one report
  reporter.py             that report, kept fresh on its own thread
  theme.py                the Plasma colour scheme, read without GTK
  ipc/                    framed control socket
  ui/                     the only package allowed to import gi
```

The threading model is the load-bearing part:

- **Main thread** — GTK event loop, UI only.
- **Collector thread** — polls `pactl` and `systemctl` every 0.25–2 s
  (faster while the window is visible) and pushes one snapshot per tick.
- **Reporter thread** — composes the whole-rig report every few seconds,
  and delivers it *only when the answer changes*. It is separate from the
  collector because it costs roughly ten times as much per tick and
  describes things that cannot change ten times a second.
- **PeakPoller thread** — one long-lived `parec` per monitor source, read
  non-blocking via `select`, RMS computed per chunk. One persistent stream
  instead of reopening one per sample, which PipeWire's Pulse compatibility
  layer cannot keep up with.
- **IPC thread** — newline-delimited commands over a Unix socket in
  `$XDG_RUNTIME_DIR/hearth/`, so a crash cannot leave a stale socket behind
  after logout.

### Judging a service by its kind

Hearth once reported a perfectly healthy session as "portal: NOT RUNNING".
That class of false alarm is now designed out, because systemd's idea of
success depends on what a unit is:

| kind | healthy state |
| --- | --- |
| daemon | `active (running)` |
| oneshot | `inactive (dead)` — it did its job and exited |
| timer | `active (waiting)` |
| socket | `active (listening)` |
| socket-activated service | `inactive (dead)` until something connects |

So where a unit's state cannot answer the question, Hearth measures the
effect instead: screen-share and voice-effect health come from the live
PipeWire graph, never from a unit. And a probe that could not run reports
*unknown*, never *broken* — "no links" and "could not ask" are opposite
facts.

---

## Companion scripts

Hearth drives a set of shell daemons in `~/bin` (`roaring_vm_sinks.sh`,
`roaring_mic_bussesd.sh`, `no_noise_ctl.sh`, `lpd8_mixer.sh`, and others).
Those are not yet part of this repository, so a fresh clone gives you the
control panel and the mixer, while the service-control buttons will report
missing units until an equivalent stack exists on the machine.

---

## License

MIT — see [LICENSE](LICENSE).
