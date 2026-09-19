"""Tests for the unit model, against systemd output recorded from this rig."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import units

FIXTURES = Path(__file__).parent / "fixtures"
SHOW = FIXTURES / "systemctl_show_units.txt"
LIST = FIXTURES / "systemctl_list_units.txt"


@pytest.fixture
def rig() -> list[units.Unit]:
    return units.parse_show(SHOW.read_text())


def _by_name(rig: list[units.Unit], name: str) -> units.Unit:
    for unit in rig:
        if unit.name == name:
            return unit
    raise AssertionError(f"{name} missing from the fixture")


def test_every_installed_unit_is_seen(rig: list[units.Unit]) -> None:
    """The hardcoded list is gone; discovery is what stops "12/12" lying."""
    assert len(rig) > 12
    assert all(unit.name for unit in rig)


def test_a_finished_oneshot_is_not_a_fault(rig: list[units.Unit]) -> None:
    """vm-share and carla-patch exit on success; three bugs came from this."""
    for name in ("roaring-vm-share.service", "roaring-carla-patch.service"):
        unit = _by_name(rig, name)
        assert unit.kind == "oneshot"
        assert unit.active_state == "inactive"
        assert unit.healthy is True
        assert "done its work" in unit.status_text


def test_a_waiting_timer_is_healthy(rig: list[units.Unit]) -> None:
    timer = _by_name(rig, "roaring-carla-backup.timer")
    assert timer.kind == "timer"
    assert timer.sub_state == "waiting"
    assert timer.healthy is True


def test_a_listening_socket_is_healthy(rig: list[units.Unit]) -> None:
    sock = _by_name(rig, "roaring-presenced.socket")
    assert sock.kind == "socket"
    assert sock.healthy is True
    assert "listening" in sock.status_text


def test_a_socket_activated_service_is_idle_not_broken(rig: list[units.Unit]) -> None:
    """The first run of this suite reported presenced as a stopped daemon.

    It is ``Type=exec`` and ``inactive (dead)`` because its socket starts it
    on demand, which is exactly the false alarm this module exists to stop.
    """
    unit = _by_name(rig, "roaring-presenced.service")
    assert unit.kind == "ondemand"
    assert unit.active_state == "inactive"
    assert unit.healthy is True
    assert unit.status_text == "presenced starts when it is needed"


def test_a_stopped_daemon_is_a_fault() -> None:
    unit = units.Unit("roaring-mic-busses.service", "daemon", "inactive", "dead")
    assert unit.healthy is False
    assert unit.status_text == "mic busses is stopped"


def test_a_failed_oneshot_is_still_a_fault() -> None:
    unit = units.Unit("roaring-vm-share.service", "oneshot", "failed", "failed")
    assert unit.healthy is False
    assert "failed" in unit.status_text


def test_an_uninstalled_unit_is_unknown_not_broken() -> None:
    assert units.kind_of("roaring-nope.service", "", loaded=False) == "unknown"
    unit = units.Unit("roaring-nope.service", "unknown", "inactive")
    assert unit.healthy is None
    assert unit not in units.problems([unit])


def test_problems_excludes_the_healthy_shapes(rig: list[units.Unit]) -> None:
    """On this rig nothing is wrong, so nothing may be reported."""
    assert units.problems(rig) == []
    assert units.summary(rig).startswith("All ")


def test_the_count_only_claims_what_it_can_judge(rig: list[units.Unit]) -> None:
    """Oneshots and uninstalled units are excluded from the tally."""
    judged = [u for u in rig if u.healthy is not None and u.kind not in ("oneshot", "ondemand")]
    assert units.summary(rig) == f"All {len(judged)} audio services are running"
    assert len(judged) < len(rig)


def test_summary_names_the_worst_problem_not_a_count() -> None:
    broken = [
        units.Unit("roaring-mic-busses.service", "daemon", "failed", "failed"),
        units.Unit("roaring-vm-sinks.service", "daemon", "inactive", "dead"),
        units.Unit("lpd8-mixer.service", "daemon", "active", "running"),
    ]
    assert units.summary(broken) == "mic busses failed (+1 more)"


def test_template_units_are_not_instances() -> None:
    """``roaring-cd-presence@.service`` has no state and must be skipped."""
    text = (
        "roaring-cd-presence@.service loaded inactive dead\n"
        "roaring-cmd-rx.service loaded active running"
    )
    assert units.parse_names(text) == ["roaring-cmd-rx.service"]


def test_names_parse_from_the_recorded_listing() -> None:
    names = units.parse_names(LIST.read_text())
    assert "roaring-presenced.socket" in names
    assert "roaring-carla-backup.timer" in names
    assert all("@." not in name for name in names)


def test_missing_properties_do_not_shift_between_units() -> None:
    """A timer has no Type=; that must not borrow the next unit's value."""
    text = (
        "Id=roaring-a.timer\nLoadState=loaded\nActiveState=active\nSubState=waiting\n"
        "\n"
        "Id=roaring-b.service\nLoadState=loaded\nActiveState=active\nSubState=running\nType=simple\n"
    )
    parsed = units.parse_show(text)
    assert [u.kind for u in parsed] == ["timer", "daemon"]


def test_no_internal_words_in_status_text(rig: list[units.Unit]) -> None:
    for unit in rig:
        text = unit.status_text
        for token in (".service", ".timer", ".socket", "roaring-", "systemctl"):
            assert token not in text, f"{token!r} leaked into {text!r}"
