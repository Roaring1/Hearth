"""Linux owned-process tests use only short synthetic Python children."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hearth import proc
from hearth.errors import CommandError

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux PDEATHSIG is required"
)


def _proc_state(pid: int) -> str | None:
    """Return process state from /proc; Z means it is no longer executing."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    end = raw.rfind(")")
    return raw[end + 2 : end + 3] if end >= 0 else "?"


def test_spawn_owned_runs_and_stops_synthetic_child() -> None:
    child = proc.spawn_owned([sys.executable, "-c", "import time; time.sleep(30)"])
    assert child.pid > 1
    assert child.poll() is None

    result = child.stop(timeout=0.3)

    assert result in {-signal.SIGTERM, -signal.SIGKILL}
    assert child.returncode == result
    assert child.wait(timeout=0.1) == result


def test_spawn_owned_surfaces_exec_failure() -> None:
    with pytest.raises(CommandError, match="exec failed"):
        proc.spawn_owned(["hearth-capture-no-such-executable"])


def test_spawn_owned_parent_thread_exit_terminates_child() -> None:
    """An owner subprocess exits without Stop; its child must receive SIGTERM."""
    helper = r"""
import os
import sys
from hearth import proc
handle = proc.spawn_owned([sys.executable, "-c", "import time; time.sleep(60)"])
print(handle.pid, flush=True)
os._exit(0)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", helper],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert parent.stdout is not None
        line = parent.stdout.readline().strip()
        assert line.isdecimal(), parent.stderr.read() if parent.stderr is not None else ""
        child_pid = int(line)
        assert parent.wait(timeout=3) == 0

        deadline = time.monotonic() + 3
        state = _proc_state(child_pid)
        while state not in {None, "Z"} and time.monotonic() < deadline:
            time.sleep(0.05)
            state = _proc_state(child_pid)
        assert state in {None, "Z"}, f"owned child {child_pid} survived owner exit (state={state})"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=1)
        if parent.stdout is not None:
            parent.stdout.close()
        if parent.stderr is not None:
            parent.stderr.close()


def test_owned_process_wait_timeout_does_not_stop_child() -> None:
    child = proc.spawn_owned([sys.executable, "-c", "import time; time.sleep(30)"])
    with pytest.raises(subprocess.TimeoutExpired):
        child.wait(timeout=0.01)
    assert child.poll() is None
    child.stop(timeout=0.3)


def test_creator_thread_exit_terminates_child_while_parent_process_stays_alive() -> None:
    """PDEATHSIG is tied to the creating thread, not merely the process PID."""
    holder: dict[str, subprocess.Popen[bytes]] = {}
    errors: list[BaseException] = []

    def create_from_owner_thread() -> None:
        try:
            holder["process"] = proc._spawn_guarded_child(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                env=None,
                cwd=None,
                startup_timeout=3.0,
            )
        except BaseException as exc:
            errors.append(exc)

    owner = threading.Thread(target=create_from_owner_thread, name="capture-owner-thread-test")
    owner.start()
    owner.join(timeout=4.0)
    assert not owner.is_alive(), "owner thread did not complete the exec handshake"
    assert not errors

    child = holder["process"]
    try:
        # The pytest process remains alive; the child should die because its
        # exact creator thread ended after the exec handshake.
        assert child.wait(timeout=3.0) == -signal.SIGTERM
    finally:
        proc.terminate(child, timeout=0.3)


def test_exec_wrapper_refuses_to_exec_when_parent_died_before_signal_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth import _capture_exec

    read_fd, write_fd = os.pipe()
    monkeypatch.setitem(os.environ, "HEARTH_CAPTURE_PARENT_PID", "123")
    monkeypatch.setitem(os.environ, "HEARTH_CAPTURE_READY_FD", str(write_fd))
    monkeypatch.setattr(_capture_exec, "install_parent_death_signal", lambda _pid: False)
    monkeypatch.setattr(
        _capture_exec.os,
        "execvpe",
        lambda *_args: pytest.fail("exec must not run after the parent-death race"),
    )
    try:
        assert _capture_exec.main(["--", sys.executable, "-c", "pass"]) == 125
    finally:
        os.close(write_fd)
    try:
        status = os.read(read_fd, 4096)
    finally:
        os.close(read_fd)
    assert status == b"Eparent exited before PDEATHSIG was armed"


def test_exec_wrapper_reports_parent_death_signal_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth import _capture_exec

    read_fd, write_fd = os.pipe()
    monkeypatch.setitem(os.environ, "HEARTH_CAPTURE_PARENT_PID", "123")
    monkeypatch.setitem(os.environ, "HEARTH_CAPTURE_READY_FD", str(write_fd))

    def fail_prctl(_pid: int) -> bool:
        raise OSError("prctl denied")

    monkeypatch.setattr(_capture_exec, "install_parent_death_signal", fail_prctl)
    try:
        assert _capture_exec.main(["--", sys.executable, "-c", "pass"]) == 126
    finally:
        os.close(write_fd)
    try:
        status = os.read(read_fd, 4096)
    finally:
        os.close(read_fd)
    assert status == b"Eparent-death setup failed: prctl denied"
