"""State polling: turn ``pactl`` / ``systemctl`` output into one snapshot.

The parsing is pure functions over text, so it is tested against output
recorded from the real rig. The thread on top of it is deliberately thin:
it polls, builds an immutable :class:`Snapshot`, and hands it to a callback.
Nothing here imports ``gi``.

One behaviour is preserved from the original on purpose: the queue holds
only the newest snapshot. A UI that falls behind should redraw current
state, not replay a backlog.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from hearth import services
from hearth.audio import pactl

log = logging.getLogger(__name__)

_PERCENT_RE = re.compile(r"(\d+)%")
_APP_NAME_RE = re.compile(r'application\.name\s*=\s*"([^"]+)"')
_APP_ICON_RE = re.compile(r'application\.icon_name\s*=\s*"([^"]+)"')

#: Poll interval bounds, seconds. Faster while the window is on screen,
#: slower when it is hidden so a background tray icon costs almost nothing.
VISIBLE_MIN = 0.25
VISIBLE_MAX = 0.5
HIDDEN_MIN = 0.5


@dataclass(frozen=True, slots=True)
class SinkState:
    """Volume, mute and transport state for one sink."""

    name: str
    index: str = ""
    volume: int = 0
    muted: bool = False
    state: str = ""


@dataclass(frozen=True, slots=True)
class StreamState:
    """One application stream (``sink-input``) and where it is playing."""

    index: str
    name: str
    sink: str = ""
    icon: str = ""
    volume: int = 100
    muted: bool = False


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the UI needs for one repaint."""

    sinks: dict[str, SinkState] = field(default_factory=dict)
    streams: dict[str, list[StreamState]] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    server_version: str = ""
    default_sink: str = ""
    taken_at: float = 0.0

    def volume(self, sink: str, default: int = 0) -> int:
        entry = self.sinks.get(sink)
        return entry.volume if entry else default

    def muted(self, sink: str, default: bool = False) -> bool:
        entry = self.sinks.get(sink)
        return entry.muted if entry else default

    @property
    def all_units_active(self) -> bool:
        return bool(self.units) and all(state == "active" for state in self.units.values())

    @property
    def failed_units(self) -> list[str]:
        return sorted(u for u, s in self.units.items() if s not in ("active", "activating"))


def _percent(line: str) -> int | None:
    match = _PERCENT_RE.search(line)
    return int(match.group(1)) if match else None


def _as_int(value: object, default: int) -> int:
    """Coerce a parsed field to int, falling back rather than raising.

    The parser stores fields as ``object`` because a pactl block is untyped
    text. A malformed volume line must not take down the polling thread, so a
    bad value yields the default instead of a ValueError.
    """
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def parse_sinks(text: str) -> dict[str, SinkState]:
    """Parse verbose ``pactl list sinks`` output.

    One call replaces the ~15 ``pactl get-sink-volume`` / ``get-sink-mute``
    spawns the app used to do on every tick.
    """
    sinks: dict[str, SinkState] = {}
    current: dict[str, object] = {}

    def flush() -> None:
        name = current.get("name")
        if not name:
            return
        sinks[str(name)] = SinkState(
            name=str(name),
            index=str(current.get("index", "")),
            volume=_as_int(current.get("volume"), 0),
            muted=bool(current.get("muted", False)),
            state=str(current.get("state", "")),
        )

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Sink #"):
            flush()
            current = {"index": line.split("#", 1)[-1].strip()}
        elif line.startswith("Name:"):
            current["name"] = line.split(":", 1)[-1].strip()
        elif line.startswith("State:"):
            current["state"] = line.split(":", 1)[-1].strip()
        elif line.startswith("Mute:"):
            current["muted"] = line.split(":", 1)[-1].strip().lower() == "yes"
        elif line.startswith("Volume:") and "volume" not in current:
            # The first Volume: line is the sink volume; later ones are base
            # volume and monitor volume, which must not overwrite it.
            value = _percent(line)
            if value is not None:
                current["volume"] = value
    flush()
    return sinks


def index_map(sinks: dict[str, SinkState]) -> dict[str, str]:
    """PulseAudio sink index -> sink name, for resolving stream targets."""
    return {s.index: name for name, s in sinks.items() if s.index}


def parse_streams(text: str, sink_by_index: dict[str, str]) -> dict[str, list[StreamState]]:
    """Parse ``pactl list sink-inputs`` into streams grouped by sink name."""
    grouped: dict[str, list[StreamState]] = {}
    current: dict[str, object] = {}

    def flush() -> None:
        if not current.get("index"):
            return
        sink = sink_by_index.get(str(current.get("sink_index", "")), "")
        name = str(current.get("name") or "").strip()
        if not sink or not name:
            return
        grouped.setdefault(sink, []).append(
            StreamState(
                index=str(current["index"]),
                name=name,
                sink=sink,
                icon=str(current.get("icon", "")),
                volume=_as_int(current.get("volume"), 100),
                muted=bool(current.get("muted", False)),
            )
        )

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Sink Input #"):
            flush()
            current = {"index": line.split("#", 1)[-1].strip()}
        elif line.startswith("Sink:") and not line.startswith("Sink Input"):
            current["sink_index"] = line.split(":", 1)[-1].strip()
        elif line.startswith("Mute:") and "muted" not in current:
            current["muted"] = line.split(":", 1)[-1].strip().lower() == "yes"
        elif line.startswith("Volume:") and "volume" not in current:
            value = _percent(line)
            if value is not None:
                current["volume"] = value
        elif "application.name" in line and "name" not in current:
            match = _APP_NAME_RE.search(line)
            if match:
                current["name"] = match.group(1)
        elif "application.icon_name" in line and "icon" not in current:
            match = _APP_ICON_RE.search(line)
            if match:
                current["icon"] = match.group(1)
    flush()
    return grouped


def parse_source_states(text: str) -> dict[str, str]:
    """Source name -> transport state from ``pactl list short sources``."""
    return {entry.name: entry.state for entry in pactl.parse_short_list(text)}


def collect(units: Sequence[str]) -> Snapshot:
    """Take one snapshot of the audio graph and the service stack."""
    sinks = parse_sinks(pactl.raw("list", "sinks"))
    streams = parse_streams(pactl.raw("list", "sink-inputs"), index_map(sinks))
    return Snapshot(
        sinks=sinks,
        streams=streams,
        units=services.states(units),
        sources=parse_source_states(pactl._text("list", "short", "sources")),
        server_version=pactl.server_version(),
        default_sink=pactl.default_sink(),
        taken_at=time.time(),
    )


class Collector(threading.Thread):
    """Background poller that pushes snapshots to a callback.

    The callback runs on this thread, so a GTK consumer must marshal to the
    main loop itself (``GLib.idle_add``). Keeping that explicit is what lets
    this module stay free of ``gi``.
    """

    def __init__(
        self,
        units: Sequence[str],
        on_snapshot: Callable[[Snapshot], None],
        *,
        interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True, name="hearth-collector")
        self._units = list(units)
        self._on_snapshot = on_snapshot
        self._interval = interval
        self._visible = threading.Event()
        # Not ``_stop``: ``threading.Thread._stop`` is a real method, and
        # shadowing it with an Event makes ``join()`` raise TypeError on
        # CPython 3.11/3.12. CI caught this; 3.13+ happens not to call it.
        self._stopping = threading.Event()
        self._wake = threading.Event()

    def set_window_visible(self, visible: bool) -> None:
        self._visible.set() if visible else self._visible.clear()
        if visible:
            self._wake.set()  # repaint immediately on show

    def refresh_now(self) -> None:
        """Ask for a snapshot without waiting out the current interval."""
        self._wake.set()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def _delay(self) -> float:
        if self._visible.is_set():
            return max(VISIBLE_MIN, min(VISIBLE_MAX, self._interval))
        return max(HIDDEN_MIN, self._interval)

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._on_snapshot(collect(self._units))
            except Exception as exc:
                # A poll failure must never kill the thread: the next tick may
                # well succeed, and a dead collector freezes the whole UI.
                log.warning("collector tick failed: %s", exc)
            self._wake.wait(timeout=self._delay())
            self._wake.clear()
