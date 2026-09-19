"""One report that answers "is my audio fine?" from every backend module.

Steps 12-16 built six modules that each know one thing -- health, the effects
chain, screen-share audio, the microphone suite, the companion tools and the
control surfaces -- and nothing yet asked them all at once. This does, without
touching GTK, so the answer can be read, tested and printed before the Phase 4
window exists to render it. ``hearth --status`` is that print.

It deliberately does **not** import the legacy core. The legacy module probes
PipeWire at import time and owns the GUI's world; the point of this composer
is to show that the new modules already stand on their own.

Two things are inherited rather than reinvented:

* the **bus list** mirrors ``legacy_core.VM_SINKS``, including ``vm_share``,
  which step 11 added after finding the rig had a bus the app could not see;
* the **portal check** asks the frontend plus *any* backend, matching the
  step 11 fix. Reporting it as "unknown" instead was tried and was worse than
  useless: the first run of this report led with "Could not check screen
  sharing" purely because the composer had declined to look.

Unit health comes from :mod:`hearth.units`, which scopes discovery to this
project and judges each unit by its kind. A bare
``systemctl --user --state=failed`` was written here first and thrown away:
on this machine it lists crashed media players, a chat app and a VPN tray
icon, none of which are audio faults.

Cost, measured on this rig before the UI was built, because a report the
window cannot afford to call is a report the window will not call:

==========================  =======
probe                       time
==========================  =======
screen share                0.212s
muted buses                 0.036s
service units               0.034s
everything else combined    0.027s
==========================  =======

The screen-share probe is the whole problem: it shells out twice to a
Python script, so most of that figure is two interpreter startups and no
amount of care here will make it fast. Left alone, a once-a-second refresh
would spend a third of a second of the main loop on it. So that one answer
is cached for :data:`SHARE_TTL` seconds -- long enough to make refreshes
cheap, short enough that nobody can start a screen share and see a stale
line. Everything else is re-read every time, and the rest together costs
about a tenth of the share probe.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from hearth import chain, health, mic, midi, services, share, tools, units
from hearth.audio import links, pactl
from hearth.errors import AudioError

log = logging.getLogger(__name__)

#: The buses, with the names a person uses for them. Mirrors VM_SINKS.
BUSES: tuple[tuple[str, str], ...] = (
    ("vm_game", "Game"),
    ("vm_chat", "Chat"),
    ("vm_music", "Music"),
    ("vm_share", "Share"),
    ("laptop_audio", "Laptop"),
)

#: The screen-share bus, whose feeders are the thing worth measuring.
SHARE_SINK = "vm_share"

#: How long the screen-share answer may be reused. See the module docstring.
SHARE_TTL = 5.0

_share_cache: tuple[float, share.ShareView] | None = None


def share_view(*, max_age: float = SHARE_TTL) -> share.ShareView:
    """The screen-share picture, reusing a recent answer when there is one.

    Pass ``max_age=0`` right after changing what is shared, so the report
    shows the change instead of the memory of it.
    """
    global _share_cache
    now = time.monotonic()
    if _share_cache is not None and now - _share_cache[0] <= max_age:
        return _share_cache[1]
    view = share.view()
    _share_cache = (now, view)
    return view


def forget_share() -> None:
    """Drop the cached screen-share answer."""
    global _share_cache
    _share_cache = None


def broken_units(found: list[units.Unit] | None = None) -> list[str]:
    """Names of units that are genuinely wrong.

    Delegated to :mod:`hearth.units`, which scopes discovery to this
    project's units and judges each by its kind. A bare
    ``systemctl --user --state=failed`` was tried first and was unusable:
    on this machine it lists crashed media players, a chat app and a VPN
    tray icon, none of which are audio faults.
    """
    found = units.state() if found is None else found
    return [unit.name for unit in units.problems(found)]


def muted_buses() -> dict[str, str]:
    """Muted buses as ``{sink: label}``, skipping any that is not present."""
    present = {entry.name for entry in pactl.sinks()}
    return {sink: label for sink, label in BUSES if sink in present and pactl.is_muted(sink)}


def share_channels(graph: list[links.Link] | None) -> int | None:
    """Channels feeding the share bus; ``None`` when the graph could not be read."""
    if graph is None:
        return None
    return len([link for link in graph if link.target_node == SHARE_SINK])


def share_line(view: share.ShareView) -> str:
    """One sentence for the share panel, whatever state it is in.

    A missing controller and a failing controller are different sentences,
    and neither is "screen share is off".
    """
    if not view.installed:
        return view.error or "Screen-share control is not installed"
    if view.error:
        return f"Screen-share control could not be read: {view.error}"
    return view.status.summary


#: The portal frontend, plus every backend that can satisfy it. Asking only
#: for the KDE backend is what made the app report a healthy session as
#: "portal: NOT RUNNING" while screen sharing worked the whole time.
PORTAL_FRONTEND = "xdg-desktop-portal"
PORTAL_BACKENDS: tuple[str, ...] = (
    "xdg-desktop-portal-kde",
    "xdg-desktop-portal-gtk",
    "xdg-desktop-portal-wlr",
    "xdg-desktop-portal-hyprland",
)


def portal_ok() -> bool | None:
    """Whether screen sharing can work: the frontend plus *any* backend.

    ``None`` when systemd could not be asked, never ``False`` -- an
    unanswerable probe is the thing that cried wolf here before.
    """
    portal_units = (PORTAL_FRONTEND, *PORTAL_BACKENDS)
    try:
        states = services.states(portal_units)
    except Exception:  # pragma: no cover - services degrades internally
        log.warning("status: could not query the desktop portal")
        return None
    if states.get(PORTAL_FRONTEND, "unknown") == "unknown":
        return None
    frontend = states.get(PORTAL_FRONTEND) == "active"
    backend = any(states.get(unit) == "active" for unit in PORTAL_BACKENDS)
    return frontend and backend


@dataclass(frozen=True, slots=True)
class Report:
    """Everything the app knows, gathered once."""

    health: health.Health
    chain: chain.ChainState
    share: share.ShareView
    mic: mic.MicView
    surfaces: list[midi.SurfaceState] = field(default_factory=list)
    units: list[units.Unit] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        return self.health.headline

    def lines(self) -> list[str]:
        """The report as plain text, in the order a person would want it.

        The headline first, then what is wrong and how to fix it, then the
        per-area sentences. Nothing here names a sink, a unit or a command.
        """
        out = [self.headline, ""]
        for issue in self.health.issues:
            out.append(f"  {issue.title}")
            if issue.detail:
                out.append(f"    {issue.detail}")
            if issue.fix:
                out.append(f"    Fix: {issue.fix}")
        if self.health.issues:
            out.append("")
        if self.units:
            out.append(f"Services        {units.summary(self.units)}")
        out.append(f"Voice effects   {self.chain.summary}")
        out.append(f"Screen share    {share_line(self.share)}")
        out.append(f"Microphone      {self.mic.summary}")
        for state in self.surfaces:
            out.append(f"Controls        {state.summary}")
        if self.missing_tools:
            out.append("")
            out.append("Not installed on this machine: " + ", ".join(self.missing_tools))
        return out


def gather(*, max_share_age: float = SHARE_TTL) -> Report:
    """Ask every module once and assemble the report.

    Each probe degrades on its own. One unreadable area must not take the
    others down, because a report that fails as a whole is a report nobody can
    use at the moment they need it most.

    ``max_share_age`` is the only cost knob: pass ``0`` to insist on a fresh
    screen-share reading, which costs about a fifth of a second.
    """
    try:
        graph: list[links.Link] | None = links.list_links()
    except AudioError as exc:
        log.warning("status: cannot read the audio graph: %s", exc)
        graph = None

    try:
        session_active: bool | None = services.is_active(chain.SESSION_UNIT)
    except Exception:  # pragma: no cover - services degrades internally
        session_active = None

    chain_state = (
        chain.analyze(graph, session_active=session_active)
        if graph is not None
        else chain.ChainState(present=False, session_active=session_active)
    )

    found = units.state()

    assessment = health.assess(
        failed_units=broken_units(found),
        muted_buses=muted_buses(),
        share_channels=share_channels(graph),
        portal_ok=portal_ok(),
    )

    return Report(
        health=assessment,
        chain=chain_state,
        share=share_view(max_age=max_share_age),
        mic=mic.view(),
        surfaces=midi.state(),
        units=found,
        missing_tools=[tool.label for tool in tools.missing()],
    )


def text() -> str:
    """The whole report as one printable string."""
    return "\n".join(gather().lines())
