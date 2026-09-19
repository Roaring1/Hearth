"""``systemctl --user`` wrappers.

The GUI polls a dozen units on every tick, so state is read with one batched
call rather than one process per unit. Nothing here raises: a dead or absent
systemd is reported as ``unknown`` and the UI shows that honestly.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from hearth import proc
from hearth.errors import CommandError

log = logging.getLogger(__name__)

UNKNOWN = "unknown"


def _run(args: Sequence[str]) -> str:
    try:
        # is-active exits non-zero for inactive units, which is information,
        # not an error, so failures are inspected rather than raised.
        return proc.run(["systemctl", "--user", *args], check=False).stdout
    except CommandError as exc:
        log.warning("systemctl %s failed: %s", " ".join(args), exc)
        return ""


def states(units: Sequence[str]) -> dict[str, str]:
    """Active state of each unit, in one call.

    ``systemctl is-active a b c`` prints one line per unit in argument order.
    A short reply (systemd restarting, unit not found) degrades to ``unknown``
    instead of silently shifting states onto the wrong units.
    """
    if not units:
        return {}
    lines = _run(["is-active", "--", *units]).strip().splitlines()
    return {unit: (lines[i].strip() if i < len(lines) else UNKNOWN) for i, unit in enumerate(units)}


def is_active(unit: str) -> bool:
    return states([unit]).get(unit) == "active"


def parse_timer_summary(text: str) -> str:
    """``NEXT LEFT`` summary from ``systemctl list-timers`` output.

    Replaces ``awk 'NR==2{print $1,$2,$5}'``: the header is line one and the
    first timer is line two.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return ""
    cols = lines[1].split()
    if len(cols) < 5:
        return ""
    return " ".join([cols[0], cols[1], cols[4]])


def timer_summary(unit: str) -> str:
    return parse_timer_summary(_run(["list-timers", unit, "--no-pager"]))


def start(unit: str) -> None:
    proc.spawn(["systemctl", "--user", "start", unit])


def stop(unit: str) -> None:
    proc.spawn(["systemctl", "--user", "stop", unit])


def restart(*units: str) -> None:
    proc.spawn(["systemctl", "--user", "restart", *units])


def reset_failed(*units: str) -> None:
    proc.spawn(["systemctl", "--user", "reset-failed", *units])


def daemon_reload() -> None:
    proc.spawn(["systemctl", "--user", "daemon-reload"])


def memory_bytes(unit: str) -> int:
    """``MemoryCurrent`` for *unit*, or 0 when unavailable.

    systemd reports ``MemoryCurrent=[not set]`` for units without accounting,
    which must not become a crash in the header widget.
    """
    raw = _run(["show", unit, "-p", "MemoryCurrent"]).strip()
    _, _, value = raw.partition("=")
    try:
        return int(value)
    except ValueError:
        return 0
