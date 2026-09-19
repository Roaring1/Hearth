"""Exception hierarchy.

Every error Hearth raises on purpose derives from :class:`HearthError`, so a
caller can catch the app's own failures without also swallowing bugs,
``KeyboardInterrupt`` or ``SystemExit``.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "AudioError",
    "CommandError",
    "ConfigError",
    "HearthError",
    "IpcError",
]


class HearthError(Exception):
    """Base class for all deliberate Hearth failures."""


class ConfigError(HearthError):
    """Configuration or settings could not be read, parsed or validated."""


class CommandError(HearthError):
    """An external command failed, timed out, or was not found.

    Carries enough context to debug without re-running the command.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        returncode: int | None = None,
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr.strip()
        self.timed_out = timed_out

        rendered = " ".join(self.argv)
        if timed_out:
            detail = "timed out"
        elif returncode is None:
            detail = "could not be executed"
        else:
            detail = f"exited {returncode}"
        if self.stderr:
            detail = f"{detail}: {self.stderr}"
        super().__init__(f"{rendered} {detail}")


class IpcError(HearthError):
    """The single-instance socket could not be reached or spoke nonsense."""


class AudioError(HearthError):
    """The audio graph is not in a state the requested operation can use."""
