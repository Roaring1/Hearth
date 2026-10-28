# Hearth

A GTK4 control panel for a PipeWire audio rig on Linux: virtual mixer buses,
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
- **Setup window** — shows routing faults and their relevant fixes, with an
  expandable unit list plus headset target/latency, mic-path and Moonlight
  source settings, and LPD8 status/reconnect controls.
- **Mic routing** — choose what feeds each mic bus; Setup shows the configured
  signal path and switches Moonlight between the Stream and Chat sources.
- **LPD8 Controls** — inspect and remap all eight pads and knobs; live control
  state is reflected from the mixer snapshot. Hotplug recovery is handled by
  the rig’s companion watcher, when installed.
- **Single instance + IPC** — a second launch focuses the running window
  instead of starting a rival process.
- **Status report** — `hearth --status` prints a plain-language report on the
  whole rig: what is wrong, how to fix it, and one line each for the voice
  effects, screen-share audio, microphone and control surfaces. Needs no
  display, so it works over SSH.
- **Status dump** — `hearth --dump` asks the running mixer to write a
  plain-language rig status summary to the state directory (`hearth --paths`
  prints the exact location).

---

## Requirements

- Python 3.11+
- GTK4 with PyGObject (`gi.repository.Gtk` 4.0)
- Cairo bindings for GTK drawing (`python3-cairo` / `python-cairo`)
- `pactl`, `parec` and `pw-link` from PipeWire's PulseAudio compatibility tools

```bash
# Fedora / Nobara
sudo dnf install python3-gobject gtk4 python3-cairo pipewire-utils pulseaudio-utils

# Arch
sudo pacman -S python-gobject gtk4 python-cairo pipewire libpulse

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 python3-cairo pipewire-bin pulseaudio-utils
```

---

## Install

```bash
git clone https://github.com/Roaring1/Hearth.git
cd Hearth

# PyGObject comes from the distro, not pip. Expose those system packages
# to pipx so the isolated app can import GTK4.
pipx install --system-site-packages .

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

For live routing behavior, including the mic-router config authority, exact
loopback matching, and what happens when a route command fails, see
[`docs/AUDIO_ROUTING.md`](docs/AUDIO_ROUTING.md).

---

## Usage

```bash
hearth                # launch, or focus the existing instance
hearth --status       # plain-language report on the whole rig
hearth --show         # bring the window to the front
hearth --unmute       # unmute every bus, headless (panic button)
hearth --dump         # write the running rig's status summary
hearth --paths        # print every location Hearth resolves
hearth --quit         # quit the running instance
hearth --version      # print version
```

`--status` answers "is my audio fine?" without a display, and is the first
thing worth pasting into a bug report:

```text
Everything is working

Services        All 18 monitored services are healthy
Voice effects   Voice effects on for Stream
Screen share    Ready to share Game, Music when you start a screen share
Microphone      No microphone recordings yet - start the recorder to check your mic
Controls        LPD8 pad controller connected - knobs and pads are live
```

**Keybinds:** `Ctrl+,` opens Setup · `Ctrl+L` opens LPD8 Controls.

---

## Architecture

A GTK4 window on top of a toolkit-free core. One rule shapes the whole
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
- **Status report** — `hearth --status` gathers the broader rig diagnosis on
  demand, rather than making the fast mixer poll run every health probe.
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

The LPD8 mapping editor is documented in [`docs/LPD8_REMAP.md`](docs/LPD8_REMAP.md);
it updates the local script atomically and protects concurrent remaps.

---

## License

MIT — see [LICENSE](LICENSE).
