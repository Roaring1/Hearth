"""Tests for the companion tool registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import proc, tools
from hearth.errors import AudioError


def test_keys_are_unique() -> None:
    keys = [t.key for t in tools.REGISTRY]
    assert len(keys) == len(set(keys))


def test_every_group_is_known() -> None:
    """A typo in a group name would silently hide a tool from the UI."""
    for tool in tools.REGISTRY:
        assert tool.group in tools.GROUP_ORDER


def test_destructive_tools_require_confirmation() -> None:
    """Anything that stops audio must be marked, or the UI cannot warn."""
    must_confirm = {"restart_stack", "start_with_carla", "astro_target"}
    for key in must_confirm:
        assert tools.get(key).confirm is True


def test_purposes_avoid_jargon() -> None:
    """Copy is aimed at someone who will not open a terminal."""
    for tool in tools.REGISTRY:
        assert tool.purpose.endswith(".")
        assert tool.label[0].isupper()
        assert "pactl" not in tool.purpose
        assert "systemctl" not in tool.purpose


def test_argv_is_a_list_not_a_shell_string() -> None:
    tool = tools.get("noise_status")
    assert tool.argv[-1] == "status"
    assert all(isinstance(part, str) for part in tool.argv)


def test_get_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        tools.get("does_not_exist")


def test_grouped_skips_empty_groups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mic_diag.sh").write_text("#!/bin/sh\n")
    groups = dict(tools.grouped())
    assert list(groups) == ["Diagnostics"]
    assert [t.key for t in groups["Diagnostics"]] == ["mic_diag"]


def test_missing_tools_are_reported_not_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    (tmp_path / "bin").mkdir()
    assert tools.installed() == []
    assert len(tools.missing()) == len(tools.REGISTRY)


def test_run_refuses_a_missing_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    with pytest.raises(AudioError) as excinfo:
        tools.run(tools.get("mic_diag"))
    assert "not installed" in str(excinfo.value)


def test_run_surfaces_a_failing_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mic_diag.sh").write_text("#!/bin/sh\n")

    def fake_run(argv: list[str], **kwargs: object) -> proc.Result:
        return proc.Result(tuple(argv), 3, "", "no microphone found")

    monkeypatch.setattr(tools.proc, "run", fake_run)
    with pytest.raises(AudioError) as excinfo:
        tools.run(tools.get("mic_diag"))
    message = str(excinfo.value)
    assert "exit 3" in message
    assert "no microphone found" in message


def test_target_tools_declare_where_to_look() -> None:
    """A picker with no source to draw from would render as an empty menu."""
    for tool in tools.REGISTRY:
        if tool.needs_target:
            assert tool.target_dir, tool.key


def test_a_target_tool_refuses_to_run_on_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without this the analyser silently studies whatever it defaults to."""
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mic_analyze.py").write_text("#!/bin/sh\n")
    monkeypatch.setattr(tools.proc, "spawn", lambda argv, **kw: None)

    with pytest.raises(AudioError) as excinfo:
        tools.invoke(tools.get("mic_analyze"))
    assert "a recording to analyse" in str(excinfo.value)


def test_a_plain_tool_refuses_a_target() -> None:
    with pytest.raises(AudioError):
        tools.get("mic_diag").argv_for("somewhere")


def test_target_is_appended_to_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mic_analyze.py").write_text("#!/bin/sh\n")
    seen: list[list[str]] = []
    monkeypatch.setattr(tools.proc, "spawn", lambda argv, **kw: seen.append(list(argv)))

    tools.invoke(tools.get("mic_analyze"), tmp_path / "session_1")
    assert seen[0][-1].endswith("session_1")


def test_targets_are_newest_first_and_filtered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    root = tmp_path / "audio_diagnostics"
    root.mkdir()
    (root / "session_20250101T000000Z").mkdir()
    (root / "session_20250201T000000Z").mkdir()
    (root / "sweep.wav").write_text("x")
    (root / "README.md").write_text("x")
    found = [p.name for p in tools.get("mic_analyze").targets()]
    assert found == ["session_20250201T000000Z", "session_20250101T000000Z"]


def test_targets_are_empty_when_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An absent folder is 'nothing yet', not a crash."""
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    assert tools.get("mic_analyze").targets() == []


def test_invoke_spawns_fire_and_forget_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tools.paths, "home", lambda: tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "roaring_restart_everything.sh").write_text("#!/bin/sh\n")
    seen: list[list[str]] = []
    monkeypatch.setattr(tools.proc, "spawn", lambda argv, **kw: seen.append(list(argv)))

    message = tools.invoke(tools.get("restart_stack"))
    assert seen and seen[0][0].endswith("roaring_restart_everything.sh")
    assert "started" in message
