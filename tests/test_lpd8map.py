"""The LPD8 remapper edits the script the hardware service actually reads."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

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
    if os.name == "nt":
        from hearth import paths

        paths.ensure_private_file(script)
    else:
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


def test_lpd8_edit_ignores_a_preexisting_fixed_temp_symlink(script, tmp_path):
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    fixed_temp = script.with_name(script.name + ".hearth-tmp")
    fixed_temp.symlink_to(victim)
    original_mode = script.stat().st_mode & 0o777

    assert lpd8map.set_knob("CC_VM_MUSIC", 2, script) is True

    assert victim.read_text(encoding="utf-8") == "leave this alone"
    assert fixed_temp.is_symlink()
    assert lpd8map.read_map(script)["CC_VM_MUSIC"] == 1
    assert script.stat().st_mode & 0o777 == original_mode
    assert list(tmp_path.glob("lpd8_mixer.sh.tmp.*")) == []


def test_lpd8_edit_refuses_a_hard_linked_lock_without_chmodding_its_target(script, tmp_path):
    original_script = script.read_text(encoding="utf-8")
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep this file", encoding="utf-8")
    original_mode = victim.stat().st_mode & 0o777
    lock_path = script.with_name(f".{script.name}.lock")
    os.link(victim, lock_path)

    with pytest.raises(OSError, match="hard-linked"):
        lpd8map.set_knob("CC_VM_MUSIC", 2, script)

    assert script.read_text(encoding="utf-8") == original_script
    assert victim.read_text(encoding="utf-8") == "keep this file"
    assert victim.stat().st_mode & 0o777 == original_mode
    assert victim.stat().st_nlink == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows ownership uses ACLs rather than st_uid")
def test_lpd8_edit_refuses_a_script_owned_by_another_user(script, monkeypatch):
    original = script.read_text(encoding="utf-8")
    real_lstat = Path.lstat

    def report_foreign_owner(path):
        metadata = real_lstat(path)
        if path == script:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=metadata.st_uid + 1,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", report_foreign_owner)
    with pytest.raises(PermissionError, match="not owned by this user"):
        lpd8map.set_knob("CC_VM_MUSIC", 2, script)
    assert script.read_text(encoding="utf-8") == original


def test_lpd8_edit_refuses_a_symlink_lock_file(script, tmp_path):
    original = script.read_text(encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    lock_path = script.with_name(f".{script.name}.lock")
    lock_path.symlink_to(victim)

    with pytest.raises(OSError):
        lpd8map.set_knob("CC_VM_MUSIC", 2, script)

    assert script.read_text(encoding="utf-8") == original
    assert victim.read_text(encoding="utf-8") == "leave this alone"


def test_concurrent_lpd8_edits_to_different_constants_do_not_get_lost(script):
    # Make the read/modify/write window large enough to expose the old
    # last-writer-wins race without using timing sleeps.
    script.write_text(SCRIPT + "# padding\n" * 20_000, encoding="utf-8")
    start = Barrier(3)

    def edit(const: str, knob: int) -> bool:
        start.wait()
        return lpd8map.set_knob(const, knob, script)

    with ThreadPoolExecutor(max_workers=2) as pool:
        music = pool.submit(edit, "CC_VM_MUSIC", 2)
        chat = pool.submit(edit, "CC_VM_CHAT", 3)
        start.wait()
        assert music.result()
        assert chat.result()

    mapping = lpd8map.read_map(script)
    assert mapping["CC_VM_MUSIC"] == 1
    assert mapping["CC_VM_CHAT"] == 2
    if os.name == "nt":
        from hearth import paths

        paths.ensure_private_file(script)
        paths.ensure_private_file(script.parent / f".{script.name}.lock")
    else:
        assert script.stat().st_mode & 0o111
        assert (script.parent / f".{script.name}.lock").stat().st_mode & 0o777 == 0o600
    assert list(script.parent.glob("lpd8_mixer.sh.tmp.*")) == []


def test_conflicts_flag_one_knob_driving_two_buses():
    assert lpd8map.conflicts({"A": 4, "B": 4, "C": 5}) == {4: ["A", "B"]}
    assert lpd8map.conflicts({"A": 4, "C": 5}) == {}
