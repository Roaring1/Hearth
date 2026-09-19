"""Tests for hearth.share, run against text recorded from the real rig."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hearth import share
from hearth.errors import AudioError

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_list_reads_every_bus() -> None:
    inv = share.parse_list(_fixture("share_list.txt"))
    aliases = [b.alias for b in inv.buses]
    assert aliases == [
        "game",
        "chat",
        "music",
        "laptop",
        "share",
        "b1",
        "b2",
        "a1",
        "a2",
        "a3",
    ]
    assert inv.selected == ("game", "music")


def test_parse_list_keeps_multiword_labels() -> None:
    """A bus label contains spaces, which a naive split would shred."""
    inv = share.parse_list(_fixture("share_list.txt"))
    by_alias = {b.alias: b for b in inv.buses}
    assert by_alias["a1"].bus == "Astro A50 Game"
    assert by_alias["a3"].bus == "Scarlett Solo"
    assert by_alias["game"].state == "MISSING"


def test_parse_list_flags_the_echo_buses() -> None:
    inv = share.parse_list(_fixture("share_list.txt"))
    unsafe = {b.alias for b in inv.buses if b.unsafe}
    assert unsafe == {"chat", "a2"}


def test_parse_list_ignores_the_empty_app_placeholder() -> None:
    inv = share.parse_list(_fixture("share_list.txt"))
    assert inv.apps == ()


def test_parse_status_absent_sink_means_not_sharing() -> None:
    st = share.parse_status(_fixture("share_status.txt"))
    assert st.selected == ("game", "music")
    assert st.sharing is False
    assert "not sharing" in st.detail


def test_status_summary_is_written_for_a_human() -> None:
    st = share.Status(("game", "music"), sharing=False)
    assert st.summary == "Ready to share Game, Music when you start a screen share"
    assert share.Status(("game",), sharing=True).summary == "Sharing Game"
    assert "silence" in share.Status().summary


def test_selection_reads_the_state_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "share_select.json"
    state.write_text(json.dumps({"targets": ["game", "b1"]}), encoding="utf-8")
    monkeypatch.setattr(share, "selection_file", lambda: state)
    assert share.selection() == ["game", "b1"]


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"targets": 3}'])
def test_selection_degrades_on_junk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    state = tmp_path / "share_select.json"
    state.write_text(content, encoding="utf-8")
    monkeypatch.setattr(share, "selection_file", lambda: state)
    assert share.selection() == []


def test_selection_missing_file_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(share, "selection_file", lambda: tmp_path / "nope.json")
    assert share.selection() == []


def test_echo_buses_are_refused_without_force() -> None:
    with pytest.raises(AudioError) as excinfo:
        share.check_safe(["game", "chat"])
    assert "hear themselves" in str(excinfo.value)


def test_echo_buses_are_allowed_with_force() -> None:
    share.check_safe(["chat", "a2"], force=True)
    share.check_safe(["game", "music"])


def test_view_degrades_when_the_script_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(share, "available", lambda: False)
    v = share.view()
    assert v.installed is False
    assert "not installed" in v.error
    assert v.status.selected == ()


def test_view_degrades_when_the_script_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(share, "available", lambda: True)

    def boom() -> share.Status:
        raise AudioError("share_ctl status failed: nope")

    monkeypatch.setattr(share, "status", boom)
    v = share.view()
    assert v.installed is True
    assert "nope" in v.error
