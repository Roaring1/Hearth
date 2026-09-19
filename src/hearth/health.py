"""One honest sentence about whether the rig is working.

The current window shows sixteen service rows, five faders, a portal state
and a timer, and leaves the person to work out whether any of it is bad.
That is backwards: the common question is "is my audio fine right now", and
the answer should be readable without knowing what a null sink is.

This module is the logic behind that answer, kept free of any widget so it
can be tested. Three rules were learned the hard way on this rig and are
encoded here:

1. **Measure the effect, not the unit.** ``roaring-vm-share`` is a oneshot
   that exits after wiring the graph; its "inactive" state is success.
   Share health is therefore judged from live links, never from systemd.
2. **Unknown is not broken.** If a probe could not run, that is reported as
   an unknown, not as a fault. A missing ``XDG_RUNTIME_DIR`` once made a
   perfectly healthy graph look empty.
3. **A waiting timer is healthy.** Scheduled maintenance that has not fired
   yet is not a problem and must never be counted as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "warning", "unknown"]

#: Order used when deciding which issue speaks for the whole rig.
RANK: dict[Severity, int] = {"critical": 0, "warning": 1, "unknown": 2}

#: What the headline says when nothing is wrong. Deliberately plain: a
#: green "ALL SYSTEMS NOMINAL" reads as a dashboard, not as reassurance.
ALL_WELL = "Everything is working"


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem, phrased for the person who has to fix it."""

    key: str
    severity: Severity
    #: Short statement of what is wrong, in plain language.
    title: str
    #: What it means for them in practice, e.g. who cannot hear what.
    detail: str = ""
    #: The one thing worth doing about it, if there is one.
    fix: str = ""


@dataclass(frozen=True, slots=True)
class Health:
    """The assessed state of the rig."""

    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing is wrong and nothing is unknown."""
        return not self.issues

    @property
    def worst(self) -> Issue | None:
        """The issue that should speak for the whole rig."""
        if not self.issues:
            return None
        return min(self.issues, key=lambda i: RANK[i.severity])

    @property
    def severity(self) -> Severity | None:
        worst = self.worst
        return worst.severity if worst else None

    @property
    def headline(self) -> str:
        """The single line a person reads first.

        Names the actual problem instead of a count. "1 issue" forces a
        click to learn anything; "Discord cannot hear you" does not.
        """
        worst = self.worst
        if worst is None:
            return ALL_WELL
        extra = len(self.issues) - 1
        if extra:
            return f"{worst.title} (+{extra} more)"
        return worst.title

    def of(self, severity: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity == severity]


def _unit_label(unit: str) -> str:
    """Turn ``roaring-mic-busses.service`` into ``mic busses``."""
    name = unit.removesuffix(".service").removesuffix(".timer")
    name = name.removeprefix("roaring-")
    return name.replace("-", " ").replace("_", " ")


def assess(
    *,
    failed_units: list[str] | None = None,
    missing_sinks: list[str] | None = None,
    muted_buses: dict[str, str] | None = None,
    share_channels: int | None = None,
    portal_ok: bool | None = None,
    healed_units: list[str] | None = None,
) -> Health:
    """Judge the rig from already-collected facts.

    Every argument accepts ``None`` for "could not measure", which produces
    an ``unknown`` rather than a fault.

    :param muted_buses: bus name -> friendly label, for buses that are muted.
    :param share_channels: live links feeding the share bus (2 per source
        pair; ``0`` means a screen share would be silent).
    :param healed_units: units the self-healer just restarted; reported so a
        silent automatic repair is still visible.
    """
    issues: list[Issue] = []

    for unit in failed_units or []:
        issues.append(
            Issue(
                key=f"unit:{unit}",
                severity="critical",
                title=f"The {_unit_label(unit)} service stopped",
                detail="Part of the audio routing is not running.",
                fix="Restart it",
            )
        )

    for sink in missing_sinks or []:
        issues.append(
            Issue(
                key=f"sink:{sink}",
                severity="critical",
                title=f"The {sink} bus is missing",
                detail="Anything sent to this bus will be silent.",
                fix="Restart all audio",
            )
        )

    for sink, label in (muted_buses or {}).items():
        issues.append(
            Issue(
                key=f"mute:{sink}",
                severity="warning",
                title=f"{label} is muted",
                detail="You will not hear anything on this bus until it is unmuted.",
                fix="Unmute",
            )
        )

    if share_channels is None:
        issues.append(
            Issue(
                key="share",
                severity="unknown",
                title="Could not check screen-share audio",
                detail="The audio graph did not answer, so this may be fine.",
            )
        )
    elif share_channels == 0:
        issues.append(
            Issue(
                key="share",
                severity="warning",
                title="Screen shares would have no sound",
                detail="Nothing is feeding the share bus right now.",
                fix="Rebuild the share routing",
            )
        )

    if portal_ok is None:
        issues.append(
            Issue(
                key="portal",
                severity="unknown",
                title="Could not check screen sharing",
                detail="The desktop portal did not report a state.",
            )
        )
    elif not portal_ok:
        issues.append(
            Issue(
                key="portal",
                severity="warning",
                title="Screen sharing is unavailable",
                detail="Apps will not be able to capture your screen.",
                fix="Restart the portal",
            )
        )

    for unit in healed_units or []:
        issues.append(
            Issue(
                key=f"healed:{unit}",
                severity="unknown",
                title=f"Restarted the {_unit_label(unit)} service for you",
                detail="It had stopped, so it was started again automatically.",
            )
        )

    return Health(issues)


def from_snapshot(
    data: dict[str, object],
    *,
    bus_labels: dict[str, str] | None = None,
    share_channels: int | None = None,
) -> Health:
    """Adapt the collector's snapshot dictionary to :func:`assess`.

    Only keys the collector genuinely produces are read, and each one is
    type-checked before use, so a shape change degrades to "unknown"
    instead of raising inside a refresh tick.
    """
    labels = bus_labels or {}

    svc = data.get("svc")
    failed = [u for u, state in svc.items() if state == "failed"] if isinstance(svc, dict) else []

    def label_of(sink: object) -> str:
        """Friendly name for a sink, falling back to its raw name."""
        name = str(sink)
        return labels.get(name, name)

    mute = data.get("mute")
    muted: dict[str, str] = {}
    if isinstance(mute, dict):
        muted = {
            str(sink): label_of(sink)
            for sink, is_muted in mute.items()
            if is_muted and str(sink) in labels
        }

    missing: list[str] = []
    vol = data.get("vol")
    sink_st = data.get("sink_st")
    if isinstance(vol, dict) and isinstance(sink_st, dict):
        missing = [label_of(sink) for sink in vol if str(sink) in labels and sink not in sink_st]

    portal = data.get("vc_portal_ok")
    healed = data.get("healed")

    return assess(
        failed_units=failed,
        missing_sinks=missing,
        muted_buses=muted,
        share_channels=share_channels,
        portal_ok=portal if isinstance(portal, bool) else None,
        healed_units=list(healed) if isinstance(healed, list) else [],
    )
