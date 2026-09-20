"""Tests for the mic router config Hearth now edits.

This file is sourced by a shell daemon every three seconds, so the two
things worth proving are that an edit changes exactly what it claims to
and that nothing else in the file moves.
"""

from __future__ import annotations

from pathlib import Path

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


def test_writing_creates_the_file_when_it_is_missing(tmp_path: Path) -> None:
    conf = tmp_path / "nested" / "router.conf"
    assert mic_routes.write({"CARLA_TO_B2": "true"}, conf) is True
    assert mic_routes.read(conf) == {"CARLA_TO_B2": "true"}


def test_a_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert mic_routes.read(tmp_path / "absent.conf") == {}


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


def test_a_host_called_laptop_is_not_printed_twice() -> None:
    values = mic_routes.parse(SAMPLE)
    lines = mic_routes.flow_lines(values, moonlight_source="b2_mic", laptop_host="laptop")
    assert lines[1].endswith("b2_mic \u2192 laptop")
