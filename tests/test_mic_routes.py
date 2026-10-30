"""Tests for the mic router config Hearth now edits.

This file is sourced by a shell daemon every three seconds, so the two
things worth proving are that an edit changes exactly what it claims to
and that nothing else in the file moves.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from hearth import mic_routes

SAMPLE = """# 9/1/2026-1
# Summary:
# - Controls which raw mic(s) feed B1 and B2.

B1_ROUTE="both"
B2_ROUTE="astro"

# loopback stability knobs
LATENCY_MSEC="10"
RATE="48000"

B1_ACTIVE="false"

B2_ACTIVE="false"
"""


def test_parses_quoted_shell_assignments() -> None:
    values = mic_routes.parse(SAMPLE)
    assert values["B1_ROUTE"] == "both"
    assert values["LATENCY_MSEC"] == "10"


def test_comments_are_not_settings() -> None:
    assert "#" not in "".join(mic_routes.parse(SAMPLE))


def test_editing_keeps_comments_and_order() -> None:
    edited = mic_routes.apply_edits(SAMPLE, {"B1_ROUTE": "sm7b"})
    assert edited.splitlines()[0] == "# 9/1/2026-1"
    assert 'B1_ROUTE="sm7b"' in edited
    assert 'B2_ROUTE="astro"' in edited
    assert "# loopback stability knobs" in edited


def test_a_new_key_is_appended_rather_than_lost() -> None:
    edited = mic_routes.apply_edits(SAMPLE, {"CARLA_TO_B2": "true"})
    assert mic_routes.parse(edited)["CARLA_TO_B2"] == "true"


def test_edit_normalizes_every_duplicate_assignment_of_a_known_key() -> None:
    text = (
        'B2_ROUTE="astro"\n'
        "# a later assignment currently overrides the first one\n"
        'B2_ROUTE="sm7b"\n'
    )

    edited = mic_routes.apply_edits(text, {"B2_ROUTE": "none"})

    assert edited.count('B2_ROUTE="none"') == 2
    assert 'B2_ROUTE="astro"' not in edited
    assert 'B2_ROUTE="sm7b"' not in edited
    assert mic_routes.parse(edited)["B2_ROUTE"] == "none"


def test_no_edits_leaves_the_file_byte_identical() -> None:
    assert mic_routes.apply_edits(SAMPLE, {}) == SAMPLE


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("both", {"sm7b", "astro"}),
        ("sm7b", {"sm7b"}),
        ("astro", {"astro"}),
        ("none", set()),
        ("", set()),
    ],
)
def test_route_values_read_as_sets(route: str, expected: set[str]) -> None:
    assert mic_routes.tokens(route) == expected


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        ({"sm7b", "astro"}, "both"),
        ({"sm7b"}, "sm7b"),
        ({"astro"}, "astro"),
        (set(), "none"),
    ],
)
def test_sets_compose_back_to_route_values(active: set[str], expected: str) -> None:
    assert mic_routes.compose(active) == expected


def test_a_route_behind_a_closed_gate_is_not_enabled() -> None:
    """The daemon forces the route to none when *_ACTIVE is false.

    Showing those ticks as on is exactly the lie this round removed.
    """
    values = mic_routes.parse(SAMPLE)
    assert mic_routes.raw_enabled(values, "mic_b1", "sm7b_mono") is False


def test_enabling_a_source_opens_the_gate() -> None:
    values = mic_routes.parse(SAMPLE)
    edits = mic_routes.raw_edits(values, "mic_b1", "sm7b_mono", True)
    assert edits == {"B1_ROUTE": "sm7b", "B1_ACTIVE": "true"}


def test_enabling_a_second_source_gives_both() -> None:
    values = mic_routes.parse(mic_routes.apply_edits(SAMPLE, {"B1_ACTIVE": "true"}))
    edits = mic_routes.raw_edits(values, "mic_b1", "astro_mic_48k", True)
    assert edits["B1_ROUTE"] == "both"


def test_turning_off_the_last_source_closes_the_gate() -> None:
    values = mic_routes.parse(
        mic_routes.apply_edits(SAMPLE, {"B1_ACTIVE": "true", "B1_ROUTE": "sm7b"})
    )
    edits = mic_routes.raw_edits(values, "mic_b1", "sm7b_mono", False)
    assert edits == {"B1_ROUTE": "none", "B1_ACTIVE": "false"}


def test_unknown_buses_and_sources_change_nothing() -> None:
    values = mic_routes.parse(SAMPLE)
    assert mic_routes.raw_edits(values, "vm_game", "sm7b_mono", True) == {}
    assert mic_routes.raw_edits(values, "mic_b1", "LifeCam", True) == {}


def test_carla_defaults_to_b1_only() -> None:
    """B2 being a silent copy of B1 unless asked for was the surprise."""
    values = mic_routes.parse(SAMPLE)
    assert mic_routes.carla_enabled(values, "mic_b1") is True
    assert mic_routes.carla_enabled(values, "mic_b2") is False


def test_a_stored_carla_choice_wins_over_the_default() -> None:
    values = mic_routes.parse(mic_routes.apply_edits(SAMPLE, {"CARLA_TO_B1": "false"}))
    assert mic_routes.carla_enabled(values, "mic_b1") is False


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is True
    assert mic_routes.read(conf)["B2_ROUTE"] == "none"
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_write_refuses_to_overwrite_an_unreadable_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    original = conf.read_text(encoding="utf-8")
    open_file = mic_routes.os.open

    def unreadable(path, flags, *args, **kwargs):
        if Path(path) == conf:
            raise PermissionError("simulated read failure")
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(mic_routes.os, "open", unreadable)

    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is False
    assert conf.read_text(encoding="utf-8") == original


def test_write_does_not_follow_a_preexisting_temp_symlink(tmp_path: Path) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone")
    fixed_temp = conf.with_suffix(conf.suffix + ".tmp")
    fixed_temp.symlink_to(victim)

    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is True
    assert victim.read_text(encoding="utf-8") == "leave this alone"
    assert mic_routes.read(conf)["B2_ROUTE"] == "none"


def test_write_refuses_a_symlink_config_without_touching_its_target(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "actual.conf"
    victim.write_text(SAMPLE, encoding="utf-8")
    original = victim.read_text(encoding="utf-8")
    config_link = tmp_path / "roaring_mic_router.conf"
    config_link.symlink_to(victim)

    assert mic_routes.write({"B2_ROUTE": "none"}, config_link) is False
    assert config_link.is_symlink()
    assert victim.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("roaring_mic_router.conf.tmp.*")) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"B1_ROUTE": "$(touch /tmp/hearth-route-injection-test)"},
        {"B2_ROUTE": 'none" ; touch /tmp/hearth-route-injection-test; #'},
        {"B1_ACTIVE": "$(touch /tmp/hearth-route-injection-test)"},
        {"CARLA_TO_B2": "sometimes"},
        {"UNOWNED_KEY": "true"},
    ],
)
def test_write_rejects_unknown_keys_and_shell_unsafe_values(
    tmp_path: Path, updates: dict[str, str]
) -> None:
    config = tmp_path / "roaring_mic_router.conf"
    config.write_text(SAMPLE, encoding="utf-8")
    original = config.read_text(encoding="utf-8")

    assert mic_routes.write(updates, config) is False
    assert config.read_text(encoding="utf-8") == original
    assert not (tmp_path / "hearth-route-injection-test").exists()
    assert list(tmp_path.glob("roaring_mic_router.conf.tmp.*")) == []


def test_write_refuses_a_hard_linked_lock_without_chmodding_its_target(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    original = conf.read_text(encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    original_mode = victim.stat().st_mode & 0o777
    (tmp_path / ".roaring_mic_router.conf.lock").hardlink_to(victim)

    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is False
    assert conf.read_text(encoding="utf-8") == original
    assert victim.read_text(encoding="utf-8") == "leave this alone"
    assert victim.stat().st_mode & 0o777 == original_mode


def test_write_refuses_a_config_owned_by_another_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    original = conf.read_text(encoding="utf-8")
    real_open = os.open
    real_fstat = os.fstat
    target_fds: set[int] = set()

    def tracking_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path) == conf:
            target_fds.add(fd)
        return fd

    def report_foreign_owner(fd):
        metadata = real_fstat(fd)
        if fd in target_fds:
            return SimpleNamespace(st_mode=metadata.st_mode, st_uid=os.getuid() + 1)
        return metadata

    monkeypatch.setattr(mic_routes.os, "open", tracking_open)
    monkeypatch.setattr(mic_routes.os, "fstat", report_foreign_owner)

    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is False
    assert conf.read_text(encoding="utf-8") == original


def test_write_refuses_a_symlink_lock_file_without_touching_its_target(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    original = conf.read_text(encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    (tmp_path / ".roaring_mic_router.conf.lock").symlink_to(victim)

    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is False
    assert conf.read_text(encoding="utf-8") == original
    assert victim.read_text(encoding="utf-8") == "leave this alone"


def test_concurrent_mic_route_writes_use_independent_atomic_temp_files(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    # Make the shared read/merge/write window large enough for the two writers
    # to overlap reliably without depending on scheduling sleeps.
    conf.write_text(SAMPLE + "# padding\n" * 20_000, encoding="utf-8")
    start = Barrier(3)

    def write_one(updates: dict[str, str]) -> bool:
        start.wait()
        return mic_routes.write(updates, conf)

    updates = [
        {"CARLA_TO_B1": "false"},
        {"CARLA_TO_B2": "true"},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write_one, item) for item in updates]
        start.wait()
        results = [future.result() for future in futures]

    assert all(results)
    loaded = mic_routes.read(conf)
    assert loaded["CARLA_TO_B1"] == "false"
    assert loaded["CARLA_TO_B2"] == "true"
    assert "B1_ROUTE" in loaded and "B2_ROUTE" in loaded
    assert list(tmp_path.glob("*.tmp.*")) == []
    assert (tmp_path / ".roaring_mic_router.conf.lock").stat().st_mode & 0o777 == 0o600


def test_writing_creates_the_file_when_it_is_missing(tmp_path: Path) -> None:
    conf = tmp_path / "nested" / "router.conf"
    assert mic_routes.write({"CARLA_TO_B2": "true"}, conf) is True
    assert mic_routes.read(conf) == {"CARLA_TO_B2": "true"}


def test_a_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert mic_routes.read(tmp_path / "absent.conf") == {}


def test_invalid_utf8_config_reads_as_empty_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "roaring_mic_router.conf"
    original = bytes([66, 49, 95, 82, 79, 85, 84, 69, 61, 34, 115, 109, 55, 98, 34, 10, 255, 254])
    conf.write_bytes(original)

    assert mic_routes.read(conf) == {}
    assert mic_routes.write({"B2_ROUTE": "none"}, conf) is False
    assert conf.read_bytes() == original


def test_flow_names_what_actually_feeds_each_bus() -> None:
    values = mic_routes.parse(
        mic_routes.apply_edits(SAMPLE, {"B1_ACTIVE": "true", "B1_ROUTE": "sm7b"})
    )
    stream, chat = mic_routes.flow_lines(values)
    assert stream == "Stream: SM7B via Carla + SM7B raw \u2192 b1_mic"
    # B2_ACTIVE is false in the sample, so its route is not in effect and
    # Carla does not feed B2 by default: the line must not invent a source.
    assert chat == "Chat: nothing \u2192 b2_mic"


def test_flow_marks_the_bus_the_laptop_is_listening_to() -> None:
    values = mic_routes.parse(SAMPLE)
    lines = mic_routes.flow_lines(values, moonlight_source="b2_mic", laptop_host="thinkpad")
    assert lines[1].endswith("b2_mic \u2192 laptop (thinkpad)")
    assert "laptop" not in lines[0]


def test_carla_live_link_is_not_changed_when_config_save_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mic_routes, "write", lambda *_args, **_kwargs: False)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(mic_routes, "link_carla", lambda bus, on: calls.append((bus, on)) or True)

    assert mic_routes.set_carla("mic_b1", True) is False
    assert calls == []


def test_carla_link_reports_missing_or_unexecutable_pw_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mic_routes.proc, "have", lambda _program: True)
    calls: list[list[str]] = []

    def cannot_run(argv, **_kwargs):
        calls.append(list(argv))
        from hearth.errors import CommandError

        raise CommandError(argv, stderr="pw-link is not executable")

    monkeypatch.setattr(mic_routes.proc, "run", cannot_run)

    assert mic_routes.link_carla("mic_b1", True) is False
    assert len(calls) == len(mic_routes.CARLA_PORTS)


def test_carla_live_link_launch_failure_does_not_undo_a_saved_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mic_routes, "write", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(mic_routes, "link_carla", lambda *_args, **_kwargs: False)

    assert mic_routes.set_carla("mic_b1", False) is True


def test_carla_config_is_saved_before_applying_the_live_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    def save(updates, path=None):
        assert updates == {"CARLA_TO_B2": "false"}
        order.append("save")
        return True

    def link(bus, on):
        assert (bus, on) == ("mic_b2", False)
        order.append("link")
        return True

    monkeypatch.setattr(mic_routes, "write", save)
    monkeypatch.setattr(mic_routes, "link_carla", link)

    assert mic_routes.set_carla("mic_b2", False) is True
    assert order == ["save", "link"]


def test_a_host_called_laptop_is_not_printed_twice() -> None:
    values = mic_routes.parse(SAMPLE)
    lines = mic_routes.flow_lines(values, moonlight_source="b2_mic", laptop_host="laptop")
    assert lines[1].endswith("b2_mic \u2192 laptop")
