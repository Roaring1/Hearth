"""Tests for the pad LEDs Hearth pushes to the LPD8.

The bytes matter more than they look: they are the same three the shell
script sends, and a disagreement between the two would show up as a lamp
that contradicts the mixer rather than as an error anyone would notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import leds
from hearth.errors import CommandError

FIXTURES = Path(__file__).parent / "fixtures"
RAW = FIXTURES / "amidi_l.txt"


def test_pad_one_is_note_sixty() -> None:
    assert leds.note_for_pad(1) == 60
    assert leds.note_for_pad(8) == 67


@pytest.mark.parametrize("pad", [0, 9, -1])
def test_pads_outside_the_device_are_refused(pad: int) -> None:
    with pytest.raises(ValueError):
        leds.note_for_pad(pad)


def test_message_matches_the_shell_script_format() -> None:
    """Note On, channel 10, velocity 127 -- uppercase hex, space separated."""
    assert leds.message(1, True) == "99 3C 7F"
    assert leds.message(1, False) == "99 3C 00"


def test_channel_is_carried_into_the_status_byte() -> None:
    assert leds.message(2, True, channel=0) == "90 3D 7F"


@pytest.mark.parametrize("channel", [-1, 16])
def test_impossible_channels_are_refused(channel: int) -> None:
    with pytest.raises(ValueError):
        leds.message(1, True, channel=channel)


def test_lit_means_audible() -> None:
    """A muted bus is a dark pad, which is what the script already does."""
    assert leds.message(7, True).endswith("7F")
    assert leds.message(7, False).endswith("00")


def test_pad_lookup_covers_the_three_bound_buses() -> None:
    assert leds.pad_for_sink("vm_music") == 1
    assert leds.pad_for_sink("vm_chat") == 2
    assert leds.pad_for_sink("vm_game") == 7


def test_unbound_buses_have_no_pad() -> None:
    """Laptop and the mic buses are not on the surface; nothing to light."""
    assert leds.pad_for_sink("vm_laptop") is None
    assert leds.pad_for_sink("mic_b1") is None


def test_port_is_read_from_a_real_amidi_listing() -> None:
    assert leds.parse_port(RAW.read_text()) == "hw:6,0,0"


def test_the_other_controller_is_not_mistaken_for_the_lpd8() -> None:
    listing = "Dir Device    Name\nIO  hw:5,0,0  Launchpad Mini MIDI 1\n"
    assert leds.parse_port(listing) is None


def test_classic_lpd8_port_is_not_used_for_led_feedback() -> None:
    """The original hardware is transmit-only for Note messages."""
    assert leds.parse_feedback_port(RAW.read_text()) is None


def test_feedback_port_requires_an_explicit_mkii_label() -> None:
    listing = "Dir Device    Name\nIO  hw:7,0,0  LPD8 mk2 MIDI 1\n"
    assert leds.parse_feedback_port(listing) == "hw:7,0,0"


def test_find_port_skips_classic_lpd8_for_led_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(leds.proc, "have", lambda _name: True)
    monkeypatch.setattr(
        leds.proc,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, out=RAW.read_text()),
    )

    assert leds.find_port() is None


def test_an_empty_listing_is_not_a_port() -> None:
    assert leds.parse_port("") is None


def test_find_port_degrades_to_none_when_amidi_probe_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leds.proc, "have", lambda _name: True)

    def timed_out(_cmd, **_kwargs):
        raise CommandError(["amidi", "-l"], timed_out=True, stderr="timed out")

    monkeypatch.setattr(leds.proc, "run", timed_out)

    assert leds.find_port(timeout=0.1) is None


def test_led_write_returns_false_when_amidi_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leds.proc, "have", lambda _name: True)

    def timed_out(_cmd, **_kwargs):
        raise CommandError(["amidi", "-p", "hw:6,0,0", "-S", "99 42 7F"], timed_out=True)

    monkeypatch.setattr(leds.proc, "run", timed_out)

    assert leds.set_pad("hw:6,0,0", 7, True) is False


def test_writing_to_no_port_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unplugged controller must not turn into a failed command."""
    called: list[list[str]] = []
    monkeypatch.setattr(leds.proc, "run", lambda cmd, **_: called.append(cmd))
    assert leds.set_pad("", 1, True) is False
    assert called == []


def test_set_sink_mute_skips_buses_without_a_pad(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(leds.proc, "run", lambda cmd, **_: called.append(cmd))
    assert leds.set_sink_mute("hw:6,0,0", "vm_laptop", True) is False
    assert called == []
