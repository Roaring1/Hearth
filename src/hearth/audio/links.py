"""Reading the graph: which node is actually wired to which.

Systemd can only say whether a script exited cleanly. It cannot say whether
the links that script was supposed to create still exist -- and on this rig
that difference is the whole game. ``roaring-vm-share`` is a ``Type=oneshot``
that makes direct ``pw-link`` connections and exits, so its unit state reads
"inactive (dead)" while it is working perfectly. Reporting that unit as a
fault is how an app invents an alarm about a healthy machine.

So health is measured from the graph itself. ``pw-link -l`` prints a port
per unindented line, followed by indented peers::

    vm_game:monitor_FL
      |-> vm_share:playback_FL
      |-> Roaring VU (internal):input_FL

``|->`` means the listed port feeds the peer; ``|<-`` means the peer feeds
it. Both are normalised here into a single signal-flow direction, so callers
never have to care which side of the graph they happened to read from.

Node names are not identifiers: "Roaring VU (internal):input_FL" contains
spaces and parentheses. Ports are split on the *last* colon for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from hearth import proc
from hearth.errors import AudioError

#: Arrow prefixes used by ``pw-link -l`` for outgoing and incoming peers.
OUT_ARROW = "|->"
IN_ARROW = "|<-"


def node_of(port: str) -> str:
    """The node half of ``node:port``, tolerating colons and spaces in names."""
    head, sep, _tail = port.rpartition(":")
    return head if sep else port


@dataclass(frozen=True, slots=True)
class Link:
    """One connection, always stored in signal-flow order."""

    source: str
    target: str

    @property
    def source_node(self) -> str:
        return node_of(self.source)

    @property
    def target_node(self) -> str:
        return node_of(self.target)


def parse(text: str) -> list[Link]:
    """Parse ``pw-link -l`` output into de-duplicated links.

    The same connection appears twice in the listing -- once under the
    producer and once under the consumer -- so identical pairs are collapsed
    while original order is preserved.
    """
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()
    current = ""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        stripped = raw.strip()
        if not raw[:1].isspace():
            current = stripped
            continue
        if not current:
            continue
        if stripped.startswith(OUT_ARROW):
            pair = (current, stripped[len(OUT_ARROW) :].strip())
        elif stripped.startswith(IN_ARROW):
            pair = (stripped[len(IN_ARROW) :].strip(), current)
        else:
            continue
        if pair in seen:
            continue
        seen.add(pair)
        links.append(Link(*pair))
    return links


def between(links: list[Link], source_node: str, target_node: str) -> list[Link]:
    """Every link from *source_node* into *target_node*."""
    return [
        link
        for link in links
        if link.source_node == source_node and link.target_node == target_node
    ]


def is_connected(links: list[Link], source_node: str, target_node: str) -> bool:
    """True when at least one channel is wired between the two nodes.

    One channel rather than two on purpose: a half-connected pair is a real
    state worth reporting separately, not a reason to claim nothing is wired.
    """
    return bool(between(links, source_node, target_node))


def channel_count(links: list[Link], source_node: str, target_node: str) -> int:
    """How many channels are wired between the two nodes (2 is a full pair)."""
    return len(between(links, source_node, target_node))


def feeders(links: list[Link], target_node: str) -> list[str]:
    """Distinct nodes feeding *target_node*, in first-seen order."""
    found: list[str] = []
    for link in links:
        if link.target_node == target_node and link.source_node not in found:
            found.append(link.source_node)
    return found


def available() -> bool:
    """Whether ``pw-link`` exists on this machine."""
    return proc.have("pw-link")


def list_links(*, timeout: float = 5.0) -> list[Link]:
    """Read the live graph.

    Raises :class:`AudioError` rather than returning an empty list on
    failure: "no links" and "could not ask" mean opposite things, and an
    empty list would read as a catastrophic outage. A stale environment
    without ``XDG_RUNTIME_DIR`` produces exactly that kind of false zero.
    """
    if not available():
        raise AudioError("pw-link is not installed")
    result = proc.run(["pw-link", "-l"], timeout=timeout, check=False)
    if not result.ok:
        raise AudioError(f"pw-link failed (exit {result.returncode}): {result.stderr.strip()}")
    return parse(result.stdout)
