"""Pad remapping rewrites every pad-mode table, or the pad moves in one mode only."""

from __future__ import annotations

import pytest

from hearth import lpd8map

SCRIPT = """#!/usr/bin/env bash
# ---- NOTE pads (PAD mode) ----
NOTE_PAD1=60
NOTE_PAD2=61
NOTE_PAD3=62
NOTE_PAD4=63
NOTE_PAD5=64
NOTE_PAD6=65
NOTE_PAD7=66
NOTE_PAD8=67

# ---- PROG pads (PROG mode) ----
PROG_1=0
PROG_2=1
PROG_3=2
PROG_4=3
PROG_5=4
PROG_6=5
PROG_7=6
PROG_8=7

# ---- CC pads (CC mode) ----
CC_PAD8=8
CC_PAD1=9
CC_PAD2=10
CC_PAD3=11
CC_PAD4=12
CC_PAD5=13
CC_PAD6=14
CC_PAD7=15
echo done
"""


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "lpd8_mixer.sh"
    path.write_text(SCRIPT)
    path.chmod(0o755)
    return path


def test_pad_numbers_match_what_the_hardware_sends():
    assert lpd8map.pad_to_note(1) == 60
    assert lpd8map.pad_to_note(8) == 67
    assert lpd8map.note_to_pad(62) == 3
    assert lpd8map.note_to_pad(200) is None
    # Pad 8 wraps back to CC 8; the rest run 9..15.
    assert lpd8map.pad_to_cc(1) == 9
    assert lpd8map.pad_to_cc(7) == 15
    assert lpd8map.pad_to_cc(8) == 8
    assert lpd8map.pad_to_prog(1) == 0
    with pytest.raises(ValueError):
        lpd8map.pad_to_note(9)


def test_read_pad_map_is_one_to_one_by_default(script):
    assert lpd8map.read_pad_map(script) == {n: n for n in range(1, 9)}


def test_read_pad_map_survives_a_missing_script(tmp_path):
    assert lpd8map.read_pad_map(tmp_path / "gone.sh") == {}


def test_set_pad_moves_every_mode_table(script):
    assert lpd8map.set_pad(1, 4, script) is True
    text = script.read_text()
    assert "NOTE_PAD1=63" in text
    assert "CC_PAD1=12" in text
    assert "PROG_1=3" in text
    # Nothing else moved.
    assert "NOTE_PAD2=61" in text
    assert lpd8map.read_pad_map(script)[1] == 4


def test_set_pad_keeps_the_script_executable(script):
    lpd8map.set_pad(2, 5, script)
    assert script.stat().st_mode & 0o111


def test_setting_the_same_pad_twice_is_a_no_op(script):
    assert lpd8map.set_pad(3, 3, script) is False


def test_unknown_slot_is_refused(script):
    with pytest.raises(ValueError):
        lpd8map.set_pad(99, 1, script)


def test_missing_note_constant_fails_loudly(tmp_path):
    path = tmp_path / "lpd8_mixer.sh"
    path.write_text("#!/usr/bin/env bash\nCC_PAD1=9\n")
    path.chmod(0o755)
    with pytest.raises(ValueError):
        lpd8map.set_pad(1, 2, path)
    # The CC table must not have moved on its own.
    assert "CC_PAD1=9" in path.read_text()


def test_two_slots_on_one_pad_are_reported(script):
    lpd8map.set_pad(2, 1, script)
    assert lpd8map.pad_conflicts(lpd8map.read_pad_map(script)) == {1: [1, 2]}


def test_no_conflict_when_every_pad_is_distinct(script):
    assert lpd8map.pad_conflicts(lpd8map.read_pad_map(script)) == {}


def test_every_advertised_slot_exists_in_the_real_script():
    """The window offers a row per slot; each row must have a constant."""
    text = SCRIPT
    for pad_slot in lpd8map.PAD_SLOTS:
        assert f"{pad_slot.note_const}=" in text
        assert f"{pad_slot.cc_const}=" in text
        assert f"{pad_slot.prog_const}=" in text
