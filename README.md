# Hearth

GTK3 PipeWire audio mixer and control panel for Linux.

Built for an Arch + KDE Plasma Wayland desktop with:
- Astro A50 (stereo-chat + stereo-game outputs)
- Focusrite Scarlett Solo (monitor speakers)
- AKAI LPD8 (hardware knobs/pads -> volume control)
- VM (virtual) audio buses for routing game, chat, music, and laptop audio independently
- Two mic buses (SM7B + Astro mic) with per-bus routing and loopback control
- Carla VST host session management
- Moonlight game streaming mic routing

---

## What it does

- **Mixer strip**: per-bus fader (0-150%), mute toggle, live VU meters (stereo, Cairo gradient), sink-input app list with per-app volume + move-to-bus
- **Services panel**: systemd user service health for all audio routing daemons, restart/stop per service
- **Hardware panel**: Astro A50 target toggle (stereo-chat / stereo-game), Scarlett monitor toggle, Carla instance count + start/stop/dedup
- **Mic routing**: per-bus source assignment, monitor loopback toggle, Moonlight mic source selector
- **LPD8 auto-reconnect**: watcher thread restarts lpd8-mixer service when the device is plugged back in
- **IPC socket**: `hearth --show`, `hearth --unmute`, `hearth --dump`, `hearth --quit`
- **Debug dump**: SIGUSR1 or `hearth --dump` writes tracemalloc snapshot + thread list + RAM to `~/.config/roaring/last_debug/`

---

## Requirements

- Python 3.10+
- GTK3 (`python-gobject`)
- `libappindicator3` (tray icon, optional)
- `pycairo` (VU meter gradient; falls back to solid blocks without it)
- `parec` from PipeWire/PulseAudio tools (VU metering)
- `pactl` (PipeWire-Pulse)
- `tmux` (not required but expected for the broader audio stack)

```bash
# Arch
sudo pacman -S python-gobject python-cairo gtk3 libappindicator-gtk3
```

---

## Usage

```bash
python3 hearth.py              # launch (or focus existing instance)
python3 hearth.py --unmute     # unmute all sinks headless (emergency)
python3 hearth.py --show       # bring window to front
python3 hearth.py --quit       # quit
python3 hearth.py --dump       # trigger in-process debug snapshot
python3 hearth.py --vu-dump    # write RMS/peak CSV (diagnostic)
```

Settings: keybinds T (astro toggle), S (scarlett toggle), U (unmute all), F5 (soft restart), Ctrl+R (hard restart), Ctrl+D (debug dump), Ctrl+L (SSH to laptop), Ctrl+, (settings dialog).

---

## Architecture

Single-file GTK3 app. Threading model:

- **Main thread**: GTK event loop, UI only
- **Collector thread**: polls pactl every 0.25-2s (faster when window visible), pushes data dict to a queue
- **PeakPoller thread**: one persistent `parec` subprocess per monitor source, reads s16le frames non-blocking via select, computes RMS peak
- **GLib 100ms timer (_drain)**: dequeues Collector data, updates all widgets
- **GLib 50ms timer (_vu_tick)**: reads PeakPoller peaks, animates VU bars
- **IPC thread**: Unix socket server at `~/.config/roaring/rac.sock`

---

## Notes

This is a personal tool built around a specific hardware setup. The VM sink names, service names, and routing topology are hardcoded at the top of `hearth.py`. If you're adapting it, `VM_SINKS`, `SERVICES`, `ASTRO_CHAT`, `ASTRO_GAME`, and `SCARLETT` are the main things to change.
