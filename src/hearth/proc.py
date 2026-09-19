"""The only module in Hearth allowed to spawn child processes.

Every call site passes an argv list. ``shell=True`` is never used, so no
caller can be broken by a device name, sink name or file path that happens
to contain shell metacharacters.

Three entry points, deliberately no more:

* :func:`run`    - run to completion, capture output, enforce a timeout.
* :func:`spawn`  - fire and forget; the child is detached and not awaited.
* :func:`stream` - long lived child whose stdout is consumed incrementally
  (used for ``parec`` in the VU meter path).

All failures raise :class:`hearth.errors.CommandError` so the UI layer has
exactly one exception type to handle. Tests inject a fake runner with
:func:`set_runner` instead of monkeypatching :mod:`subprocess`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
