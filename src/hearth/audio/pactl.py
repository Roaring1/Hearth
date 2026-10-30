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
    exact_source: bool = False,
) -> list[Loopback]:
    """Loopback modules, optionally filtered by source and target sinks.

    Replaces the ``pactl | awk | grep`` pipeline the GUI used to run when a
    mic loopback checkbox was switched off. The default source filter is a
    substring search for compatibility; use exact_source for state checks and
    destructive operations where a partial match could select the wrong stream.
    """
    found: list[Loopback] = []
    for mod in modules:
        if mod.name != "module-loopback":
            continue
        src = mod.arg("source") or ""
        snk = mod.arg("sink") or ""
        if source is not None and ((source != src) if exact_source else (source not in src)):
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

    Retained for best-effort diagnostic callers. State snapshots should use
    :func:`raw_checked` so a command failure is not mistaken for an empty
    device list.
    """
    return _text(*args)


def raw_checked(*args: str) -> str:
    """Raw stdout of ``pactl <args>``; raise when the read did not succeed.

    A successful empty list is different from a failed server query. The
    collector uses this for the core sink, stream and source inventory so the
    GUI can preserve the last good snapshot during a transient PipeWire error.
    """
    return proc.run(["pactl", *args]).stdout


def sinks() -> list[ShortEntry]:
    return parse_short_list(_text("list", "short", "sinks"))


def sources() -> list[ShortEntry]:
    return parse_short_list(_text("list", "short", "sources"))


def modules(*, strict: bool = False, timeout: float = 2.0) -> list[Module]:
    """List loaded modules; strict mode detects query failures with a bounded wait."""
    if strict:
        result = proc.run(["pactl", "list", "short", "modules"], timeout=timeout)
        return parse_modules(result.stdout)
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


def _spawn_mutation(*args: str) -> bool:
    """Launch a pactl mutation without blocking the UI.

    True means the child process was started, not that its eventual exit code
    was successful. Callers can immediately recover from missing executables
    or permission failures without awaiting a high-frequency audio command.
    """
    try:
        proc.spawn(["pactl", *args])
    except CommandError as exc:
        # Volume faders can call this many times per second, so the UI reports
        # a visible failure and the shared logger stays quiet at normal level.
        log.debug("pactl %s could not start: %s", args[0] if args else "", exc)
        return False
    return True


def set_mute(sink: str, muted: bool) -> bool:
    if not sink:
        return True
    return _spawn_mutation("set-sink-mute", sink, "1" if muted else "0")


def move_sink_input(index: str, sink: str) -> bool:
    """Send one application's stream to a different bus."""
    if not index or not sink:
        return True
    return _spawn_mutation("move-sink-input", str(index), sink)


def is_source_muted(source: str) -> bool:
    if not source:
        return False
    return "yes" in _text("get-source-mute", source).lower()


def set_source_mute(source: str, muted: bool) -> bool:
    if not source:
        return True
    return _spawn_mutation("set-source-mute", source, "1" if muted else "0")


def set_stream_mute(index: str, muted: bool) -> bool:
    """Mute one sink input (a single application), not the whole bus."""
    if not index:
        return True
    return _spawn_mutation("set-sink-input-mute", str(index), "1" if muted else "0")


def set_stream_volume(index: str, percent: int) -> bool:
    """Set one application's sink-input volume without changing its bus."""
    if not index:
        return True
    return _spawn_mutation(
        "set-sink-input-volume", str(index), f"{max(0, min(150, int(percent)))}%"
    )


def set_volume(sink: str, percent: int) -> bool:
    if not sink:
        return True
    return _spawn_mutation("set-sink-volume", sink, f"{int(percent)}%")


def set_default_sink(sink: str) -> bool:
    if not sink:
        return True
    return _spawn_mutation("set-default-sink", sink)


def load_loopback(
    source: str,
    sink: str,
    *,
    latency_msec: int = 12,
    timeout: float = 2.0,
) -> None:
    """Load a loopback and wait for a checked, bounded pactl result.

    Callers that mutate routes should run this outside the GTK thread. Unlike
    volume fader changes, route operations are infrequent and need ordering:
    the caller must know whether loading completed before it reconciles state.
    """
    proc.run(
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
        ],
        timeout=timeout,
    )


def unload_loopbacks(
    source: str,
    sinks_wanted: Sequence[str] = DEFAULT_LOOPBACK_SINKS,
) -> int:
    """Unload every loopback from *source* into one of *sinks_wanted*.

    Returns how many modules were unloaded.
    """
    targets = select_loopbacks(
        modules(strict=True), source=source, sinks=sinks_wanted, exact_source=True
    )
    unloaded = 0
    errors: list[CommandError] = []
    for loopback in targets:
        try:
            proc.run(["pactl", "unload-module", str(loopback.index)], timeout=2.0)
            unloaded += 1
        except CommandError as exc:
            log.warning("could not unload loopback %s: %s", loopback.index, exc)
            errors.append(exc)
    if errors:
        raise errors[0]
    return unloaded
