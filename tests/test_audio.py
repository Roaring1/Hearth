"""Audio-layer tests, driven by output recorded from the real rig.

The fixtures in ``tests/fixtures/`` are verbatim ``pactl`` output, so a
format change in PipeWire shows up here rather than as an empty mixer.
"""

from __future__ import annotations

import subprocess

import pytest

from hearth import proc
from hearth.audio import pactl


@pytest.fixture
def sinks_text(fixture_text):
    return fixture_text("pactl_short_sinks.txt")


def test_parse_short_list_reads_every_sink(sinks_text):
    entries = pactl.parse_short_list(sinks_text)
    names = [e.name for e in entries]
    assert "vm_game" in names
    assert "vm_share" in names  # the bus the old hard-coded list forgot
    assert len(entries) == 10
    first = entries[0]
    assert first.index == 39
    assert first.state == "RUNNING"


def test_parse_short_list_ignores_headers_and_blanks():
    assert pactl.parse_short_list("") == []
    assert pactl.parse_short_list("\n  \nnot a row\n") == []


def test_find_sink_matches_by_make_not_serial(sinks_text):
    found = pactl.find_sink(pactl.parse_short_list(sinks_text), "focusrite")
    assert found.startswith("alsa_output.usb-Focusrite_Scarlett_Solo")
    assert pactl.find_sink(pactl.parse_short_list(sinks_text), "nope") == ""


def test_parse_info_version(fixture_text):
    assert pactl.parse_info_version(fixture_text("pactl_info.txt")) == "1.4.11"
    assert pactl.parse_info_version("Server Name: PulseAudio") == ""
    assert pactl.parse_info_version("") == ""


def test_select_loopbacks_filters_source_and_sink(fixture_text):
    modules = pactl.parse_modules(fixture_text("pactl_short_modules.txt"))
    assert any(m.name == "module-loopback" for m in modules)

    all_lb = pactl.select_loopbacks(modules)
    assert len(all_lb) == 3
    assert all(lb.source.endswith(".monitor") for lb in all_lb)

    game = pactl.select_loopbacks(modules, source="vm_game")
    assert len(game) == 1
    assert game[0].sink.endswith("stereo-chat")

    # Filtering by the mic buses must not match the monitor loopbacks above.
    assert pactl.select_loopbacks(modules, sinks=pactl.DEFAULT_LOOPBACK_SINKS) == []


def test_module_arg_parsing():
    mod = pactl.Module(index=1, name="module-loopback", args="source=a sink=b latency_msec=12")
    assert mod.arg("sink") == "b"
    assert mod.arg("latency_msec") == "12"
    assert mod.arg("missing") is None


def test_commands_are_argv_and_never_shell(monkeypatch):
    calls: list[list[str]] = []

    def fake_spawn(argv, **_kwargs):
        calls.append(list(argv))
        return

    monkeypatch.setattr(proc, "spawn", fake_spawn)
    pactl.set_mute("vm game; rm -rf /", True)
    pactl.set_volume("vm_game", 55)
    pactl.set_default_sink("vm_game")

    assert calls[0] == ["pactl", "set-sink-mute", "vm game; rm -rf /", "1"]
    assert calls[1] == ["pactl", "set-sink-volume", "vm_game", "55%"]
    assert calls[2] == ["pactl", "set-default-sink", "vm_game"]


def test_empty_sink_name_is_a_no_op(monkeypatch):
    called = False

    def fake_spawn(argv, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(proc, "spawn", fake_spawn)
    pactl.set_mute("", True)
    pactl.set_volume("", 10)
    pactl.set_default_sink("")
    assert not called, "an unset monitor sink must not produce a pactl call"


def test_reads_survive_a_missing_pactl():
    def missing(_argv, **_kwargs):
        raise FileNotFoundError("pactl")

    proc.set_runner(missing)
    try:
        assert pactl.sinks() == []
        assert pactl.server_version() == ""
        assert pactl.default_sink() == ""
        assert pactl.volume("vm_game") == 0
        assert pactl.is_muted("vm_game") is False
    finally:
        proc.set_runner(None)


def test_volume_and_mute_parse_real_output():
    def fake(argv, **_kwargs):
        vol = "Volume: front-left: 36044 /  55% / -15.56 dB"
        text = vol if "get-sink-volume" in argv else "Mute: yes"
        return subprocess.CompletedProcess(argv, 0, text + "\n", "")

    proc.set_runner(fake)
    try:
        assert pactl.volume("vm_game") == 55
        assert pactl.is_muted("vm_game") is True
    finally:
        proc.set_runner(None)
