"""The microphone effects chain (Carla), judged by the graph rather than by systemd.

The SM7B does not go straight to Discord or OBS. It goes
``sm7b_mono -> Carla -> mic_b1``, where Carla runs the gate, EQ, compressor,
de-esser, limiter and the rest of the rack. If those links are missing the
microphone still "works" -- it is just raw, quiet and ungated, which is the
exact failure nobody notices until someone says you sound bad.

Three units are involved and each reports success differently, which is why
unit state alone is useless here:

* ``roaring-carla-session.service`` is long-running; active means Carla is up.
* ``roaring-carla-patch.service`` is a oneshot that makes links and exits, so
  ``inactive (dead)`` is its **success** state -- the same trap that made
  ``roaring-vm-share`` look broken.
* ``roaring-carla-backup.timer`` is a timer; ``waiting`` is healthy.

So this module asks the graph. ``links.py`` already reports what is actually
connected; everything below is interpretation of that.

It also detects a feedback path, because this rack can build one: Carla's
output feeds ``mic_b1``, and anything that routes ``mic_b1``'s monitor back
into a Carla input closes a loop through the whole effects chain. The
``roaring_carla_patch.sh`` header states that invariant in prose; here it is
checked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hearth import services
from hearth.audio import links
from hearth.errors import AudioError

log = logging.getLogger(__name__)

#: The PipeWire node Carla registers itself as.
CARLA_NODE = "Carla"

#: The raw microphone node, straight off the Scarlett.
MIC_NODE = "sm7b_mono"

#: Mic buses, with what they are actually for. Never show "B1" to a person.
BUS_NODES = {"mic_b1": "Stream", "mic_b2": "Discord"}

#: The long-running unit. Active means Carla is up.
SESSION_UNIT = "roaring-carla-session.service"

#: A oneshot that creates the links and exits; inactive is success.
PATCH_UNIT = "roaring-carla-patch.service"

#: An optional watcher that re-creates links after a PipeWire restart.
WATCH_UNIT = "roaring-carla-watchd.service"


@dataclass(frozen=True, slots=True)
class ChainState:
    """What the effects chain is doing right now, read from the graph."""

    present: bool
    """Carla appears in the graph at all."""

    inputs: list[str] = field(default_factory=list)
    """Nodes feeding Carla's inputs."""

    outputs: list[str] = field(default_factory=list)
    """Nodes Carla's output reaches."""

    feedback: list[str] = field(default_factory=list)
    """Nodes on both sides: Carla hears its own output through them."""

    session_active: bool | None = None
    """``None`` when the unit could not be queried -- unknown, not broken."""

    @property
    def mic_connected(self) -> bool:
        """The raw microphone reaches the chain."""
        return MIC_NODE in self.inputs

    @property
    def processed_buses(self) -> list[str]:
        """Mic buses receiving Carla's processed output, in bus order."""
        return [node for node in BUS_NODES if node in self.outputs]

    @property
    def processed_labels(self) -> list[str]:
        return [BUS_NODES[node] for node in self.processed_buses]

    @property
    def working(self) -> bool:
        """Mic in, at least one bus out, and no loop."""
        return self.mic_connected and bool(self.processed_buses) and not self.feedback

    @property
    def summary(self) -> str:
        """One sentence naming the state and, when wrong, the consequence."""
        if not self.present:
            if self.session_active:
                return "Voice effects are starting"
            return "Voice effects are off - your microphone is raw"
        if not self.mic_connected:
            return "Your microphone is not reaching the voice effects"
        if self.feedback:
            through = ", ".join(BUS_NODES.get(n, n) for n in self.feedback)
            return f"Voice effects are feeding back through {through}"
        labels = self.processed_labels
        if not labels:
            return "Voice effects are running but not reaching any mic bus"
        return f"Voice effects on for {' and '.join(labels)}"


def analyze(graph: list[links.Link], *, session_active: bool | None = None) -> ChainState:
    """Interpret a parsed link list as chain state.

    Pure: takes the graph, returns the reading. Everything that touches the
    machine lives in :func:`state`.
    """
    feeders = links.feeders(graph, CARLA_NODE)
    targets = [
        link.target_node
        for link in graph
        if link.source_node == CARLA_NODE and link.target_node != CARLA_NODE
    ]
    # De-duplicate while keeping first-seen order; a stereo pair is two links.
    outputs = list(dict.fromkeys(targets))
    inputs = [node for node in feeders if node != CARLA_NODE]
    present = bool(inputs or outputs)
    return ChainState(
        present=present,
        inputs=inputs,
        outputs=outputs,
        feedback=[node for node in outputs if node in inputs],
        session_active=session_active,
    )


def state() -> ChainState:
    """Read the chain from the live graph.

    A failed probe is reported as "not present" only when the graph could be
    read and Carla genuinely was not in it. If ``pw-link`` itself cannot be
    asked, the session unit's state is the only thing reported, because
    "could not look" must never be rendered as "your effects are off".
    """
    try:
        session_active: bool | None = services.is_active(SESSION_UNIT)
    except Exception:  # pragma: no cover - services degrades internally
        log.warning("chain: could not query %s", SESSION_UNIT)
        session_active = None
    try:
        graph = links.list_links()
    except AudioError as exc:
        log.warning("chain: cannot read the audio graph: %s", exc)
        return ChainState(present=False, session_active=session_active)
    return analyze(graph, session_active=session_active)


def unit_notes() -> dict[str, str]:
    """How to read each Carla unit, so the UI never cries wolf.

    Returned as text rather than booleans because the whole point is that
    ``inactive`` means different things per unit.
    """
    return {
        SESSION_UNIT: "Runs Carla. Should be active.",
        PATCH_UNIT: "Connects the chain once at startup, then exits. Inactive is normal.",
        WATCH_UNIT: "Optional. Re-connects the chain if PipeWire restarts.",
    }
