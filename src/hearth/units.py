"""Every unit of the rig, and what "healthy" means for each kind.

The app has now been caught three separate times reading a healthy unit as a
fault, always the same way: assuming that "active" means working.

* ``roaring-vm-share.service`` is a ``Type=oneshot`` that creates ``pw-link``
  connections and exits, so ``inactive (dead)`` is its **success** state.
* ``roaring-carla-patch.service`` is the same shape.
* ``padfire.service`` reads inactive while Padfire runs, because it is started
  through the desktop's autostart generator instead.

And two kinds have no "running" state at all: a ``.timer`` is healthy when it
is *waiting*, and a ``.socket`` is healthy when it is *listening*.

So this module records the kind of each unit and judges accordingly. The list
is **discovered from systemd**, not hardcoded, because the previous hardcoded
list is what let the app print "12/12 active" while six real units went
unwatched. A unit that exists on the machine appears here whether or not
anyone remembered to add it.

A oneshot's unit state still cannot tell you whether its work survived. That
question is answered by measuring the graph -- see :mod:`hearth.audio.links`
and :mod:`hearth.chain`. This module's job is only to stop the *unit* view
from lying.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from hearth import proc

log = logging.getLogger(__name__)

Kind = Literal["daemon", "oneshot", "ondemand", "timer", "socket", "unknown"]

#: Unit-name patterns belonging to this project.
PATTERNS: tuple[str, ...] = ("roaring-*", "lpd8-*", "hearth*")

#: systemd ``Type=`` values that mean a long-running process.
DAEMON_TYPES = frozenset({"simple", "exec", "notify", "dbus", "forking", "idle"})

#: What each kind means, in words a service row can show instead of a colour.
KIND_NOTES: dict[Kind, str] = {
    "daemon": "Runs continuously. Should be running.",
    "oneshot": "Does its work once and exits. Not running is normal.",
    "ondemand": "Starts when something connects to it. Idle is normal.",
    "timer": "Schedules a job. Waiting is normal.",
    "socket": "Waits for a connection. Listening is normal.",
    "unknown": "Not installed on this machine.",
}


def label(unit: str) -> str:
    """``roaring-mic-busses.service`` -> ``mic busses``."""
    name = unit
    for suffix in (".service", ".timer", ".socket"):
        name = name.removesuffix(suffix)
    name = name.removeprefix("roaring-")
    return name.replace("-", " ").replace("_", " ")


def kind_of(unit: str, unit_type: str, *, loaded: bool = True, triggered_by: str = "") -> Kind:
    """Classify a unit from its name, systemd ``Type=`` and its trigger.

    ``triggered_by`` matters: ``roaring-presenced.service`` is ``Type=exec``
    and sits ``inactive (dead)`` because its socket starts it on demand. The
    first run of the tests reported it as a stopped daemon, which would have
    been a brand-new false alarm of exactly the kind this module exists to
    prevent.
    """
    if not loaded:
        return "unknown"
    if unit.endswith(".timer"):
        return "timer"
    if unit.endswith(".socket"):
        return "socket"
    if unit_type == "oneshot":
        return "oneshot"
    if triggered_by.strip():
        return "ondemand"
    if unit_type in DAEMON_TYPES:
        return "daemon"
    return "unknown"


@dataclass(frozen=True, slots=True)
class Unit:
    """One unit, with enough context to be judged fairly."""

    name: str
    kind: Kind
    active_state: str
    sub_state: str = ""

    @property
    def failed(self) -> bool:
        return self.active_state == "failed"

    @property
    def healthy(self) -> bool | None:
        """``None`` when the unit is not installed, so it is never a fault."""
        if self.kind == "unknown":
            return None
        if self.failed:
            return False
        if self.kind in ("oneshot", "ondemand"):
            # A oneshot ran and exited; an on-demand service is waiting to be
            # asked. Neither is a fault, and whether a oneshot's effect
            # survived is a graph question, not a unit question.
            return True
        return self.active_state == "active"

    @property
    def status_text(self) -> str:
        """Plain language, naming the consequence rather than the state word."""
        name = label(self.name)
        if self.kind == "unknown":
            return f"{name} is not installed"
        if self.failed:
            return f"{name} failed"
        if self.kind == "oneshot":
            return f"{name} has done its work"
        if self.kind == "ondemand":
            if self.active_state == "active":
                return f"{name} is running"
            return f"{name} starts when it is needed"
        if self.kind == "timer":
            return f"{name} is scheduled" if self.healthy else f"{name} is not scheduled"
        if self.kind == "socket":
            return f"{name} is listening" if self.healthy else f"{name} is not listening"
        return f"{name} is running" if self.healthy else f"{name} is stopped"


def parse_show(text: str) -> list[Unit]:
    """Parse ``systemctl show`` output for one or more units.

    Records are separated by a blank line. Properties absent from a record
    (a ``.timer`` has no ``Type``) simply stay empty rather than shifting
    values between units, which is the failure mode of positional parsing.
    """
    units: list[Unit] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key.strip()] = value.strip()
        name = fields.get("Id", "")
        if not name:
            continue
        loaded = fields.get("LoadState", "loaded") == "loaded"
        units.append(
            Unit(
                name=name,
                kind=kind_of(
                    name,
                    fields.get("Type", ""),
                    loaded=loaded,
                    triggered_by=fields.get("TriggeredBy", ""),
                ),
                active_state=fields.get("ActiveState", "unknown"),
                sub_state=fields.get("SubState", ""),
            )
        )
    return units


def parse_names(text: str) -> list[str]:
    """Pull unit names out of ``systemctl list-units --plain --no-legend``."""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\u25cf ").strip()
        if not line:
            continue
        name = line.split()[0]
        if "@." in name:
            # A template unit is not an instance and cannot have a state.
            continue
        if name not in names:
            names.append(name)
    return names


def discover(*, timeout: float = 5.0) -> list[str]:
    """Names of every installed unit belonging to this project."""
    if not proc.have("systemctl"):
        return []
    result = proc.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", *PATTERNS],
        timeout=timeout,
        check=False,
    )
    if not result.ok:
        log.warning("units: could not list units (exit %s)", result.returncode)
        return []
    return parse_names(result.stdout)


def inspect(names: list[str], *, timeout: float = 5.0) -> list[Unit]:
    """Read the state of the named units in a single call."""
    if not names or not proc.have("systemctl"):
        return []
    result = proc.run(
        [
            "systemctl",
            "--user",
            "show",
            "--property=Id",
            "--property=Type",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=TriggeredBy",
            *names,
        ],
        timeout=timeout,
        check=False,
    )
    if not result.ok:
        log.warning("units: could not inspect units (exit %s)", result.returncode)
        return []
    return parse_show(result.stdout)


def state() -> list[Unit]:
    """Every project unit on this machine, judged by its kind."""
    return inspect(discover())


def problems(units: list[Unit] | None = None) -> list[Unit]:
    """Units that are genuinely wrong: failed, or a stopped long-running one.

    A waiting timer, a listening socket and a finished oneshot are absent by
    construction. That is the entire point of the module.
    """
    units = state() if units is None else units
    return [unit for unit in units if unit.healthy is False]


def summary(units: list[Unit] | None = None) -> str:
    """One line about the stack, counting only what can meaningfully be wrong.

    The count excludes oneshots and unknown units, so it never claims to be
    watching something it cannot judge -- the honest version of "12/12".
    """
    units = state() if units is None else units
    judged = [
        unit
        for unit in units
        if unit.healthy is not None and unit.kind not in ("oneshot", "ondemand")
    ]
    bad = [unit for unit in judged if unit.healthy is False]
    if not judged:
        return "No audio services found"
    if not bad:
        return f"All {len(judged)} audio services are running"
    if len(bad) == 1:
        return bad[0].status_text
    return f"{bad[0].status_text} (+{len(bad) - 1} more)"
