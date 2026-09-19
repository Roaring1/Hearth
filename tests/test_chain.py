"""Tests for the microphone effects chain wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import chain
from hearth.audio import links
from hearth.errors import AudioError

FIXTURE = Path(__file__).parent / "fixtures" / "pw_link_list.txt"

HEALTHY = """sm7b_mono:capture_MONO
  |-> Carla:audio-in1
  |-> Carla:audio-in2
Carla:audio-out1
  |-> mic_b1:playback_FL
Carla:audio-out2
  |-> mic_b1:playback_FR
"""

BOTH_BUSES = (
    HEALTHY
    + """Carla:audio-out1
  |-> mic_b2:playback_FL
Carla:audio-out2
  |-> mic_b2:playback_FR
"""
)

FEEDBACK = (
    HEALTHY
    + """mic_b1:monitor_FL
  |-> Carla:audio-in2
mic_b1:monitor_FR
  |-> Carla:audio-in2
"""
)


def graph(text: str) -> list[links.Link]:
    return links.parse(text)


def test_a_working_chain_names_the_bus_it_serves() -> None:
    state = chain.analyze(graph(HEALTHY))
    assert state.present
    assert state.mic_connected
    assert state.working
    assert state.processed_labels == ["Stream"]
    assert state.summary == "Voice effects on for Stream"


def test_both_mic_buses_are_listed_in_bus_order() -> None:
    state = chain.analyze(graph(BOTH_BUSES))
    assert state.processed_labels == ["Stream", "Discord"]
    assert state.summary == "Voice effects on for Stream and Discord"


def test_a_feedback_loop_is_detected_and_named() -> None:
    """Carla's output returning to its input through a mic bus is a loop."""
    state = chain.analyze(graph(FEEDBACK))
    assert state.feedback == ["mic_b1"]
    assert not state.working
    assert state.summary == "Voice effects are feeding back through Stream"


def test_a_missing_carla_says_the_mic_is_raw_not_that_nothing_is_wrong() -> None:
    state = chain.analyze(graph("sm7b_mono:capture_MONO\n  |-> mic_b1:playback_FL\n"))
    assert not state.present
    assert state.summary == "Voice effects are off - your microphone is raw"


def test_carla_up_but_unlinked_is_reported_as_starting() -> None:
    state = chain.analyze([], session_active=True)
    assert not state.present
    assert state.summary == "Voice effects are starting"


def test_carla_running_with_no_microphone_is_its_own_message() -> None:
    text = "Carla:audio-out1\n  |-> mic_b1:playback_FL\n"
    state = chain.analyze(graph(text))
    assert state.present
    assert not state.mic_connected
    assert state.summary == "Your microphone is not reaching the voice effects"


def test_carla_processing_into_nothing_is_reported() -> None:
    text = "sm7b_mono:capture_MONO\n  |-> Carla:audio-in1\n"
    state = chain.analyze(graph(text))
    assert state.present
    assert state.mic_connected
    assert state.summary == "Voice effects are running but not reaching any mic bus"


def test_the_recorded_live_graph_parses_into_a_present_chain() -> None:
    state = chain.analyze(graph(FIXTURE.read_text()))
    assert state.present
    assert state.mic_connected
    assert "mic_b1" in state.outputs


def test_an_unreadable_graph_does_not_claim_the_effects_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Could not look' must not render the same as 'your effects are off'."""

    def boom(**_: object) -> list[links.Link]:
        raise AudioError("pw-link unavailable")

    monkeypatch.setattr(chain.links, "list_links", boom)
    monkeypatch.setattr(chain.services, "is_active", lambda _unit: True)
    state = chain.state()
    assert state.session_active is True
    assert state.summary == "Voice effects are starting"


def test_unit_notes_explain_the_oneshot_and_the_watcher() -> None:
    notes = chain.unit_notes()
    assert "Inactive is normal" in notes[chain.PATCH_UNIT]
    assert notes[chain.SESSION_UNIT].startswith("Runs Carla")
    assert set(notes) == {chain.SESSION_UNIT, chain.PATCH_UNIT, chain.WATCH_UNIT}


def test_no_message_leaks_an_internal_node_name() -> None:
    for text, active in ((HEALTHY, True), (BOTH_BUSES, True), (FEEDBACK, True), ("", False)):
        summary = chain.analyze(graph(text), session_active=active).summary
        assert "mic_b1" not in summary
        assert "mic_b2" not in summary
        assert "Carla" not in summary
