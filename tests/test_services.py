"""Tests for the systemd user-unit wrapper."""

from __future__ import annotations

import subprocess

from hearth import proc, services


def _runner(stdout: str, returncode: int = 0):
    def fake(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    return fake


def test_states_maps_lines_to_units_in_order():
    proc.set_runner(_runner("active\ninactive\nfailed\n", returncode=3))
    try:
        got = services.states(["a.service", "b.service", "c.service"])
    finally:
        proc.set_runner(None)
    assert got == {"a.service": "active", "b.service": "inactive", "c.service": "failed"}


def test_short_reply_degrades_to_unknown_instead_of_shifting():
    proc.set_runner(_runner("active\n"))
    try:
        got = services.states(["a.service", "b.service"])
    finally:
        proc.set_runner(None)
    assert got == {"a.service": "active", "b.service": services.UNKNOWN}


def test_states_with_no_units_does_not_spawn():
    def explode(_argv, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("systemctl was called with no units")

    proc.set_runner(explode)
    try:
        assert services.states([]) == {}
    finally:
        proc.set_runner(None)


def test_missing_systemd_reports_unknown():
    def missing(_argv, **_kwargs):
        raise FileNotFoundError("systemctl")

    proc.set_runner(missing)
    try:
        assert services.states(["a.service"]) == {"a.service": services.UNKNOWN}
        assert services.is_active("a.service") is False
        assert services.timer_summary("a.timer") == ""
        assert services.memory_bytes("a.service") == 0
    finally:
        proc.set_runner(None)


def test_timer_summary_from_real_output(fixture_text):
    text = fixture_text("systemctl_list_timers.txt")
    summary = services.parse_timer_summary(text)
    assert summary.startswith("Sun 2026-")
    assert summary.count(" ") == 2


def test_timer_summary_handles_no_timers():
    assert services.parse_timer_summary("NEXT LEFT LAST PASSED UNIT ACTIVATES\n") == ""
    assert services.parse_timer_summary("") == ""


def test_memory_bytes_handles_not_set():
    proc.set_runner(_runner("MemoryCurrent=[not set]\n"))
    try:
        assert services.memory_bytes("a.service") == 0
    finally:
        proc.set_runner(None)

    proc.set_runner(_runner("MemoryCurrent=153092096\n"))
    try:
        assert services.memory_bytes("a.service") == 153092096
    finally:
        proc.set_runner(None)


def test_actions_are_argv(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(proc, "spawn", lambda argv, **_k: calls.append(list(argv)))

    services.restart("xdg-desktop-portal-kde", "xdg-desktop-portal")
    services.reset_failed()
    services.daemon_reload()

    assert calls[0] == [
        "systemctl",
        "--user",
        "restart",
        "xdg-desktop-portal-kde",
        "xdg-desktop-portal",
    ]
    assert calls[1] == ["systemctl", "--user", "reset-failed"]
    assert calls[2] == ["systemctl", "--user", "daemon-reload"]
