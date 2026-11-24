"""Lazy, read-only GTK patchbay view over collected Hearth snapshots."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from hearth.routing_grid import (  # noqa: E402
    Evidence,
    Group,
    GridModel,
    build_model,
)


GROUP_LABELS = {
    Group.INPUTS: "INPUTS",
    Group.PROCESSING: "PROCESSING",
    Group.BUSES: "BUSES",
    Group.DESTINATIONS: "DESTINATIONS",
}
_GROUP_ORDER = tuple(Group)
_COLUMN_X = {group: 16 + i * 248 for i, group in enumerate(_GROUP_ORDER)}
_COLUMN_WIDTH = 224
_CARD_HEIGHT = 72
_ROW_GAP = 22
_TOP = 42
_CANVAS_WIDTH = 1008
_MIN_CANVAS_HEIGHT = 520
_EVIDENCE_STROKES = {
    Evidence.LIVE: "#8b9b8a",
    Evidence.STALE: "#9a927e",
    Evidence.CONFIGURED: "#8b9aaf",
    Evidence.CAPTURE: "#9b91ad",
    Evidence.UNAVAILABLE: "#a18b83",
    Evidence.DEGRADED: "#b29a78",
    Evidence.UNKNOWN: "#80858d",
}


class RoutingGridWindow(Gtk.Window):
    """A GTK-native four-column patchbay. It has no timer or query APIs."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Hearth Routing Grid")
        self.set_transient_for(parent)
        self.set_hide_on_close(True)
        self.set_default_size(1040, 720)
        self.set_size_request(760, 520)
        self.add_css_class("hearth")
        self._model = GridModel()
        self._fingerprint: tuple[object, ...] | None = None
        self._render_count = 0
        self._positions: dict[str, tuple[float, float, Group]] = {}
        self._node_widgets: list[Gtk.Widget] = []
        self._edges = self._model.edges
        self._latest_snapshot = None
        self._opened = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="ROUTING GRID", xalign=0.0)
        title.add_css_class("setup-verdict")
        title.set_hexpand(True)
        header.append(title)
        self.status_label = Gtk.Label(label="UNKNOWN", xalign=1.0)
        self.status_label.add_css_class("setup-tag")
        header.append(self.status_label)
        root.append(header)

        self.detail_label = Gtk.Label(label="Waiting for the collector's next snapshot", xalign=0.0)
        self.detail_label.add_css_class("setup-note")
        self.detail_label.set_wrap(True)
        root.append(self.detail_label)

        self.intent_label = Gtk.Label(label="Microphone routing intent: unknown", xalign=0.0)
        self.intent_label.add_css_class("setup-note")
        self.intent_label.set_wrap(True)
        root.append(self.intent_label)

        self.vesktop_label = Gtk.Label(label="Vesktop direct selection: unknown", xalign=0.0)
        self.vesktop_label.add_css_class("setup-note")
        self.vesktop_label.set_wrap(True)
        root.append(self.vesktop_label)

        self.consumer_label = Gtk.Label(label="Microphone consumers: not enumerated", xalign=0.0)
        self.consumer_label.add_css_class("setup-note")
        self.consumer_label.set_wrap(True)
        root.append(self.consumer_label)

        legend = Gtk.Label(
            label=(
                "Solid = live link   Dashed = configured intent   Dotted = observed capture   "
                "Faded = stale or unavailable"
            ),
            xalign=0.0,
        )
        legend.add_css_class("setup-note")
        legend.set_wrap(True)
        root.append(legend)

        scroller = Gtk.ScrolledWindow()
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        root.append(scroller)

        self.canvas = Gtk.Fixed()
        self.canvas.set_size_request(_CANVAS_WIDTH, _MIN_CANVAS_HEIGHT)
        scroller.set_child(self.canvas)

        self.edge_layer = Gtk.DrawingArea()
        self.edge_layer.set_content_width(_CANVAS_WIDTH)
        self.edge_layer.set_content_height(_MIN_CANVAS_HEIGHT)
        self.edge_layer.set_draw_func(self._draw_edges)
        self.canvas.put(self.edge_layer, 0.0, 0.0)

        self.column_cards: dict[Group, Gtk.Box] = {}
        self._build_columns()

        self.health_label = Gtk.Label(label="", xalign=0.0)
        self.health_label.add_css_class("setup-note")
        self.health_label.set_wrap(True)
        root.append(self.health_label)

        self.ownership_label = Gtk.Label(
            label="MIDI ownership: LPD8 - Hearth. Launchpad Mini - Padfire. "
            "The Routing Grid does not open or control MIDI devices.",
            xalign=0.0,
        )
        self.ownership_label.add_css_class("setup-note")
        self.ownership_label.set_wrap(True)
        root.append(self.ownership_label)

        self.connect("close-request", self._on_close)

    def _build_columns(self) -> None:
        for group in _GROUP_ORDER:
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            frame.add_css_class("setup-box")
            frame.set_size_request(_COLUMN_WIDTH, -1)
            heading = Gtk.Label(label=GROUP_LABELS[group], xalign=0.0)
            heading.add_css_class("setup-tag")
            frame.append(heading)
            self.column_cards[group] = frame
            self.canvas.put(frame, _COLUMN_X[group], 8.0)

    def _on_close(self, *_args) -> bool:
        self._opened = False
        return False

    def present_with_snapshot(self, snapshot) -> None:
        """Explicit open path: show first, then render the cached snapshot."""
        self._opened = True
        self.present()
        self.apply_snapshot(snapshot, now=time.time())

    def apply_snapshot(self, snapshot, *, now: float | None = None) -> None:
        """Consume collector data only; app calls this only while mapped."""
        if not self._opened and not self.get_mapped():
            # Initial open is allowed to set _opened via present_with_snapshot.
            return
        self._latest_snapshot = snapshot
        if snapshot is None:
            graph = None
            capture_clients = None
            route_intent = None
            route_health = None
            sinks = None
            sources = None
            sink_inputs = None
        else:
            graph = getattr(snapshot, "graph", None)
            capture_clients = getattr(snapshot, "capture_clients", None)
            route_intent = getattr(snapshot, "route_intent", None)
            route_health = getattr(snapshot, "route_health", None)
            sinks = getattr(snapshot, "sinks", None)
            sources = getattr(snapshot, "sources", None)
            sink_inputs = getattr(snapshot, "sink_inputs", None)
        model = build_model(
            graph,
            capture_clients=capture_clients,
            route_intent=route_intent,
            route_health=route_health,
            sinks=sinks,
            sources=sources,
            sink_inputs=sink_inputs,
            now=now if now is not None else time.time(),
        )
        fingerprint = model.fingerprint
        if fingerprint == self._fingerprint:
            self._model = model
            return
        self._model = model
        self._fingerprint = fingerprint
        self._render_count += 1
        self._render_model()

    def apply_graph(self, graph, *, now: float | None = None) -> None:
        """Compatibility helper for synthetic visual tests."""
        from types import SimpleNamespace

        self.apply_snapshot(
            SimpleNamespace(
                graph=graph,
                capture_clients=None,
                route_intent=None,
                route_health=None,
                sinks={},
                sources={},
                sink_inputs={},
            ),
            now=now if now is not None else time.time(),
        )

    def _render_model(self) -> None:
        model = self._model
        self.status_label.set_text(model.status.upper())
        self.detail_label.set_text(model.detail or "No graph detail")

        if model.microphone_intent_status == "verified":
            intent_text = "Microphone route config: saved intent shown separately from live links"
        elif model.microphone_intent_status == "unavailable":
            intent_text = "Microphone route config: unavailable"
        else:
            intent_text = "Microphone route config: unknown"
        self.intent_label.set_text(intent_text)

        if model.vesktop_status == "verified":
            labels = {
                "game": "Game",
                "music": "Music",
                "chat": "Chat",
                "share": "Screen Share",
            }
            targets = ", ".join(labels.get(t.lower(), t) for t in model.vesktop_targets)
            self.vesktop_label.set_text(
                f"Vesktop direct selection: {targets or 'nothing selected'}. "
                "This is separate from the Screen Share master path."
            )
        elif model.vesktop_status == "unavailable":
            self.vesktop_label.set_text("Vesktop direct selection: unavailable")
        else:
            self.vesktop_label.set_text("Vesktop direct selection: unknown")

        if model.consumer_status == "verified" and model.consumer_count == 0:
            self.consumer_label.set_text(
                "Microphone consumers: no application capture clients observed in this source-output snapshot"
            )
        elif model.consumer_status == "verified":
            self.consumer_label.set_text(
                f"Microphone consumers: {model.consumer_count} observed "
                "(client IDs shown; application names were not queried)"
            )
        elif model.consumer_status == "truncated":
            self.consumer_label.set_text(
                f"Microphone consumers: incomplete. {model.consumer_detail}"
            )
        else:
            self.consumer_label.set_text(
                f"Microphone consumers: unknown, not evidence of no consumers. {model.consumer_detail}"
            )

        self.health_label.set_text(
            "Route health: "
            + (
                "  |  ".join(
                    f"{note.bus} to {note.sink}: {note.state} "
                    f"({note.verified_links}/{note.expected_links})"
                    for note in model.route_health
                )
                if model.route_health
                else "not verified"
            )
        )

        for widget in self._node_widgets:
            self.canvas.remove(widget)
        self._node_widgets.clear()

        by_group: dict[Group, list] = {group: [] for group in _GROUP_ORDER}
        for node in model.nodes:
            by_group[node.group].append(node)

        counts = {group: len(items) for group, items in by_group.items()}
        max_rows = max(counts.values(), default=0)
        canvas_height = max(_MIN_CANVAS_HEIGHT, _TOP + max_rows * (_CARD_HEIGHT + _ROW_GAP) + 24)
        self.canvas.set_size_request(_CANVAS_WIDTH, canvas_height)
        self.edge_layer.set_content_width(_CANVAS_WIDTH)
        self.edge_layer.set_content_height(canvas_height)
        self.edge_layer.set_size_request(_CANVAS_WIDTH, canvas_height)
        self._positions = {}

        for group in _GROUP_ORDER:
            for row_index, node in enumerate(by_group[group]):
                y = float(_TOP + row_index * (_CARD_HEIGHT + _ROW_GAP))
                x = float(_COLUMN_X[group])
                self._positions[node.id] = (x, y, group)
                card = self._node_card(node)
                self.canvas.put(card, x, y)
                self._node_widgets.append(card)
        self._edges = model.edges
        self.edge_layer.queue_draw()

    @staticmethod
    def _node_card(node) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.add_css_class("setup-box")
        card.set_size_request(_COLUMN_WIDTH, _CARD_HEIGHT)
        card.set_tooltip_text(node.detail or node.label)
        title = Gtk.Label(label=node.label, xalign=0.0)
        title.add_css_class("setup-tag")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        status = Gtk.Label(label=node.evidence.value.upper(), xalign=0.0)
        status.add_css_class("setup-note")
        ports = Gtk.Label(
            label=", ".join(node.ports[:2])
            + (f" +{len(node.ports) - 2}" if len(node.ports) > 2 else ""),
            xalign=0.0,
        )
        ports.add_css_class("setup-note")
        ports.set_ellipsize(Pango.EllipsizeMode.END)
        card.append(title)
        card.append(status)
        card.append(ports)
        return card

    def _draw_edges(self, _area, cr, width: int, height: int) -> None:
        """Draw simple directed connections behind the GTK node cards."""
        for edge in self._edges:
            source = self._positions.get(edge.source)
            target = self._positions.get(edge.target)
            if source is None or target is None:
                continue
            sx = source[0] + _COLUMN_WIDTH
            sy = source[1] + _CARD_HEIGHT / 2
            tx = target[0]
            ty = target[1] + _CARD_HEIGHT / 2
            colour = _EVIDENCE_STROKES.get(edge.evidence, "#80858d")
            red, green, blue = (int(colour[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
            cr.set_source_rgba(red, green, blue, 0.9)
            cr.set_line_width(2.0)
            if edge.evidence is Evidence.CONFIGURED:
                cr.set_dash([7.0, 5.0])
            elif edge.evidence in {Evidence.CAPTURE, Evidence.UNKNOWN}:
                cr.set_dash([2.0, 4.0])
            elif edge.evidence in {Evidence.DEGRADED, Evidence.UNAVAILABLE, Evidence.STALE}:
                cr.set_dash([4.0, 4.0])
            else:
                cr.set_dash([])
            if tx >= sx:
                bend = max(24.0, (tx - sx) * 0.5)
                cr.move_to(sx, sy)
                cr.curve_to(sx + bend, sy, tx - bend, ty, tx, ty)
            else:
                mid_x = _COLUMN_X[Group.PROCESSING] + _COLUMN_WIDTH + 12.0
                cr.move_to(sx, sy)
                cr.curve_to(mid_x, sy, mid_x, ty, tx, ty)
            cr.stroke()
            cr.set_dash([])
            # Arrow head points into the destination node.
            cr.move_to(tx - 8.0, ty - 4.0)
            cr.line_to(tx, ty)
            cr.line_to(tx - 8.0, ty + 4.0)
            cr.stroke()
