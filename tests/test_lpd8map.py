"""The LPD8 remapper edits the script the hardware service actually reads."""

from __future__ import annotations

import pytest

from hearth import lpd8map

SCRIPT = """#!/usr/bin/env bash
# ---- Knob CC mapping ----
CC_VM_MUSIC=4      # k5
CC_VM_CHAT=5       # k6
CC_VM_GAME=6       # k7
CC_MIC_VOL=7       # k8
CC_VM_GAME_ALT=0   # k1
echo done
"""


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "lpd8_mixer.sh"
    path.write_text(SCRIPT)
    path.chmod(0o755)
    return path


def test_read_map_reports_every_binding(script):
    assert lpd8map.read_map(script) == {
        "CC_VM_MUSIC": 4,
        "CC_VM_CHAT": 5,
        "CC_VM_GAME": 6,
        "CC_MIC_VOL": 7,
        "CC_VM_GAME_ALT": 0,
    }


def test_knob_numbers_are_the_printed_ones():
    assert lpd8map.knob_to_cc(1) == 0
    assert lpd8map.knob_to_cc(8) == 7
    assert lpd8map.cc_to_knob(7) == 8
    assert lpd8map.cc_to_knob(99) is None
    with pytest.raises(ValueError):
        lpd8map.knob_to_cc(9)


def test_set_knob_rewrites_only_that_constant(script):
    assert lpd8map.set_knob("CC_VM_MUSIC", 2, script) is True
    text = script.read_text()
    assert "CC_VM_MUSIC=1      # k5" in text
    assert "CC_VM_CHAT=5" in text
    assert lpd8map.read_map(script)["CC_VM_MUSIC"] == 1


def test_set_knob_keeps_the_script_executable(script):
    lpd8map.set_knob("CC_VM_CHAT", 3, script)
    assert script.stat().st_mode & 0o111


def test_setting_the_same_knob_twice_is_a_no_op(script):
    assert lpd8map.set_knob("CC_VM_CHAT", 6, script) is False


def test_unknown_binding_is_refused(script):
    with pytest.raises(ValueError):
        lpd8map.set_knob("CC_NOT_A_THING", 1, script)


def test_missing_constant_is_refused_rather_than_appended(tmp_path):
    path = tmp_path / "lpd8_mixer.sh"
    path.write_text("#!/usr/bin/env bash\necho hi\n")
    with pytest.raises(ValueError):
        lpd8map.set_knob("CC_VM_CHAT", 1, path)
    assert "CC_VM_CHAT" not in path.read_text()


def test_a_missing_script_reads_as_unknown_not_as_unbound(tmp_path):
    assert lpd8map.read_map(tmp_path / "nope.sh") == {}


def test_conflicts_flag_one_knob_driving_two_buses():
    assert lpd8map.conflicts({"A": 4, "B": 4, "C": 5}) == {4: ["A", "B"]}
    assert lpd8map.conflicts({"A": 4, "C": 5}) == {}
