"""``pactl`` parsing and commands.

The parsing functions are pure: they take text and return data, so they can
be tested against fixtures recorded from a real rig (``tests/fixtures/``).
The command functions are thin argv wrappers over :mod:`hearth.proc`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from hearth import proc
from hearth.errors import CommandError

log = logging.getLogger(__name__)

#: Output buses a mic loopback may legitimately feed.
DEFAULT_LOOPBACK_SINKS: tuple[str, ...] = ("vm_game", "vm_chat", "vm_music")

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True, slots=True)
class ShortEntry:
    """One row of ``pactl list short sinks`` / ``... sources``."""

    index: int
    name: str
    driver: str
    spec: str
    state: str


@dataclass(frozen=True, slots=True)
class Module:
    """One row of ``pactl list short modules``."""

    index: int
    name: str
    args: str

    def arg(self, key: str) -> str | None:
        """Value of ``key=value`` in the module argument string."""
        for token in self.args.split():
            head, sep, tail = token.partition("=")
            if sep and head == key:
                return tail
        return None


@dataclass(frozen=True, slots=True)
class Loopback:
    """A ``module-loopback`` instance, resolved to its endpoints."""

    index: int
    source: str
    sink: str


def parse_short_list(text: str) -> list[ShortEntry]:
    """Parse ``pactl list short sinks|sources`` output.

    Rows are tab separated, but a missing trailing column is normal, so short
    rows are padded rather than dropped.
    """
    entries: list[ShortEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t") if "\t" in line else line.split()
        if len(cols) < 2 or not cols[0].strip().isdigit():
            continue
        cols = [c.strip() for c in cols] + [""] * (5 - len(cols))
        entries.append(
            ShortEntry(
                index=int(cols[0]),
                name=cols[1],
                driver=cols[2],
                spec=cols[3],
                state=cols[4],
            )
        )
    return entries


def parse_modules(text: str) -> list[Module]:
    """Parse ``pactl list short modules`` output."""
    modules: list[Module] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t") if "\t" in line else line.split(None, 2)
        if len(cols) < 2 or not cols[0].strip().isdigit():
            continue
        args = cols[2].strip() if len(cols) > 2 else ""
        modules.append(Module(index=int(cols[0]), name=cols[1].strip(), args=args))
    return modules


def select_loopbacks(
    modules: Iterable[Module],
    *,
    source: str | None = None,
    sinks: Sequence[str] | None = None,
) -> list[Loopback]:
    """Loopback modules, optionally filtered by source and target sinks.

    Replaces the ``pactl | awk | grep`` pipeline the GUI used to run when a
    mic loopback checkbox was switched off.
    """
    found: list[Loopback] = []
    for mod in modules:
        if mod.name != "module-loopback":
            continue
        src = mod.arg("source") or ""
        snk = mod.arg("sink") or ""
        if source is not None and source not in src:
            continue
        if sinks is not None and snk not in sinks:
            continue
        found.append(Loopback(index=mod.index, source=src, sink=snk))
    return found


def parse_info_version(text: str) -> str:
    """Server version from ``pactl info`` output, or ``""``.

    On PipeWire the line reads ``Server Name: PulseAudio (on PipeWire 1.4.11)``.
    """
    for line in text.splitlines():
        if "Server Name" in line:
            match = _VERSION_RE.search(line)
            if match:
                return match.group(1)
    return ""


def find_sink(entries: Iterable[ShortEntry], match: str) -> str:
    """First sink whose name contains *match*, case insensitively.

    This is how the monitor interface is identified by make (``Focusrite``)
    instead of by the serial number baked into its ALSA name.
    """
    needle = match.lower()
    for entry in entries:
        if needle in entry.name.lower():
            return entry.name
    return ""


# Commands. Every one is argv; none of them can be tripped by a sink name.


def _text(*args: str, check: bool = False) -> str:
    try:
        return proc.run(["pactl", *args], check=check).stdout
    except CommandError as exc:
        log.warning("pactl %s failed: %s", " ".join(args), exc)
        return ""


def raw(*args: str) -> str:
    """Raw stdout of ``pactl <args>``, or ``""`` when the call fails.

    Used by the collector, which parses verbose output that has no useful
    typed representation here.
    """
    return _text(*args)


def sinks() -> list[ShortEntry]:
    return parse_short_list(_text("list", "short", "sinks"))


def sources() -> list[ShortEntry]:
    return parse_short_list(_text("list", "short", "sources"))


def modules() -> list[Module]:
    return parse_modules(_text("list", "short", "modules"))


def server_version() -> str:
    return parse_info_version(_text("info"))


def default_sink() -> str:
    return _text("get-default-sink").strip()


def detect_sink(match: str) -> str:
    return find_sink(sinks(), match)


def volume(sink: str) -> int:
    """Volume percentage of *sink*, or 0 when it does not exist."""
    if not sink:
        return 0
    match = re.search(r"(\d+)%", _text("get-sink-volume", sink))
    return int(match.group(1)) if match else 0


def is_muted(sink: str) -> bool:
    if not sink:
        return False
    return "yes" in _text("get-sink-mute", sink).lower()


def set_mute(sink: str, muted: bool) -> None:
    if sink:
        proc.spawn(["pactl", "set-sink-mute", sink, "1" if muted else "0"])


def set_volume(sink: str, percent: int) -> None:
    if sink:
        proc.spawn(["pactl", "set-sink-volume", sink, f"{int(percent)}%"])


def set_default_sink(sink: str) -> None:
    if sink:
        proc.spawn(["pactl", "set-default-sink", sink])


def load_loopback(source: str, sink: str, *, latency_msec: int = 12) -> None:
    """Route *source* into *sink* with a new ``module-loopback``."""
    proc.spawn(
        [
            "pactl",
            "load-module",
            "module-loopback",
            f"source={source}",
            f"sink={sink}",
            f"latency_msec={int(latency_msec)}",
            "rate=48000",
            "channels=2",
            "channel_map=front-left,front-right",
            "remix=yes",
            "source_dont_move=true",
            "sink_dont_move=true",
        ]
    )


def unload_loopbacks(
    source: str,
    sinks_wanted: Sequence[str] = DEFAULT_LOOPBACK_SINKS,
) -> int:
    """Unload every loopback from *source* into one of *sinks_wanted*.

    Returns how many modules were unloaded.
    """
    targets = select_loopbacks(modules(), source=source, sinks=sinks_wanted)
    for loopback in targets:
        try:
            proc.run(["pactl", "unload-module", str(loopback.index)])
        except CommandError as exc:
            log.warning("could not unload loopback %s: %s", loopback.index, exc)
    return len(targets)
