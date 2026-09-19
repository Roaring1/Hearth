"""Tests for the health model behind the app's one-line answer."""

from __future__ import annotations

from hearth import health

LABELS = {
    "vm_game": "Game",
    "vm_chat": "Discord voice",
    "vm_music": "Music",
    "vm_share": "Share bus",
}


def test_a_healthy_rig_says_so_plainly() -> None:
    result = health.assess(share_channels=4, portal_ok=True)
    assert result.ok
    assert result.headline == health.ALL_WELL
    assert result.severity is None


def test_headline_names_the_problem_not_a_count() -> None:
    result = health.assess(
        failed_units=["roaring-mic-busses.service"],
        share_channels=4,
        portal_ok=True,
    )
    assert result.headline == "The mic busses service stopped"


def test_worst_issue_speaks_for_the_rig() -> None:
    result = health.assess(
        failed_units=["roaring-mic-busses.service"],
        muted_buses={"vm_music": "Music"},
        share_channels=0,
        portal_ok=False,
    )
    assert result.severity == "critical"
    assert result.headline.startswith("The mic busses service stopped (+")
    assert "+3 more" in result.headline


def test_unit_labels_are_readable() -> None:
    assert health._unit_label("roaring-vesktop-mic-watchd.service") == "vesktop mic watchd"
    assert health._unit_label("roaring-pipewire-restart.timer") == "pipewire restart"


def test_unmeasured_is_unknown_not_broken() -> None:
    """A probe that could not run must never be reported as a fault."""
    result = health.assess(share_channels=None, portal_ok=None)
    assert not result.ok
    assert result.severity == "unknown"
    assert {i.key for i in result.of("unknown")} == {"share", "portal"}
    assert not result.of("critical")


def test_a_silent_share_bus_is_a_warning_with_a_fix() -> None:
    (issue,) = health.assess(share_channels=0, portal_ok=True).issues
    assert issue.severity == "warning"
    assert issue.title == "Screen shares would have no sound"
    assert issue.fix


def test_automatic_repairs_are_still_reported() -> None:
    """Self-healing must not be silent, or the rig looks haunted."""
    result = health.assess(
        share_channels=4,
        portal_ok=True,
        healed_units=["roaring-cmd-rx.service"],
    )
    assert result.headline == "Restarted the cmd rx service for you"


def test_every_issue_is_phrased_for_a_person() -> None:
    result = health.assess(
        failed_units=["roaring-mic-busses.service"],
        missing_sinks=["Music"],
        muted_buses={"vm_game": "Game"},
        share_channels=0,
        portal_ok=False,
    )
    for issue in result.issues:
        assert issue.title[0].isupper()
        assert "systemctl" not in issue.title
        assert "pactl" not in issue.title
        assert issue.detail.endswith(".")


def test_from_snapshot_reads_the_collector_shape() -> None:
    data: dict[str, object] = {
        "svc": {"roaring-mic-busses.service": "active", "roaring-cmd-rx.service": "failed"},
        "mute": {"vm_game": False, "vm_music": True},
        "vol": {"vm_game": 55, "vm_music": 37, "vm_share": 100},
        "sink_st": {"vm_game": "RUNNING", "vm_music": "IDLE", "vm_share": "IDLE"},
        "vc_portal_ok": True,
        "healed": [],
    }
    result = health.from_snapshot(data, bus_labels=LABELS, share_channels=4)
    keys = {i.key for i in result.issues}
    assert keys == {"unit:roaring-cmd-rx.service", "mute:vm_music"}


def test_from_snapshot_flags_a_missing_bus() -> None:
    data: dict[str, object] = {
        "vol": {"vm_game": 55, "vm_share": 100},
        "sink_st": {"vm_game": "RUNNING"},
        "vc_portal_ok": True,
    }
    result = health.from_snapshot(data, bus_labels=LABELS, share_channels=2)
    assert [i.key for i in result.of("critical")] == ["sink:Share bus"]


def test_from_snapshot_degrades_on_an_unexpected_shape() -> None:
    """A shape change must not raise inside a refresh tick."""
    result = health.from_snapshot({"svc": "nonsense", "mute": 3}, bus_labels=LABELS)
    assert result.severity == "unknown"
    assert not result.of("critical")
