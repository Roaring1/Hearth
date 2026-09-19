"""Screen-share audio selection (``roaring_share_ctl.py``).

Vesktop's screenshare audio is fed by venmic's ``vencord-sink``. Which buses
and apps reach that sink is decided by ``~/bin/roaring_share_ctl.py``, a
CLI the user currently has to open a terminal to drive. This module is the
GTK-free seam that lets the app drive it instead.

Deliberate choices:

* The selection is read straight from the controller's JSON state file, so
  the common "what is selected right now" question costs no subprocess.
* ``chat`` and ``a2`` carry incoming Discord voice. Selecting them makes the
  far end hear themselves, which is why the CLI refuses them without
  ``--force``. That refusal is re-implemented here rather than delegated,
  so the app can explain the consequence *before* spawning anything.
* Nothing here imports ``gi``; the UI layer renders what these functions
  return.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from hearth import paths, proc
from hearth.errors import AudioError

log = logging.getLogger(__name__)

#: alias -> human label. Mirrors ``BUSES`` in the controller.
BUSES: dict[str, str] = {
    "game": "Game",
    "chat": "Discord voice",
    "music": "Music",
    "laptop": "Laptop",
    "share": "Share bus",
    "b1": "Mic B1 (stream)",
    "b2": "Mic B2 (Discord)",
    "a1": "Astro A50 Game",
    "a2": "Astro A50 Chat",
    "a3": "Scarlett Solo",
}

#: Aliases that carry incoming call audio and cause far-end echo.
UNSAFE: frozenset[str] = frozenset({"chat", "a2"})

ECHO_WARNING = (
    "This carries incoming Discord voice, so the people you are talking to will hear themselves."
)


def script_path() -> Path:
    """Location of the controller script."""
    return paths.home() / "bin" / "roaring_share_ctl.py"


def selection_file() -> Path:
    """The controller's own selection state.

    This lives outside the Hearth config dir because the ``--watch`` daemon
    already reads it from there; moving it would need the same symlink dance
    the mixer confs got, and is not worth it for a file the user never opens.
    """
    return paths.home() / ".config" / "roaring" / "share_select.json"


def available() -> bool:
    """True when the controller is installed on this machine."""
    return script_path().is_file()


@dataclass(frozen=True, slots=True)
class BusRow:
    """One row of the controller's bus table."""

    alias: str
    bus: str
    state: str
    fader: str
    note: str

    @property
    def label(self) -> str:
        return BUSES.get(self.alias, self.bus)

    @property
    def unsafe(self) -> bool:
        return self.alias in UNSAFE


@dataclass(frozen=True, slots=True)
class Inventory:
    """What can be shared, and what is selected."""

    buses: tuple[BusRow, ...] = ()
    apps: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Status:
    """Selection versus reality."""

    selected: tuple[str, ...] = ()
    sharing: bool = False
    detail: str = ""

    @property
    def summary(self) -> str:
        """One line a non-technical user can act on."""
        if not self.selected:
            return "Nothing is shared - viewers hear silence"
        names = ", ".join(BUSES.get(a, a) for a in self.selected)
        if not self.sharing:
            return f"Ready to share {names} when you start a screen share"
        return f"Sharing {names}"


def _run(args: list[str], *, timeout: float = 10.0) -> str:
    if not available():
        raise AudioError("roaring_share_ctl.py is not installed")
    result = proc.run([str(script_path()), *args], timeout=timeout, check=False)
    if not result.ok:
        raise AudioError(f"share_ctl {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


# ------------------------------------------------------------------ parsing

_STATE_HEADER = ("ALIAS", "BUS", "STATE")


def parse_list(text: str) -> Inventory:
    """Parse ``roaring_share_ctl.py list`` output.

    The table is fixed-width and a bus label can contain spaces
    ("Astro A50 Game"), so columns are sliced by the header offsets rather
    than split on whitespace.
    """
    lines = text.splitlines()
    buses: list[BusRow] = []
    apps: list[str] = []
    selected: list[str] = []

    cuts: list[int] = []
    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("APPS PLAYING NOW"):
            section = "apps"
            continue
        if stripped.startswith("SELECTED"):
            section = "selected"
            continue
        if all(h in line for h in _STATE_HEADER):
            cuts = [line.index(h) for h in ("ALIAS", "BUS", "STATE", "FADER", "NOTE")]
            section = "buses"
            continue

        if not stripped:
            continue
        if section == "buses" and cuts:
            alias = line[cuts[0] : cuts[1]].strip()
            if alias not in BUSES:
                continue
            buses.append(
                BusRow(
                    alias=alias,
                    bus=line[cuts[1] : cuts[2]].strip(),
                    state=line[cuts[2] : cuts[3]].strip(),
                    fader=line[cuts[3] : cuts[4]].strip(),
                    note=line[cuts[4] :].strip(),
                )
            )
        elif section == "apps":
            if stripped.startswith("("):
                continue
            apps.append(stripped)
        elif section == "selected":
            if stripped.startswith("("):
                continue
            selected.extend(p.strip() for p in stripped.split(",") if p.strip())

    return Inventory(tuple(buses), tuple(apps), tuple(selected))


def parse_status(text: str) -> Status:
    """Parse ``roaring_share_ctl.py status`` output."""
    selected: list[str] = []
    sharing = False
    detail = ""
    for line in text.splitlines():
        head, _, tail = line.partition(":")
        value = tail.strip()
        key = head.strip().lower()
        if key == "selection":
            if value and not value.startswith("("):
                selected = [p.strip() for p in value.split(",") if p.strip()]
        elif key == "share sink":
            detail = value
            sharing = not value.lower().startswith("absent")
    return Status(tuple(selected), sharing, detail)


# ------------------------------------------------------------------ reading


def selection() -> list[str]:
    """Current selection, read from the state file (no subprocess)."""
    try:
        raw = json.loads(selection_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    targets = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(targets, list):
        return []
    return [str(t) for t in targets]


def inventory() -> Inventory:
    """Buses, live app streams and the current selection."""
    return parse_list(_run(["list"]))


def status() -> Status:
    """Selection versus what is actually linked."""
    return parse_status(_run(["status"]))


# ------------------------------------------------------------------ writing


def check_safe(items: list[str], *, force: bool = False) -> None:
    """Raise unless every item is safe to share (or *force* is set)."""
    if force:
        return
    risky = [i for i in items if i.strip().lower() in UNSAFE]
    if risky:
        names = ", ".join(BUSES.get(r, r) for r in risky)
        raise AudioError(f"{names}: {ECHO_WARNING}")


def _mutate(cmd: str, items: list[str], *, force: bool = False) -> str:
    check_safe(items, force=force)
    args = [cmd, *items]
    if force:
        args.append("--force")
    log.info("share: %s %s", cmd, " ".join(items) or "(none)")
    return _run(args, timeout=20.0)


def set_selection(items: list[str], *, force: bool = False) -> str:
    """Replace the selection."""
    return _mutate("set", items, force=force)


def add(items: list[str], *, force: bool = False) -> str:
    """Add to the selection."""
    return _mutate("add", items, force=force)


def remove(items: list[str]) -> str:
    """Remove from the selection. Never needs a force flag."""
    return _run(["rm", *items], timeout=20.0)


def clear() -> str:
    """Drop everything."""
    return _run(["clear"], timeout=20.0)


def apply() -> str:
    """One-shot reconcile of links to match the selection."""
    return _run(["apply"], timeout=20.0)


@dataclass(frozen=True, slots=True)
class ShareView:
    """Everything the UI needs for the share panel, in one object."""

    installed: bool = False
    status: Status = field(default_factory=Status)
    inventory: Inventory = field(default_factory=Inventory)
    error: str = ""


def view() -> ShareView:
    """Collect the share panel's state, degrading instead of raising.

    The UI must never crash because a companion script is missing or slow,
    so every failure becomes a string the panel can show.
    """
    if not available():
        return ShareView(installed=False, error="Screen-share control is not installed")
    try:
        return ShareView(installed=True, status=status(), inventory=inventory())
    except (AudioError, OSError) as exc:
        log.warning("share view failed: %s", exc)
        return ShareView(installed=True, error=str(exc))
