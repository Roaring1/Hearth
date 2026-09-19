"""Filesystem locations, resolved once, per the XDG Base Directory Specification.

This is the only module allowed to look at ``$HOME`` or the ``XDG_*``
environment variables. Everything else asks for a named location.

The spec is explicit about two things that are usually got wrong:

* a value in an ``XDG_*`` variable must be an absolute path, and a relative one
  must be treated as unset;
* sockets and pid files belong in ``$XDG_RUNTIME_DIR`` (which the session
  cleans up), logs belong in ``$XDG_STATE_HOME``, and only genuinely
  regenerable data belongs in ``$XDG_CACHE_HOME``.

Hearth historically put its socket and pid file in the config directory and
its log in the cache directory. Both are corrected here, with the old
locations still readable so an existing install keeps working.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hearth import APP_NAME

__all__ = [
    "cache_dir",
    "config_dir",
    "config_file",
    "dumps_dir",
    "ensure_dirs",
    "home",
    "legacy_config_dir",
    "log_file",
    "pid_file",
    "runtime_dir",
    "settings_file",
    "socket_file",
    "state_dir",
]


def home() -> Path:
    """The user's home directory."""
    return Path.home()


def _xdg(var: str, default: Path) -> Path:
    """Read an XDG base directory variable, ignoring unset or relative values."""
    raw = os.environ.get(var, "")
    if not raw:
        return default
    candidate = Path(raw)
    if not candidate.is_absolute():
        # The specification says a relative path is invalid and must be
        # ignored, not resolved against the working directory.
        return default
    return candidate


def config_home() -> Path:
    """The config base directory itself, which every app on the desktop shares.

    Hearth writes only inside :func:`config_dir`, but it reads one file
    from here -- Plasma's ``kdeglobals`` -- to match the desktop's colours.
    """
    return _xdg("XDG_CONFIG_HOME", home() / ".config")


def config_dir() -> Path:
    """User configuration: config.json, settings, device mapping."""
    return config_home() / APP_NAME


def state_dir() -> Path:
    """Data that should survive a restart but is not worth syncing: logs, dumps."""
    return _xdg("XDG_STATE_HOME", home() / ".local" / "state") / APP_NAME


def cache_dir() -> Path:
    """Data that may be deleted at any time without loss."""
    return _xdg("XDG_CACHE_HOME", home() / ".cache") / APP_NAME


def runtime_dir() -> Path:
    """Session-scoped directory for the IPC socket and pid file.

    ``XDG_RUNTIME_DIR`` has no specified fallback, so when it is absent (cron,
    ssh without a session, containers) we use a private, user-owned directory
    under the system temp dir rather than writing a socket into the home
    directory.
    """
    raw = os.environ.get("XDG_RUNTIME_DIR", "")
    base = Path(raw) if raw and Path(raw).is_absolute() else Path(tempfile.gettempdir())
    if not raw:
        return base / f"{APP_NAME}-{os.getuid()}"
    return base / APP_NAME


def legacy_config_dir() -> Path:
    """Pre-rename configuration directory, still read for migration."""
    return _xdg("XDG_CONFIG_HOME", home() / ".config") / "roaring"


def config_file() -> Path:
    """Machine-specific device and host configuration."""
    return config_dir() / "config.json"


def settings_file() -> Path:
    """User-tunable application settings."""
    return config_dir() / "rac_settings.json"


def socket_file() -> Path:
    """Single-instance IPC socket."""
    return runtime_dir() / "hearth.sock"


def pid_file() -> Path:
    """Pid file for the running instance."""
    return runtime_dir() / "hearth.pid"


def log_file() -> Path:
    """Rotating application log."""
    return state_dir() / "hearth.log"


def dumps_dir() -> Path:
    """Debug snapshots and VU captures."""
    return state_dir() / "dumps"


def ensure_dirs() -> None:
    """Create every directory Hearth writes to.

    The runtime directory is created with 0700 because it holds a control
    socket that can mute the machine's audio.
    """
    for path in (config_dir(), state_dir(), cache_dir(), dumps_dir()):
        path.mkdir(parents=True, exist_ok=True)
    runtime = runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
