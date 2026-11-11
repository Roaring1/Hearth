"""The only module in Hearth allowed to spawn child processes.

Every call site passes an argv list. ``shell=True`` is never used, so no
caller can be broken by a device name, sink name or file path that happens
to contain shell metacharacters.

Four entry points, deliberately no more:

* :func:`run`    - run to completion, capture output, enforce a timeout.
* :func:`spawn`  - fire and forget; the child is detached and not awaited.
* :func:`stream` - long lived child whose stdout is consumed incrementally
  (used for ``parec`` in the VU meter path).
* :func:`spawn_owned` - child tied to a dedicated owner thread and Linux
  parent-death signal; the owner waits until the child exits.

All failures raise :class:`hearth.errors.CommandError` so the UI layer has
exactly one exception type to handle. Tests inject a fake runner with
:func:`set_runner` instead of monkeypatching :mod:`subprocess`.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hearth.errors import CommandError

log = logging.getLogger(__name__)

#: Default wall-clock limit for :func:`run`. Audio tooling (``pactl``,
#: ``pw-dump``, ``systemctl``) answers in milliseconds when healthy; a hung
#: call means the daemon is wedged and we want the error, not a frozen UI.
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class Result:
    """Outcome of a completed :func:`run`."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def out(self) -> str:
        """stdout with trailing whitespace removed (the usual want)."""
        return self.stdout.strip()


# A runner takes the same arguments as subprocess.run and returns an object
# with returncode/stdout/stderr. Swapped out wholesale in tests.
Runner = Callable[..., subprocess.CompletedProcess[str]]

_runner: Runner = subprocess.run


def set_runner(runner: Runner | None) -> None:
    """Replace the process runner (tests only). ``None`` restores the default."""
    global _runner
    _runner = runner or subprocess.run


def _argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, str):  # pragma: no cover - guard against a classic bug
        raise TypeError("argv must be a sequence of strings, not a shell string")
    if not argv:
        raise ValueError("argv must not be empty")
    return tuple(str(part) for part in argv)


def _env(extra: Mapping[str, str] | None) -> dict[str, str] | None:
    if not extra:
        return None
    env = os.environ.copy()
    env.update(extra)
    return env


def have(program: str) -> bool:
    """True when *program* is on PATH. Used for optional dependencies."""
    return shutil.which(program) is not None


def run(
    argv: Sequence[str],
    *,
    timeout: float | None = DEFAULT_TIMEOUT,
    check: bool = True,
    stdin: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> Result:
    """Run *argv* to completion and capture its output.

    Raises :class:`CommandError` if the program is missing, times out, or (when
    ``check`` is true) exits non-zero. Pass ``check=False`` for probes where a
    non-zero exit is a legitimate answer, and inspect :attr:`Result.returncode`.
    """
    parts = _argv(argv)
    log.debug("run %s", parts)
    try:
        completed = _runner(
            list(parts),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            env=_env(env),
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} not found") from exc
    except PermissionError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} is not executable") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(parts, timed_out=True, stderr=f"timed out after {timeout}s") from exc

    result = Result(
        argv=parts,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and not result.ok:
        raise CommandError(parts, returncode=result.returncode, stderr=result.stderr.strip())
    return result


def spawn(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.Popen[bytes]:
    """Start *argv* and return immediately.

    Output is discarded and the child is placed in its own session so that
    quitting Hearth never kills a restart or a long-running helper mid-flight.
    """
    parts = _argv(argv)
    log.debug("spawn %s", parts)
    try:
        return subprocess.Popen(
            list(parts),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_env(env),
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} not found") from exc
    except PermissionError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} is not executable") from exc


def stream(
    argv: Sequence[str],
    *,
    binary: bool = True,
    bufsize: int = 0,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[Any]:
    """Start *argv* with a readable stdout pipe and return the process.

    The caller owns the process: read from ``proc.stdout`` and call
    :func:`terminate` when finished. Used for ``parec`` in the meter path.
    """
    parts = _argv(argv)
    log.debug("stream %s", parts)
    try:
        return subprocess.Popen(
            list(parts),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=bufsize,
            text=not binary,
            start_new_session=True,
            env=_env(env),
        )
    except FileNotFoundError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} not found") from exc
    except PermissionError as exc:
        raise CommandError(parts, stderr=f"{parts[0]} is not executable") from exc


def terminate(proc: subprocess.Popen[Any] | None, *, timeout: float = 1.0) -> None:
    """Stop a child from :func:`stream` or :func:`spawn`, escalating if needed."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover - kernel is stuck
            log.warning("child %s ignored SIGKILL", proc.pid)


class OwnedProcess:
    """A child whose creating owner thread remains alive until it exits."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        owner: threading.Thread,
        finished: threading.Event,
        wait_error: list[BaseException],
    ) -> None:
        self._process = process
        self._owner = owner
        self._finished = finished
        self._wait_error = wait_error

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def poll(self) -> int | None:
        """Return the child's exit status, or None while it is running."""
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        """Wait for child exit and owner-thread cleanup."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        if not self._finished.wait(timeout):
            raise subprocess.TimeoutExpired([str(self.pid)], timeout if timeout is not None else 0)
        self._owner.join(timeout=timeout)
        if self._owner.is_alive():
            raise TimeoutError("owned process thread did not finish")
        if self._wait_error:
            raise RuntimeError(
                f"owned child wait failed: {self._wait_error[0]}"
            ) from self._wait_error[0]
        return self._process.returncode if self._process.returncode is not None else 0

    def stop(self, *, timeout: float = 1.0) -> int:
        """Terminate, escalate if needed, and join the owner thread."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        terminate(self._process, timeout=timeout)
        try:
            return self.wait(timeout=timeout + 0.5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=timeout)
            return self.wait(timeout=timeout + 0.5)


def _spawn_guarded_child(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    cwd: str | None,
    startup_timeout: float,
    cancel: threading.Event | None = None,
) -> subprocess.Popen[bytes]:
    """Create a PDEATHSIG wrapper and wait until it successfully execs *argv*.

    The readiness pipe is closed by FD_CLOEXEC on successful exec. If exec
    fails, the wrapper writes an error to the still-open pipe. This makes the
    handshake prove both that PR_SET_PDEATHSIG was installed and exec succeeded.
    """
    if startup_timeout <= 0:
        raise ValueError("startup_timeout must be positive")
    parts = _argv(argv)
    read_fd, write_fd = os.pipe()
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    child_env["HEARTH_CAPTURE_PARENT_PID"] = str(os.getpid())
    child_env["HEARTH_CAPTURE_READY_FD"] = str(write_fd)
    wrapper = str(Path(__file__).with_name("_capture_exec.py"))
    wrapper_argv = [sys.executable, wrapper, "--", *parts]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            wrapper_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(write_fd,),
            start_new_session=True,
            env=child_env,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        os.close(read_fd)
        raise CommandError(wrapper_argv, stderr=f"{sys.executable} not found") from exc
    except PermissionError as exc:
        os.close(read_fd)
        raise CommandError(wrapper_argv, stderr=f"{sys.executable} is not executable") from exc
    except BaseException:
        os.close(read_fd)
        raise
    finally:
        os.close(write_fd)

    deadline = time.monotonic() + startup_timeout
    status = bytearray()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise CommandError(parts, stderr="owned process startup was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandError(
                    parts, timed_out=True, stderr="parent-death/exec handshake timed out"
                )
            wait_slice = min(remaining, 0.1) if cancel is not None else remaining
            if not select.select([read_fd], [], [], wait_slice)[0]:
                continue
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            status.extend(chunk)
    except Exception:
        terminate(process, timeout=0.5)
        raise
    finally:
        os.close(read_fd)

    if status != b"R":
        if status.startswith(b"RE"):
            detail = status[2:].decode("utf-8", errors="replace").strip()
        elif status.startswith(b"E"):
            detail = status[1:].decode("utf-8", errors="replace").strip()
        else:
            detail = "wrapper exited before readiness"
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            terminate(process, timeout=0.5)
        raise CommandError(parts, returncode=process.returncode, stderr=detail)
    if process.poll() is not None:
        raise CommandError(
            parts, returncode=process.returncode, stderr="viewer exited immediately after exec"
        )
    return process


def spawn_owned(
    argv: Sequence[str],
    *,
    startup_timeout: float = 3.0,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> OwnedProcess:
    """Start a child with parent-thread death signaling and a live wait owner.

    The caller receives the handle only after the child wrapper has installed
    Linux ``PR_SET_PDEATHSIG`` and successfully exec'd *argv*. A dedicated
    daemon owner thread performs creation and remains blocked in ``wait()`` for
    the child's entire lifetime. Its death therefore sends SIGTERM to the
    child, even while the Hearth process remains alive.
    """
    if not sys.platform.startswith("linux"):
        raise CommandError(argv, stderr="owned child processes require Linux")
    parts = _argv(argv)
    if startup_timeout <= 0:
        raise ValueError("startup_timeout must be positive")

    ready = threading.Event()
    finished = threading.Event()
    process_box: list[subprocess.Popen[bytes]] = []
    errors: list[BaseException] = []
    wait_errors: list[BaseException] = []
    cancel = threading.Event()

    def owner() -> None:
        process: subprocess.Popen[bytes] | None = None
        try:
            process = _spawn_guarded_child(
                parts, env=env, cwd=cwd, startup_timeout=startup_timeout, cancel=cancel
            )
            process_box.append(process)
            ready.set()
            process.wait()
        except BaseException as exc:
            (wait_errors if ready.is_set() else errors).append(exc)
            if not ready.is_set():
                ready.set()
            if process is not None and process.poll() is None:
                terminate(process, timeout=0.5)
        finally:
            finished.set()

    thread = threading.Thread(target=owner, name="hearth-owned-capture-child", daemon=True)
    thread.start()
    if not ready.wait(startup_timeout + 1.0):
        cancel.set()
        if not finished.wait(1.0):
            raise CommandError(
                parts,
                timed_out=True,
                stderr=(
                    "owned process startup timed out; owner thread did not acknowledge cancellation"
                ),
            )
        thread.join(timeout=0.5)
        raise CommandError(parts, timed_out=True, stderr="owned process startup timed out")
    if errors:
        error = errors[0]
        if isinstance(error, CommandError):
            raise error
        raise CommandError(parts, stderr=str(error)) from error
    if not process_box:
        raise CommandError(parts, stderr="owned process was not returned")
    process = process_box[0]
    return OwnedProcess(process, thread, finished, wait_errors)
