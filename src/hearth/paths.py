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
import stat
import tempfile
from pathlib import Path

from hearth import APP_NAME

__all__ = [
    "cache_dir",
    "config_dir",
    "config_file",
    "data_root",
    "dumps_dir",
    "ensure_dirs",
    "ensure_private_file",
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


def _posix_uid() -> int:
    """UID for Linux ownership checks (called only from POSIX branches)."""
    return int(os.getuid())  # type: ignore[attr-defined]


def data_root() -> Path:
    """Private per-user application data root on Windows."""
    raw = os.environ.get("LOCALAPPDATA", "")
    base = Path(raw) if raw and Path(raw).is_absolute() else home() / "AppData" / "Local"
    return base / "Hearth"


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
    if os.name == "nt":
        override = os.environ.get("XDG_CONFIG_HOME", "")
        if override and Path(override).is_absolute():
            return Path(override)
        raw = os.environ.get("APPDATA", "")
        return Path(raw) if raw and Path(raw).is_absolute() else home() / "AppData" / "Roaming"
    return _xdg("XDG_CONFIG_HOME", home() / ".config")


def config_dir() -> Path:
    """User configuration: config.json, settings, device mapping."""
    if os.name == "nt":
        if os.environ.get("XDG_CONFIG_HOME") and Path(os.environ["XDG_CONFIG_HOME"]).is_absolute():
            return config_home() / APP_NAME
        return data_root() / "config"
    return config_home() / APP_NAME


def state_dir() -> Path:
    """Data that should survive a restart but is not worth syncing: logs, dumps."""
    if os.name == "nt":
        override = os.environ.get("XDG_STATE_HOME", "")
        return (
            Path(override) / APP_NAME
            if override and Path(override).is_absolute()
            else data_root() / "state"
        )
    return _xdg("XDG_STATE_HOME", home() / ".local" / "state") / APP_NAME


def cache_dir() -> Path:
    """Data that may be deleted at any time without loss."""
    if os.name == "nt":
        override = os.environ.get("XDG_CACHE_HOME", "")
        return (
            Path(override) / APP_NAME
            if override and Path(override).is_absolute()
            else data_root() / "cache"
        )
    return _xdg("XDG_CACHE_HOME", home() / ".cache") / APP_NAME


def runtime_dir() -> Path:
    """Session-scoped directory for the IPC socket and pid file.

    ``XDG_RUNTIME_DIR`` has no specified fallback, so when it is absent (cron,
    ssh without a session, containers) we use a private, user-owned directory
    under the system temp dir rather than writing a socket into the home
    directory.
    """
    if os.name == "nt":
        override = os.environ.get("XDG_RUNTIME_DIR", "")
        return (
            Path(override) / APP_NAME
            if override and Path(override).is_absolute()
            else data_root() / "runtime"
        )
    raw = os.environ.get("XDG_RUNTIME_DIR", "")
    candidate = Path(raw) if raw else None
    if candidate is not None and candidate.is_absolute():
        return candidate / APP_NAME
    # An invalid relative value is equivalent to an unset variable. Keep the
    # fallback unique per UID; never collapse all users into /tmp/hearth.
    return Path(tempfile.gettempdir()) / f"{APP_NAME}-{_posix_uid()}"


def legacy_config_dir() -> Path:
    """Pre-rename configuration directory, still read for migration."""
    if os.name == "nt":
        return config_home() / "roaring"
    return _xdg("XDG_CONFIG_HOME", home() / ".config") / "roaring"


def config_file() -> Path:
    """Machine-specific device and host configuration."""
    return config_dir() / "config.json"


def settings_file() -> Path:
    """User-tunable application settings."""
    return config_dir() / "rac_settings.json"


def socket_file() -> Path:
    """Single-instance IPC socket."""
    return runtime_dir() / ("hearth.pipe" if os.name == "nt" else "hearth.sock")


def pid_file() -> Path:
    """Pid file for the running instance."""
    return runtime_dir() / "hearth.pid"


def log_file() -> Path:
    """Rotating application log."""
    return state_dir() / "hearth.log"


def dumps_dir() -> Path:
    """Debug snapshots and VU captures."""
    return state_dir() / "dumps"


def ensure_private_dir(path: Path) -> Path:
    """Create a Hearth-owned directory with owner-only access.

    Config, state and diagnostic files can reveal details about the local rig.
    Repair existing directories even if an older release created them under a
    permissive umask. Intentional directory symlinks are resolved before
    checking ownership and permissions.
    """
    if os.name == "nt":
        from hearth.platform.windows_fs import ensure_private_dir as windows_ensure_private_dir

        return windows_ensure_private_dir(path, root=data_root())
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(f"private path must be a directory: {path}")
    if metadata.st_uid != _posix_uid():
        raise PermissionError(f"private directory is not owned by this user: {resolved}")
    resolved.chmod(0o700)
    return resolved


def ensure_private_file(path: Path) -> Path:
    """Validate a regular, single-link file inside Hearth's private tree."""
    if os.name == "nt":
        from hearth.platform.windows_fs import ensure_private_file as windows_ensure_private_file

        return windows_ensure_private_file(path, root=data_root())
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing a non-regular private file: {path}")
    if metadata.st_uid != _posix_uid():
        raise PermissionError(f"private file is not owned by this user: {path}")
    if metadata.st_nlink != 1:
        raise OSError(f"refusing a hard-linked private file: {path}")
    return path


def ensure_private_fd(fd: int, path: Path) -> None:
    """Validate an open file descriptor against Hearth's private root."""
    if os.name == "nt":
        from hearth.platform.windows_fs import validate_private_fd

        validate_private_fd(fd, path, root=data_root())
        return
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing a non-regular open file: {path}")
    if metadata.st_uid != _posix_uid():
        raise PermissionError(f"open file is not owned by this user: {path}")
    if metadata.st_nlink != 1:
        raise OSError(f"refusing a hard-linked open file: {path}")


def ensure_dirs() -> None:
    """Create Hearth directories with private permissions.

    The runtime directory holds a control socket that can mute the machine's
    audio. Config, state, cache and dump directories can reveal local rig
    details, so they are owner-only as well.
    """
    if os.name == "nt":
        for path in (
            data_root(),
            config_dir(),
            state_dir(),
            cache_dir(),
            dumps_dir(),
            runtime_dir(),
        ):
            ensure_private_dir(path)
        return
    for path in (config_dir(), state_dir(), cache_dir(), dumps_dir()):
        ensure_private_dir(path)
    runtime = runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = runtime.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(f"runtime path must be a real directory: {runtime}")
    if metadata.st_uid != _posix_uid():
        raise PermissionError(f"runtime directory is not owned by this user: {runtime}")
    # mkdir(mode=...) does not change the permissions of an existing path.
    # Repair an overly broad mode before creating a control socket in it.
    runtime.chmod(0o700)
