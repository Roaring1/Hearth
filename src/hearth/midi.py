"""Control surfaces: the pad controllers, and who owns them.

Two MIDI surfaces are physically plugged into this rig and only one of them
belongs to Hearth:

* the **LPD8**, driven by ``lpd8-mixer.service``, which is what moves the bus
  volumes and mutes when a knob or pad is touched;
* a **Launchpad Mini**, which is driven by the sibling *Padfire* app.

That ownership split is the reason this module exists in this shape. A panel
that listed both devices as "Hearth control surfaces" would invite someone to
rebind pads another program is already driving, and a panel that hid the
Launchpad entirely would leave a connected device unexplained. Each surface is
therefore reported with the name of whatever owns it.

Three states have to stay distinguishable, per the rule the portal false alarm
taught: **connected**, **not connected**, and **could not ask**. ``aseqdump``
missing or failing is the third one, and it is never rendered as "unplugged".

A device being present is also not the same as it working. The LPD8 can sit
there lit up while ``lpd8-mixer.service`` is stopped, in which case every knob
and pad silently does nothing -- the failure a person would otherwise diagnose
by twisting a knob and doubting their hardware.

Where the map comes from
------------------------
The pad and knob assignments are **not** in ``roaring_mixer.conf``; that file
carries only the Astro target and the loopback latency. They are constants in
``~/bin/lpd8_mixer.sh``. They are mirrored here as plain-language data so the
app can show a legend, and :data:`MAP_SOURCE` records where the truth lives so
a later edit to the script is known to need a matching edit here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from hearth import proc, services
from hearth.errors import AudioError

log = logging.getLogger(__name__)

#: The script the control map below is mirrored from.
MAP_SOURCE = "~/bin/lpd8_mixer.sh"

#: The unit that turns LPD8 events into volume and mute changes.
LPD8_UNIT = "lpd8-mixer.service"


@dataclass(frozen=True, slots=True)
class SeqPort:
    """One ALSA sequencer port, as listed by ``aseqdump -l``."""

    client: int
    port: int
    client_name: str
    port_name: str

    @property
    def address(self) -> str:
        return f"{self.client}:{self.port}"


@dataclass(frozen=True, slots=True)
class RawPort:
    """One raw ALSA MIDI device, as listed by ``amidi -l``."""

    device: str
    name: str


@dataclass(frozen=True, slots=True)
class Control:
    """One knob or pad, described the way a person would describe it."""

    kind: str
    """``knob`` or ``pad``."""

    number: int
    """The number printed on the hardware."""

    action: str
    """What turning or pressing it does, in plain words."""


#: The LPD8 knobs, by the number printed on the device.
LPD8_KNOBS: tuple[Control, ...] = (
    Control("knob", 1, "Game volume"),
    Control("knob", 5, "Music volume"),
    Control("knob", 6, "Chat volume"),
    Control("knob", 7, "Game volume"),
    Control("knob", 8, "Microphone level"),
)

#: The LPD8 pads. A lit pad means that channel is on, not muted.
LPD8_PADS: tuple[Control, ...] = (
    Control("pad", 1, "Mute or unmute music"),
    Control("pad", 2, "Mute or unmute chat"),
    Control("pad", 3, "Switch the headset between chat and game"),
    Control("pad", 4, "Mute or unmute the headset"),
    Control("pad", 5, "Save a diagnostic report"),
    Control("pad", 6, "Mute or unmute the microphone"),
    Control("pad", 7, "Mute or unmute game audio"),
    Control("pad", 8, "Switch the desk speakers on or off"),
)

#: How to read the pad lights, shown alongside the legend.
LED_NOTE = "A lit pad means that sound is on. A dark pad means it is muted."


@dataclass(frozen=True, slots=True)
class Surface:
    """A control surface Hearth knows about, whether or not it owns it."""

    key: str
    label: str
    match: str
    """Substring matched against the device name reported by ALSA."""

    owner: str
    """The program that drives this device. Not always Hearth."""

    unit: str | None = None
    """The unit Hearth can act on, when Hearth owns the device."""

    controls: tuple[Control, ...] = ()


#: Every surface this rig has. Ownership is stated, never assumed.
SURFACES: tuple[Surface, ...] = (
    Surface(
        key="lpd8",
        label="LPD8 pad controller",
        match="LPD8",
        owner="Hearth",
        unit=LPD8_UNIT,
        controls=LPD8_KNOBS + LPD8_PADS,
    ),
    Surface(
        key="launchpad",
        label="Launchpad Mini",
        match="Launchpad",
        owner="Padfire",
    ),
)


@dataclass(frozen=True, slots=True)
class SurfaceState:
    """What one surface is doing right now."""

    surface: Surface
    connected: bool | None
    """``None`` means the devices could not be listed -- unknown, not absent."""

    address: str = ""
    """ALSA sequencer address, for diagnostics only. Never user-facing copy."""

    device: str = ""
    """Raw ALSA device, for diagnostics only."""

    service_active: bool | None = None
    """State of :attr:`Surface.unit`; ``None`` when there is none to query."""

    @property
    def working(self) -> bool:
        """Plugged in, and whatever drives it is running."""
        if not self.connected:
            return False
        if self.surface.unit is None:
            return True
        return self.service_active is True

    @property
    def summary(self) -> str:
        """One sentence naming the state and, when wrong, what to do."""
        label = self.surface.label
        if self.connected is None:
            return f"Cannot tell whether the {label} is connected"
        if not self.connected:
            if self.surface.unit is None:
                return f"No {label} connected"
            return f"No {label} connected - plug it in to use the knobs and pads"
        if self.surface.unit is not None and self.service_active is False:
            return (
                f"The {label} is connected but its controls are not running - "
                "its knobs and pads will do nothing"
            )
        if self.surface.unit is not None and self.service_active is None:
            return f"The {label} is connected; its controls could not be checked"
        if self.surface.owner != "Hearth":
            return f"{label} connected, controlled by {self.surface.owner}"
        return f"{label} connected - knobs and pads are live"


def parse_seq_ports(text: str) -> list[SeqPort]:
    """Parse ``aseqdump -l``.

    The listing is column-aligned but the names contain spaces, so the address
    is split off first and the remainder is split on runs of two or more
    spaces. Splitting on single spaces would turn "Launchpad Mini" into two
    fields and lose the port name.
    """
    ports: list[SeqPort] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Port"):
            continue
        address, _sep, rest = line.partition(" ")
        client, sep, port = address.partition(":")
        if not sep or not client.isdigit() or not port.isdigit():
            continue
        fields = [part for part in rest.split("  ") if part.strip()]
        client_name = fields[0].strip() if fields else ""
        port_name = fields[1].strip() if len(fields) > 1 else ""
        ports.append(SeqPort(int(client), int(port), client_name, port_name))
    return ports


def parse_raw_ports(text: str) -> list[RawPort]:
    """Parse ``amidi -l`` into device/name pairs, ignoring the direction column."""
    ports: list[RawPort] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Dir"):
            continue
        fields = line.split(None, 2)
        if len(fields) < 2 or not fields[1].startswith("hw:"):
            continue
        name = fields[2].strip() if len(fields) > 2 else ""
        ports.append(RawPort(fields[1], name))
    return ports


def find_seq(ports: list[SeqPort], match: str) -> SeqPort | None:
    """First sequencer port whose client or port name contains *match*."""
    lowered = match.lower()
    for port in ports:
        if lowered in port.client_name.lower() or lowered in port.port_name.lower():
            return port
    return None


def find_raw(ports: list[RawPort], match: str) -> RawPort | None:
    """First raw device whose name contains *match*."""
    lowered = match.lower()
    for port in ports:
        if lowered in port.name.lower():
            return port
    return None


def list_seq_ports(*, timeout: float = 5.0) -> list[SeqPort]:
    """Read the live sequencer port list.

    Raises rather than returning an empty list, for the same reason
    :func:`hearth.audio.links.list_links` does: "nothing is plugged in" and
    "the probe could not run" are opposite facts and must not collapse.
    """
    if not proc.have("aseqdump"):
        raise AudioError("aseqdump is not installed")
    result = proc.run(["aseqdump", "-l"], timeout=timeout, check=False)
    if not result.ok:
        raise AudioError(f"aseqdump failed (exit {result.returncode}): {result.stderr.strip()}")
    return parse_seq_ports(result.stdout)


def list_raw_ports(*, timeout: float = 5.0) -> list[RawPort]:
    """Read the live raw MIDI device list, or an empty list if unavailable.

    Unlike the sequencer list this one only enriches diagnostics, so a failure
    degrades quietly instead of making the whole panel unknown.
    """
    if not proc.have("amidi"):
        return []
    result = proc.run(["amidi", "-l"], timeout=timeout, check=False)
    if not result.ok:
        log.warning("midi: amidi -l failed (exit %s)", result.returncode)
        return []
    return parse_raw_ports(result.stdout)


def analyze(
    seq: list[SeqPort] | None,
    raw: list[RawPort] | None = None,
    *,
    service_states: dict[str, bool | None] | None = None,
) -> list[SurfaceState]:
    """Interpret device lists as surface states.

    Pure. Pass ``seq=None`` for "the probe could not run", which produces
    ``connected=None`` rather than a device-missing report.
    """
    raw = raw or []
    service_states = service_states or {}
    states: list[SurfaceState] = []
    for surface in SURFACES:
        if seq is None:
            states.append(SurfaceState(surface=surface, connected=None))
            continue
        port = find_seq(seq, surface.match)
        device = find_raw(raw, surface.match)
        states.append(
            SurfaceState(
                surface=surface,
                connected=port is not None,
                address=port.address if port else "",
                device=device.device if device else "",
                service_active=service_states.get(surface.unit) if surface.unit else None,
            )
        )
    return states


def state() -> list[SurfaceState]:
    """Read every known surface from the machine."""
    try:
        seq: list[SeqPort] | None = list_seq_ports()
    except AudioError as exc:
        log.warning("midi: cannot list control surfaces: %s", exc)
        seq = None
    raw = list_raw_ports() if seq is not None else []
    service_states: dict[str, bool | None] = {}
    for surface in SURFACES:
        if surface.unit is None:
            continue
        try:
            service_states[surface.unit] = services.is_active(surface.unit)
        except Exception:  # pragma: no cover - services degrades internally
            log.warning("midi: could not query %s", surface.unit)
            service_states[surface.unit] = None
    return analyze(seq, raw, service_states=service_states)


def summary(states: list[SurfaceState] | None = None) -> str:
    """One line for the whole control-surface story.

    Names the problem rather than counting devices, and stays quiet about
    surfaces owned by another program: Padfire not running is Padfire's
    business, not a Hearth fault.
    """
    states = state() if states is None else states
    owned = [s for s in states if s.surface.owner == "Hearth"]
    broken = [s for s in owned if s.connected and not s.working]
    if broken:
        return broken[0].summary
    unknown = [s for s in owned if s.connected is None]
    if unknown:
        return unknown[0].summary
    live = [s for s in states if s.working]
    if not live:
        return "No control surface connected"
    return " and ".join(s.surface.label for s in live) + " connected"
