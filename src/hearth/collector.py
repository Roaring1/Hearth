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
import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from hearth import proc, services
from hearth.audio import pactl
from hearth.audio.route_health import (
    BusRouteHealth,
    RouteState,
    parse_link_inventory,
    plan_route_recovery,
)
from hearth.errors import CommandError

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
class GraphSnapshot:
    """Bounded PipeWire inventory with query provenance and freshness."""

    ports: tuple[str, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()
    status: str = "unknown"  # verified, unknown, truncated
    detail: str = ""
    taken_at: float = 0.0


GRAPH_MAX_PORTS = 512
GRAPH_MAX_EDGES = 1024
CAPTURE_MAX_CLIENTS = 128


def bounded_graph(listing: str, *, taken_at: float | None = None) -> GraphSnapshot:
    """Parse and cap one existing PipeWire listing; never probes itself."""
    inventory = parse_link_inventory(listing)
    ports = sorted(inventory.ports)
    edges = sorted(inventory.edges)
    truncated = len(ports) > GRAPH_MAX_PORTS or len(edges) > GRAPH_MAX_EDGES
    bounded_ports = tuple(ports[:GRAPH_MAX_PORTS])
    retained_port_nodes = {port.rpartition(":")[0] for port in bounded_ports}
    bounded_edges = tuple(
        edge
        for edge in edges[:GRAPH_MAX_EDGES]
        if edge[0].rpartition(":")[0] in retained_port_nodes
        and edge[1].rpartition(":")[0] in retained_port_nodes
    )
    return GraphSnapshot(
        ports=bounded_ports,
        edges=bounded_edges,
        status="truncated" if truncated else "verified",
        detail=(
            f"Graph capped at {GRAPH_MAX_PORTS} ports and {GRAPH_MAX_EDGES} edges"
            if truncated
            else "PipeWire graph inventory verified"
        ),
        taken_at=time.time() if taken_at is None else taken_at,
    )


def graph_unknown(detail: str, *, taken_at: float | None = None) -> GraphSnapshot:
    """Represent a failed graph query distinctly from a verified empty graph."""
    return GraphSnapshot(
        status="unknown",
        detail=detail[:240],
        taken_at=time.time() if taken_at is None else taken_at,
    )


def _graph_from_listing(listing: str, *, taken_at: float | None = None) -> GraphSnapshot:
    if not listing.strip():
        return GraphSnapshot(
            status="verified",
            detail="PipeWire returned a verified empty port/link inventory",
            taken_at=time.time() if taken_at is None else taken_at,
        )
    return bounded_graph(listing, taken_at=taken_at)


def _graph_from_failure(error: Exception, *, taken_at: float | None = None) -> GraphSnapshot:
    return graph_unknown(str(error), taken_at=taken_at)


def parse_capture_clients(listing: str, source_names: dict[int, str]) -> tuple[CaptureClient, ...]:
    """Parse ``pactl list short source-outputs`` into source/client facts."""
    clients: list[CaptureClient] = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        columns = line.split("\t") if "\t" in line else line.split()
        if len(columns) < 4 or not all(column.strip().isdigit() for column in columns[:4]):
            continue
        index, _module, client, source_index = (int(column) for column in columns[:4])
        clients.append(
            CaptureClient(
                index=index,
                client=client,
                source_index=source_index,
                source=source_names.get(source_index, ""),
            )
        )
    return tuple(clients)


def _route_intent_snapshot(*, taken_at: float | None = None) -> RouteIntentSnapshot:
    """Read saved intent only; never uses it as proof of a live PipeWire edge."""
    from hearth import mic_routes, share

    timestamp = time.time() if taken_at is None else taken_at
    try:
        text = mic_routes.CONF_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        mic_status = "unavailable"
        microphone_edges: tuple[tuple[str, str], ...] = ()
    except (OSError, UnicodeError) as exc:
        mic_status = "unknown"
        microphone_edges = ()
        log.warning("collector microphone route config could not be read: %s", exc)
    else:
        values = mic_routes.parse(text)
        required = {"B1_ACTIVE", "B1_ROUTE", "B2_ACTIVE", "B2_ROUTE"}
        if not required.issubset(values):
            mic_status = "unknown"
            microphone_edges = ()
        else:
            mic_status = "verified"
            edges: set[tuple[str, str]] = set()
            for bus, prefix in mic_routes.BUS_PREFIX.items():
                active = values.get(f"{prefix}_ACTIVE", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                if not active:
                    continue
                if mic_routes.carla_enabled(values, bus):
                    edges.add(("Carla", bus))
                for source in ("sm7b_mono", "astro_mic_48k"):
                    if mic_routes.raw_enabled(values, bus, source):
                        edges.add((source, bus))
            microphone_edges = tuple(sorted(edges))

    try:
        vesktop_status, vesktop_targets = share.selection_state()
    except Exception as exc:  # saved selection must never invalidate graph facts
        log.warning("collector Vesktop selection could not be read: %s", exc)
        vesktop_status, vesktop_targets = "unknown", ()
    return RouteIntentSnapshot(
        microphone_edges=microphone_edges,
        microphone_status=mic_status,
        vesktop_targets=tuple(vesktop_targets),
        vesktop_status=vesktop_status,
        detail=(
            "Saved microphone routing intent; not evidence of live links"
            if mic_status == "verified"
            else "Microphone routing intent could not be verified"
        ),
        taken_at=timestamp,
    )


@dataclass(frozen=True, slots=True)
class CaptureClient:
    """One observed application subscription to a PulseAudio source."""

    index: int
    client: int
    source_index: int
    source: str


@dataclass(frozen=True, slots=True)
class CaptureClientSnapshot:
    """Optional microphone-consumer inventory, independent of PipeWire graph."""

    clients: tuple[CaptureClient, ...] = ()
    status: str = "unknown"  # verified, unknown, truncated
    detail: str = ""
    taken_at: float = 0.0


@dataclass(frozen=True, slots=True)
class RouteIntentSnapshot:
    """Saved microphone route intent and Vesktop's separate direct selection."""

    microphone_edges: tuple[tuple[str, str], ...] = ()
    microphone_status: str = "unknown"  # verified, unknown, unavailable
    vesktop_targets: tuple[str, ...] = ()
    vesktop_status: str = "unknown"  # verified, unknown, unavailable
    detail: str = ""
    taken_at: float = 0.0


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
    #: None means the route query failed; an empty dict is a verified empty graph.
    routes: dict[str, set[str]] | None = None
    #: Actual four-link PipeWire health for managed VM output routes.
    #: None means the link inventory could not be verified this tick.
    route_health: dict[str, dict[str, BusRouteHealth]] | None = None
    #: Bounded raw PipeWire port/edge inventory for read-only views.
    graph: GraphSnapshot | None = None
    #: Optional capture clients; failure here must not stale the PipeWire graph.
    capture_clients: CaptureClientSnapshot | None = None
    #: Saved mic routes and Vesktop selection, clearly separate from live links.
    route_intent: RouteIntentSnapshot | None = None

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


MANAGED_VM_SOURCES = frozenset({"vm_game.monitor", "vm_chat.monitor", "vm_music.monitor"})


def build_route_health(
    modules: Sequence[pactl.Module],
    link_listing: str,
    available_sinks: set[str] | frozenset[str],
) -> dict[str, dict[str, BusRouteHealth]]:
    """Verify each active managed VM output route from real PipeWire links.

    Multiple module instances, missing endpoint ports, and unidentifiable
    ownership stay UNKNOWN. The health display is advisory and never repairs
    routes; the separate route guardian owns bounded recovery.
    """
    health: dict[str, dict[str, BusRouteHealth]] = {}
    managed = [
        loop for loop in pactl.select_loopbacks(modules) if loop.source in MANAGED_VM_SOURCES
    ]
    if not link_listing.strip():
        for source, sink in sorted({(loop.source, loop.sink) for loop in managed}):
            bus = source.removesuffix(".monitor")
            if sink not in available_sinks:
                result = BusRouteHealth(
                    bus,
                    RouteState.UNAVAILABLE,
                    0,
                    detail="configured sink is absent",
                )
            else:
                result = BusRouteHealth(
                    bus,
                    RouteState.UNKNOWN,
                    0,
                    detail="PipeWire returned an empty port/link inventory",
                )
            health.setdefault(source, {})[sink] = result
        return health

    inventory = parse_link_inventory(link_listing)
    pairs = sorted({(loop.source, loop.sink) for loop in managed})
    for source, sink in pairs:
        bus = source.removesuffix(".monitor")
        owned = pactl.select_loopbacks(
            modules,
            source=source,
            sinks=[sink],
            exact_source=True,
        )
        if len(owned) > 1:
            result = BusRouteHealth(
                bus,
                RouteState.UNKNOWN,
                0,
                detail=f"{len(owned)} matching loopback modules; ownership is ambiguous",
            )
        else:
            result = plan_route_recovery(
                bus,
                sink,
                inventory,
                sink_present=sink in available_sinks,
                module_exists=bool(owned),
            ).health
        health.setdefault(source, {})[sink] = result
    return health


def collect(units: Sequence[str]) -> Snapshot:
    """Take one snapshot of the audio graph and the service stack.

    Core graph queries are checked: if the audio server did not answer, raise
    rather than emitting an empty snapshot that would make every strip look
    unplugged. Routes are optional; failure to read the module table marks them
    unknown so the UI can keep its last verified route graph.
    """
    if os.name == "nt":
        from hearth import windows_audio

        return windows_audio.collect(units)

    sinks = parse_sinks(pactl.raw_checked("list", "sinks"))
    streams = parse_streams(
        pactl.raw_checked("list", "sink-inputs"),
        index_map(sinks),
    )
    raw_sources = pactl.raw_checked("list", "short", "sources")
    sources = parse_source_states(raw_sources)
    source_names = {int(entry.index): entry.name for entry in pactl.parse_short_list(raw_sources)}
    routes: dict[str, set[str]] | None
    route_health: dict[str, dict[str, BusRouteHealth]] | None
    graph: GraphSnapshot | None
    try:
        # Optional module metadata is bounded so a slow compatibility server
        # does not hold up otherwise valid mixer snapshots.
        modules = pactl.modules(strict=True, timeout=0.75)
    except CommandError as exc:
        log.warning("collector module query failed; retaining last known routes: %s", exc)
        modules = None

    if modules is None:
        routes = None
        route_health = None
    else:
        routes = {}
        for loop in pactl.select_loopbacks(modules):
            routes.setdefault(loop.source, set()).add(loop.sink)

    # Reuse the single graph query used for route health, independently of
    # module-table availability. No duplicate probe is added for the Grid.
    try:
        link_listing = proc.run(["pw-link", "-iol"], timeout=0.75).stdout
    except CommandError as exc:
        log.warning("collector PipeWire link query failed; graph unknown: %s", exc)
        graph = _graph_from_failure(exc)
        route_health = None
    else:
        graph = _graph_from_listing(link_listing)
        route_health = (
            build_route_health(modules, link_listing, set(sinks)) if modules is not None else None
        )

    # This optional inventory describes applications capturing source audio.
    # Its failure is isolated from the graph and from ordinary sink playback.
    try:
        capture_listing = proc.run(
            ["pactl", "list", "short", "source-outputs"], timeout=0.75
        ).stdout
    except CommandError as exc:
        log.warning("collector source-output query failed; mic consumers unknown: %s", exc)
        capture_clients = CaptureClientSnapshot(
            status="unknown", detail=str(exc)[:240], taken_at=time.time()
        )
    else:
        clients = parse_capture_clients(capture_listing, source_names)
        truncated = len(clients) > CAPTURE_MAX_CLIENTS
        capture_clients = CaptureClientSnapshot(
            clients=clients[:CAPTURE_MAX_CLIENTS],
            status="truncated" if truncated else "verified",
            detail=(
                f"Capture-client inventory capped at {CAPTURE_MAX_CLIENTS} entries"
                if truncated
                else "Application microphone consumers enumerated"
            ),
            taken_at=time.time(),
        )

    route_intent = _route_intent_snapshot()

    return Snapshot(
        sinks=sinks,
        streams=streams,
        units=services.states(units),
        sources=sources,
        routes=routes,
        route_health=route_health,
        graph=graph,
        capture_clients=capture_clients,
        route_intent=route_intent,
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
