"""The microphone suite, read by the app instead of by a person in a terminal.

The rig already records the SM7B chain twice -- RAW straight off the
interface and POST after the Carla effects chain -- and ``mic_analyze.py``
turns each session into a ``summary.json`` with noise floor, peak, clipping
runs, hum findings and a list of flagged issues. All of that is currently
invisible unless you open a file manager and read JSON.

This module does three things and nothing else:

* finds sessions under ``~/audio_diagnostics`` newest-first,
* parses one session's ``summary.json`` into a small typed record,
* turns that record into a sentence a person can act on.

Two deliberate choices:

* **The analyzer is never run from here.** Analysis takes minutes and pegs a
  core; it belongs on an explicit button in ``tools.py``, not in a read path
  that a refresh tick might call.
* **"No recordings yet" is a designed state, not an error.** This rig has
  never produced a session, so the empty case is the one a first-time user
  will actually see, and it has to explain what to do rather than show an
  empty box.

Every field is optional because a truncated or in-progress session is
normal: the daemon writes ``summary.json`` only after a segment finishes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from hearth import paths

log = logging.getLogger(__name__)

#: Where mic_capture.sh writes its session directories.
SESSION_ROOT_NAME = "audio_diagnostics"
SESSION_PREFIX = "session_"

#: The unit that runs the background recorder.
DAEMON_UNIT = "roaring-mic-daemon.service"

#: Routing config shared with the ~/bin daemons. The Hearth copy wins; the
#: loose legacy path stays as a fallback because the move left a symlink.
ROUTER_CONF = "roaring_mic_router.conf"

#: What the two mic buses are actually for. The UI must never show "B1".
BUS_LABELS = {"B1": "Stream", "B2": "Discord"}

#: Route values the shell daemons accept, with human names.
ROUTE_LABELS = {
    "none": "Off",
    "sm7b": "SM7B only",
    "astro": "Headset mic only",
    "both": "Both mics",
}

#: An SM7B + Cloudlifter chain in a quiet room should sit well below this.
QUIET_FLOOR_DBFS = -55.0


def session_root() -> Path:
    return paths.home() / SESSION_ROOT_NAME


def sessions() -> list[Path]:
    """Session directories, newest first.

    Sorted by name rather than mtime: the directory name is a UTC timestamp
    (``session_20260919T061704Z``), so it sorts correctly even after a copy
    or restore has rewritten every mtime.
    """
    root = session_root()
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(SESSION_PREFIX)]
    return sorted(found, key=lambda p: p.name, reverse=True)


def latest_session() -> Path | None:
    found = sessions()
    return found[0] if found else None


@dataclass(frozen=True, slots=True)
class Flag:
    """One issue the analyzer raised."""

    severity: str
    key: str
    message: str

    @property
    def serious(self) -> bool:
        return self.severity == "high"


@dataclass(frozen=True, slots=True)
class Channel:
    """Measurements for one side of the chain (RAW or POST)."""

    noise_floor_dbfs: float | None = None
    peak_dbfs: float | None = None
    voice_mean_rms_dbfs: float | None = None
    clip_event_count: int = 0
    hum_count: int = 0


@dataclass(frozen=True, slots=True)
class Analysis:
    """One analyzed recording session."""

    path: Path
    generated_utc: str = ""
    duration_s: float | None = None
    raw: Channel = field(default_factory=Channel)
    post: Channel = field(default_factory=Channel)
    gain_applied_db: float | None = None
    flags: list[Flag] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name.removeprefix(SESSION_PREFIX)

    @property
    def serious_flags(self) -> list[Flag]:
        return [f for f in self.flags if f.serious]

    @property
    def dashboard(self) -> Path | None:
        """The rendered dashboard image, if it was generated.

        The background daemon skips plotting to save CPU, so its absence is
        normal and means "render it on demand", not "analysis failed".
        """
        candidate = self.path / "analysis" / "dashboard.png"
        return candidate if candidate.is_file() else None

    @property
    def report(self) -> Path | None:
        candidate = self.path / "analysis" / "report.md"
        return candidate if candidate.is_file() else None

    @property
    def headline(self) -> str:
        """What this recording says about the microphone, in one sentence."""
        serious = self.serious_flags
        if serious:
            return serious[0].message.split(".")[0].strip() or "The recording found a problem"
        if self.raw.clip_event_count:
            return "Your microphone is clipping"
        floor = self.raw.noise_floor_dbfs
        if floor is not None and floor > QUIET_FLOOR_DBFS:
            return f"Background noise is high ({floor:.0f} dBFS)"
        if self.flags:
            return (
                f"{len(self.flags)} minor thing to check"
                if len(self.flags) == 1
                else (f"{len(self.flags)} minor things to check")
            )
        return "Your microphone sounded clean"


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _channel(blob: object) -> Channel:
    if not isinstance(blob, dict):
        return Channel()
    hum = blob.get("hum_findings")
    clips = blob.get("clip_event_count")
    return Channel(
        noise_floor_dbfs=_as_float(blob.get("noise_floor_dbfs")),
        peak_dbfs=_as_float(blob.get("peak_dbfs")),
        voice_mean_rms_dbfs=_as_float(blob.get("voice_mean_rms_dbfs")),
        clip_event_count=clips if isinstance(clips, int) else 0,
        hum_count=len(hum) if isinstance(hum, list) else 0,
    )


def _flags(blob: object) -> list[Flag]:
    if not isinstance(blob, list):
        return []
    out: list[Flag] = []
    for item in blob:
        if not isinstance(item, dict):
            continue
        out.append(
            Flag(
                severity=str(item.get("severity", "low")),
                key=str(item.get("key", "")),
                message=str(item.get("message", "")),
            )
        )
    # High severity first, original order preserved within a severity.
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda f: order.get(f.severity, 3))


def parse_summary(path: Path, text: str) -> Analysis:
    """Parse a ``summary.json`` body into an :class:`Analysis`.

    Malformed or partial JSON yields an empty analysis rather than raising:
    the daemon writes this file while recording continues, so reading it
    mid-write is expected rather than exceptional.
    """
    try:
        blob = json.loads(text)
    except (ValueError, TypeError):
        log.warning("mic: unreadable summary in %s", path)
        return Analysis(path=path)
    if not isinstance(blob, dict):
        return Analysis(path=path)
    chain = blob.get("chain_effect")
    return Analysis(
        path=path,
        generated_utc=str(blob.get("generated_utc", "")),
        duration_s=_as_float(blob.get("duration_s")),
        raw=_channel(blob.get("raw")),
        post=_channel(blob.get("post")),
        gain_applied_db=(
            _as_float(chain.get("gain_applied_db")) if isinstance(chain, dict) else None
        ),
        flags=_flags(blob.get("flags")),
    )


def load_analysis(session: Path) -> Analysis | None:
    """Read one session's analysis, or ``None`` if it has not been analyzed."""
    summary = session / "analysis" / "summary.json"
    if not summary.is_file():
        return None
    try:
        text = summary.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("mic: cannot read %s: %s", summary, exc)
        return None
    return parse_summary(session, text)


def latest_analysis() -> Analysis | None:
    """The newest session that has actually been analyzed.

    Walks backwards rather than stopping at the newest directory, because
    the in-flight segment has no summary yet and stopping there would show
    "nothing recorded" to someone who has hours of recordings.
    """
    for session in sessions():
        analysis = load_analysis(session)
        if analysis is not None:
            return analysis
    return None


def parse_routes(text: str) -> dict[str, str]:
    """Read ``B1_ROUTE`` / ``B2_ROUTE`` out of the shell config.

    The file is sourced by bash, so values may be quoted and the file may
    contain comments and unrelated keys. Only the two routes are returned.
    """
    routes: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in ("B1_ROUTE", "B2_ROUTE"):
            continue
        routes[key[:2]] = value.strip().strip('"').strip("'")
    return routes


def router_conf() -> Path:
    """The routing config, preferring the relocated copy."""
    managed = paths.config_dir() / ROUTER_CONF
    if managed.exists():
        return managed
    return paths.home() / ".config" / ROUTER_CONF


def routes() -> dict[str, str]:
    """Current mic routing as ``{"B1": "both", "B2": "astro"}``."""
    conf = router_conf()
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return parse_routes(text)


def describe_routes(current: dict[str, str] | None = None) -> list[str]:
    """Routing as sentences: ``["Stream: Both mics", "Discord: Headset mic only"]``."""
    values = routes() if current is None else current
    lines: list[str] = []
    for bus, label in BUS_LABELS.items():
        value = values.get(bus)
        if value is None:
            continue
        lines.append(f"{label}: {ROUTE_LABELS.get(value, value)}")
    return lines


@dataclass(frozen=True, slots=True)
class MicView:
    """Everything a mic panel needs, with the empty case spelled out."""

    recording: bool
    analysis: Analysis | None
    session_count: int
    routes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.analysis is not None:
            return self.analysis.headline
        if self.recording:
            return "Recording now - the first results appear when this segment ends"
        if self.session_count:
            return "Recorded, but not analysed yet"
        return "No microphone recordings yet - start the recorder to check your mic"


def view(*, recording: bool | None = None) -> MicView:
    """Assemble the mic panel state.

    :param recording: pass the daemon's state if it is already known, to
        avoid a second ``systemctl`` call inside a refresh tick.
    """
    if recording is None:
        from hearth import services

        recording = services.is_active(DAEMON_UNIT)
    found = sessions()
    return MicView(
        recording=recording,
        analysis=latest_analysis(),
        session_count=len(found),
        routes=describe_routes(),
    )
