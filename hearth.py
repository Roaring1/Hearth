#!/usr/bin/env python3
# 28/05/2026
# hearth -- GTK3 PipeWire mixer + control panel (Nobara, KDE Plasma Wayland)
#
#   - manages VM sinks, Astro A50, Scarlett Solo, Carla, Moonlight, LPD8
#   - Collector + PeakPoller threads; IPC via Unix socket (rac.sock)
#   - 28/05/2026: vesktop compat -- portal health monitor + ↺ restart, default-sink display
#                 for venmic only_default_speakers check, venmic log in log viewer
#   - 28/05/2026: fix crash -- col/row in QA grid loop shadowed Collector instance in closure
#   - 28/05/2026: fix toggle direction -- [M] now hides overview notebook, not mixer faders
#   - 28/05/2026: fix app list in strips -- removed ScrolledWindow (scroll bleed + unusable btns)
#                 compact label now shows app names; interactive controls in ⋮ popover
#   - 28/05/2026: GUI pass -- mixer collapse [M], tighter strips, compact overview
#   - 28/05/2026: LPD8 pad ref in expander, QA 2-col grid, padfire condensed
#   - 28/05/2026: tracemalloc depth 25->10
#   - 28/05/2026: batch svc/vol/mute queries -- ~25 fewer subprocess spawns/tick
#   - 28/05/2026: loopback latency_msec now reads S["latency_msec"] not hardcoded 10
#   - 28/05/2026: fix double pkill at startup killing parec procs PeakPoller just launched
#   - 28/05/2026: fix @keyframes pulse-bad keyframe order (50% was after 100%)
#   - 28/05/2026: antivibe pass, renamed to hearth.py
#   - 06/05/2026: LPD8 auto-reconnect watcher + manual reconnect button
#   - 06/05/2026: carla_count matched bwrap wrappers, not just real process
#   - 06/05/2026: mic loopback toggle now creates PW modules (was conf-only)
#   - 29/04/2026: SIGUSR1/--dump -> tracemalloc snapshot in last_debug/
#   - 29/04/2026: _drain used remove() not destroy() -> 11 GB RAM leak

import os, re, json, math, time, queue, signal, socket, threading, subprocess, argparse
import tracemalloc as _tracemalloc
from pathlib import Path

VER         = "4.7"
APP_ID      = "hearth"
HOME        = Path.home()
BIN         = HOME / "bin"
CFGDIR      = HOME / ".config" / "hearth"
SOCK        = str(CFGDIR / "rac.sock")
PIDF        = str(CFGDIR / "rac.pid")
SETTF       = str(CFGDIR / "rac_settings.json")

# One-time migration from old ~/.config/roaring/ location
_OLD_CFGDIR = HOME / ".config" / "roaring"
for _old, _new in [
    (_OLD_CFGDIR / "rac_settings.json", CFGDIR / "rac_settings.json"),
]:
    if _old.exists() and not _new.exists():
        import shutil as _shutil
        CFGDIR.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(str(_old), str(_new))
MXCONF      = str(HOME / ".config" / "roaring_mixer.conf")
MCCONF      = str(HOME / ".config" / "roaring_mic_router.conf")

ASTRO_CHAT  = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
ASTRO_GAME  = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"
SCARLETT    = "alsa_output.usb-Focusrite_Scarlett_Solo_USB_Y7XZGYX15C77AB-00.Direct__Direct__sink"

VM_SINKS = [
    ("GAME",   "vm_game"),
    ("CHAT",   "vm_chat"),
    ("MUSIC",  "vm_music"),
    ("LAPTOP", "laptop_audio"),
]
MIC_OPTS = ["none", "sm7b", "astro", "both"]

SERVICES = [
    ("pipewire",              False),
    ("pipewire-pulse",        False),
    ("wireplumber",           False),
    ("roaring-vm-sinks",      True),
    ("roaring-mic-busses",    True),
    ("roaring-mic-routesd",   True),
    ("roaring-audio-routesd", True),
    ("roaring-moonlight-mic",  True),
    ("roaring-laptop-audio",   True),
    ("lpd8-mixer",            True),
    ("roaring-carla-session", True),
    ("default-sink-vm-game",  True),
]
CORE = {"pipewire","pipewire-pulse","wireplumber",
        "roaring-vm-sinks","roaring-mic-busses","lpd8-mixer"}

DEFAULT_SETTINGS = {
    "refresh_ms":        2000,
    "vu_enabled":        True,
    "vu_speed":          0.35,
    "mem_warn_mb":       80,
    "mem_crit_mb":       200,
    "start_minimized":   False,
    "notify_fail":       True,
    "latency_msec":      12,
    "mixer_collapsed":   False,
}


def _startup_cleanup():
    # remove leftover empty debug dirs and truncated VU dumps from old sessions
    for _d in sorted((HOME / "Desktop").glob("roaring_debug_*/")):
        try:
            if _d.is_dir() and not any(_d.iterdir()):
                _d.rmdir()
        except Exception:
            pass
    for _gz in HOME.glob("roaring_vu_dump_*.csv.gz"):
        try:
            _gz.unlink()
        except Exception:
            pass


def load_settings():
    try:
        d = json.loads(Path(SETTF).read_text())
        s = dict(DEFAULT_SETTINGS); s.update(d); return s
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    CFGDIR.mkdir(parents=True, exist_ok=True)
    Path(SETTF).write_text(json.dumps(s, indent=2))


# shell helpers

def sh(cmd, timeout=4):
    # shell=True: several callers pipe through awk/grep, so we need /bin/sh
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

def sh_bg(cmd):
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

def run_bin(script, *args):
    parts = [f"bash {BIN}/{script}"] + list(args)
    sh_bg(" ".join(parts))

def pactl_vol(sink):
    m = re.search(r'(\d+)%', sh(f"pactl get-sink-volume {sink} 2>/dev/null"))
    return int(m.group(1)) if m else 0

def pactl_muted(sink):
    return "yes" in sh(f"pactl get-sink-mute {sink} 2>/dev/null").lower()

def svc_state(unit):
    return sh(f"systemctl --user is-active {unit} 2>/dev/null") or "unknown"

def svc_states_batch(units):
    # one systemctl call for all units -- replaces N separate is-active spawns per tick
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "--", *units],
            capture_output=True, text=True
        )
        lines = r.stdout.strip().splitlines()
        return {u: (lines[i].strip() if i < len(lines) else "unknown")
                for i, u in enumerate(units)}
    except Exception:
        return {u: "unknown" for u in units}

def carla_count():
    # bwrap wrappers carry the project path in argv and inflate the count
    # /app/share/carla/carla is only in the real python3 process
    try:
        r = subprocess.run(
            ['pgrep', '-c', '-f', '/app/share/carla/carla'],
            capture_output=True, text=True
        )
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0

def pw_mem_mb():
    raw = sh("systemctl --user show pipewire-pulse.service -p MemoryCurrent 2>/dev/null")
    try:    return int(raw.split("=")[1]) / 1048576
    except: return 0.0

def pw_version():
    return sh("pactl info 2>/dev/null | grep 'Server Name' | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'")

def read_conf(path):
    out = {}
    try:
        for ln in Path(path).read_text().splitlines():
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    except: pass
    return out

def write_conf_key(path, key, value):
    p = Path(path)
    if not p.exists(): return
    text = p.read_text()
    nl = f'{key}="{value}"'
    if re.search(rf'^{re.escape(key)}=', text, re.MULTILINE):
        text = re.sub(rf'^{re.escape(key)}=.*', nl, text, flags=re.MULTILINE)
    else:
        text += f'\n{nl}\n'
    p.write_text(text)

MOONLIGHT_SVC      = Path.home() / ".config/systemd/user/roaring-moonlight-mic.service"
MOONLIGHT_MIC_OPTS = ["b1_mic", "b2_mic"]

def moonlight_mic_source():
    try:
        for ln in MOONLIGHT_SVC.read_text().splitlines():
            if ln.strip().startswith("Environment=MIC_SOURCE="):
                return ln.strip().split("=", 2)[2]
    except Exception:
        pass
    return "b2_mic"

def set_moonlight_mic(src):
    try:
        t = MOONLIGHT_SVC.read_text()
        t = re.sub(r'^(Environment=MIC_SOURCE=).*', rf'\g<1>{src}', t, flags=re.MULTILINE)
        MOONLIGHT_SVC.write_text(t)
    except Exception as e:
        print(f"set_moonlight_mic write error: {e}")
    sh_bg("systemctl --user daemon-reload"
          " && systemctl --user restart roaring-moonlight-mic.service")

def scarlett_on():
    return (HOME / ".cache" / "roaring_scarlett_loopbacks").exists()

def astro_target():
    t = read_conf(MXCONF).get("ASTRO_TARGET", "")
    return "game" if "game" in t else "chat"

def unmute_all():
    for s in [ASTRO_CHAT, ASTRO_GAME, "vm_game", "vm_chat", "vm_music",
              "laptop_audio", SCARLETT]:
        sh_bg(f"pactl set-sink-mute {s} 0")
    sh_bg(f"pactl set-sink-volume {ASTRO_CHAT} 100%")
    sh_bg(f"pactl set-sink-volume {ASTRO_GAME} 100%")

def force_default_sink(sink="vm_game"):
    sh_bg(f"pactl set-default-sink {sink}")


# IPC

def _write_pid():
    CFGDIR.mkdir(parents=True, exist_ok=True)
    Path(PIDF).write_text(str(os.getpid()))

def _read_pid():
    try:    return int(Path(PIDF).read_text().strip())
    except: return None

def _pid_alive(pid):
    try:    os.kill(pid, 0); return True
    except: return False

def ipc_send(cmd, timeout=0.5):
    if not Path(SOCK).exists(): return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout); s.connect(SOCK)
        s.sendall((cmd+"\n").encode()); r = s.recv(256); s.close()
        return r.decode().strip()
    except: return False

def ipc_serve(handler):
    sp = Path(SOCK); sp.parent.mkdir(parents=True, exist_ok=True)
    if sp.exists():
        try: sp.unlink()
        except: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try: srv.bind(str(sp)); srv.listen(4); os.chmod(str(sp), 0o600)
    except: return
    while True:
        try:
            c, _ = srv.accept()
            d = c.recv(256).decode().strip()
            c.sendall(((handler(d) or "ok")+"\n").encode()); c.close()
        except: break


class Collector(threading.Thread):
    def __init__(self, q, settings):
        super().__init__(daemon=True)
        self._q = q
        self._s = settings
        self._log = [str(HOME / ".cache" / "lpd8-mixer.log")]
        self._window_visible = threading.Event()
        self._window_visible.clear()

    def set_log(self, path): self._log[0] = path

    def set_window_visible(self, visible: bool):
        if visible:
            self._window_visible.set()
        else:
            self._window_visible.clear()

    def run(self):
        while True:
            try:    self._tick()
            except Exception: pass
            base_ms = self._s.get("refresh_ms", 2000)
            if self._window_visible.is_set():
                interval = max(0.25, min(0.5, base_ms / 1000))
            else:
                interval = max(0.5, base_ms / 1000)
            time.sleep(interval)

    def _tick(self):
        d = {}
        d["svc"]  = svc_states_batch([u for u, _ in SERVICES])
        _all_sinks = list(VM_SINKS) + [("B1", "mic_b1"), ("B2", "mic_b2")]
        _sink_names = [s for _, s in _all_sinks] + [ASTRO_CHAT, ASTRO_GAME, SCARLETT]

        # single pactl list sinks call to get vol + mute + state for everything
        # replaces 15 separate pactl_vol/pactl_muted subprocess spawns per tick
        _pvol:  dict = {}
        _pmute: dict = {}
        _psink_st: dict = {}
        _pidx_to_sink: dict = {}
        _cur_snk: dict = {}
        for _ln in sh("pactl list sinks 2>/dev/null").splitlines():
            _s = _ln.strip()
            if _s.startswith("Sink #"):
                if _cur_snk.get("name"):
                    _n = _cur_snk["name"]
                    _pvol[_n]     = _cur_snk.get("vol", 0)
                    _pmute[_n]    = _cur_snk.get("muted", False)
                    _psink_st[_n] = _cur_snk.get("state", "--")
                    if _cur_snk.get("idx"):
                        _pidx_to_sink[_cur_snk["idx"]] = _n
                _cur_snk = {"idx": _s.split("#", 1)[-1].strip()}
            elif _s.startswith("State:"):
                _cur_snk["state"] = _s.split(":", 1)[-1].strip()
            elif _s.startswith("Name:"):
                _cur_snk["name"] = _s.split(":", 1)[-1].strip()
            elif _s.startswith("Mute:"):
                _cur_snk["muted"] = _s.split(":", 1)[-1].strip().lower() == "yes"
            elif _s.startswith("Volume:") and "vol" not in _cur_snk:
                _vm = re.search(r'(\d+)%', _s)
                if _vm: _cur_snk["vol"] = int(_vm.group(1))
        if _cur_snk.get("name"):
            _n = _cur_snk["name"]
            _pvol[_n]     = _cur_snk.get("vol", 0)
            _pmute[_n]    = _cur_snk.get("muted", False)
            _psink_st[_n] = _cur_snk.get("state", "--")
            if _cur_snk.get("idx"):
                _pidx_to_sink[_cur_snk["idx"]] = _n

        d["vol"]  = {s: _pvol.get(s, 0)     for _, s in _all_sinks}
        d["mute"] = {s: _pmute.get(s, False) for _, s in _all_sinks}
        d["astro_chat_mute"] = _pmute.get(ASTRO_CHAT, False)
        d["astro_game_mute"] = _pmute.get(ASTRO_GAME, False)
        d["astro_chat_vol"]  = _pvol.get(ASTRO_CHAT, 100)
        d["sink_st"]         = _psink_st

        d["carla"]    = carla_count()
        d["mem_mb"]   = pw_mem_mb()
        d["astro"]    = astro_target()
        d["scarlett"] = scarlett_on()
        d["timer"]    = sh(
            "systemctl --user list-timers roaring-pipewire-restart.timer "
            "--no-pager 2>/dev/null | awk 'NR==2{print $1,$2,$5}'"
        ).strip()
        d["src_st"] = {}
        for _ln in sh("pactl list short sources 2>/dev/null").splitlines():
            _p = _ln.split()
            if len(_p) >= 5: d["src_st"][_p[1]] = _p[4]

        # sink-inputs: app info grouped by sink (pa_index, name, icon_name)
        # reuses _pidx_to_sink from the full sink parse above
        _si_apps: dict = {}
        _cur_si: dict = {}
        for _ln in sh("pactl list sink-inputs 2>/dev/null").splitlines():
            _s = _ln.strip()
            if _s.startswith("Sink Input #"):
                if _cur_si.get("sink_idx") and _cur_si.get("name"):
                    _sn = _pidx_to_sink.get(_cur_si["sink_idx"])
                    if _sn:
                        _si_apps.setdefault(_sn, []).append({
                            "index":     _cur_si.get("pa_idx", ""),
                            "name":      _cur_si["name"],
                            "icon":      _cur_si.get("icon", ""),
                            "vol_pct":   _cur_si.get("vol_pct", 100),
                            "muted":     _cur_si.get("muted", False),
                        })
                _cur_si = {"pa_idx": _s.split("#", 1)[-1].strip()}
            elif _s.startswith("Sink:") and not _s.startswith("Sink Input"):
                _cur_si["sink_idx"] = _s.split(":", 1)[-1].strip()
            elif _s.startswith("Mute:") and "muted" not in _cur_si:
                _cur_si["muted"] = _s.split(":", 1)[-1].strip().lower() == "yes"
            elif _s.startswith("Volume:") and "vol_pct" not in _cur_si:
                _vm = re.search(r'(\d+)%', _s)
                if _vm: _cur_si["vol_pct"] = int(_vm.group(1))
            elif "application.name" in _s and "name" not in _cur_si:
                _m = re.search(r'application\.name\s*=\s*"([^"]+)"', _s)
                if _m: _cur_si["name"] = _m.group(1)
            elif "application.icon_name" in _s:
                _m = re.search(r'application\.icon_name\s*=\s*"([^"]+)"', _s)
                if _m: _cur_si["icon"] = _m.group(1)
        if _cur_si.get("sink_idx") and _cur_si.get("name"):
            _sn = _pidx_to_sink.get(_cur_si["sink_idx"])
            if _sn:
                _si_apps.setdefault(_sn, []).append({
                    "index":   _cur_si.get("pa_idx", ""),
                    "name":    _cur_si["name"],
                    "icon":    _cur_si.get("icon", ""),
                    "vol_pct": _cur_si.get("vol_pct", 100),
                    "muted":   _cur_si.get("muted", False),
                })
        d["sink_inputs"] = _si_apps

        mc = read_conf(MCCONF)
        d["b1_route"] = mc.get("B1_ROUTE", "none")
        d["b2_route"] = mc.get("B2_ROUTE", "none")

        # loopback: does mic_b?.monitor route into any output bus?
        _mods = sh("pactl list short modules 2>/dev/null")
        def _lb(src, _m=_mods):
            return any(
                "module-loopback" in ln and src in ln
                and any(t in ln for t in ("vm_game", "vm_chat", "vm_music"))
                for ln in _m.splitlines()
            )
        d["lb_b1"] = _lb("mic_b1.monitor")
        d["lb_b2"] = _lb("mic_b2.monitor")
        d["pw_ver"]   = pw_version()
        d["moonlight_mic_src"] = moonlight_mic_source()
        try:
            lf = self._log[0]
            d["log"] = "".join(open(lf).readlines()[-80:]) if Path(lf).exists() \
                       else f"(log not found: {lf})"
        except: d["log"] = ""
        while not self._q.empty():
            try: self._q.get_nowait()
            except: pass
        # vesktop: portal alive + default sink name (venmic uses the default sink for
        # its only_default_speakers filter -- if this shows the Astro ALSA name instead
        # of vm_game, apps routing through vm_game may not appear in venmic's picker)
        _vc_portal_st = sh("systemctl --user is-active xdg-desktop-portal-kde 2>/dev/null").strip()
        d["vc_portal_ok"]      = (_vc_portal_st == "active")
        d["vc_default_sink"]   = sh("pactl get-default-sink 2>/dev/null").strip()
        _vc_log = HOME / ".var/app/dev.vencord.Vesktop/.local/state/venmic/venmic.log"
        d["vc_venmic_log_exists"] = _vc_log.exists()
        self._q.put(d)


try:
    import cairo as _cairo
    _HAVE_CAIRO = True
except ImportError:
    _HAVE_CAIRO = False

class VU:
    # smooth Cairo VU bar, L+R correlated stereo, gradient fill, peak hold dot
    # hard idle decay -- drops to zero instantly when parec stops feeding
    def __init__(self, speed=0.35, w=28, h=120):
        import gi; gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        self.da = Gtk.DrawingArea()
        self.da.set_size_request(w, h)
        self._w, self._h  = w, h
        self._running     = False
        self._speed       = speed
        self._vol_scale   = 1.0
        self._lv          = [0.0, 0.0]
        self._tg          = [0.0, 0.0]
        self._tg_shared   = 0.0
        self._pk          = [0.0, 0.0]
        self._pkt         = [0,   0  ]
        self._real_level  = 0.0
        self._real_ts     = 0.0
        self.da.connect("draw", self._draw)

    def feed_level(self, level: float):
        self._real_level = max(0.0, min(1.0, float(level)))
        self._real_ts    = time.monotonic()

    def set_state(self, state, vol=50):
        self._running   = (state == "RUNNING")
        self._vol_scale = max(0.08, vol / 100.0)

    def tick(self):
        use_real = (time.monotonic() - self._real_ts) < 2.0
        if use_real:
            level = self._real_level
            for i in range(2):
                tgt = max(0.0, min(1.0, level))
                self._lv[i] += (tgt - self._lv[i]) * self._speed
                self._lv[i]  = max(0.0, min(1.0, self._lv[i]))
                if self._lv[i] >= self._pk[i]:
                    self._pk[i] = self._lv[i]; self._pkt[i] = 55
                elif self._pkt[i] > 0:
                    self._pkt[i] -= 1
                else:
                    self._pk[i] = max(0.0, self._pk[i] - 0.015)
        else:
            # idle -- instantly drop, no phantom blips
            for i in range(2):
                self._lv[i]   = 0.0
                self._pk[i]   = 0.0
                self._tg[i]   = 0.0
            self._tg_shared = 0.0
        self.da.queue_draw()

    def _draw(self, widget, cr):
        ww, hh  = self._w, self._h
        bar_w   = (ww - 6) // 2

        for i, x0 in enumerate([2, bar_w + 4]):
            lv     = max(0.0, min(1.0, self._lv[i]))
            pk     = max(0.0, min(1.0, self._pk[i]))
            fill_h = int(lv * hh)

            cr.set_source_rgba(0.10, 0.10, 0.12, 0.92)
            cr.rectangle(x0, 0, bar_w, hh)
            cr.fill()

            if fill_h > 0:
                if _HAVE_CAIRO:
                    pat = _cairo.LinearGradient(x0, hh, x0, 0)
                    pat.add_color_stop_rgba(0.00, 0.19, 0.82, 0.35, 0.96)
                    pat.add_color_stop_rgba(0.65, 0.19, 0.82, 0.35, 0.96)
                    pat.add_color_stop_rgba(0.82, 1.00, 0.62, 0.04, 0.96)
                    pat.add_color_stop_rgba(1.00, 1.00, 0.27, 0.22, 0.96)
                    cr.set_source(pat)
                    cr.rectangle(x0, hh - fill_h, bar_w, fill_h)
                    cr.fill()
                else:
                    # fallback: 3-band solid blocks
                    g_h = min(fill_h, int(0.65 * hh))
                    a_h = min(fill_h - g_h, int(0.17 * hh))
                    r_h = fill_h - g_h - a_h
                    y = hh - fill_h
                    if g_h:
                        cr.set_source_rgba(0.19, 0.82, 0.35, 0.92)
                        cr.rectangle(x0, y, bar_w, g_h); cr.fill(); y += g_h
                    if a_h:
                        cr.set_source_rgba(1.0, 0.62, 0.04, 0.92)
                        cr.rectangle(x0, y, bar_w, a_h); cr.fill(); y += a_h
                    if r_h:
                        cr.set_source_rgba(1.0, 0.27, 0.22, 0.92)
                        cr.rectangle(x0, y, bar_w, r_h); cr.fill()

                cr.set_source_rgba(1.0, 1.0, 1.0, 0.14)
                cr.rectangle(x0, hh - fill_h, 1, fill_h)
                cr.fill()

            if pk > 0.015:
                pk_y = int((1.0 - pk) * hh)
                if   pk < 0.65: cr.set_source_rgba(0.19, 0.92, 0.35, 1.0)
                elif pk < 0.84: cr.set_source_rgba(1.00, 0.72, 0.04, 1.0)
                else:           cr.set_source_rgba(1.00, 0.37, 0.22, 1.0)
                cr.rectangle(x0, max(0, pk_y - 1), bar_w, 2)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.04)
            cr.set_line_width(1.0)
            cr.rectangle(x0 + 0.5, 0.5, bar_w - 1, hh - 1)
            cr.stroke()


def make_led(sz=8):
    import gi; gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    _c = ["#3a3a3c"]
    da  = Gtk.DrawingArea(); da.set_size_request(sz + 4, sz + 4)
    def draw(w, cr):
        try:   r=int(_c[0][1:3],16)/255; g=int(_c[0][3:5],16)/255; b=int(_c[0][5:7],16)/255
        except: r=g=b=0.25
        cr.set_source_rgba(r, g, b, 0.18)
        cr.arc(sz/2+2, sz/2+2, sz/2+2, 0, 6.2832); cr.fill()
        cr.set_source_rgba(r, g, b, 1.0)
        cr.arc(sz/2+2, sz/2+2, sz/2,   0, 6.2832); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.32)
        cr.arc(sz/2+1, sz/2+1, sz/4, 0, 6.2832); cr.fill()
    def set_c(h): _c[0]=h; da.queue_draw()
    da.connect("draw", draw)
    return da, set_c


# PeakPoller -- persistent parec subprocess per monitor source
#
# get_peak_sample() opens a new PA stream every call. PipeWire compat
# needs >40ms to set up the link; timeout always fires first.
# WirePlumber then spams "link failed: item deactivated before format was set"
# hundreds/sec, burning channel IDs.
#
# One long-lived parec per source instead; read s16le frames non-blocking
# via select, compute RMS peak. Zero stream churn.

class PeakPoller(threading.Thread):

    def __init__(self, vu_dump: bool = False):
        super().__init__(daemon=True)
        self._peaks: dict        = {}
        self._lock               = threading.Lock()
        self._srcs:  list        = []
        self.available           = False
        self._vu_dump            = vu_dump

    def set_sources(self, sources: list):
        with self._lock:
            self._srcs = list(sources)

    def get_peak(self, source: str) -> float:
        with self._lock:
            return self._peaks.get(source, 0.0)

    def run(self):
        import subprocess, struct, select as _select, shutil, os, gzip, csv, math as _math
        if not shutil.which("parec"):
            return

        RATE        = 8000
        CHANNELS    = 1
        BYTES_FRAME = CHANNELS * 2
        READ_CHUNK  = 4096
        COOLDOWN    = 2.0
        procs:    dict = {}
        born_at:  dict = {}
        dead_at:  dict = {}
        leftover: dict = {}  # alignment buffer between reads
        import time as _t2

        # --vu-dump writes RMS/peak CSV; off by default since v4.3
        # was always-on before -- created unbounded gzip + leftover dumps
        _dgz  = None
        _dcsv = None
        if self._vu_dump:
            _dp   = Path.home() / f"roaring_vu_dump_{int(_t2.time())}.csv.gz"
            _dgz  = gzip.open(str(_dp), "wt", newline="", compresslevel=6)
            _dcsv = csv.writer(_dgz)
            _dcsv.writerow(["t_ms","source","rms","peak","n_samples","rms_dbfs","peak_dbfs"])
            _dgz.flush()

        def _log_row(src, rms, peak, n):
            if _dcsv is None:
                return
            try:
                rdb = 20*_math.log10(rms)  if rms  > 1e-7 else -96.0
                pdb = 20*_math.log10(peak) if peak > 1e-7 else -96.0
                _dcsv.writerow([int(_t2.monotonic()*1000), src,
                                f"{rms:.5f}", f"{peak:.5f}", n,
                                f"{rdb:.1f}", f"{pdb:.1f}"])
                _dgz.flush()
            except Exception:
                pass

        def _start(src):
            try:
                return subprocess.Popen(
                    ["parec", f"--device={src}",
                     f"--channels={CHANNELS}", f"--rate={RATE}",
                     "--format=s16le", "--raw", "--latency-msec=33",
                     # plasma-pa filters on these to hide stream from "Input Streams" list
                     "--property=media.category=Monitor",
                     "--property=stream.dont-record=1",
                     "--property=application.id=roaring.vu.monitor",
                     "--property=application.name=Roaring VU (internal)"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except Exception:
                return None

        try:
            while True:
                now = time.monotonic()
                with self._lock:
                    srcs = list(self._srcs)
                for src in srcs:
                    if src not in procs:
                        if now - dead_at.get(src, 0.0) < COOLDOWN:
                            continue
                        p = _start(src)
                        if p:
                            procs[src] = p; born_at[src] = now
                for src in list(procs.keys()):
                    if src not in srcs:
                        try: procs[src].kill(); procs[src].wait(timeout=0.2)
                        except Exception: pass
                        del procs[src]
                        born_at.pop(src, None); leftover.pop(src, None)
                if not self.available:
                    if any(now - born_at.get(s, now) >= 1.0 for s in procs):
                        self.available = True
                for src, p in list(procs.items()):
                    if p.poll() is not None:
                        dead_at[src] = time.monotonic()
                        procs.pop(src, None)
                        born_at.pop(src, None); leftover.pop(src, None)
                        continue
                    try:
                        rdy, _, _ = _select.select([p.stdout], [], [], 0)
                        if not rdy:
                            continue
                        raw_new = os.read(p.stdout.fileno(), READ_CHUNK)
                        if not raw_new:
                            dead_at[src] = time.monotonic()
                            procs.pop(src, None)
                            born_at.pop(src, None); leftover.pop(src, None)
                            continue
                        raw = leftover.get(src, b"") + raw_new
                        n   = len(raw) // BYTES_FRAME
                        leftover[src] = raw[n * BYTES_FRAME:]
                        if n == 0:
                            continue
                        samples = struct.unpack(f"<{n}h", raw[:n * BYTES_FRAME])
                        rms  = _math.sqrt(sum(s*s for s in samples)/n) / 32768.0
                        peak = max(abs(s) for s in samples) / 32768.0
                        with self._lock:
                            self._peaks[src] = rms
                        _log_row(src, rms, peak, n)
                    except Exception:
                        pass
                time.sleep(0.033)
        except Exception:
            self.available = False
        finally:
            for p in procs.values():
                try:
                    p.kill()
                    p.wait(timeout=0.5)
                except Exception:
                    pass
            # gzip never closed before -> CRC trailer missing -> "truncated gzip input"
            try:
                if _dgz is not None:
                    _dgz.close()
            except Exception:
                pass


ST_COL = {"active":"#30d158","running":"#30d158","exited":"#30d158",
          "activating":"#ff9f0a","deactivating":"#ff9f0a",
          "inactive":"#3a3a3c","failed":"#ff453a","unknown":"#3a3a3c"}
ST_CSS = {"active":"s-ok","running":"s-ok","exited":"s-ok",
          "activating":"s-warn","deactivating":"s-warn",
          "inactive":"s-dim","failed":"s-bad","unknown":"s-dim"}

# CSS string encoded to avoid b"" byte-literal unicode issues

CSS = (
"""
* { font-family: "Cantarell", "Noto Sans", "Helvetica Neue", sans-serif; }
window { background-color: #1c1c1e; }

button, togglebutton, .ch-strip, .svc-row, .mute-btn {
    transition: background 160ms ease, color 160ms ease,
                border-color 160ms ease, opacity 160ms ease;
}
notebook tab { transition: color 120ms ease; }

@keyframes pulse-bad {
    0%   { opacity: 1.0; }
    50%  { opacity: 0.42; }
    100% { opacity: 1.0; }
}
.pulse-bad { animation: pulse-bad 1.1s ease-in-out infinite; }

/* top bar */
.topbar {
    background: #252527;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    padding: 3px 8px; min-height: 30px;
}
.app-logo {
    font-size: 11px; font-weight: 800;
    letter-spacing: 5px; color: #0a84ff;
}
.health-pill {
    font-size: 9px; font-weight: 700; letter-spacing: 1px;
    padding: 3px 9px; border-radius: 10px;
    background: rgba(255,255,255,0.07); min-width: 60px;
}
.hp-ok   { color: #30d158; }
.hp-warn { color: #ff9f0a; }
.hp-bad  { color: #ff453a; font-weight: 900; animation: pulse-bad 1.1s ease-in-out infinite; }

/* mixer */
.mixer-area {
    background: rgba(20,20,22,0.98);
    border-bottom: 1px solid rgba(255,255,255,0.05);
}

/* channel strip */
.ch-strip {
    background: rgba(40,40,44,0.80);
    border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);
    padding: 4px 4px 6px 4px; margin: 3px 2px; min-width: 64px;
}
.ch-strip:hover {
    background: rgba(52,52,58,0.90);
    border-color: rgba(255,255,255,0.12);
}
.ch-name {
    font-size: 7.5px; font-weight: 700;
    letter-spacing: 2px; color: rgba(255,255,255,0.35);
    margin-bottom: 3px;
}
.ch-vol {
    font-family: "JetBrains Mono","Fira Code","Courier New",monospace;
    font-size: 12px; font-weight: 300;
    color: rgba(255,255,255,0.82); margin-top: 1px;
}
.ch-st       { font-size: 7px; letter-spacing: 1px; color: rgba(255,255,255,0.20); }
.ch-st-run   { color: #30d158; }
.ch-st-idle  { color: rgba(255,255,255,0.20); }

/* fader */
scale trough {
    background: rgba(255,255,255,0.07); border-radius: 3px;
    min-height: 4px; min-width: 4px;
}
scale highlight { background: #0a84ff; border-radius: 3px; }
scale slider {
    background: white; border-radius: 5px;
    min-width: 13px; min-height: 13px; margin: -5px 0;
    transition: background 120ms ease;
}
scale slider:hover { background: #ddeeff; }

/* mute button */
.mute-btn {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 6px; padding: 3px 6px;
    font-size: 7px; font-weight: 700;
    letter-spacing: 1px; color: rgba(255,255,255,0.28);
}
.mute-btn:checked {
    background: rgba(255,69,58,0.78); color: white;
    border-color: rgba(255,69,58,0.38);
}
.mute-btn:hover { background: rgba(255,255,255,0.10); }

/* hardware panel */
.hw-panel  { padding: 7px 6px; min-width: 72px; }
.hw-ok     { color: #30d158; font-size: 9px; }
.hw-idle   { color: rgba(255,255,255,0.26); font-size: 9px; }
.hw-bad    { color: #ff453a; font-size: 9px; font-weight: 700;
             animation: pulse-bad 1.1s ease-in-out infinite; }

.mic-panel { padding: 5px 5px; min-width: 88px; }

/* expander in overview (LPD8 reference) */
expander title { font-size: 8px; color: rgba(255,255,255,0.30); }

/* buttons */
.btn-emu {
    background: #ff9f0a; color: #1c1c1e; border: none;
    font-weight: 800; font-size: 9px; letter-spacing: 0.5px;
    padding: 4px 10px; border-radius: 7px;
}
.btn-emu:hover { background: #ffb340; }
.btn-emu.alert { background: #ff453a; color: white; }

.btn-pri {
    background: #0a84ff; color: white; border: none;
    font-weight: 700; font-size: 9px;
    padding: 5px 12px; border-radius: 8px;
}
.btn-pri:hover { background: #2a9aff; }

button.act {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 7px; padding: 4px 10px;
    color: rgba(255,255,255,0.70); font-size: 9px;
}
button.act:hover  { background: rgba(255,255,255,0.12); }
button.act:active { background: rgba(255,255,255,0.17); }

.btn-xs {
    padding: 2px 6px; font-size: 8px;
    min-width: 0; border-radius: 5px;
}
.btn-ssh {
    background: rgba(10,132,255,0.16);
    border: 1px solid rgba(10,132,255,0.28);
    border-radius: 7px; padding: 4px 10px;
    color: #4db0ff; font-size: 9px;
}
.btn-ssh:hover { background: rgba(10,132,255,0.30); }

/* notebook */
notebook header {
    background: #1c1c1e;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
notebook tab {
    padding: 5px 16px; font-size: 9px;
    letter-spacing: 1px; color: rgba(255,255,255,0.32);
}
notebook tab:checked {
    color: rgba(255,255,255,0.85);
    border-bottom: 2px solid #0a84ff;
}
notebook tab:hover { color: rgba(255,255,255,0.55); }

/* service rows */
.svc-row {
    background: rgba(255,255,255,0.035);
    border-radius: 6px; border: 1px solid rgba(255,255,255,0.04);
    padding: 4px 7px; margin: 1px 0;
}
.svc-row:hover { background: rgba(255,255,255,0.07); }
.svc-name { font-size: 9.5px; color: rgba(255,255,255,0.68); }
.s-ok   { color: #30d158; font-size: 8.5px; }
.s-warn { color: #ff9f0a; font-size: 8.5px; }
.s-bad  { color: #ff453a; font-size: 8.5px; font-weight: 700; }
.s-dim  { color: rgba(255,255,255,0.26); font-size: 8.5px; }

textview, textview text {
    background: rgba(16,16,18,0.96); color: rgba(255,255,255,0.62);
    font-family: "JetBrains Mono","Fira Code","Courier New",monospace;
    font-size: 10px;
}
.log-tv, .log-tv text {
    font-size: 9px; background: rgba(14,14,16,0.98);
    color: rgba(255,255,255,0.45);
}
scrolledwindow { border: 1px solid rgba(255,255,255,0.07); border-radius: 7px; }

.ptitle {
    font-size: 8px; font-weight: 700;
    letter-spacing: 3px; color: rgba(255,255,255,0.24);
    margin-bottom: 2px;
}
.sec-hdr {
    font-size: 11px; font-weight: 600;
    color: rgba(255,255,255,0.62); margin-bottom: 2px;
}
.dim-s { color: rgba(255,255,255,0.34); font-size: 9px; }
.ok    { color: #30d158; }
.wrn   { color: #ff9f0a; }
.bad   { color: #ff453a; font-weight: 700; }

/* config form */
.cfg-field {
    background: rgba(255,255,255,0.04);
    border-radius: 9px; border: 1px solid rgba(255,255,255,0.07);
    padding: 9px 12px; margin: 3px 0;
}
.cfg-label { font-size: 9px; font-weight: 600; color: rgba(255,255,255,0.50); }
.cfg-note  { font-size: 8px; color: rgba(255,255,255,0.24); }

.statusbar {
    font-family: "JetBrains Mono","Fira Code","Courier New",monospace;
    font-size: 8px; padding: 2px 10px;
    border-top: 1px solid rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.26); background: rgba(18,18,20,0.99);
}
.statusbar.alert { color: #ff453a; font-weight: 700; }
separator { background: rgba(255,255,255,0.06); }

.app-row-name {
    font-size: 7px; color: rgba(255,255,255,0.52);
}
.app-move-btn {
    font-size: 8px; min-width: 0; padding: 0 2px;
    color: rgba(255,255,255,0.30); border-radius: 3px;
    border: none; background: transparent;
}
.app-move-btn:hover { background: rgba(255,255,255,0.10); }
.app-list-lbl { font-size: 7.5px; color: rgba(255,255,255,0.35); }

.si-mute-btn {
    font-size: 8px; min-width: 0; padding: 0 2px;
    border-radius: 3px; border: none; background: transparent;
    color: rgba(255,255,255,0.45);
}
.si-mute-btn:checked {
    color: #ff453a; background: rgba(255,69,58,0.18);
}
.si-mute-btn:hover { background: rgba(255,255,255,0.10); }
.si-vol-slider trough {
    min-height: 3px; border-radius: 2px;
    background: rgba(255,255,255,0.12);
}
.si-vol-slider highlight {
    min-height: 3px; border-radius: 2px;
    background: rgba(48,209,88,0.65);
}
.si-vol-slider slider {
    min-width: 8px; min-height: 8px;
    border-radius: 4px; background: rgba(255,255,255,0.70);
    border: none;
}
.si-vol-lbl {
    font-size: 7px; color: rgba(255,255,255,0.38);
}

/* LPD8 pad reference */
.pad-btn {
    background: rgba(36,36,42,0.90);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 5px; font-size: 7px; font-weight: 600;
    letter-spacing: 0.3px; color: rgba(255,255,255,0.36);
    padding: 3px 2px;
    transition: background 160ms ease;
}
.pad-mute   { border-color: rgba(255,159,10,0.28); color: rgba(255,159,10,0.60); }
.pad-action { background: rgba(8,44,84,0.60);
              border-color: rgba(10,132,255,0.30); color: rgba(100,168,255,0.70); }
.pad-danger { background: rgba(72,12,12,0.70);
              border-color: rgba(255,69,58,0.38); color: rgba(255,105,96,0.80);
              animation: pulse-bad 1.8s ease-in-out infinite; }
"""
).encode("utf-8")


def settings_dialog(parent, S, on_save):
    import gi; gi.require_version("Gtk","3.0")
    from gi.repository import Gtk

    dlg = Gtk.Dialog(title="Settings", transient_for=parent, modal=True)
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
    dlg.set_default_size(420, 0)
    box = dlg.get_content_area()
    box.set_spacing(0); box.set_border_width(20)

    def sec(txt):
        l = Gtk.Label(label=txt); l.set_xalign(0)
        l.get_style_context().add_class("ptitle")
        l.set_margin_top(14); l.set_margin_bottom(6)
        box.pack_start(l, False, False, 0)

    def row(lbl_txt, widget, note=None):
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hb.set_margin_bottom(6)
        lb = Gtk.Label(label=lbl_txt); lb.set_xalign(0); lb.set_width_chars(26)
        lb.get_style_context().add_class("cfg-label")
        hb.pack_start(lb, False, False, 0)
        hb.pack_end(widget, False, False, 0)
        if note:
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vb.pack_start(hb, False, False, 0)
            nl = Gtk.Label(label=note); nl.set_xalign(0)
            nl.get_style_context().add_class("cfg-note"); vb.pack_start(nl, False, False, 0)
            box.pack_start(vb, False, False, 0)
        else:
            box.pack_start(hb, False, False, 0)

    def hsep():
        s = Gtk.Separator(); s.set_margin_top(8); s.set_margin_bottom(2)
        box.pack_start(s, False, False, 0)

    sec("POLLING")
    r_map = {"500 ms":500,"1 s":1000,"2 s":2000,"5 s":5000,"10 s":10000}
    r_combo = Gtk.ComboBoxText()
    for k in r_map: r_combo.append_text(k)
    cur_r = S.get("refresh_ms", 2000)
    r_combo.set_active(list(r_map.keys()).index(min(r_map, key=lambda k: abs(r_map[k]-cur_r))))
    row("Data refresh interval", r_combo)

    hsep()
    sec("VU METERS")
    vu_sw = Gtk.Switch(); vu_sw.set_active(S.get("vu_enabled", True))
    row("Show VU meters", vu_sw, "Correlated stereo simulation (no DSP tap)")
    sp_adj = Gtk.Adjustment(S.get("vu_speed", 0.35)*100, 10, 80, 5)
    sp_sc = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=sp_adj)
    sp_sc.set_digits(0); sp_sc.set_size_request(140, -1)
    row("VU response speed (%)", sp_sc, "Higher = more reactive")

    hsep()
    sec("MEMORY WARNINGS  (pipewire-pulse)")
    w_sp = Gtk.SpinButton.new_with_range(20, 800, 10)
    w_sp.set_value(S.get("mem_warn_mb", 80)); w_sp.set_size_request(80,-1)
    row("Warning threshold (MB)", w_sp)
    c_sp = Gtk.SpinButton.new_with_range(50, 2000, 10)
    c_sp.set_value(S.get("mem_crit_mb", 200)); c_sp.set_size_request(80,-1)
    row("Critical threshold (MB)", c_sp)

    hsep()
    sec("LOOPBACK LATENCY")
    lat_sp = Gtk.SpinButton.new_with_range(1, 200, 1)
    lat_sp.set_value(S.get("latency_msec", 12)); lat_sp.set_size_request(80,-1)
    row("Latency (ms)", lat_sp, "Written to roaring_mixer.conf on save")

    hsep()
    sec("BEHAVIOUR")
    min_sw = Gtk.Switch(); min_sw.set_active(S.get("start_minimized", False))
    row("Start minimized to tray", min_sw)
    ntf_sw = Gtk.Switch(); ntf_sw.set_active(S.get("notify_fail", True))
    row("Desktop notification on failures", ntf_sw)

    box.show_all()
    resp = dlg.run()
    if resp == Gtk.ResponseType.OK:
        S["refresh_ms"]      = r_map[r_combo.get_active_text()]
        S["vu_enabled"]      = vu_sw.get_active()
        S["vu_speed"]        = sp_adj.get_value() / 100.0
        S["mem_warn_mb"]     = int(w_sp.get_value())
        S["mem_crit_mb"]     = int(c_sp.get_value())
        S["latency_msec"]    = int(lat_sp.get_value())
        S["start_minimized"] = min_sw.get_active()
        S["notify_fail"]     = ntf_sw.get_active()
        save_settings(S)
        write_conf_key(MXCONF, "LATENCY_MSEC", str(S["latency_msec"]))
        on_save(S)
    dlg.destroy()


def run_app(vu_dump: bool = False):
    import warnings; warnings.filterwarnings("ignore")
    import gi
    gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk, GLib

    HAVE_IND = False
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AI
        HAVE_IND = True
    except: AI = None

    prov = Gtk.CssProvider(); prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

    S   = load_settings()
    _write_pid()
    _startup_cleanup()
    _tracemalloc.start(10)  # 25 was holding ~30MB extra in the alloc table
    q   = queue.Queue()
    col = Collector(q, S); col.start()
    _vu: dict = {}
    peak_poller = PeakPoller(vu_dump=vu_dump)
    _mon_srcs = ([f"{s}.monitor" for _, s in VM_SINKS]
                 + ["mic_b1.monitor", "mic_b2.monitor"])
    peak_poller.set_sources(_mon_srcs)
    # kill stale parec from prior sessions before handing off to PeakPoller
    for src in ["vm_game.monitor","vm_chat.monitor","vm_music.monitor","laptop_audio.monitor","mic_b1.monitor","mic_b2.monitor"]:
        subprocess.run(["pkill", "-f", f"parec.*--device={src}"], stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    peak_poller.start()

    # window
    win = Gtk.Window(title="Hearth")
    win.set_default_size(880, 620)
    win.set_resizable(True)
    win.connect("delete-event", lambda *_: win.hide() or True)
    win.connect("map",   lambda *_: col.set_window_visible(True))
    win.connect("unmap", lambda *_: col.set_window_visible(False))
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.add(root)

    # top bar
    topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    topbar.get_style_context().add_class("topbar")
    root.pack_start(topbar, False, False, 0)

    mb_btn = Gtk.MenuButton(); mb_btn.set_label("=")
    mb_btn.get_style_context().add_class("btn-xs")
    topbar.pack_start(mb_btn, False, False, 0)

    logo = Gtk.Label(label="HEARTH"); logo.get_style_context().add_class("app-logo")
    topbar.pack_start(logo, False, False, 6)
    topbar.pack_start(Gtk.Box(), True, True, 0)

    lbl_mem = Gtk.Label(label="")
    lbl_mem.get_style_context().add_class("dim-s")
    topbar.pack_start(lbl_mem, False, False, 8)

    # overview panel collapse toggle -- hides the notebook so the mixer fills the window
    # useful when alt+tabbing mid-game: just faders + VU, no services clutter
    _mix_collapsed = [S.get("mixer_collapsed", False)]
    btn_collapse = Gtk.Button(label="▾")
    btn_collapse.get_style_context().add_class("btn-xs")
    btn_collapse.set_tooltip_text("Show / hide overview panel  [M]")
    topbar.pack_start(btn_collapse, False, False, 0)

    btn_emu = Gtk.Button(label="UNMUTE  [U]")
    btn_emu.get_style_context().add_class("btn-emu")
    btn_emu.set_tooltip_text("Emergency: unmute Astro Chat/Game + all VM buses")
    topbar.pack_end(btn_emu, False, False, 4)

    hp_led, hp_set = make_led(10)
    lbl_hp = Gtk.Label(label="...")
    lbl_hp.get_style_context().add_class("health-pill")
    lbl_hp.get_style_context().add_class("hp-ok")
    topbar.pack_end(lbl_hp, False, False, 0)
    topbar.pack_end(hp_led, False, False, 4)

    # mixer strip area
    mix_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    mix_area.get_style_context().add_class("mixer-area")
    root.pack_start(mix_area, False, False, 0)

    _pre_collapse_h = [0]
    def _toggle_mixer(*_):
        _mix_collapsed[0] = not _mix_collapsed[0]
        if _mix_collapsed[0]:
            _pre_collapse_h[0] = win.get_allocated_height()
            nb.hide(); btn_collapse.set_label("▸")
            win.resize(win.get_allocated_width(), 1)
        else:
            nb.show_all(); btn_collapse.set_label("▾")
            if _pre_collapse_h[0] > 100:
                win.resize(win.get_allocated_width(), _pre_collapse_h[0])
        S["mixer_collapsed"] = _mix_collapsed[0]
        save_settings(S)
    btn_collapse.connect("clicked", _toggle_mixer)

    mix_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    mix_row.set_border_width(4)
    mix_area.pack_start(mix_row, False, False, 0)

    _adjs: dict = {}; _mutes: dict = {}; _v_lbl: dict = {}
    _st_lbl: dict = {}; _block: dict = {}; _vol_fn: dict = {}
    _mute_fn: dict = {}; _app_lbl: dict = {}
    _app_compact: dict = {}; _app_popbtn: dict = {}
    _sink_has_inputs: dict = {}
    _all_sinks = [s for _, s in VM_SINKS] + ["mic_b1", "mic_b2"]
    _sink_labels = {s: l for l, s in VM_SINKS}
    _sink_labels.update({"mic_b1": "B1", "mic_b2": "B2"})

    def _make_app_row(app_info: dict, current_sink: str):
        pa_idx   = app_info.get("index", "")
        name     = app_info.get("name", "?")
        vol_pct  = app_info.get("vol_pct", 100)
        muted    = app_info.get("muted", False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        mute_btn = Gtk.ToggleButton(label="M")
        mute_btn.get_style_context().add_class("si-mute-btn")
        mute_btn.set_active(muted)
        if pa_idx:
            def _on_si_mute(b, idx=pa_idx):
                sh_bg(f"pactl set-sink-input-mute {idx} {'1' if b.get_active() else '0'}")
            mute_btn.connect("toggled", _on_si_mute)
        top_row.pack_start(mute_btn, False, False, 0)

        nl = Gtk.Label(label=name[:18]); nl.set_xalign(0); nl.set_ellipsize(3)
        nl.get_style_context().add_class("app-row-name")
        top_row.pack_start(nl, True, True, 0)

        if len(_all_sinks) > 1:
            mv = Gtk.MenuButton()
            mv.set_label("⇄"); mv.set_relief(Gtk.ReliefStyle.NONE)
            mv.get_style_context().add_class("app-move-btn")
            mv.set_tooltip_text("Move to output...")
            pop = Gtk.Popover(relative_to=mv)
            pop.set_position(Gtk.PositionType.BOTTOM)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pop_box.set_border_width(6)
            for sname in _all_sinks:
                if sname == current_sink:
                    continue
                btn = Gtk.Button(label=_sink_labels.get(sname, sname))
                btn.set_relief(Gtk.ReliefStyle.NONE)
                btn.get_style_context().add_class("app-row-name")
                def _move(_, idx=pa_idx, dst=sname, p=pop):
                    sh_bg(f"pactl move-sink-input {idx} {dst}")
                    p.popdown()
                btn.connect("clicked", _move)
                pop_box.pack_start(btn, False, False, 0)
            pop_box.show_all()
            pop.add(pop_box)
            mv.set_popover(pop)
            top_row.pack_end(mv, False, False, 0)

        outer.pack_start(top_row, False, False, 0)

        # volume row
        if pa_idx:
            vol_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            vol_row.set_hexpand(True)

            si_adj = Gtk.Adjustment(value=vol_pct, lower=0, upper=150,
                                    step_increment=1, page_increment=5)
            si_slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                  adjustment=si_adj)
            si_slider.set_draw_value(False)
            si_slider.set_hexpand(True)
            si_slider.set_size_request(-1, 16)
            si_slider.get_style_context().add_class("si-vol-slider")
            si_slider.set_tooltip_text("Stream volume (0-150%) - double-click to reset to 100%")

            si_lbl = Gtk.Label(label=f"{vol_pct}%")
            si_lbl.set_width_chars(5)
            si_lbl.set_xalign(1.0)
            si_lbl.get_style_context().add_class("si-vol-lbl")

            def _on_si_vol(adj, idx=pa_idx, lv=si_lbl):
                v = int(adj.get_value())
                lv.set_text(f"{v}%")
                sh_bg(f"pactl set-sink-input-volume {idx} {v}%")

            def _on_si_dbl(widget, event, a=si_adj):
                if event.type == Gdk.EventType._2BUTTON_PRESS:
                    a.set_value(100)
                    return True
                return False

            si_adj.connect("value-changed", _on_si_vol)
            si_slider.connect("button-press-event", _on_si_dbl)

            vol_row.pack_start(si_slider, True, True, 0)
            vol_row.pack_start(si_lbl,    False, False, 0)
            outer.pack_start(vol_row, False, False, 0)

        outer.show_all()
        return outer

    STRIP_TIP = {
        "vm_game":      "GAME bus\nAll game audio. Routes to Astro A50 (game or chat target).",
        "vm_chat":      "CHAT bus\nVoice comms (Discord, etc). Routes to Astro A50 stereo-chat.",
        "vm_music":     "MUSIC bus\nSpotify, YouTube, background audio. Routes to Astro A50.",
        "laptop_audio": "LAPTOP bus\nNetwork audio to RoaringsLaptop via RTP :46000.\n"
                        "Used during Moonlight game streaming sessions.",
    }

    def add_strip(parent, label, sink, fader_h=90):
        col_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col_box.get_style_context().add_class("ch-strip")
        tip = STRIP_TIP.get(sink, "")
        if tip: col_box.set_tooltip_text(tip)

        nl = Gtk.Label(label=label); nl.set_xalign(0.5)
        nl.get_style_context().add_class("ch-name")
        col_box.pack_start(nl, False, False, 0)

        vu_w = VU(speed=S.get("vu_speed", 0.35), w=24, h=fader_h)
        _vu[sink] = vu_w

        hrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        hrow.pack_start(vu_w.da, False, False, 0)

        adj = Gtk.Adjustment(value=50, lower=0, upper=150, step_increment=1)
        _adjs[sink] = adj; _block[sink] = False
        slider = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj)
        slider.set_inverted(True); slider.set_draw_value(False)
        slider.set_size_request(20, fader_h)
        slider.set_tooltip_text("Volume (0-150%) - double-click to reset to 100%")

        def _on_vol(a, s=sink):
            if _block.get(s): return
            v = int(a.get_value())
            if s in _v_lbl:
                _db = ("-inf" if v <= 0 else
                       f"{'+'  if v > 100 else ''}{20*math.log10(max(v,1)/100):.1f}")
                _col = "#ff9f0a" if v > 100 else "#ffffffd9"
                _v_lbl[s].set_markup(
                    f'<span foreground="{_col}">{v}%</span>'
                    f'<span font_size="small" foreground="#ffffff73">'
                    f' {_db}dB</span>'
                )
            sh_bg(f"pactl set-sink-volume {s} {v}%")
        _vol_fn[sink] = _on_vol
        adj.connect("value-changed", _on_vol)

        def _on_strip_dbl(widget, event, a=adj):
            if event.type == Gdk.EventType._2BUTTON_PRESS:
                a.set_value(100)
                return True
            return False
        slider.connect("button-press-event", _on_strip_dbl)

        hrow.pack_start(slider, True, True, 0)
        col_box.pack_start(hrow, True, True, 0)

        vl = Gtk.Label(label="--"); vl.set_xalign(0.5)
        vl.get_style_context().add_class("ch-vol")
        _v_lbl[sink] = vl
        col_box.pack_start(vl, False, False, 0)

        # _st_lbl still tracked for drain logic but not shown -- redundant with VU color
        stl = Gtk.Label(label=""); _st_lbl[sink] = stl

        mt = Gtk.ToggleButton(label="MUTE")
        mt.get_style_context().add_class("mute-btn")
        _mutes[sink] = mt

        def _on_mute(b, s=sink):
            if _block.get(s): return
            sh_bg(f"pactl set-sink-mute {s} {'1' if b.get_active() else '0'}")
        _mute_fn[sink] = _on_mute
        mt.connect("toggled", _on_mute)
        col_box.pack_start(mt, False, False, 0)

        _abox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # compact app name tag -- plain text only, no scroll, no buttons in strip
        # scroll events no longer bleed into the fader; hovering doesn't spawn a scrollbar
        _atag = Gtk.Label(label="—")
        _atag.get_style_context().add_class("app-list-lbl")
        _atag.set_ellipsize(3)        # PANGO_ELLIPSIZE_END
        _atag.set_xalign(0.5)
        _atag.set_single_line_mode(True)
        col_box.pack_start(_atag, False, False, 0)
        _app_compact[sink] = _atag

        # interactive app controls live in a popover -- click ⋮ to open
        # no_show_all so show_all() on the strip doesn't force it visible
        _pop_mb = Gtk.MenuButton()
        _pop_mb.set_label("⋮")
        _pop_mb.get_style_context().add_class("btn-xs")
        _pop_mb.set_tooltip_text("Per-stream mute / volume")
        _pop_mb.set_no_show_all(True)
        _pop_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _pop_inner.set_border_width(8)
        _pop_hdr = Gtk.Label(label=label)
        _pop_hdr.get_style_context().add_class("ptitle")
        _pop_hdr.set_xalign(0)
        _pop_inner.pack_start(_pop_hdr, False, False, 0)
        _pop_inner.pack_start(_abox, False, False, 0)
        _pop_inner.show_all()
        _popover = Gtk.Popover(relative_to=_pop_mb)
        _popover.set_position(Gtk.PositionType.BOTTOM)
        _popover.add(_pop_inner)
        _pop_mb.set_popover(_popover)
        col_box.pack_start(_pop_mb, False, False, 0)
        _app_lbl[sink] = _abox       # popover content box (same destroy/rebuild pattern as before)
        _app_popbtn[sink] = _pop_mb  # button visibility tracks whether any apps exist

        parent.pack_start(col_box, True, True, 0)

    for label, sink in VM_SINKS:
        add_strip(mix_row, label, sink)

    sv = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
    sv.set_margin_start(4); sv.set_margin_end(4)
    mix_row.pack_start(sv, False, False, 0)

    for label, sink in [("B1", "mic_b1"), ("B2", "mic_b2")]:
        add_strip(mix_row, label, sink, fader_h=80)

    sv_mic = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
    sv_mic.set_margin_start(4); sv_mic.set_margin_end(4)
    mix_row.pack_start(sv_mic, False, False, 0)

    # hardware output panel
    def ptitle(txt):
        l = Gtk.Label(label=txt); l.set_xalign(0)
        l.get_style_context().add_class("ptitle"); return l

    hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    hw_box.get_style_context().add_class("hw-panel")
    mix_row.pack_start(hw_box, False, False, 0)

    hw_box.pack_start(ptitle("OUTPUT"), False, False, 0)
    lbl_astro_tgt = Gtk.Label(label="Astro: chat"); lbl_astro_tgt.set_xalign(0)
    lbl_astro_tgt.get_style_context().add_class("hw-ok")
    hw_box.pack_start(lbl_astro_tgt, False, False, 0)
    lbl_astro_hw = Gtk.Label(label="HW: ..."); lbl_astro_hw.set_xalign(0)
    lbl_astro_hw.get_style_context().add_class("dim-s")
    hw_box.pack_start(lbl_astro_hw, False, False, 0)
    # btn_astro not shown in panel -- wired via [T] keybind and tray
    btn_astro = Gtk.Button(label="Toggle Target  [T]")
    btn_astro.set_tooltip_text("Astro A50: stereo-chat <-> stereo-game")

    hw_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 1)
    hw_box.pack_start(ptitle("MONITOR"), False, False, 0)
    lbl_scarlett = Gtk.Label(label="Scarlett: OFF"); lbl_scarlett.set_xalign(0)
    lbl_scarlett.get_style_context().add_class("hw-idle")
    hw_box.pack_start(lbl_scarlett, False, False, 0)
    # btn_scarlett not shown -- wired via [S] keybind and tray
    btn_scarlett = Gtk.Button(label="Toggle Scarlett  [S]")
    btn_scarlett.set_tooltip_text("Mirror VM buses to Focusrite Scarlett speakers")

    hw_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 1)
    hw_box.pack_start(ptitle("CARLA"), False, False, 0)
    lbl_carla = Gtk.Label(label="checking..."); lbl_carla.set_xalign(0)
    lbl_carla.get_style_context().add_class("hw-idle")
    hw_box.pack_start(lbl_carla, False, False, 0)
    cr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    hw_box.pack_start(cr_row, False, False, 0)
    for ico in ["Start", "Stop", "Dedup"]:
        b = Gtk.Button(label=ico); b.get_style_context().add_class("btn-xs")
        cr_row.pack_start(b, False, False, 0)
    carla_btns = cr_row.get_children()

    mix_row.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

    # mic panel
    mic_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    mic_box.get_style_context().add_class("mic-panel")
    mix_row.pack_start(mic_box, False, False, 0)
    mic_box.pack_start(ptitle("MIC ROUTING"), False, False, 0)

    _mc_combos = {}; _mc_fn = {}
    _loopback_checks = {}; _loopback_fns = {}
    _lb_last_click: dict = {}  # bus -> timestamp of last user click (race guard)

    for bus, src_key, tip in [
        ("B1", "b1_route", "Bus 1 -> Stream / OBS"),
        ("B2", "b2_route", "Bus 2 -> Discord / Chat"),
    ]:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        lb = Gtk.Label(label=f"{bus}:"); lb.set_width_chars(3)
        lb.get_style_context().add_class("dim-s")
        row.pack_start(lb, False, False, 0)

        combo = Gtk.ComboBoxText()
        for opt in MIC_OPTS:
            combo.append_text(opt)
        conf_route = read_conf(MCCONF).get(f"{bus}_ROUTE", "none")
        combo.set_active(MIC_OPTS.index(conf_route) if conf_route in MIC_OPTS else 0)
        _mc_combos[bus] = (combo, src_key)

        check = Gtk.ToggleButton(label="🔊")
        check.get_style_context().add_class("btn-xs")
        check.set_tooltip_text(f"Monitor {bus} -> headset (mic_{bus.lower()}.monitor -> vm_game)")
        _loopback_checks[bus] = check

        def _on_mc(c, b=bus):
            write_conf_key(MCCONF, f"{b}_ROUTE", MIC_OPTS[c.get_active()])
        _mc_fn[bus] = _on_mc
        combo.connect("changed", _on_mc)

        # loopback routes into ALL three output buses so the mic is audible everywhere
        # 100% volume -- the mic_b?.monitor signal is already at correct level from Carla
        # 30% was inaudible through two loopbacks in series
        def _on_lb_toggled(chk, b=bus):
            _lb_last_click[b] = time.time()
            src = f"mic_{b.lower()}.monitor"
            if chk.get_active():
                lat = S.get("latency_msec", 10)
                for tgt in ("vm_game", "vm_chat", "vm_music"):
                    sh_bg(
                        f"pactl load-module module-loopback "
                        f"source={src} sink={tgt} "
                        f"latency_msec={lat} rate=48000 channels=2 "
                        f"channel_map=front-left,front-right remix=yes "
                        f"source_dont_move=true sink_dont_move=true"
                    )
            else:
                sh_bg(
                    f"for id in $(pactl list short modules | "
                    f"awk '$2==\"module-loopback\" && /{src}/ "
                    f"&& ($0 ~ /sink=vm_game/ || $0 ~ /sink=vm_chat/ || $0 ~ /sink=vm_music/) "
                    f"{{print $1}}'); "
                    f"do pactl unload-module $id 2>/dev/null; done"
                )
        _loopback_fns[bus] = _on_lb_toggled
        check.connect("toggled", _on_lb_toggled)
        check.set_active(False)  # drain syncs from real PW state

        row.pack_start(combo, True, True, 0)
        row.pack_start(check, False, False, 0)
        row.set_tooltip_text(tip)
        mic_box.pack_start(row, False, False, 0)

    mic_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 3)
    mic_box.pack_start(ptitle("MOONLIGHT MIC"), False, False, 0)

    _ml_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    _ml_lbl = Gtk.Label(label="src:"); _ml_lbl.set_width_chars(4)
    _ml_lbl.get_style_context().add_class("dim-s"); _ml_row.pack_start(_ml_lbl, False, False, 0)
    _ml_combo = Gtk.ComboBoxText()
    for _o in MOONLIGHT_MIC_OPTS: _ml_combo.append_text(_o)
    _ml_combo.set_active(0)
    _ml_row.set_tooltip_text("Mic source streamed to Windows via UDP -> CABLE Input")
    def _on_ml_combo(c): set_moonlight_mic(MOONLIGHT_MIC_OPTS[c.get_active()])
    _ml_fn = _on_ml_combo
    _ml_combo.connect("changed", _ml_fn)
    _ml_row.pack_start(_ml_combo, True, True, 0)
    mic_box.pack_start(_ml_row, False, False, 0)
    _ml_svc_lbl = Gtk.Label(label="svc: ..."); _ml_svc_lbl.set_xalign(0)
    _ml_svc_lbl.get_style_context().add_class("dim-s")
    mic_box.pack_start(_ml_svc_lbl, False, False, 0)

    mic_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 3)
    mic_box.pack_start(ptitle("SOURCES"), False, False, 0)
    lbl_sm7b  = Gtk.Label(label="SM7B:      --"); lbl_sm7b.set_xalign(0)
    lbl_amic  = Gtk.Label(label="Astro Mic: --"); lbl_amic.set_xalign(0)
    lbl_b1src = Gtk.Label(label="b1_mic:    --"); lbl_b1src.set_xalign(0)
    lbl_b2src = Gtk.Label(label="b2_mic:    --"); lbl_b2src.set_xalign(0)
    for l in [lbl_sm7b, lbl_amic, lbl_b1src, lbl_b2src]:
        l.get_style_context().add_class("dim-s"); mic_box.pack_start(l, False, False, 0)

    # notebook
    nb = Gtk.Notebook(); nb.set_tab_pos(Gtk.PositionType.TOP)
    root.pack_start(nb, True, True, 0)

    def ntab(name):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        b.set_border_width(0)
        nb.append_page(b, Gtk.Label(label=name.upper()))
        return b

    def hsep():
        s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        s.set_margin_top(2); s.set_margin_bottom(2); return s

    # tab 1 -- overview (services, quick actions, LPD8 reference)
    t_main = ntab("Overview")
    ov_h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    t_main.pack_start(ov_h, True, True, 0)

    # left col -- services compact + status
    svc_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    svc_frame.set_border_width(8); svc_frame.set_hexpand(True)
    ov_h.pack_start(svc_frame, True, True, 0)

    svc_hdr_l = Gtk.Label(label="SERVICES"); svc_hdr_l.set_xalign(0)
    svc_hdr_l.get_style_context().add_class("ptitle")
    svc_frame.pack_start(svc_hdr_l, False, False, 0)

    svc_grid = Gtk.Grid()
    svc_grid.set_column_spacing(5); svc_grid.set_row_spacing(2)
    svc_grid.set_column_homogeneous(True)
    _svc_w = {}
    for ri, (unit, ctrl) in enumerate(SERVICES):
        rw = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        rw.get_style_context().add_class("svc-row")
        led, lset = make_led(5); rw.pack_start(led, False, False, 0)
        nl = Gtk.Label(label=unit); nl.get_style_context().add_class("svc-name")
        nl.set_xalign(0); nl.set_ellipsize(3); rw.pack_start(nl, True, True, 0)
        sl = Gtk.Label(label="..."); sl.set_width_chars(7)
        sl.get_style_context().add_class("s-dim"); rw.pack_start(sl, False, False, 0)
        if ctrl:
            for lbl_b, act_b in [("↺", "restart"), ("◼", "stop")]:
                b = Gtk.Button(label=lbl_b); b.get_style_context().add_class("btn-xs")
                b.set_tooltip_text(f"{act_b} {unit}")
                b.connect("clicked", lambda _, u=unit, a=act_b:
                          sh_bg(f"systemctl --user {a} {u}"))
                rw.pack_end(b, False, False, 0)
        _svc_w[unit] = (sl, lset)
        svc_grid.attach(rw, ri % 2, ri // 2, 1, 1)

    scr_svc = Gtk.ScrolledWindow()
    scr_svc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scr_svc.set_min_content_height(80); scr_svc.set_max_content_height(180)
    scr_svc.add(svc_grid)
    svc_frame.pack_start(scr_svc, True, True, 0)

    lbl_mute_sum = Gtk.Label(label="checking..."); lbl_mute_sum.set_xalign(0)
    lbl_mute_sum.get_style_context().add_class("dim-s")
    svc_frame.pack_start(lbl_mute_sum, False, False, 0)

    lbl_timer = Gtk.Label(label=""); lbl_timer.set_xalign(0)
    lbl_timer.get_style_context().add_class("dim-s")
    svc_frame.pack_start(lbl_timer, False, False, 0)

    ov_h.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

    # right col -- quick actions (2-col grid) + LPD8 expander + padfire one-liner
    qa_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    qa_frame.set_border_width(8); qa_frame.set_size_request(210, -1)
    ov_h.pack_start(qa_frame, False, False, 0)

    qa_hdr_l = Gtk.Label(label="QUICK ACTIONS"); qa_hdr_l.set_xalign(0)
    qa_hdr_l.get_style_context().add_class("ptitle")
    qa_frame.pack_start(qa_hdr_l, False, False, 0)

    # 2-column button grid for quick actions (saves ~80px vs stacked column)
    _qa_refs = {}
    _qa_defs = [
        ("UNMUTE ALL  [U]",      "btn-emu", "unmute"),
        ("Soft Restart  F5",     "act",     "soft"),
        ("Hard Restart  Ctrl+R", "act",     "hard"),
        ("SSH  Ctrl+L",          "btn-ssh", "ssh"),
        ("Reset Failed",         "act",     "rfail"),
        ("Force Default Sink",   "act",     "fdef"),
    ]
    qa_grid = Gtk.Grid()
    qa_grid.set_column_spacing(3); qa_grid.set_row_spacing(3)
    qa_grid.set_column_homogeneous(True)
    for qi, (qlbl, qcls, qkey) in enumerate(_qa_defs):
        qb = Gtk.Button(label=qlbl); qb.get_style_context().add_class(qcls)
        # UNMUTE spans both columns -- most important, needs to be big
        if qkey == "unmute":
            qa_grid.attach(qb, 0, 0, 2, 1)
        else:
            grow = 1 + (qi - 1) // 2   # was "row" and "col" -- both shadow outer scope vars
            gcol = (qi - 1) % 2
            qa_grid.attach(qb, gcol, grow, 1, 1)
        _qa_refs[qkey] = qb
    # Debug Dump gets its own row below (less frequent, keeps grid clean)
    btn_dump = Gtk.Button(label="Debug Dump  Ctrl+D")
    btn_dump.get_style_context().add_class("act")
    _qa_refs["dump"] = btn_dump

    qa_frame.pack_start(qa_grid, False, False, 0)
    qa_frame.pack_start(btn_dump, False, False, 0)
    qa_frame.pack_start(hsep(), False, False, 0)

    # LPD8 -- pad reference in an expander (rarely needed mid-session)
    lpd8_exp = Gtk.Expander(label="  LPD8")
    lpd8_exp.get_style_context().add_class("dim-s")
    lpd8_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    lpd8_inner.set_margin_top(4)

    pad_grid = Gtk.Grid()
    pad_grid.set_column_spacing(3); pad_grid.set_row_spacing(3)
    PAD_DEFS = [
        ("1\nMUSIC\nMUTE",    "pad-mute"),
        ("2\nCHAT\nMUTE",     "pad-mute"),
        ("3\nASTRO\nTARGET",  "pad-action"),
        ("4\nASTRO HW\n!!!",  "pad-danger"),
        ("5\nDEBUG\nDUMP",    "pad-action"),
        ("6\nMIC SRC\nMUTE",  "pad-mute"),
        ("7\nGAME\nMUTE",     "pad-mute"),
        ("8\nSCARLETT\nMIRR", "pad-action"),
    ]
    for pi, (ptxt, pcls) in enumerate(PAD_DEFS):
        pl = Gtk.Label(label=ptxt)
        pl.get_style_context().add_class("pad-btn")
        pl.get_style_context().add_class(pcls)
        pl.set_justify(Gtk.Justification.CENTER)
        pl.set_size_request(42, 36)
        pad_grid.attach(pl, pi % 4, pi // 4, 1, 1)
    lpd8_inner.pack_start(pad_grid, False, False, 0)

    knob_ref = Gtk.Label(label="K1·K7=GAME  K5=MUSIC  K6=CHAT  K8=MIC vol")
    knob_ref.set_xalign(0); knob_ref.get_style_context().add_class("dim-s")
    lpd8_inner.pack_start(knob_ref, False, False, 0)

    lpd8_exp.add(lpd8_inner)
    qa_frame.pack_start(lpd8_exp, False, False, 0)

    # LPD8 status + reconnect (always visible, one line)
    _lpd8_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    lbl_lpd8 = Gtk.Label(label="lpd8-mixer: --"); lbl_lpd8.set_xalign(0)
    lbl_lpd8.get_style_context().add_class("dim-s")
    _lpd8_row.pack_start(lbl_lpd8, True, True, 0)
    btn_lpd8_reconnect = Gtk.Button(label="↺ LPD8")
    btn_lpd8_reconnect.get_style_context().add_class("act")
    btn_lpd8_reconnect.set_tooltip_text(
        "Restart lpd8-mixer service.\n"
        "Auto-reconnect is active: plugging the LPD8 back in will trigger\n"
        "an automatic restart when the service is failed/inactive."
    )
    _lpd8_row.pack_start(btn_lpd8_reconnect, False, False, 0)
    qa_frame.pack_start(_lpd8_row, False, False, 0)

    qa_frame.pack_start(hsep(), False, False, 0)

    # padfire -- condensed to one status line + two buttons
    pf_hdr = Gtk.Label(label="PADFIRE"); pf_hdr.set_xalign(0)
    pf_hdr.get_style_context().add_class("ptitle")
    qa_frame.pack_start(pf_hdr, False, False, 0)

    pf_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    lbl_pf_status = Gtk.Label(label="○ not running"); lbl_pf_status.set_xalign(0)
    lbl_pf_status.get_style_context().add_class("s-dim")
    pf_top.pack_start(lbl_pf_status, True, True, 0)
    btn_pf_show = Gtk.Button(label="Open")
    btn_pf_show.get_style_context().add_class("btn-xs")
    btn_pf_show.connect("clicked", lambda *_: _pf_ipc("show"))
    btn_pf_stop = Gtk.Button(label="■ Stop")
    btn_pf_stop.get_style_context().add_class("btn-xs")
    btn_pf_stop.connect("clicked", lambda *_: _pf_ipc("stop_all"))
    pf_top.pack_start(btn_pf_show, False, False, 0)
    pf_top.pack_start(btn_pf_stop, False, False, 0)
    qa_frame.pack_start(pf_top, False, False, 0)

    lbl_pf_page = Gtk.Label(label=""); lbl_pf_page.set_xalign(0)
    lbl_pf_page.get_style_context().add_class("dim-s")
    qa_frame.pack_start(lbl_pf_page, False, False, 0)

    lbl_pf_playing = Gtk.Label(label=""); lbl_pf_playing.set_xalign(0)
    lbl_pf_playing.get_style_context().add_class("dim-s")
    lbl_pf_playing.set_line_wrap(True)
    qa_frame.pack_start(lbl_pf_playing, False, False, 0)

    qa_frame.pack_start(hsep(), False, False, 0)

    # vesktop screen sharing compat
    # "only one source" on Wayland: by design -- xdg-desktop-portal-kde IS the picker.
    # the portal popup IS the multi-source selector; Vesktop receives one pre-selected source.
    # "entire screen doesn't work": usually portal dead, or DMA-BUF format mismatch (GPU driver).
    # venmic audio sources missing: check default sink -- must be vm_game for only_default_speakers.
    vc_hdr = Gtk.Label(label="VESKTOP"); vc_hdr.set_xalign(0)
    vc_hdr.get_style_context().add_class("ptitle")
    qa_frame.pack_start(vc_hdr, False, False, 0)

    vc_portal_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    lbl_vc_portal = Gtk.Label(label="portal: checking...")
    lbl_vc_portal.set_xalign(0)
    lbl_vc_portal.get_style_context().add_class("dim-s")
    lbl_vc_portal.set_tooltip_text(
        "xdg-desktop-portal-kde\n"
        "Must be active for screen sharing on KDE Wayland.\n"
        "On Wayland the KDE portal popup IS the source picker -- "
        "one source at a time is by design, not a bug."
    )
    vc_portal_row.pack_start(lbl_vc_portal, True, True, 0)
    btn_vc_restart = Gtk.Button(label="↺")
    btn_vc_restart.get_style_context().add_class("btn-xs")
    btn_vc_restart.set_tooltip_text(
        "Restart xdg-desktop-portal + xdg-desktop-portal-kde.\n"
        "Try this first when \'Share Entire Screen\' does nothing."
    )
    btn_vc_restart.connect("clicked", lambda *_: sh_bg(
        "systemctl --user restart xdg-desktop-portal-kde xdg-desktop-portal 2>/dev/null"
    ))
    vc_portal_row.pack_start(btn_vc_restart, False, False, 0)
    qa_frame.pack_start(vc_portal_row, False, False, 0)

    lbl_vc_sink = Gtk.Label(label="default sink: ...")
    lbl_vc_sink.set_xalign(0)
    lbl_vc_sink.get_style_context().add_class("dim-s")
    lbl_vc_sink.set_tooltip_text(
        "venmic reads the PipeWire default sink to filter audio sources\n"
        "(only_default_speakers). Should be vm_game.\n"
        "If it shows the Astro ALSA name, game audio may not appear\n"
        "in Vesktop\'s audio source picker."
    )
    qa_frame.pack_start(lbl_vc_sink, False, False, 0)

    lbl_vc_venmic = Gtk.Label(label="venmic log: not found")
    lbl_vc_venmic.set_xalign(0)
    lbl_vc_venmic.get_style_context().add_class("dim-s")
    lbl_vc_venmic.set_tooltip_text(
        "~/.var/app/dev.vencord.Vesktop/.local/state/venmic/venmic.log\n"
        "Enable with VENMIC_ENABLE_LOG=1 in Vesktop env, or via\n"
        "Flatseal -> dev.vencord.Vesktop -> Environment."
    )
    qa_frame.pack_start(lbl_vc_venmic, False, False, 0)

    # tab 2 -- config & log
    t_sys = ntab("Config & Log")
    sys_scr = Gtk.ScrolledWindow()
    sys_scr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sys_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    sys_inner.set_border_width(8)
    sys_scr.add(sys_inner); t_sys.pack_start(sys_scr, True, True, 0)

    fl_hdr = Gtk.Label(label="SIGNAL FLOW"); fl_hdr.set_xalign(0)
    fl_hdr.get_style_context().add_class("ptitle")
    sys_inner.pack_start(fl_hdr, False, False, 0)

    flow_lbl = Gtk.Label(); flow_lbl.set_xalign(0); flow_lbl.set_yalign(0)
    flow_lbl.get_style_context().add_class("dim-s")
    flow_lbl.set_markup(
        "SM7B  <b>-&gt;</b>  B1 (mic_b1)  <b>-&gt;</b>  b1_mic source\n"
        "Astro Mic 48k  <b>-&gt;</b>  B2 (mic_b2)  <b>-&gt;</b>  b2_mic  <b>-&gt;</b>  Moonlight  <b>-&gt;</b>  Laptop\n"
        "GAME / CHAT / MUSIC  <b>-&gt;</b>  Astro A50  (stereo-chat or stereo-game)\n"
        "LAPTOP  <b>-&gt;</b>  RTP :46000  <b>-&gt;</b>  RoaringsLaptop\n"
        "Scarlett monitor (opt.)  <b>-&gt;</b>  VM buses  <b>-&gt;</b>  Scarlett speakers"
    )
    sys_inner.pack_start(flow_lbl, False, False, 0)
    sys_inner.pack_start(hsep(), False, False, 0)

    # mixer config
    mcfg_hdr = Gtk.Label(label="MIXER CONFIG"); mcfg_hdr.set_xalign(0)
    mcfg_hdr.get_style_context().add_class("ptitle")
    sys_inner.pack_start(mcfg_hdr, False, False, 0)

    def _cfg_row(label_txt, widget):
        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        r.set_margin_bottom(2)
        l = Gtk.Label(label=label_txt); l.set_xalign(0); l.set_width_chars(22)
        l.get_style_context().add_class("cfg-label"); r.pack_start(l, False, False, 0)
        r.pack_start(widget, False, False, 0); return r

    cfg_f = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    cfg_f.get_style_context().add_class("cfg-field")

    at_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    rb_chat = Gtk.RadioButton(label="Chat (stereo-chat)")
    rb_game = Gtk.RadioButton.new_with_label_from_widget(rb_chat, "Game (stereo-game)")
    at_box.pack_start(rb_chat, False, False, 0)
    at_box.pack_start(rb_game, False, False, 0)
    cfg_f.pack_start(_cfg_row("Astro A50 target", at_box), False, False, 0)

    lat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    lat_sp2 = Gtk.SpinButton.new_with_range(1, 200, 1); lat_sp2.set_size_request(65, -1)
    lat_ms_l = Gtk.Label(label="ms"); lat_ms_l.get_style_context().add_class("dim-s")
    lat_box.pack_start(lat_sp2, False, False, 0); lat_box.pack_start(lat_ms_l, False, False, 0)
    cfg_f.pack_start(_cfg_row("Loopback latency", lat_box), False, False, 0)

    b_mx_save = Gtk.Button(label="Save Mixer Config")
    b_mx_save.get_style_context().add_class("act")
    cfg_f.pack_start(b_mx_save, False, False, 0)
    sys_inner.pack_start(cfg_f, False, False, 0)

    def _load_mxf():
        conf = read_conf(MXCONF)
        tgt  = conf.get("ASTRO_TARGET", "")
        rb_game.set_active("game" in tgt); rb_chat.set_active("game" not in tgt)
        try: lat_sp2.set_value(int(conf.get("LATENCY_MSEC", "12")))
        except: lat_sp2.set_value(12)

    def _save_mxf(*_):
        tgt = ASTRO_GAME if rb_game.get_active() else ASTRO_CHAT
        write_conf_key(MXCONF, "ASTRO_TARGET", tgt)
        write_conf_key(MXCONF, "LATENCY_MSEC", str(int(lat_sp2.get_value())))
        sh_bg("systemctl --user restart roaring-audio-routesd.service 2>/dev/null || true")

    b_mx_save.connect("clicked", _save_mxf)
    _load_mxf()
    sys_inner.pack_start(hsep(), False, False, 0)

    # app settings
    acfg_hdr = Gtk.Label(label="APP SETTINGS"); acfg_hdr.set_xalign(0)
    acfg_hdr.get_style_context().add_class("ptitle")
    sys_inner.pack_start(acfg_hdr, False, False, 0)

    app_f = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    app_f.get_style_context().add_class("cfg-field")

    r_map = {"500 ms":500,"1 s":1000,"2 s":2000,"5 s":5000,"10 s":10000}
    r_combo = Gtk.ComboBoxText()
    for k in r_map: r_combo.append_text(k)
    cur_r = S.get("refresh_ms", 2000)
    r_combo.set_active(list(r_map.keys()).index(
        min(r_map, key=lambda k: abs(r_map[k]-cur_r))))
    app_f.pack_start(_cfg_row("Data refresh interval", r_combo), False, False, 0)

    vu_sw = Gtk.Switch(); vu_sw.set_active(S.get("vu_enabled", True))
    app_f.pack_start(_cfg_row("VU meters", vu_sw), False, False, 0)

    sp_adj = Gtk.Adjustment(S.get("vu_speed", 0.35)*100, 10, 80, 5)
    sp_sc = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=sp_adj)
    sp_sc.set_digits(0); sp_sc.set_size_request(110, -1)
    app_f.pack_start(_cfg_row("VU speed (%)", sp_sc), False, False, 0)

    mem_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    w_sp = Gtk.SpinButton.new_with_range(20, 800, 10)
    w_sp.set_value(S.get("mem_warn_mb", 80)); w_sp.set_size_request(60, -1)
    c_sp = Gtk.SpinButton.new_with_range(50, 2000, 10)
    c_sp.set_value(S.get("mem_crit_mb", 200)); c_sp.set_size_request(60, -1)
    slash_l = Gtk.Label(label="/"); slash_l.get_style_context().add_class("dim-s")
    mem_box.pack_start(w_sp, False, False, 0)
    mem_box.pack_start(slash_l, False, False, 0)
    mem_box.pack_start(c_sp, False, False, 0)
    app_f.pack_start(_cfg_row("Mem warn / crit (MB)", mem_box), False, False, 0)

    min_sw = Gtk.Switch(); min_sw.set_active(S.get("start_minimized", False))
    app_f.pack_start(_cfg_row("Start minimized", min_sw), False, False, 0)

    def _save_app_cfg(*_):
        S["refresh_ms"]      = r_map[r_combo.get_active_text()]
        S["vu_enabled"]      = vu_sw.get_active()
        S["vu_speed"]        = sp_adj.get_value() / 100.0
        S["mem_warn_mb"]     = int(w_sp.get_value())
        S["mem_crit_mb"]     = int(c_sp.get_value())
        S["start_minimized"] = min_sw.get_active()
        save_settings(S); col._s = S

    b_app_save = Gtk.Button(label="Save App Settings")
    b_app_save.get_style_context().add_class("act")
    b_app_save.connect("clicked", _save_app_cfg)
    app_f.pack_start(b_app_save, False, False, 0)
    sys_inner.pack_start(app_f, False, False, 0)
    sys_inner.pack_start(hsep(), False, False, 0)

    # active modules expander
    rt_exp = Gtk.Expander(label="  Active Loopback / Sink Modules")
    rt_exp.get_style_context().add_class("dim-s")
    rt_inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    b_rtref = Gtk.Button(label="Refresh")
    b_rtref.get_style_context().add_class("btn-xs")
    route_tv = Gtk.TextView(); route_tv.set_editable(False)
    route_tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    route_tv.get_style_context().add_class("log-tv")
    route_buf = route_tv.get_buffer()
    rs2 = Gtk.ScrolledWindow()
    rs2.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    rs2.set_min_content_height(80); rs2.add(route_tv)
    rt_inner_box.pack_start(b_rtref, False, False, 0)
    rt_inner_box.pack_start(rs2, True, True, 0)
    rt_exp.add(rt_inner_box)
    sys_inner.pack_start(rt_exp, False, False, 0)

    def _refresh_routing(*_):
        raw = sh("pactl list short modules 2>/dev/null")
        lines = []
        for ln in raw.splitlines():
            parts = ln.split(None, 2)
            if len(parts) < 2: continue
            mod_type = parts[1]; args_raw = parts[2] if len(parts) > 2 else ""
            if any(x in mod_type.lower() for x in ["loopback","null-sink","remap-source","rtp"]):
                args_short = args_raw[:80] + ("..." if len(args_raw) > 80 else "")
                lines.append(f"  {mod_type}\n    {args_short}")
        route_buf.set_text("\n\n".join(lines) if lines else
                           "(no loopback / null-sink / remap / rtp modules found)")

    b_rtref.connect("clicked", _refresh_routing)
    sys_inner.pack_start(hsep(), False, False, 0)

    # log viewer
    log_hdr = Gtk.Label(label="LOG"); log_hdr.set_xalign(0)
    log_hdr.get_style_context().add_class("ptitle")
    sys_inner.pack_start(log_hdr, False, False, 0)

    lsr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    ll2 = Gtk.Label(label="SOURCE:"); ll2.get_style_context().add_class("dim-s")
    lsr.pack_start(ll2, False, False, 0)
    LOG_SRCS = {
        "LPD8":      str(HOME/".cache"/"lpd8-mixer.log"),
        "routesd":   str(HOME/".cache"/"roaring-audio-routesd.log"),
        "mic-bus":   str(HOME/".cache"/"roaring-mic-bussesd.log"),
        "mic-route": str(HOME/".cache"/"roaring-mic-routesd.log"),
        "vm-sinks":  str(HOME/".cache"/"roaring-vm-sinks.log"),
        "venmic":    str(HOME/".var/app/dev.vencord.Vesktop/.local/state/venmic/venmic.log"),
    }
    for nm, lp in LOG_SRCS.items():
        b2 = Gtk.Button(label=nm); b2.get_style_context().add_class("btn-xs")
        b2.connect("clicked", lambda _, p=lp: col.set_log(p))
        lsr.pack_start(b2, False, False, 0)
    sys_inner.pack_start(lsr, False, False, 0)

    log_scr = Gtk.ScrolledWindow()
    log_scr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    log_scr.set_min_content_height(160)
    log_tv = Gtk.TextView(); log_tv.set_editable(False); log_tv.set_cursor_visible(False)
    log_tv.set_wrap_mode(Gtk.WrapMode.CHAR)
    log_tv.get_style_context().add_class("log-tv")
    log_buf = log_tv.get_buffer(); log_scr.add(log_tv)
    sys_inner.pack_start(log_scr, True, True, 0)

    # status bar
    sbar = Gtk.Label(label=""); sbar.set_xalign(0); sbar.set_ellipsize(3)
    sbar.get_style_context().add_class("statusbar")
    root.pack_start(sbar, False, False, 0)

    # tray
    ind = None
    _ti = {}
    if HAVE_IND:
        ind = AI.Indicator.new(APP_ID, "audio-card",
                               AI.IndicatorCategory.APPLICATION_STATUS)
        ind.set_status(AI.IndicatorStatus.ACTIVE)
        tm = Gtk.Menu()
        def _tmi(key, lbl2):
            it = Gtk.MenuItem(label=lbl2); tm.append(it); _ti[key] = it; return it
        def _tms(): tm.append(Gtk.SeparatorMenuItem())
        _tmi("show",     "Show Window")
        _tmi("unmute",   "UNMUTE ALL  [U]")
        _tms()
        _tmi("astro",    "Toggle Astro  [T]")
        _tmi("scarlett", "Toggle Scarlett  [S]")
        _tms()
        _tmi("soft",     "Restart (soft)  F5")
        _tmi("hard",     "Restart (hard)  Ctrl+R")
        _tmi("dump",     "Debug Dump  Ctrl+D")
        _tmi("ssh",      "SSH to Laptop  Ctrl+L")
        _tms()
        _tmi("settings", "Settings...  Ctrl+,")
        _tms()
        _tmi("quit",     "Quit")
        tm.show_all(); ind.set_menu(tm)

    # actions

    def _msg(title, body="", modal=False):
        d = Gtk.MessageDialog(transient_for=win, modal=modal,
                              message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text=title)
        if body: d.format_secondary_text(body)
        if modal: d.run(); d.destroy()
        else:     d.connect("response", lambda dd, _: dd.destroy()); d.show()

    def _confirm(title, body=""):
        d = Gtk.MessageDialog(transient_for=win, modal=True,
                              message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.YES_NO, text=title)
        if body: d.format_secondary_text(body)
        r = d.run(); d.destroy(); return r == Gtk.ResponseType.YES

    def _act_toggle_astro(*_):  run_bin("roaring_toggle_astro_target.sh")
    def _act_toggle_scarlett(*_): run_bin("toggle_scarlett_speakers.sh")

    def _act_unmute(*_):
        unmute_all()
        _msg("Unmuted", "Sent unmute to: Astro Chat, Astro Game, vm_game, vm_chat, vm_music, laptop_audio, Scarlett.")

    def _carla_start(*_):
        sh_bg("systemctl --user reset-failed roaring-carla-session.service 2>/dev/null; "
              "systemctl --user start roaring-carla-session.service")

    def _carla_stop(*_):
        sh_bg("systemctl --user stop roaring-carla-session.service 2>/dev/null; "
              "pkill -TERM -f '/app/share/carla/carla' 2>/dev/null; sleep 1; "
              "pkill -KILL -f 'bwrap.*-- carla' 2>/dev/null; true")

    def _carla_dedup(*_):
        n = carla_count()
        if n <= 1: _msg("Carla", f"{n} instance running -- nothing to deduplicate."); return
        if _confirm("Dedup Carla", f"{n} Carla python3 processes detected.\n"
                    "Kill all and restart roaring-carla-session?"):
            _carla_stop()
            GLib.timeout_add(1800, lambda: (_carla_start(), False)[-1])

    carla_btns[0].connect("clicked", lambda *_: _carla_start())
    carla_btns[1].connect("clicked", lambda *_: _carla_stop())
    carla_btns[2].connect("clicked", lambda *_: _carla_dedup())

    _qa_refs["unmute"].connect("clicked",  lambda *_: _act_unmute())
    _qa_refs["soft"].connect("clicked",    lambda *_: _act_soft())
    _qa_refs["hard"].connect("clicked",    lambda *_: _act_hard())
    _qa_refs["dump"].connect("clicked",    lambda *_: _act_dump())
    _qa_refs["ssh"].connect("clicked",     lambda *_: _act_ssh_laptop())
    _qa_refs["rfail"].connect("clicked",   lambda *_: sh_bg("systemctl --user reset-failed"))
    _qa_refs["fdef"].connect("clicked",    lambda *_: force_default_sink("vm_game"))

    def _act_soft(*_):
        run_bin("roaring_restart_everything.sh", "--soft", "--no-dump", "--no-prompt-carla")

    def _act_hard(*_):
        if _confirm("Hard Restart",
                    "PipeWire will fully restart -- audio cuts for ~1s.\n"
                    "After restart, press U if audio is still silent."):
            run_bin("roaring_restart_everything.sh", "--hard", "--no-dump", "--no-prompt-carla")

    def _act_dump(*_):
        _trigger_dump()
        run_bin("roaring_audio_debug_dump.sh")
        _msg("Debug Dump",
             f"In-process snapshot -> {_DUMP_DIR}/summary.txt\n"
             "Shell dump (pactl/pw state) -> ~/Desktop.")

    def _act_ssh_laptop(*_):
        import shutil as _sh2
        import subprocess as _sp
        # Read SSH target from ~/.config/hearth/config.json
        # Keys: ssh_host, ssh_user, ssh_key (optional, defaults to ~/.ssh/id_ed25519)
        _ssh_cfg_path = CFGDIR / "config.json"
        try:
            _ssh_cfg = json.loads(_ssh_cfg_path.read_text()) if _ssh_cfg_path.exists() else {}
        except Exception:
            _ssh_cfg = {}
        WIN_IP   = _ssh_cfg.get("ssh_host", "")
        WIN_USER = _ssh_cfg.get("ssh_user", "")
        WIN_KEY  = _ssh_cfg.get("ssh_key",  str(Path.home() / ".ssh" / "id_ed25519"))
        if not WIN_IP or not WIN_USER:
            _msg(
                "SSH not configured",
                f"Create {_ssh_cfg_path} with:\n"
                '{"ssh_host": "192.168.x.x", "ssh_user": "username", '
                '"ssh_key": "~/.ssh/keyname"}'
            )
            return
        ssh_cmd  = f"ssh -i {WIN_KEY} -o BatchMode=yes -o ConnectTimeout=8 {WIN_USER}@{WIN_IP}"
        for term, args in [
            ("konsole", ["konsole", "-e", "bash", "-c",
                         f"{ssh_cmd}; echo; echo '--- session ended ---'; read -p 'Press Enter to close'",
                         "--", "--noclose"]),
            ("xterm",   ["xterm", "-T", f"SSH {WIN_IP}", "-e", ssh_cmd]),
            ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c",
                                f"{ssh_cmd}; read -p 'Press Enter'"]),
            ("xfce4-terminal", ["xfce4-terminal", "-e", ssh_cmd]),
        ]:
            if _sh2.which(term): _sp.Popen(args); return
        _msg("SSH to Laptop", "No terminal emulator found.\nInstall konsole or xterm.")

    def _act_settings(*_):
        def _on_save(new_s):
            nonlocal S
            S = new_s
            col._s = new_s
        settings_dialog(win, S, _on_save)

    def _act_about(*_):
        d = Gtk.AboutDialog(transient_for=win, modal=True)
        d.set_program_name("Hearth")
        d.set_version(VER)
        d.set_comments(
            "GTK3 PipeWire mixer + control panel.\n"
            "PipeWire / KDE Plasma Wayland\n"
            "Astro A50 + Focusrite Scarlett Solo + AKAI LPD8"
        )
        d.run(); d.destroy()

    # LPD8 auto-reconnect watcher
    _lpd8_watcher_running = threading.Event()

    def _lpd8_device_present():
        try:
            out = subprocess.check_output(
                ["amidi", "-l"], stderr=subprocess.DEVNULL, timeout=3
            ).decode()
            if re.search(r"(?i)lpd8|akai.*lpd", out):
                return True
        except Exception:
            pass
        try:
            cards = Path("/proc/asound/cards").read_text()
            return bool(re.search(r"(?i)lpd8|akai", cards))
        except Exception:
            return False

    def _act_lpd8_reconnect(*_):
        _lpd8_watcher_running.clear()
        btn_lpd8_reconnect.set_label("Restarting...")
        btn_lpd8_reconnect.set_sensitive(False)
        sh_bg(
            "systemctl --user reset-failed lpd8-mixer.service 2>/dev/null; "
            "systemctl --user restart lpd8-mixer.service"
        )
        GLib.timeout_add(2500, _lpd8_reconnect_restore)

    def _lpd8_reconnect_restore():
        btn_lpd8_reconnect.set_label("↺ LPD8")
        btn_lpd8_reconnect.set_sensitive(True)
        return False

    def _lpd8_watcher_body():
        while _lpd8_watcher_running.is_set():
            if _lpd8_device_present():
                _lpd8_watcher_running.clear()
                GLib.idle_add(_act_lpd8_reconnect)
                return
            time.sleep(3)

    def _maybe_start_lpd8_watcher():
        if not _lpd8_watcher_running.is_set():
            _lpd8_watcher_running.set()
            threading.Thread(
                target=_lpd8_watcher_body, name="lpd8-watcher", daemon=True
            ).start()

    btn_lpd8_reconnect.connect("clicked", _act_lpd8_reconnect)

    btn_astro.connect("clicked",    lambda *_: _act_toggle_astro())
    btn_scarlett.connect("clicked", lambda *_: _act_toggle_scarlett())
    btn_emu.connect("clicked",      lambda *_: _act_unmute())

    if HAVE_IND and _ti:
        _ti["show"].connect("activate",     lambda *_: (win.show_all(), win.present()))
        _ti["unmute"].connect("activate",   lambda *_: _act_unmute())
        _ti["astro"].connect("activate",    lambda *_: _act_toggle_astro())
        _ti["scarlett"].connect("activate", lambda *_: _act_toggle_scarlett())
        _ti["soft"].connect("activate",     lambda *_: _act_soft())
        _ti["hard"].connect("activate",     lambda *_: _act_hard())
        _ti["dump"].connect("activate",     lambda *_: _act_dump())
        _ti["ssh"].connect("activate",      lambda *_: _act_ssh_laptop())
        _ti["settings"].connect("activate", lambda *_: _act_settings())
        _ti["quit"].connect("activate",     lambda *_: Gtk.main_quit())

    # hamburger menu
    menu = Gtk.Menu()
    def _mi(lbl2, fn2):
        it = Gtk.MenuItem(label=lbl2); it.connect("activate", lambda *_: fn2())
        menu.append(it)
    def _ms(): menu.append(Gtk.SeparatorMenuItem())

    _mi("Unmute All  [U]",                  _act_unmute)
    _mi("Toggle Astro  [T]",                _act_toggle_astro)
    _mi("Toggle Scarlett  [S]",             _act_toggle_scarlett)
    _ms()
    _mi("Restart (soft)  F5",              _act_soft)
    _mi("Restart (hard)  Ctrl+R",          _act_hard)
    _mi("Debug Dump  Ctrl+D",              _act_dump)
    _mi("SSH to Laptop  Ctrl+L",           _act_ssh_laptop)
    _mi("Reset Failed Units",              lambda: sh_bg("systemctl --user reset-failed"))
    _mi("Force Default Sink -> vm_game",   lambda: force_default_sink("vm_game"))
    _ms()
    _mi("Settings...  Ctrl+,",             _act_settings)
    _mi("About",                           _act_about)
    _ms()
    _mi("Hide to Tray  Esc",               lambda: win.hide())
    _mi("Quit",                            Gtk.main_quit)
    menu.show_all(); mb_btn.set_popup(menu)

    # keybinds
    def _on_key(w, ev):
        k    = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        if k == Gdk.KEY_Escape or (ctrl and k == Gdk.KEY_q): win.hide(); return True
        if k in (Gdk.KEY_t, Gdk.KEY_T):  _act_toggle_astro();   return True
        if k in (Gdk.KEY_s, Gdk.KEY_S):  _act_toggle_scarlett(); return True
        if k in (Gdk.KEY_u, Gdk.KEY_U):  _act_unmute();          return True
        if k in (Gdk.KEY_m, Gdk.KEY_M):  _toggle_mixer();        return True
        if k == Gdk.KEY_F5:              _act_soft();             return True
        if ctrl and k == Gdk.KEY_r:      _act_hard();             return True
        if ctrl and k == Gdk.KEY_d:      _act_dump();             return True
        if ctrl and k == Gdk.KEY_l:      _act_ssh_laptop();       return True
        if ctrl and k == Gdk.KEY_comma:  _act_settings();         return True
        return False
    win.connect("key-press-event", _on_key)

    # drain -- 100ms GLib timer, no blocking I/O
    _prev_log         = [""]
    _prev_states      = {}
    _prev_lpd8_st     = [""]
    # widget rebuild cache: keyed on sink -> list of "pa_index:name" strings
    # only tear down and rebuild when this changes -- prevents the GObject
    # signal-closure / refcount leak that caused 11 GB+ when running every tick
    _prev_sink_inputs: dict = {}

    # in-process dump (SIGUSR1/--dump) -- writes to last_debug/, always overwrites
    # tracemalloc: python3 -c "import tracemalloc,pprint; s=tracemalloc.Snapshot.load('...pkl'); pprint.pprint(s.statistics('lineno')[:30])"
    _DUMP_DIR   = CFGDIR / "last_debug"
    _dump_event = threading.Event()

    def _do_dump_work():
        try:
            _DUMP_DIR.mkdir(parents=True, exist_ok=True)
            ts    = time.strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"=== hearth in-process dump -- {ts} ===\n\n"]

            try:
                for _ln in Path("/proc/self/status").read_text().splitlines():
                    if _ln.startswith("VmRSS:"):
                        _kb = int(_ln.split()[1])
                        lines.append(f"Process RSS:       {_kb/1024:.1f} MB\n")
                    elif _ln.startswith("VmPeak:"):
                        _kb = int(_ln.split()[1])
                        lines.append(f"Process VmPeak:    {_kb/1024:.1f} MB\n")
            except Exception as _e:
                lines.append(f"RSS read error: {_e}\n")

            _thr = threading.enumerate()
            lines.append(f"\nThreads ({len(_thr)}):\n")
            for _t in _thr:
                lines.append(f"  {_t.name!r:<28} daemon={_t.daemon} alive={_t.is_alive()}\n")

            lines.append(f"\n_prev_sink_inputs ({len(_prev_sink_inputs)} sinks tracked):")
            for _sk, _v in _prev_sink_inputs.items():
                lines.append(f"\n  {_sk}: {len(_v)} app(s) -> {_v}")
            lines.append("\n")

            lines.append(f"\nCollector queue depth: {q.qsize()}\n")

            if _tracemalloc.is_tracing():
                _snap = _tracemalloc.take_snapshot()
                _top  = _snap.statistics("lineno")[:25]
                lines.append(f"\ntracemalloc top 25 allocations:\n")
                for _st in _top:
                    lines.append(f"  {_st}\n")
                _snap.dump(str(_DUMP_DIR / "tracemalloc.pkl"))
                lines.append(f"\nFull snapshot: {_DUMP_DIR}/tracemalloc.pkl\n")
            else:
                lines.append("\ntracemalloc: not tracing\n")

            (_DUMP_DIR / "summary.txt").write_text("".join(lines))
            print(f"[hearth] dump -> {_DUMP_DIR}/summary.txt", flush=True)
        except Exception as _ex:
            print(f"[hearth] dump error: {_ex}", flush=True)
        finally:
            _dump_event.clear()

    def _trigger_dump():
        if not _dump_event.is_set():
            _dump_event.set()
            threading.Thread(target=_do_dump_work, daemon=True,
                             name="hearth-dump").start()

    def _on_sigusr1(sig, frame):
        _trigger_dump()

    signal.signal(signal.SIGUSR1, _on_sigusr1)

    def _drain():
        try:    data = q.get_nowait()
        except queue.Empty: return True

        # services
        for unit, _ in SERVICES:
            st = data["svc"].get(unit, "unknown")
            if unit in _svc_w and _prev_states.get(unit) != st:
                sl, lset = _svc_w[unit]
                ctx = sl.get_style_context()
                for c in list(ctx.list_classes()):
                    if c.startswith("s-"): ctx.remove_class(c)
                ctx.add_class(ST_CSS.get(st, "s-dim"))
                sl.set_text(st); lset(ST_COL.get(st, "#3a3a3c"))
                _prev_states[unit] = st

        # health chip
        fail = [u for u in CORE if data["svc"].get(u) == "failed"]
        deg  = [u for u in CORE if data["svc"].get(u) in ("inactive","unknown")]
        ctx_hp = lbl_hp.get_style_context()
        for c in ["hp-ok","hp-warn","hp-bad"]: ctx_hp.remove_class(c)
        if fail:
            hp_set("#ff453a"); lbl_hp.set_text(f"{len(fail)} FAILED"); ctx_hp.add_class("hp-bad")
            if ind: ind.set_icon("dialog-error")
        elif deg:
            hp_set("#ff9f0a"); lbl_hp.set_text("DEGRADED"); ctx_hp.add_class("hp-warn")
            if ind: ind.set_icon("dialog-warning")
        else:
            hp_set("#30d158"); lbl_hp.set_text("ALL OK"); ctx_hp.add_class("hp-ok")
            if ind: ind.set_icon("audio-card")

        # memory
        mb   = data.get("mem_mb", 0.0)
        warn = S.get("mem_warn_mb", 80); crit = S.get("mem_crit_mb", 200)
        ctx_m = lbl_mem.get_style_context()
        for c in ["dim-s","wrn","bad"]: ctx_m.remove_class(c)
        if   mb > crit: ctx_m.add_class("bad");   lbl_mem.set_text(f"PW {mb:.0f}MB !")
        elif mb > warn: ctx_m.add_class("wrn");   lbl_mem.set_text(f"PW {mb:.0f}MB")
        else:           ctx_m.add_class("dim-s"); lbl_mem.set_text(f"PW {mb:.0f}MB")

        # faders + VU
        ss = data.get("sink_st", {})
        _drain_sinks = list(VM_SINKS) + [("B1", "mic_b1"), ("B2", "mic_b2")]
        for _, sink in _drain_sinks:
            _block[sink] = True
            v   = data["vol"].get(sink, 0)
            m   = data["mute"].get(sink, False)
            st2 = ss.get(sink, "--")

            if sink in _adjs:
                _adjs[sink].handler_block_by_func(_vol_fn[sink])
                _adjs[sink].set_value(v)
                _adjs[sink].handler_unblock_by_func(_vol_fn[sink])

            if sink in _mutes and sink in _mute_fn:
                _mutes[sink].handler_block_by_func(_mute_fn[sink])
                _mutes[sink].set_active(m)
                _mutes[sink].handler_unblock_by_func(_mute_fn[sink])

            if sink in _v_lbl:
                _db = ("-inf" if v <= 0 else
                       f"{chr(43) if v > 100 else ''}{20*math.log10(max(v,1)/100):.1f}")
                _col = "#ff9f0a" if v > 100 else "#ffffffd9"
                _v_lbl[sink].set_markup(
                    f'<span foreground="{_col}">{v}%</span>'
                    f'<span font_size="small" foreground="#ffffff73">'
                    f' {_db}dB</span>'
                )
            if sink in _st_lbl:
                stl = _st_lbl[sink]; stl.set_text(st2)
                sctx = stl.get_style_context()
                for c2 in ["ch-st-run","ch-st-idle"]: sctx.remove_class(c2)
                sctx.add_class("ch-st-run" if st2 == "RUNNING" else "ch-st-idle")
            if sink in _vu:
                _vu[sink].set_state(st2, v)
            if sink in _app_lbl:
                _apps = data.get("sink_inputs", {}).get(sink, [])
                _sink_has_inputs[sink] = bool(_apps) or sink in ("mic_b1", "mic_b2")
                # only rebuild when app list changes -- same destroy/rebuild to avoid GObject leak
                _cur_keys = [f"{_a.get('index','')}:{_a.get('name','')}" for _a in _apps]
                if _cur_keys != _prev_sink_inputs.get(sink):
                    _prev_sink_inputs[sink] = _cur_keys
                    # compact strip label: plain text, no scroll, no interactive widgets
                    if sink in _app_compact:
                        if _apps:
                            _names = [_a.get("name","?")[:10] for _a in _apps[:3]]
                            _app_compact[sink].set_text("  ".join(_names))
                        else:
                            _app_compact[sink].set_text("—")
                    # popover content: full interactive controls, built same way as before
                    _pop_abox = _app_lbl[sink]
                    for _ch in list(_pop_abox.get_children()):
                        _ch.destroy()  # destroy() not remove() -- frees GObject refs
                    if _apps:
                        for _ap in _apps[:8]: _pop_abox.add(_make_app_row(_ap, sink))
                        if sink in _app_popbtn: _app_popbtn[sink].show()
                    else:
                        _ph = Gtk.Label(label="—")
                        _ph.get_style_context().add_class("app-row-name")
                        _ph.set_xalign(0.5); _pop_abox.add(_ph)
                        if sink in _app_popbtn: _app_popbtn[sink].hide()
                    _pop_abox.show_all()
            _block[sink] = False

        # astro HW
        a_muted = data.get("astro_chat_mute", False)
        a_vol   = data.get("astro_chat_vol", 100)
        at      = data.get("astro", "chat")
        at_disp = "stereo-game" if at == "game" else "stereo-chat"
        lbl_astro_tgt.set_text(f"Astro: {at_disp}")
        ctx_tgt = lbl_astro_tgt.get_style_context()
        for c in ["hw-ok","hw-idle","hw-bad"]: ctx_tgt.remove_class(c)
        ctx_bu = btn_emu.get_style_context()
        if a_muted:
            lbl_astro_hw.set_text("HW MUTED -- press U!")
            ctx_tgt.add_class("hw-bad")
            if "alert" not in ctx_bu.list_classes(): ctx_bu.add_class("alert")
            if ind: ind.set_icon("audio-volume-muted")
        else:
            lbl_astro_hw.set_text(f"HW: {a_vol}%  ok")
            ctx_tgt.add_class("hw-ok")
            if "alert" in ctx_bu.list_classes(): ctx_bu.remove_class("alert")

        # scarlett
        sc_on = data.get("scarlett", False)
        lbl_scarlett.set_text(f"Scarlett: {'ON' if sc_on else 'OFF'}")
        ctx_sc = lbl_scarlett.get_style_context()
        for c in ["hw-ok","hw-idle"]: ctx_sc.remove_class(c)
        ctx_sc.add_class("hw-ok" if sc_on else "hw-idle")

        # carla
        n = data.get("carla", 0)
        ctx_cl = lbl_carla.get_style_context()
        for c in ["hw-ok","hw-idle","hw-bad"]: ctx_cl.remove_class(c)
        if n == 0:   lbl_carla.set_text("not running"); ctx_cl.add_class("hw-idle")
        elif n == 1: lbl_carla.set_text("running (1)"); ctx_cl.add_class("hw-ok")
        else:        lbl_carla.set_text(f"{n} instances -- dedup!"); ctx_cl.add_class("hw-bad")

        # mic routing combos
        for bus, pair in [("B1", _mc_combos.get("B1")), ("B2", _mc_combos.get("B2"))]:
            if pair is None: continue
            combo, src_key = pair
            val = data.get(src_key, "none") if src_key else "none"
            idx = MIC_OPTS.index(val) if val in MIC_OPTS else 0
            if combo.get_active() != idx:
                fn = _mc_fn.get(bus)
                if fn:
                    combo.handler_block_by_func(fn); combo.set_active(idx)
                    combo.handler_unblock_by_func(fn)
                else: combo.set_active(idx)

        # loopback toggles -- sync to real PW state with race guard
        # after user click, skip sync for 3s so toggle isn't reset before sh_bg finishes
        for bus, key in [("B1", "lb_b1"), ("B2", "lb_b2")]:
            chk = _loopback_checks.get(bus)
            fn  = _loopback_fns.get(bus)
            if chk is None or fn is None: continue
            if time.time() - _lb_last_click.get(bus, 0.0) < 3.0:
                continue
            real = data.get(key, False)
            if chk.get_active() != real:
                chk.handler_block_by_func(fn)
                chk.set_active(real)
                chk.handler_unblock_by_func(fn)

        # moonlight mic
        ml_src = data.get("moonlight_mic_src", "b2_mic")
        ml_idx = MOONLIGHT_MIC_OPTS.index(ml_src) if ml_src in MOONLIGHT_MIC_OPTS else 0
        if _ml_combo.get_active() != ml_idx:
            _ml_combo.handler_block_by_func(_ml_fn)
            _ml_combo.set_active(ml_idx)
            _ml_combo.handler_unblock_by_func(_ml_fn)
        _ml_svc_lbl.set_text(f"svc: {data['svc'].get('roaring-moonlight-mic', '--')}")

        # mic sources
        src = data.get("src_st", {})
        lbl_sm7b.set_text( f"SM7B:      {src.get('sm7b_mono',    '--')}")
        lbl_amic.set_text(  f"Astro Mic: {src.get('astro_mic_48k','--')}")
        lbl_b1src.set_text( f"b1_mic:    {src.get('b1_mic',       '--')}")
        lbl_b2src.set_text( f"b2_mic:    {src.get('b2_mic',       '--')}")

        # LPD8 -- kick auto-watcher on failure/disconnect
        lpd8_st = data['svc'].get('lpd8-mixer', '--')
        lbl_lpd8.set_text(f"lpd8-mixer:  {lpd8_st}")
        if (lpd8_st in ("failed", "inactive")
                and _prev_lpd8_st[0] not in ("failed", "inactive", "")):
            _maybe_start_lpd8_watcher()
        _prev_lpd8_st[0] = lpd8_st

        # timer
        t = data.get("timer", "")
        lbl_timer.set_text(f"Weekly restart: {t}" if t else "Weekly restart timer: not installed")

        # mute summary
        muted_l = []
        if a_muted: muted_l.append("ASTRO-HW")
        for _, sink in list(VM_SINKS) + [("B1", "mic_b1"), ("B2", "mic_b2")]:
            if data["mute"].get(sink, False): muted_l.append(sink.upper())
        ctx_ms = lbl_mute_sum.get_style_context()
        for c in ["dim-s","bad","ok"]: ctx_ms.remove_class(c)
        if muted_l:
            lbl_mute_sum.set_text(f"Muted: {', '.join(muted_l)}"); ctx_ms.add_class("bad")
        else:
            lbl_mute_sum.set_text("All sinks unmuted"); ctx_ms.add_class("ok")

        # log
        log_txt = data.get("log", "")
        if log_txt != _prev_log[0]:
            _prev_log[0] = log_txt
            log_buf.set_text(log_txt)
            log_tv.scroll_to_iter(log_buf.get_end_iter(), 0, False, 0, 1)

        # status bar
        fail_all = [u for u,_ in SERVICES if data["svc"].get(u)=="failed"]
        ctx_sb = sbar.get_style_context()
        for c in ["statusbar","alert"]: ctx_sb.remove_class(c)
        ctx_sb.add_class("statusbar")
        pw_v = data.get("pw_ver","")
        if fail_all:
            ctx_sb.add_class("alert"); sbar.set_text(f"FAILED: {', '.join(fail_all)}")
        elif a_muted:
            ctx_sb.add_class("alert")
            sbar.set_text("Astro A50 hardware sink MUTED -- press U to restore audio")
        else:
            n_active = sum(1 for _,v in data["svc"].items() if v in ("active","running"))
            pw_str   = f"PipeWire {pw_v}  |  " if pw_v else ""
            sbar.set_text(
                f"{pw_str}{n_active}/{len(SERVICES)} svc active  |  "
                "T=astro  S=scarlett  U=unmute  M=mixer  F5=soft  Ctrl+R=hard  "
                "Ctrl+D=dump  Ctrl+L=ssh  Ctrl+,=settings"
            )
        # vesktop portal + venmic status in QA frame
        _portal_ok = data.get("vc_portal_ok", False)
        _dflt_sink = data.get("vc_default_sink", "")
        _venmic_log_found = data.get("vc_venmic_log_exists", False)

        ctx_vp = lbl_vc_portal.get_style_context()
        for _c in ["s-ok","s-bad","dim-s"]: ctx_vp.remove_class(_c)
        if _portal_ok:
            lbl_vc_portal.set_text("portal: ok"); ctx_vp.add_class("s-ok")
        else:
            lbl_vc_portal.set_text("portal: NOT RUNNING -- click ↺"); ctx_vp.add_class("s-bad")

        ctx_vs = lbl_vc_sink.get_style_context()
        for _c in ["s-ok","s-warn","dim-s"]: ctx_vs.remove_class(_c)
        _sink_ok = "vm_game" in _dflt_sink
        ctx_vs.add_class("s-ok" if _sink_ok else "s-warn")
        lbl_vc_sink.set_text(f"default sink: {_dflt_sink[:32] or '?'}")

        ctx_vn = lbl_vc_venmic.get_style_context()
        for _c in ["s-ok","dim-s"]: ctx_vn.remove_class(_c)
        if _venmic_log_found:
            lbl_vc_venmic.set_text("venmic log: found (select in Log tab)")
            ctx_vn.add_class("s-ok")
        else:
            lbl_vc_venmic.set_text("venmic log: not found (VENMIC_ENABLE_LOG needed)")
            ctx_vn.add_class("dim-s")

        return True

    # padfire peer probe
    _PF_SOCK = str(Path.home() / ".config" / "padfire" / "padfire.sock")

    def _pf_ipc(cmd, timeout=0.5):
        if not Path(_PF_SOCK).exists(): return None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout); s.connect(_PF_SOCK)
            s.sendall((cmd + "\n").encode())
            r = s.recv(4096); s.close()
            return r.decode().strip()
        except Exception: return None

    def _pf_probe():
        while True:
            raw = _pf_ipc("status")
            try:    data = json.loads(raw) if raw else None
            except: data = None
            GLib.idle_add(_pf_update_panel, data)
            time.sleep(2)

    def _pf_update_panel(data):
        try:
            if data is None:
                lbl_pf_status.set_text("○ not running")
                ctx = lbl_pf_status.get_style_context()
                ctx.remove_class("s-ok"); ctx.add_class("s-dim")
                lbl_pf_page.set_text("")
                lbl_pf_playing.set_text("")
                return False
            conn = data.get("connected", False)
            page = data.get("page", 0)
            info = data.get("playing_info", [])
            ver  = data.get("version", "")
            mon  = data.get("monitor_enabled", False)
            ctx  = lbl_pf_status.get_style_context()
            ctx.remove_class("s-dim"); ctx.add_class("s-ok")
            midi_str = ("● MIDI" if conn else "○ no MIDI")
            mon_str  = "  ◉ MON" if mon else ""
            lbl_pf_status.set_text(f"{midi_str}{mon_str}  v{ver}")
            lbl_pf_page.set_text(f"Page {page + 1}/8")
            if info:
                lines = [f"▶ {i.get('label','?')}  ->  {i.get('sink','?')}"
                         for i in info[:4]]
                lbl_pf_playing.set_text("\n".join(lines))
            else:
                lbl_pf_playing.set_text("no sounds playing")
        except Exception as _e:
            print(f"[hearth] pf_update_panel: {_e}")
        return False

    threading.Thread(target=_pf_probe, daemon=True, name="pf-probe").start()

    GLib.timeout_add(100, _drain)

    # VU at ~20fps -- feed_level MUST be here, not in _drain
    # _drain is queue-driven (~2s cadence from pactl poll); parec reads at 33ms
    # meter was decaying to zero between drain ticks when feed was in _drain
    def _vu_tick():
        try:
            if S.get("vu_enabled", True):
                for sink, vu in _vu.items():
                    if not _sink_has_inputs.get(sink, True):
                        vu.feed_level(0.0); vu.tick(); continue
                    if peak_poller.available:
                        _rms_lin = peak_poller.get_peak(f"{sink}.monitor")
                        if _rms_lin > 1e-7:
                            _db    = 20 * math.log10(_rms_lin)
                            _level = max(0.0, min(1.0, (_db + 60.0) / 60.0))
                        else:
                            _level = 0.0
                        vu.feed_level(_level)
                    vu.tick()
        except Exception: pass
        return True
    GLib.timeout_add(50, _vu_tick)

    GLib.idle_add(_refresh_routing)

    # IPC server
    def _ipc(cmd):
        if cmd == "quit":   GLib.idle_add(Gtk.main_quit); return "quitting"
        if cmd == "show":   GLib.idle_add(lambda: (win.show_all(),win.present()) and False); return "ok"
        if cmd == "unmute": GLib.idle_add(lambda: unmute_all() or False); return "ok"
        if cmd == "dump":   _trigger_dump(); return f"dumping -> {_DUMP_DIR}/summary.txt"
        if cmd == "get_sinks":
            result = {}
            for label, sink in VM_SINKS:
                result[sink] = {
                    "vol":   pactl_vol(sink),
                    "mute":  pactl_muted(sink),
                    "label": label,
                }
            return json.dumps(result)
        if cmd.startswith("padfire_status "):
            return "ok"
        return "unknown"
    threading.Thread(target=ipc_serve, args=(_ipc,), daemon=True).start()

    if not S.get("start_minimized", False):
        win.show_all()
    else:
        if not HAVE_IND:
            win.show_all()

    # apply initial collapse state after show_all (hide must come after show)
    if _mix_collapsed[0]:
        nb.hide(); btn_collapse.set_label("▸")
        win.resize(win.get_allocated_width(), 1)

    Gtk.main()

    try: Path(SOCK).unlink(missing_ok=True)
    except: pass
    try: Path(PIDF).unlink(missing_ok=True)
    except: pass


def main():
    p = argparse.ArgumentParser(prog=APP_ID, description="Hearth audio control")
    p.add_argument("--quit",     action="store_true", help="quit running instance")
    p.add_argument("--show",     action="store_true", help="show/focus running instance")
    p.add_argument("--unmute",   action="store_true", help="unmute all sinks and exit (headless)")
    p.add_argument("--dump",     action="store_true",
                   help="trigger in-process debug dump on running instance; "
                        "writes to ~/.config/roaring/last_debug/")
    p.add_argument("--vu-dump",  action="store_true",
                   help="write per-source RMS/peak CSV to ~/roaring_vu_dump_<ts>.csv.gz "
                        "(diagnostic only; off by default since v4.3)")
    args = p.parse_args()

    if args.unmute: unmute_all(); print("unmuted all sinks"); return
    if args.quit:
        r = ipc_send("quit")
        print("quit" if r else "no running instance"); return
    if args.dump:
        r = ipc_send("dump")
        print(r if r else "no running instance"); return

    if ipc_send("show"): return
    pid = _read_pid()
    if pid and _pid_alive(pid): ipc_send("show"); return

    run_app(vu_dump=args.vu_dump)

if __name__ == "__main__":
    main()
