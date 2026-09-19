"""Tests for the control-surface wrapper, against recorded device listings."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import midi

FIXTURES = Path(__file__).parent / "fixtures"
SEQ = FIXTURES / "aseqdump_l.txt"
RAW = FIXTURES / "amidi_l.txt"


@pytest.fixture
def seq() -> list[midi.SeqPort]:
    return midi.parse_seq_ports(SEQ.read_text())


@pytest.fixture
def raw() -> list[midi.RawPort]:
    return midi.parse_raw_ports(RAW.read_text())


def test_parses_the_recorded_sequencer_list(seq: list[midi.SeqPort]) -> None:
    addresses = {port.address: port.client_name for port in seq}
    assert addresses["40:0"] == "LPD8"
    assert addresses["36:0"] == "Launchpad Mini"


def test_client_names_with_spaces_survive(seq: list[midi.SeqPort]) -> None:
    """ "Launchpad Mini" must not be shredded into two fields."""
    port = midi.find_seq(seq, "Launchpad")
    assert port is not None
    assert port.client_name == "Launchpad Mini"
    assert port.port_name == "Launchpad Mini MIDI 1"


def test_header_row_is_not_a_device(seq: list[midi.SeqPort]) -> None:
    assert all(port.client_name != "Client name" for port in seq)


def test_parses_raw_devices(raw: list[midi.RawPort]) -> None:
    devices = {port.name: port.device for port in raw}
    assert devices["LPD8 MIDI 1"] == "hw:6,0,0"
    assert devices["Launchpad Mini MIDI 1"] == "hw:5,0,0"


def test_both_surfaces_are_seen_on_this_rig(
    seq: list[midi.SeqPort], raw: list[midi.RawPort]
) -> None:
    states = midi.analyze(seq, raw, service_states={midi.LPD8_UNIT: True})
    assert [s.connected for s in states] == [True, True]
    lpd8 = states[0]
    assert lpd8.address == "40:0"
    assert lpd8.device == "hw:6,0,0"
    assert lpd8.working


def test_the_launchpad_is_reported_as_padfires(
    seq: list[midi.SeqPort], raw: list[midi.RawPort]
) -> None:
    """It is plugged in and driven by a sibling app, not by Hearth."""
    launchpad = midi.analyze(seq, raw)[1]
    assert launchpad.surface.owner == "Padfire"
    assert launchpad.surface.unit is None
    assert "Padfire" in launchpad.summary


def test_connected_but_service_stopped_is_its_own_state(seq: list[midi.SeqPort]) -> None:
    """Hardware present and controls dead is the confusing case; name it."""
    lpd8 = midi.analyze(seq, service_states={midi.LPD8_UNIT: False})[0]
    assert lpd8.connected
    assert not lpd8.working
    assert "will do nothing" in lpd8.summary


def test_unplugged_is_a_designed_state() -> None:
    lpd8 = midi.analyze([], service_states={midi.LPD8_UNIT: True})[0]
    assert lpd8.connected is False
    assert lpd8.summary == "No LPD8 pad controller connected - plug it in to use the knobs and pads"


def test_unknown_is_not_unplugged() -> None:
    """A probe that could not run must never read as "no device"."""
    for state in midi.analyze(None):
        assert state.connected is None
        assert not state.working
        assert state.summary.startswith("Cannot tell")


def test_summary_prefers_the_broken_owned_surface(
    seq: list[midi.SeqPort], raw: list[midi.RawPort]
) -> None:
    states = midi.analyze(seq, raw, service_states={midi.LPD8_UNIT: False})
    assert "will do nothing" in midi.summary(states)


def test_summary_is_quiet_when_everything_is_live(
    seq: list[midi.SeqPort], raw: list[midi.RawPort]
) -> None:
    states = midi.analyze(seq, raw, service_states={midi.LPD8_UNIT: True})
    assert midi.summary(states) == "LPD8 pad controller and Launchpad Mini connected"


def test_the_control_legend_covers_every_pad_and_knob() -> None:
    pads = [c.number for c in midi.LPD8_PADS]
    assert pads == [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(c.kind == "knob" for c in midi.LPD8_KNOBS)


def test_no_internal_names_reach_the_user() -> None:
    """No sink names, no MIDI jargon, no unit names in anything shown."""
    banned = ("vm_", "pactl", "systemctl", "CC", "hw:", "aseqdump", ".service")
    texts = [c.action for c in midi.LPD8_KNOBS + midi.LPD8_PADS]
    texts.append(midi.LED_NOTE)
    texts += [s.label for s in midi.SURFACES]
    for text in texts:
        for token in banned:
            assert token not in text, f"{token!r} leaked into {text!r}"
