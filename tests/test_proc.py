"""Tests for the subprocess layer.

No real audio tooling is invoked: the runner is replaced with a fake so the
suite passes on a machine with no PipeWire, no pactl and no sound card.
"""

from __future__ import annotations

import subprocess

import pytest

from hearth import proc
from hearth.errors import CommandError


@pytest.fixture(autouse=True)
def _restore_runner():
    yield
    proc.set_runner(None)


def fake(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a runner that always returns the given completed process."""

    def runner(argv, **kwargs):
        runner.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    runner.calls = []
    return runner


def test_run_returns_stripped_output():
    proc.set_runner(fake(stdout="Server Name: PulseAudio (on PipeWire 1.4.11)\n"))
    result = proc.run(["pactl", "info"])
    assert result.ok
    assert result.out.endswith("1.4.11)")
    assert result.argv == ("pactl", "info")


def test_run_never_uses_a_shell():
    runner = fake()
    proc.set_runner(runner)
    proc.run(["pactl", "set-sink-mute", "vm_game", "0"])
    _argv, kwargs = runner.calls[0]
    assert "shell" not in kwargs
    assert kwargs["check"] is False
    assert kwargs["timeout"] == proc.DEFAULT_TIMEOUT


def test_run_raises_on_nonzero_exit():
    proc.set_runner(fake(returncode=1, stderr="No such sink\n"))
    with pytest.raises(CommandError) as excinfo:
        proc.run(["pactl", "set-sink-volume", "nope", "50%"])
    assert "No such sink" in str(excinfo.value)


def test_run_can_tolerate_nonzero_exit():
    proc.set_runner(fake(returncode=3, stdout="inactive\n"))
    result = proc.run(["systemctl", "--user", "is-active", "lpd8-mixer"], check=False)
    assert result.returncode == 3
    assert result.out == "inactive"


def test_run_reports_timeouts():
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    proc.set_runner(hang)
    with pytest.raises(CommandError) as excinfo:
        proc.run(["pw-dump"], timeout=0.25)
    assert excinfo.value.timed_out is True


def test_run_reports_missing_binary():
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    proc.set_runner(missing)
    with pytest.raises(CommandError) as excinfo:
        proc.run(["carla-single"])
    assert "not found" in str(excinfo.value)


def test_run_rejects_a_shell_string():
    proc.set_runner(fake())
    with pytest.raises(TypeError):
        proc.run("pactl info | grep Server")  # type: ignore[arg-type]


def test_run_rejects_empty_argv():
    with pytest.raises(ValueError):
        proc.run([])


def test_terminate_is_a_noop_for_finished_children():
    class Done:
        def poll(self):
            return 0

        def terminate(self):  # pragma: no cover - must not be called
            raise AssertionError("terminate() called on a finished child")

    proc.terminate(Done())
    proc.terminate(None)
