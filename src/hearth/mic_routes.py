"""What feeds the two mic buses, and who decides.

Three things were writing to ``mic_b1`` and ``mic_b2`` without agreeing:

* ``roaring_carla_patch.sh`` pw-links Carla's processed output into **both**
  buses at login, unconditionally;
* ``roaring_mic_routesd.sh`` re-asserts raw ``sm7b_mono`` / ``astro_mic_48k``
  loopbacks from ``roaring_mic_router.conf`` every three seconds;
* Hearth's round-19 IN chooser loaded loopbacks straight through ``pactl``.

The last one always lost. A loopback Hearth added was removed by the daemon
on its next pass, and Carla's feed could not be refused at all -- so the IN
menu showed a choice the machine was not actually taking. A control that
does not control is worse than no control.

So the config file is the authority and this module is how Hearth edits it.
Ticking a source writes ``B1_ROUTE`` / ``B2_ROUTE`` (and the ``*_ACTIVE``
gate the daemon checks first), and the daemon then makes the graph match
within one pass. Carla gets the same treatment through ``CARLA_TO_B1`` /
``CARLA_TO_B2``: the patch script reads them at login, and Hearth applies
the change immediately with ``pw-link`` so the tick is not a promise about
the next reboot.

The file is shell -- the daemon sources it -- so edits are line-level
substitutions that keep comments, order and unknown keys intact, and the
write is atomic.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

from hearth import proc
from hearth.errors import CommandError

log = logging.getLogger(__name__)

#: The file ``roaring_mic_routesd.sh`` sources.
CONF_PATH = Path("~/.config/hearth/roaring_mic_router.conf").expanduser()

#: Bus name to the prefix used by every key about that bus.
BUS_PREFIX: dict[str, str] = {"mic_b1": "B1", "mic_b2": "B2"}

#: PulseAudio source name to the token the conf uses for it.
RAW_TOKEN: dict[str, str] = {"sm7b_mono": "sm7b", "astro_mic_48k": "astro"}

#: Carla feeds B1 by default because that is the processed SM7B chain the
#: rig is built around. It does **not** feed B2 by default: B2 being a
#: silent copy of B1 unless told otherwise was the surprise.
CARLA_DEFAULT: dict[str, bool] = {"mic_b1": True, "mic_b2": False}

#: Carla's output ports and the bus ports they land on.
CARLA_PORTS: tuple[tuple[str, str], ...] = (
    ("Carla:audio-out1", "playback_FL"),
    ("Carla:audio-out2", "playback_FR"),
)

_ASSIGN = re.compile(r'^(?P<key>[A-Z0-9_]+)="?(?P<value>[^"#]*)"?\s*$')
_TRUE = {"1", "true", "yes", "on"}
_ROUTE_VALUES = {"none", "sm7b", "astro", "both"}
_ROUTE_KEYS = {"B1_ROUTE", "B2_ROUTE"}
_BOOL_KEYS = {"B1_ACTIVE", "B2_ACTIVE", "CARLA_TO_B1", "CARLA_TO_B2"}
_WRITE_KEYS = _ROUTE_KEYS | _BOOL_KEYS


def parse(text: str) -> dict[str, str]:
    """Read ``KEY="value"`` assignments, ignoring comments and blanks."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGN.match(stripped)
        if match:
            values[match.group("key")] = match.group("value").strip()
    return values


def apply_edits(text: str, updates: dict[str, str]) -> str:
    """Return ``text`` with ``updates`` applied, preserving everything else.

    Every occurrence of an updated key is rewritten where it stands; a new
    key is appended. Updating all duplicates matters because the shell daemon
    uses the last assignment it sources. Comments, ordering and keys this
    module knows nothing about survive, because a config file shared with two
    shell scripts is not ours to rewrite.
    """
    if not updates:
        return text
    remaining = dict(updates)
    out: list[str] = []
    for line in text.splitlines():
        match = _ASSIGN.match(line.strip())
        key = match.group("key") if match else None
        if key in updates:
            # Rewrite every occurrence; leaving a later duplicate untouched
            # would make the sourced shell config override this edit.
            out.append(f'{key}="{updates[key]}"')
            remaining.pop(key, None)
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f'{key}="{value}"')
    return "\n".join(out) + "\n"


def read(path: Path | None = None) -> dict[str, str]:
    """Current config values, empty when missing or unreadable."""
    target = path or CONF_PATH
    try:
        return parse(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        log.warning("could not read microphone routing config %s: %s", target, exc)
        return {}


def write(updates: dict[str, str], path: Path | None = None) -> bool:
    """Apply validated updates atomically and serialize concurrent writers.

    The daemon sources this file on a three-second timer, so a malformed or
    partial update can change the live microphone graph or become shell code.
    Only known keys and finite value choices are writable. Unique same-directory
    temp files keep reads atomic; a private advisory lock prevents lost updates.
    """
    if not updates:
        return True
    for key, value in updates.items():
        valid = key in _WRITE_KEYS and isinstance(value, str)
        if key in _ROUTE_KEYS:
            valid = valid and value in _ROUTE_VALUES
        elif key in _BOOL_KEYS:
            valid = valid and value in {"true", "false"}
        if not valid:
            log.warning("refusing invalid microphone routing update %r=%r", key, value)
            return False

    target = path or CONF_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(f".{target.name}.lock")
        lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        lock_flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(lock_path, lock_flags, 0o600)
    except OSError as exc:
        log.warning("could not lock %s; refusing to update: %s", target, exc)
        return False

    try:
        lock_metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_metadata.st_mode):
            raise OSError(f"microphone config lock is not a regular file: {lock_path}")
        if lock_metadata.st_uid != os.getuid():
            raise PermissionError(f"microphone config lock is not owned by this user: {lock_path}")
        if lock_metadata.st_nlink != 1:
            raise OSError(f"refusing hard-linked microphone config lock: {lock_path}")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            cfg_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            cfg_flags |= getattr(os, "O_NOFOLLOW", 0)
            cfg_fd = -1
            try:
                try:
                    cfg_fd = os.open(target, cfg_flags)
                except FileNotFoundError:
                    original = ""
                    original_mode = None
                else:
                    metadata = os.fstat(cfg_fd)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise OSError(f"refusing to edit non-regular config: {target}")
                    if metadata.st_uid != os.getuid():
                        raise PermissionError(
                            f"microphone config is not owned by this user: {target}"
                        )
                    with os.fdopen(cfg_fd, "r", encoding="utf-8") as stream:
                        cfg_fd = -1
                        original = stream.read()
                    original_mode = stat.S_IMODE(metadata.st_mode)
            except (OSError, UnicodeError) as exc:
                # O_NOFOLLOW rejects symlinks at open time, and invalid bytes
                # are not treated as a missing config. Preserve the source file.
                log.warning("could not safely read %s; refusing to overwrite: %s", target, exc)
                return False
            finally:
                if cfg_fd >= 0:
                    os.close(cfg_fd)

            updated = apply_edits(original, updates)
            fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.tmp.", dir=target.parent)
            tmp = Path(tmp_name)
            try:
                if original_mode is not None:
                    os.fchmod(fd, original_mode)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                tmp.replace(target)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            log.warning("could not write %s: %s", target, exc)
            return False
        return True
    except OSError as exc:
        log.warning("could not lock or update %s: %s", target, exc)
        return False
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError as exc:
            # Closing the descriptor releases the lock as a fallback.
            log.debug("could not explicitly unlock %s: %s", target, exc)
        finally:
            try:
                os.close(lock_fd)
            except OSError as exc:
                log.debug("could not close config lock %s: %s", target, exc)


def tokens(route: str) -> set[str]:
    """The raw sources named by one ``*_ROUTE`` value."""
    value = (route or "").strip().lower()
    if value == "both":
        return {"sm7b", "astro"}
    if value in {"sm7b", "astro"}:
        return {value}
    return set()


def compose(active: set[str]) -> str:
    """The ``*_ROUTE`` value for a set of raw sources."""
    if {"sm7b", "astro"} <= active:
        return "both"
    if "sm7b" in active:
        return "sm7b"
    if "astro" in active:
        return "astro"
    return "none"


def raw_enabled(values: dict[str, str], bus: str, source: str) -> bool:
    """Is this raw source currently routed into this bus?

    The ``*_ACTIVE`` gate is checked as well, because the daemon forces the
    route to ``none`` when it is false -- so a route naming a source that
    the gate disables is not a source you can hear.
    """
    prefix = BUS_PREFIX.get(bus)
    token = RAW_TOKEN.get(source)
    if prefix is None or token is None:
        return False
    if values.get(f"{prefix}_ACTIVE", "").strip().lower() not in _TRUE:
        return False
    return token in tokens(values.get(f"{prefix}_ROUTE", ""))


def raw_edits(values: dict[str, str], bus: str, source: str, on: bool) -> dict[str, str]:
    """Config changes that add or remove one raw source on one bus."""
    prefix = BUS_PREFIX.get(bus)
    token = RAW_TOKEN.get(source)
    if prefix is None or token is None:
        return {}
    active = tokens(values.get(f"{prefix}_ROUTE", ""))
    if values.get(f"{prefix}_ACTIVE", "").strip().lower() not in _TRUE:
        # The gate was off, so whatever the route said was not in effect.
        # Start from nothing rather than silently re-enabling an old choice.
        active = set()
    if on:
        active.add(token)
    else:
        active.discard(token)
    route = compose(active)
    return {
        f"{prefix}_ROUTE": route,
        # Turning the last source off leaves the gate closed, which is how
        # the daemon expresses "this bus takes nothing".
        f"{prefix}_ACTIVE": "true" if route != "none" else "false",
    }


def carla_key(bus: str) -> str | None:
    """The conf key holding whether Carla feeds this bus."""
    prefix = BUS_PREFIX.get(bus)
    return f"CARLA_TO_{prefix}" if prefix else None


def carla_enabled(values: dict[str, str], bus: str) -> bool:
    """Is Carla's processed output wired into this bus?"""
    key = carla_key(bus)
    if key is None:
        return False
    raw = values.get(key)
    if raw is None:
        return CARLA_DEFAULT.get(bus, False)
    return raw.strip().lower() in _TRUE


def set_carla(bus: str, on: bool, *, path: Path | None = None) -> bool:
    """Record the Carla feed for a bus and apply it to the live graph.

    The config write is what survives a reboot; the ``pw-link`` calls are
    what make the tick do something now. A failure to reach ``pw-link`` --
    Carla not running, say -- does not undo the stored choice: the patch
    script will honour it the next time Carla starts.
    """
    key = carla_key(bus)
    if key is None:
        return False
    stored = write({key: "true" if on else "false"}, path)
    if not stored:
        # The router daemon reads this same file every three seconds. If the
        # choice was not persisted, applying it live would only be temporary
        # and would fight the daemon's authoritative configuration.
        return False
    if not link_carla(bus, on):
        # The stored preference remains authoritative; the patch script can
        # apply it when Carla is available again. Do not make a live-link
        # launch failure look like a failed config write to the UI.
        log.warning("saved Carla routing for %s, but the live link could not be applied", bus)
    return True


def link_carla(bus: str, on: bool, *, timeout: float = 5.0) -> bool:
    """Connect or disconnect Carla's output ports for one bus."""
    if bus not in BUS_PREFIX or not proc.have("pw-link"):
        return False
    ok = True
    for source, port in CARLA_PORTS:
        cmd = ["pw-link"] if on else ["pw-link", "-d"]
        try:
            result = proc.run([*cmd, source, f"{bus}:{port}"], timeout=timeout, check=False)
        except CommandError as exc:
            log.warning(
                "pw-link %s %s -> %s:%s could not run: %s",
                "on" if on else "off",
                source,
                bus,
                port,
                exc,
            )
            ok = False
            continue
        if not result.ok:
            # Already connected, already gone, or Carla is not up. The saved
            # preference is retained and the next patch run can reconcile.
            log.debug("pw-link %s %s -> %s:%s failed", "on" if on else "off", source, bus, port)
            ok = False
    return ok


#: What each bus is for, in the words the rig uses out loud.
BUS_LABEL: dict[str, str] = {"mic_b1": "Stream", "mic_b2": "Chat"}

#: The capture source each bus is published as, and what apps pick.
BUS_SOURCE: dict[str, str] = {"mic_b1": "b1_mic", "mic_b2": "b2_mic"}

#: Raw device names as a person would say them.
SOURCE_LABEL: dict[str, str] = {"sm7b_mono": "SM7B raw", "astro_mic_48k": "Astro mic"}


def feeds(values: dict[str, str], bus: str) -> list[str]:
    """Everything currently poured into one mic bus, named for a human."""
    names: list[str] = []
    if carla_enabled(values, bus):
        names.append("SM7B via Carla")
    for source, label in SOURCE_LABEL.items():
        if raw_enabled(values, bus, source):
            names.append(label)
    return names


def flow_lines(
    values: dict[str, str],
    *,
    moonlight_source: str = "",
    laptop_host: str = "",
) -> list[str]:
    """The mic path as it is configured right now, one line per bus.

    The old window drew this as a fixed diagram in the source, so it kept
    saying "SM7B -> B1, Astro -> B2" no matter what the config held. These
    lines are read from the same file the daemon obeys, which means a bus
    with nothing in it says so.
    """
    lines: list[str] = []
    for bus, label in BUS_LABEL.items():
        source = BUS_SOURCE[bus]
        names = feeds(values, bus)
        line = f"{label}: {' + '.join(names) if names else 'nothing'} \u2192 {source}"
        if moonlight_source and moonlight_source == source:
            # The configured host is often literally "laptop", and
            # "laptop (laptop)" is noise, so the name is only worth
            # printing when it says something the word does not.
            named = laptop_host and laptop_host.strip().lower() != "laptop"
            line += f" \u2192 laptop ({laptop_host})" if named else " \u2192 laptop"
        lines.append(line)
    return lines
