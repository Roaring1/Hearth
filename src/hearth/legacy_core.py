"""Legacy non-GTK core of Hearth 5.1.

This is the part of the original single-module ``hearth.py`` that never
touched GTK: configuration, pactl/systemctl helpers, the Collector and the
PeakPoller. It is imported by :mod:`hearth.ui` and by the CLI.

Phase 3 is progressively replacing it with the typed modules alongside it
(``paths``, ``config``, ``proc``, ``audio``, ``services``, ``ipc``,
``collector``, ``meters``). Nothing new should be added here.
"""

# 28/05/2026
# hearth -- GTK3 PipeWire mixer + control panel (Nobara, KDE Plasma Wayland)
#
#   - manages VM sinks, Astro A50, Scarlett Solo, Carla, Moonlight, LPD8
#   - Collector + PeakPoller threads; IPC via Unix socket (rac.sock)
#   - 21/06/2026: v4.8 -- fix mic loopback unload, stale stream controls,
#                 LPD8 reconnect watcher startup, and unsafe SSH command quoting
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

import os, re, json, math, time, queue, signal, socket, threading, subprocess, argparse, shlex
import logging
import tracemalloc as _tracemalloc
from pathlib import Path

from hearth import config, ipc, paths, settings

VER = "5.1"
APP_ID = "hearth"
# One logger for the whole module. Handlers are attached in main() only --
# importing this file must never reconfigure logging for someone else.
log = logging.getLogger("hearth")
# How often the Collector may re-run no_noise_ctl.sh reconcile (seconds).
NO_NOISE_RECONCILE_SEC = 5.0
# Age at which startup prunes old ~/roaring_vu_dump_*.csv.gz captures (days).
VU_DUMP_KEEP_DAYS = 7
HOME = Path.home()
BIN = HOME / "bin"
CFGDIR = paths.config_dir()

# The socket and pid file belong in $XDG_RUNTIME_DIR: a tmpfs the session
# owns and clears on logout, so a crash cannot leave a stale socket that
# outlives the login. They used to sit in the config directory, which meant
# a backup tool could try to copy a socket. Nothing outside this process
# ever referenced those two paths (verified by grep across ~/bin and the
# user units), so this move has no external consumers.
paths.ensure_dirs()
SOCK = str(paths.socket_file())
PIDF = str(paths.pid_file())
SETTF = str(paths.settings_file())

# Remove the pre-move socket/pid so a stale file in the old location can
# never be mistaken for a running instance.
for _stale in (CFGDIR / "rac.sock", CFGDIR / "rac.pid"):
    if _stale.exists():
        try:
            _stale.unlink()
        except OSError:
            pass

# One-time migration from old ~/.config/roaring/ location
_OLD_CFGDIR = HOME / ".config" / "roaring"
for _old, _new in [
    (_OLD_CFGDIR / "rac_settings.json", CFGDIR / "rac_settings.json"),
]:
    if _old.exists() and not _new.exists():
        import shutil as _shutil

        CFGDIR.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(str(_old), str(_new))


# The two shell conf files moved from ~/.config/ into the Hearth config dir.
# Symlinks at the old paths keep the ~/bin daemons working untouched, so this
# resolver prefers the new location and falls back to the old one rather than
# assuming the move has happened on every machine.
def _conf_path(name: str) -> str:
    new = CFGDIR / name
    old = HOME / ".config" / name
    return str(new if new.exists() or not old.exists() else old)


MXCONF = _conf_path("roaring_mixer.conf")
MCCONF = _conf_path("roaring_mic_router.conf")

# ---------------------------------------------------------------------------
# Machine-specific configuration
#
# Everything that identifies *this* rig lives in ~/.config/hearth/config.json,
# not in the source. Nothing here may contain a device serial number, a
# hostname, or an IP address: this file is published.
#
#   {
#     "astro_chat_sink":  "alsa_output...stereo-chat",
#     "astro_game_sink":  "alsa_output...stereo-game",
#     "monitor_sink":     "",            // blank = autodetect by match
#     "monitor_sink_match": "Focusrite",  // substring used for autodetect
#     "laptop_host":      "laptop",
#     "laptop_rtp_port":  46000,
#     "ssh_host": "", "ssh_user": "", "ssh_key": ""
#   }
# ---------------------------------------------------------------------------
CFGFILE = CFGDIR / "config.json"

# The parsing, validation and defaults now live in hearth.config, which is
# tested against the malformed files this one silently accepted: a
# wrong-typed value used to travel all the way to the command that used it.
# These names stay so the UI's star import keeps working unchanged.


def load_config():
    """The raw config dictionary, for callers that still expect a dict."""
    raw = config.read_raw(CFGFILE)
    return raw if isinstance(raw, dict) else {}


CONFIG = config.load(CFGFILE)
CFG = load_config()

for _unknown in config.unknown_keys(CFG):
    log.warning("config.json: %r is not a setting Hearth understands", _unknown)

ASTRO_CHAT = CONFIG.astro_chat_sink
ASTRO_GAME = CONFIG.astro_game_sink
# Resolved below, once the shell helpers exist: config value wins, otherwise
# the sink is discovered from `pactl` by substring match so the interface's
# serial number never has to be written down.
MONITOR_MATCH = CONFIG.monitor_sink_match
SCARLETT = CONFIG.monitor_sink

# Network audio target shown in the LAPTOP bus tooltips / routing summary.
LAPTOP_HOST = CONFIG.laptop_host
LAPTOP_RTP_PORT = CONFIG.laptop_rtp_port

VM_SINKS = [
    ("GAME", "vm_game"),
    ("CHAT", "vm_chat"),
    ("MUSIC", "vm_music"),
    ("SHARE", "vm_share"),
    ("LAPTOP", "laptop_audio"),
]
MIC_OPTS = ["none", "sm7b", "astro", "both"]

# (unit, user_can_control). Every long-running unit of the rig belongs here:
# a health grid that silently ignores units is how "12/12 active" was printed
# while four watchdogs and the command receiver were unmonitored.
SERVICES = [
    ("pipewire", False),
    ("pipewire-pulse", False),
    ("wireplumber", False),
    ("roaring-vm-sinks", True),
    ("roaring-mic-busses", True),
    ("roaring-mic-routesd", True),
    ("roaring-audio-routesd", True),
    ("roaring-moonlight-mic", True),
    ("roaring-laptop-audio", True),
    ("lpd8-mixer", True),
    ("roaring-carla-session", True),
    ("default-sink-vm-game", True),
    ("roaring-cmd-rx", True),
    ("roaring-sober-mic-watch", True),
    ("roaring-vesktop-mic-watchd", True),
    ("roaring-vesktop-stream-watch", True),
]
CORE = {
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "roaring-vm-sinks",
    "roaring-mic-busses",
    "lpd8-mixer",
}

# Defined in hearth.settings, which also validates each value and, unlike
# the save path this replaced, writes the file atomically.
DEFAULT_SETTINGS = settings.DEFAULTS


def _startup_cleanup():
    # remove leftover empty debug dirs and truncated VU dumps from old sessions
    for _d in sorted((HOME / "Desktop").glob("roaring_debug_*/")):
        try:
            if _d.is_dir() and not any(_d.iterdir()):
                _d.rmdir()
        except Exception:
            pass
    # Only prune stale dumps. Deleting them all meant the capture you made
    # last session was gone the next time Hearth started, which is exactly
    # when you want to look at it.
    _cutoff = time.time() - (VU_DUMP_KEEP_DAYS * 86400)
    # Both locations: the state dir where captures land now, and the loose
    # $HOME files written before the move, so old ones still age out.
    _stale = list(paths.dumps_dir().glob("vu_dump_*.csv.gz")) + list(
        HOME.glob("roaring_vu_dump_*.csv.gz")
    )
    for _gz in _stale:
        try:
            if _gz.stat().st_mtime < _cutoff:
                _gz.unlink()
        except Exception:
            pass


def load_settings():
    return settings.load(Path(SETTF))


def save_settings(s):
    # Atomic, via hearth.settings. The plain write this replaced could leave
    # a truncated file if Hearth died mid-save -- and the app saves while
    # quitting, which is exactly when it is most likely to be killed.
    settings.save(s, Path(SETTF))


# process helpers -- argv only. No shell is spawned anywhere in this file:
# every external command is an explicit argument list, so a sink or window
# name containing a space, quote or semicolon can never become shell syntax.


def sh_bg_argv(argv):
    """Fire-and-forget an argv list. No shell, so no quoting hazards."""
    try:
        subprocess.Popen(
            [str(a) for a in argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def pactl_bg(*args):
    """Fire-and-forget pactl call."""
    sh_bg_argv(["pactl", *args])


def systemctl_bg(*args):
    """Fire-and-forget systemctl --user call."""
    sh_bg_argv(["systemctl", "--user", *args])


def run_steps(steps, timeout=20):
    """Run argv lists in order on a worker thread; floats are sleeps.

    Replaces the `a; b; c` and `a && b` shell strings that used to be handed
    to /bin/sh. Failures are ignored on purpose: these are best-effort service
    pokes, and the UI reflects the real state on the next Collector tick.
    """

    def _worker():
        for step in steps:
            if isinstance(step, (int, float)):
                time.sleep(step)
                continue
            try:
                subprocess.run(
                    [str(a) for a in step], capture_output=True, text=True, timeout=timeout
                )
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()


def run_bin(script, *args):
    sh_bg_argv(["bash", str(BIN / script), *map(str, args)])


def detect_sink(match: str) -> str:
    """First sink name containing `match`, or "" if PipeWire has none.

    Used so the monitor interface is identified by make ("Focusrite") rather
    than by the serial number baked into its ALSA sink name.
    """
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=4
        )
        for ln in r.stdout.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2 and match.lower() in parts[1].lower():
                return parts[1]
    except Exception:
        pass
    return ""


if not SCARLETT:
    SCARLETT = detect_sink(MONITOR_MATCH)


def pactl_vol(sink):
    if not sink:
        return 0
    m = re.search(r"(\d+)%", pactl_out("get-sink-volume", sink))
    return int(m.group(1)) if m else 0


def pactl_muted(sink):
    if not sink:
        return False
    return "yes" in pactl_out("get-sink-mute", sink).lower()


def svc_states_batch(units):
    # one systemctl call for all units -- replaces N separate is-active spawns per tick
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "--", *units], capture_output=True, text=True
        )
        lines = r.stdout.strip().splitlines()
        return {u: (lines[i].strip() if i < len(lines) else "unknown") for i, u in enumerate(units)}
    except Exception:
        return {u: "unknown" for u in units}


# Self-healing. A failed unit previously sat red in the grid until someone
# noticed and clicked. Restarting is the same action a human would take, so
# the app takes it -- with three deliberate limits:
#   1. only units this project owns (roaring-*). Restarting pipewire or
#      wireplumber under a live session is a bigger hammer than a GUI should
#      swing unattended, and a failed core service is exactly when a person
#      should be looking.
#   2. one attempt per unit per cooldown, so a unit that fails on start
#      cannot become a restart loop.
#   3. opt-out via the autoheal setting.
HEAL_COOLDOWN_S = 120.0
_HEAL_LAST: dict = {}


def autoheal(states, enabled=True):
    """Restart failed roaring-* units. Returns the list of units restarted."""
    if not enabled:
        return []
    healed = []
    now = time.monotonic()
    for unit, st in states.items():
        if st != "failed" or not unit.startswith("roaring-"):
            continue
        if now - _HEAL_LAST.get(unit, 0.0) < HEAL_COOLDOWN_S:
            continue
        _HEAL_LAST[unit] = now
        logging.getLogger(__name__).warning("autoheal: %s failed, restarting it", unit)
        systemctl_bg("restart", unit)
        healed.append(unit)
    return healed


def pactl_out(*args, timeout=4) -> str:
    """Run pactl with an argv list and return stdout (never raises)."""
    try:
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def systemctl_out(*args, timeout=4) -> str:
    """Run systemctl --user with an argv list and return stdout (never raises)."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout
        )
        return r.stdout
    except Exception:
        return ""


def timer_summary(unit: str) -> str:
    """'NEXT LEFT' summary for a user timer, parsed in Python instead of awk."""
    lines = systemctl_out("list-timers", unit, "--no-pager").splitlines()
    if len(lines) < 2:
        return ""
    cols = lines[1].split()
    if len(cols) < 5:
        return ""
    return " ".join([cols[0], cols[1], cols[4]])


LOOPBACK_SINKS = ("vm_game", "vm_chat", "vm_music")


def loopback_load(source: str, sink: str, latency_msec: int) -> None:
    """Load one module-loopback. Every value is an argv element, never shell text."""
    sh_bg_argv(
        [
            "pactl",
            "load-module",
            "module-loopback",
            f"source={source}",
            f"sink={sink}",
            f"latency_msec={latency_msec}",
            "rate=48000",
            "channels=2",
            "channel_map=front-left,front-right",
            "remix=yes",
            "source_dont_move=true",
            "sink_dont_move=true",
        ]
    )


def loopback_unload(source: str, sinks=LOOPBACK_SINKS) -> int:
    """Unload every module-loopback carrying `source` into one of `sinks`.

    Replaces a `pactl | awk | for | pactl` shell pipeline: the module list is
    parsed in Python and each id is unloaded with its own argv call.
    """
    unloaded = 0
    for ln in pactl_out("list", "short", "modules").splitlines():
        cols = ln.split("\t") if "\t" in ln else ln.split()
        if len(cols) < 3 or cols[1] != "module-loopback":
            continue
        args = cols[2]
        if source not in args:
            continue
        if not any(f"sink={s}" in args for s in sinks):
            continue
        try:
            subprocess.run(
                ["pactl", "unload-module", cols[0]], capture_output=True, text=True, timeout=4
            )
            unloaded += 1
        except Exception:
            pass
    return unloaded


def carla_count():
    # bwrap wrappers carry the project path in argv and inflate the count
    # /app/share/carla/carla is only in the real python3 process
    try:
        r = subprocess.run(
            ["pgrep", "-c", "-f", "/app/share/carla/carla"], capture_output=True, text=True
        )
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0


def pw_mem_mb():
    raw = systemctl_out("show", "pipewire-pulse.service", "-p", "MemoryCurrent").strip()
    try:
        return int(raw.split("=")[1]) / 1048576
    except Exception:
        return 0.0


def pw_version():
    """PipeWire version from `pactl info`, parsed in Python (no grep, no shell)."""
    for ln in pactl_out("info").splitlines():
        if "Server Name" in ln:
            m = re.search(r"(\d+\.\d+\.\d+)", ln)
            if m:
                return m.group(1)
    return ""


def read_conf(path):
    out = {}
    try:
        for ln in Path(path).read_text().splitlines():
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    except Exception:
        pass
    return out


def write_conf_key(path, key, value):
    p = Path(path)
    if not p.exists():
        return
    text = p.read_text()
    nl = f'{key}="{value}"'
    if re.search(rf"^{re.escape(key)}=", text, re.MULTILINE):
        # literal replacement (lambda) so values containing backslashes or
        # regex group refs like \1 / \g<0> are written verbatim, not
        # interpreted as re.sub replacement escapes (fuzz-found bug).
        text = re.sub(rf"^{re.escape(key)}=.*", lambda _m: nl, text, flags=re.MULTILINE)
    else:
        text += f"\n{nl}\n"
    # Atomic replace: a partial write here used to leave the mixer or
    # mic-router config truncated, which the shell daemons then read as
    # "no settings at all".
    tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(text)
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


MOONLIGHT_SVC = Path.home() / ".config/systemd/user/roaring-moonlight-mic.service"
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
        t = re.sub(r"^(Environment=MIC_SOURCE=).*", rf"\g<1>{src}", t, flags=re.MULTILINE)
        MOONLIGHT_SVC.write_text(t)
    except Exception as e:
        log.warning("could not update %s: %s", MOONLIGHT_SVC, e)
    run_steps(
        [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "restart", "roaring-moonlight-mic.service"],
        ]
    )


def scarlett_on():
    return (HOME / ".cache" / "roaring_scarlett_loopbacks").exists()


def astro_target():
    t = read_conf(MXCONF).get("ASTRO_TARGET", "")
    return "game" if "game" in t else "chat"


# Sinks touched by unmute-all / mute-all. Kept in one place so the two
# directions can never drift apart again (mute-all used to skip the mic
# buses, so "unmute everything" could not undo "mute everything").
UNMUTE_SINKS = [
    s
    for s in [ASTRO_CHAT, ASTRO_GAME, "vm_game", "vm_chat", "vm_music", "laptop_audio", SCARLETT]
    if s
]
# Below this level a sink is inaudible, so panic-unmute is allowed to lift it.
UNMUTE_RESCUE_BELOW_PCT = 5


def monitor_sink():
    """The monitor interface sink, re-detected if it wasn't up at startup."""
    global SCARLETT
    if not SCARLETT:
        SCARLETT = detect_sink(MONITOR_MATCH)
        if SCARLETT and SCARLETT not in UNMUTE_SINKS:
            UNMUTE_SINKS.append(SCARLETT)
    return SCARLETT


def unmute_all():
    monitor_sink()  # refresh if PipeWire came up after we did
    for s in UNMUTE_SINKS:
        pactl_bg("set-sink-mute", s, "0")
    # Don't stomp on deliberate volume settings. Only rescue the headset if
    # it is sitting at (near) zero, which is the case this shortcut exists
    # for -- "I hear nothing, fix it".
    for s in (ASTRO_CHAT, ASTRO_GAME):
        try:
            if pactl_vol(s) < UNMUTE_RESCUE_BELOW_PCT:
                pactl_bg("set-sink-volume", s, "100%")
        except Exception:
            pass


def force_default_sink(sink="vm_game"):
    pactl_bg("set-default-sink", sink)


# IPC


# The control socket itself now lives in hearth.ipc, which is the tested
# implementation: framing with a 1 MiB ceiling, a handler that cannot kill
# the listener, a stale-socket cleaner that provably never removes a live
# one, and a deterministic stop(). These wrappers keep the old names and
# return conventions so the UI and CLI call sites are untouched.

IPC_MAX_MSG = ipc.MAX_MSG
_ipc_read_line = ipc.read_line


def _write_pid():
    ipc.write_pid(Path(PIDF))


def _read_pid():
    return ipc.read_pid(Path(PIDF))


def _pid_alive(pid):
    return ipc.pid_alive(pid)


def ipc_send(cmd, timeout=0.5):
    """Send one command; the reply, or ``False`` when nobody is listening.

    ``False`` rather than ``None`` because every existing caller tests this
    for truthiness and one of them prints it.
    """
    reply = ipc.send(cmd, socket_path=Path(SOCK), timeout=timeout)
    return False if reply is None else reply


def ipc_serve(handler):
    """Serve the control socket until the process exits.

    Blocking, because the caller runs it on its own thread. A socket that
    cannot be bound is logged and degrades to "no IPC", never an exception
    on a background thread: losing the control socket must not be fatal to
    a window that is otherwise working.
    """
    server = ipc.Server(handler, socket_path=Path(SOCK))
    try:
        server.bind()
    except OSError as exc:
        log.warning("IPC unavailable: %s", exc)
        return
    server.serve_forever()


class Collector(threading.Thread):
    def __init__(self, q, settings):
        super().__init__(daemon=True)
        self._q = q
        self._s = settings
        self._log = [str(HOME / ".cache" / "lpd8-mixer.log")]
        self._window_visible = threading.Event()
        self._window_visible.clear()

    def set_log(self, path):
        self._log[0] = path

    def set_window_visible(self, visible: bool):
        if visible:
            self._window_visible.set()
        else:
            self._window_visible.clear()

    def run(self):
        while True:
            try:
                self._tick()
            except Exception:
                pass
            base_ms = self._s.get("refresh_ms", 2000)
            if self._window_visible.is_set():
                interval = max(0.25, min(0.5, base_ms / 1000))
            else:
                interval = max(0.5, base_ms / 1000)
            time.sleep(interval)

    def _tick(self):
        d = {}
        d["svc"] = svc_states_batch([u for u, _ in SERVICES])
        d["healed"] = autoheal(d["svc"], self._s.get("autoheal", True))
        _all_sinks = list(VM_SINKS) + [("B1", "mic_b1"), ("B2", "mic_b2")]

        # single pactl list sinks call to get vol + mute + state for everything
        # replaces 15 separate pactl_vol/pactl_muted subprocess spawns per tick
        _pvol: dict = {}
        _pmute: dict = {}
        _psink_st: dict = {}
        _pidx_to_sink: dict = {}
        _cur_snk: dict = {}
        for _ln in pactl_out("list", "sinks").splitlines():
            _s = _ln.strip()
            if _s.startswith("Sink #"):
                if _cur_snk.get("name"):
                    _n = _cur_snk["name"]
                    _pvol[_n] = _cur_snk.get("vol", 0)
                    _pmute[_n] = _cur_snk.get("muted", False)
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
                _vm = re.search(r"(\d+)%", _s)
                if _vm:
                    _cur_snk["vol"] = int(_vm.group(1))
        if _cur_snk.get("name"):
            _n = _cur_snk["name"]
            _pvol[_n] = _cur_snk.get("vol", 0)
            _pmute[_n] = _cur_snk.get("muted", False)
            _psink_st[_n] = _cur_snk.get("state", "--")
            if _cur_snk.get("idx"):
                _pidx_to_sink[_cur_snk["idx"]] = _n

        d["vol"] = {s: _pvol.get(s, 0) for _, s in _all_sinks}
        d["mute"] = {s: _pmute.get(s, False) for _, s in _all_sinks}
        d["astro_chat_mute"] = _pmute.get(ASTRO_CHAT, False)
        d["astro_game_mute"] = _pmute.get(ASTRO_GAME, False)
        d["astro_chat_vol"] = _pvol.get(ASTRO_CHAT, 100)
        d["sink_st"] = _psink_st

        d["carla"] = carla_count()
        d["mem_mb"] = pw_mem_mb()
        d["astro"] = astro_target()
        d["scarlett"] = scarlett_on()

        # No Noise -- reconcile is idempotent, but it is still a bash + pactl
        # spawn, and the tick runs as often as every 0.25s while the window is
        # visible. 5s is well inside human reaction time for a room-correction
        # filter that only changes when the Scarlett mirror is toggled.
        _now = time.time()
        if _now - getattr(self, "_nn_last", 0.0) >= NO_NOISE_RECONCILE_SEC:
            self._nn_last = _now
            _nn = BIN / "no_noise_ctl.sh"
            if _nn.exists():
                try:
                    subprocess.run(["bash", str(_nn), "reconcile"], capture_output=True, timeout=4)
                except Exception:
                    pass
        d["no_noise_active"] = (HOME / ".cache" / "roaring_no_noise_active").exists()
        d["timer"] = timer_summary("roaring-pipewire-restart.timer")
        d["src_st"] = {}
        for _ln in pactl_out("list", "short", "sources").splitlines():
            _p = _ln.split()
            if len(_p) >= 5:
                d["src_st"][_p[1]] = _p[4]

        # sink-inputs: app info grouped by sink (pa_index, name, icon_name)
        # reuses _pidx_to_sink from the full sink parse above
        _si_apps: dict = {}
        _cur_si: dict = {}
        for _ln in pactl_out("list", "sink-inputs").splitlines():
            _s = _ln.strip()
            if _s.startswith("Sink Input #"):
                if _cur_si.get("sink_idx") and _cur_si.get("name"):
                    _sn = _pidx_to_sink.get(_cur_si["sink_idx"])
                    if _sn:
                        _si_apps.setdefault(_sn, []).append(
                            {
                                "index": _cur_si.get("pa_idx", ""),
                                "name": _cur_si["name"],
                                "icon": _cur_si.get("icon", ""),
                                "vol_pct": _cur_si.get("vol_pct", 100),
                                "muted": _cur_si.get("muted", False),
                            }
                        )
                _cur_si = {"pa_idx": _s.split("#", 1)[-1].strip()}
            elif _s.startswith("Sink:") and not _s.startswith("Sink Input"):
                _cur_si["sink_idx"] = _s.split(":", 1)[-1].strip()
            elif _s.startswith("Mute:") and "muted" not in _cur_si:
                _cur_si["muted"] = _s.split(":", 1)[-1].strip().lower() == "yes"
            elif _s.startswith("Volume:") and "vol_pct" not in _cur_si:
                _vm = re.search(r"(\d+)%", _s)
                if _vm:
                    _cur_si["vol_pct"] = int(_vm.group(1))
            elif "application.name" in _s and "name" not in _cur_si:
                _m = re.search(r'application\.name\s*=\s*"([^"]+)"', _s)
                if _m:
                    _cur_si["name"] = _m.group(1)
            elif "application.icon_name" in _s:
                _m = re.search(r'application\.icon_name\s*=\s*"([^"]+)"', _s)
                if _m:
                    _cur_si["icon"] = _m.group(1)
        if _cur_si.get("sink_idx") and _cur_si.get("name"):
            _sn = _pidx_to_sink.get(_cur_si["sink_idx"])
            if _sn:
                _si_apps.setdefault(_sn, []).append(
                    {
                        "index": _cur_si.get("pa_idx", ""),
                        "name": _cur_si["name"],
                        "icon": _cur_si.get("icon", ""),
                        "vol_pct": _cur_si.get("vol_pct", 100),
                        "muted": _cur_si.get("muted", False),
                    }
                )
        d["sink_inputs"] = _si_apps

        mc = read_conf(MCCONF)
        d["b1_route"] = mc.get("B1_ROUTE", "none")
        d["b2_route"] = mc.get("B2_ROUTE", "none")

        # loopback: does mic_b?.monitor route into any output bus?
        _mods = pactl_out("list", "short", "modules")

        def _lb(src, _m=_mods):
            return any(
                "module-loopback" in ln
                and src in ln
                and any(t in ln for t in ("vm_game", "vm_chat", "vm_music"))
                for ln in _m.splitlines()
            )

        d["lb_b1"] = _lb("mic_b1.monitor")
        d["lb_b2"] = _lb("mic_b2.monitor")
        d["pw_ver"] = pw_version()
        d["moonlight_mic_src"] = moonlight_mic_source()
        try:
            lf = self._log[0]
            d["log"] = (
                "".join(open(lf).readlines()[-80:])
                if Path(lf).exists()
                else f"(log not found: {lf})"
            )
        except Exception:
            d["log"] = ""
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except Exception:
                pass
        # vesktop: portal alive + default sink name (venmic uses the default sink for
        # its only_default_speakers filter -- if this shows the Astro ALSA name instead
        # of vm_game, apps routing through vm_game may not appear in venmic's picker)
        _vc_units = [
            "xdg-desktop-portal",
            "xdg-desktop-portal-kde",
            "xdg-desktop-portal-gtk",
            "xdg-desktop-portal-wlr",
            "xdg-desktop-portal-hyprland",
        ]
        _vc_st = svc_states_batch(_vc_units)
        _vc_live = [u for u in _vc_units[1:] if _vc_st.get(u) == "active"]
        d["vc_portal_ok"] = _vc_st.get("xdg-desktop-portal") == "active" and bool(_vc_live)
        d["vc_portal_backend"] = _vc_live[0].rsplit("-", 1)[-1] if _vc_live else ""
        d["vc_default_sink"] = pactl_out("get-default-sink").strip()
        _vc_log = HOME / ".var/app/dev.vencord.Vesktop/.local/state/venmic/venmic.log"
        d["vc_venmic_log_exists"] = _vc_log.exists()
        self._q.put(d)


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
        self._peaks: dict = {}
        self._lock = threading.Lock()
        self._srcs: list = []
        self.available = False
        self._vu_dump = vu_dump
        # Shared with run(): the live parec children, so stop() can reap them.
        self._procs: dict = {}
        self._stop = threading.Event()

    def stop(self, timeout: float = 1.0):
        """Ask the poll loop to exit and kill any surviving parec children.

        This thread is a daemon, so the loop's own cleanup does not reliably
        run at interpreter exit -- that is why every startup had to pkill
        stale parec processes. Call this from the GTK shutdown path instead.
        """
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)
        for src, p in list(self._procs.items()):
            try:
                p.kill()
                p.wait(timeout=0.2)
            except Exception:
                pass
            self._procs.pop(src, None)

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

        RATE = 8000
        CHANNELS = 1
        BYTES_FRAME = CHANNELS * 2
        READ_CHUNK = 4096
        COOLDOWN = 2.0
        procs = self._procs  # shared with stop() so children get reaped
        born_at: dict = {}
        dead_at: dict = {}
        leftover: dict = {}  # alignment buffer between reads
        import time as _t2

        # --vu-dump writes RMS/peak CSV; off by default since v4.3
        # was always-on before -- created unbounded gzip + leftover dumps
        _dgz = None
        _dcsv = None
        if self._vu_dump:
            # Captures are state, not clutter for $HOME. paths.dumps_dir()
            # is what --paths reports, so a bug report can be asked for it.
            _ddir = paths.dumps_dir()
            _ddir.mkdir(parents=True, exist_ok=True)
            _dp = _ddir / f"vu_dump_{int(_t2.time())}.csv.gz"
            _dgz = gzip.open(str(_dp), "wt", newline="", compresslevel=6)
            _dcsv = csv.writer(_dgz)
            _dcsv.writerow(["t_ms", "source", "rms", "peak", "n_samples", "rms_dbfs", "peak_dbfs"])
            _dgz.flush()

        def _log_row(src, rms, peak, n):
            if _dcsv is None:
                return
            try:
                rdb = 20 * _math.log10(rms) if rms > 1e-7 else -96.0
                pdb = 20 * _math.log10(peak) if peak > 1e-7 else -96.0
                _dcsv.writerow(
                    [
                        int(_t2.monotonic() * 1000),
                        src,
                        f"{rms:.5f}",
                        f"{peak:.5f}",
                        n,
                        f"{rdb:.1f}",
                        f"{pdb:.1f}",
                    ]
                )
                _dgz.flush()
            except Exception:
                pass

        def _start(src):
            try:
                return subprocess.Popen(
                    [
                        "parec",
                        f"--device={src}",
                        f"--channels={CHANNELS}",
                        f"--rate={RATE}",
                        "--format=s16le",
                        "--raw",
                        "--latency-msec=33",
                        # plasma-pa filters on these to hide stream from "Input Streams" list
                        "--property=media.category=Monitor",
                        "--property=stream.dont-record=1",
                        "--property=application.id=roaring.vu.monitor",
                        "--property=application.name=Roaring VU (internal)",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
            except Exception:
                return None

        try:
            while not self._stop.is_set():
                now = time.monotonic()
                with self._lock:
                    srcs = list(self._srcs)
                for src in srcs:
                    if src not in procs:
                        if now - dead_at.get(src, 0.0) < COOLDOWN:
                            continue
                        p = _start(src)
                        if p:
                            procs[src] = p
                            born_at[src] = now
                for src in list(procs.keys()):
                    if src not in srcs:
                        p = procs.pop(src, None)
                        if p is not None:
                            try:
                                p.kill()
                                p.wait(timeout=0.2)
                            except Exception:
                                pass
                        born_at.pop(src, None)
                        leftover.pop(src, None)
                        with self._lock:
                            self._peaks.pop(src, None)
                if not self.available:
                    if any(now - born_at.get(s, now) >= 1.0 for s in procs):
                        self.available = True
                for src, p in list(procs.items()):
                    if p.poll() is not None:
                        dead_at[src] = time.monotonic()
                        try:
                            p.wait(timeout=0.1)
                        except Exception:
                            pass
                        procs.pop(src, None)
                        born_at.pop(src, None)
                        leftover.pop(src, None)
                        with self._lock:
                            self._peaks.pop(src, None)
                        continue
                    try:
                        rdy, _, _ = _select.select([p.stdout], [], [], 0)
                        if not rdy:
                            continue
                        raw_new = os.read(p.stdout.fileno(), READ_CHUNK)
                        if not raw_new:
                            dead_at[src] = time.monotonic()
                            try:
                                p.wait(timeout=0.1)
                            except Exception:
                                pass
                            procs.pop(src, None)
                            born_at.pop(src, None)
                            leftover.pop(src, None)
                            with self._lock:
                                self._peaks.pop(src, None)
                            continue
                        raw = leftover.get(src, b"") + raw_new
                        n = len(raw) // BYTES_FRAME
                        leftover[src] = raw[n * BYTES_FRAME :]
                        if n == 0:
                            continue
                        samples = struct.unpack(f"<{n}h", raw[: n * BYTES_FRAME])
                        rms = _math.sqrt(sum(s * s for s in samples) / n) / 32768.0
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
