"""User preferences, read from and written to ``rac_settings.json``.

These are the choices the app itself changes as you use it -- refresh rate,
VU speed, window size, whether the mixer is collapsed. That makes this file
different from :mod:`hearth.config` in one important way: **Hearth writes
it**, often, including while quitting.

So the save path is atomic. The previous implementation was a plain
``write_text``, which is precisely the bug that was found and fixed for the
mixer config in Phase 1 and never fixed here: a crash or a full disk midway
through leaves a truncated JSON file, and the next launch silently reverts
every preference to its default. Writing a temporary file in the same
directory and renaming it over the target cannot do that -- the rename is
atomic, so the file is either the old one or the new one.

Unknown keys are preserved on load rather than dropped. A settings file
written by a newer Hearth must survive being opened by an older one, or
downgrading silently destroys preferences.
"""

from __future__ import annotations

import json
import logging
import math
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

from hearth import paths
from hearth.platform.locking import lock_file, unlock_file

log = logging.getLogger(__name__)

#: Every preference, with the value used when the file says nothing.
DEFAULTS: dict[str, Any] = {
    "refresh_ms": 2000,
    "autoheal": True,
    "vu_enabled": True,
    "vu_speed": 0.35,
    "mem_warn_mb": 80,
    "mem_crit_mb": 200,
    "start_minimized": False,
    "notify_fail": True,
    "latency_msec": 12,
    "mixer_collapsed": False,
    # Which edge minimised channels stack along: "right" or "bottom".
    "mixer_rail_dock": "right",
    "win_w": 880,
    "win_h": 620,
}


def _valid(key: str, value: Any) -> bool:
    """Whether ``value`` is usable for ``key``, judged against the default.

    ``bool`` is checked before the numbers because ``True`` is an ``int``,
    and a checkbox setting holding ``1`` should not silently become a
    number field.
    """
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        if key == "latency_msec":
            return 1 <= value <= 200
        if key == "win_w":
            return 320 <= value <= 16384
        if key == "win_h":
            return 240 <= value <= 16384
        return True
    if isinstance(default, float):
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            # A huge JSON integer cannot be represented as a finite float.
            return False
    return isinstance(value, type(default))


def merge(raw: object) -> dict[str, Any]:
    """Overlay stored values onto the defaults, discarding unusable ones.

    One bad value must not cost the user every other preference, so each key
    is judged on its own.
    """
    merged = dict(DEFAULTS)
    if not isinstance(raw, dict):
        if raw is not None:
            log.warning("settings: expected an object, got %s", type(raw).__name__)
        return merged
    for key, value in raw.items():
        if key not in DEFAULTS:
            # Kept, not dropped: a file written by a newer Hearth must
            # survive being opened by an older one.
            merged[key] = value
        elif _valid(key, value):
            merged[key] = value
        else:
            log.warning("settings: ignoring %r for %r; keeping the default", value, key)
    return merged


def load(path: Path | None = None) -> dict[str, Any]:
    """Every preference, always complete and always usable."""
    target = paths.settings_file() if path is None else path
    try:
        raw = json.loads(target.read_text())
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError) as exc:
        log.warning("settings: %s is unreadable (%s); using defaults", target, exc)
        return dict(DEFAULTS)
    return merge(raw)


def _save_unlocked(values: dict[str, Any], path: Path | None = None) -> None:
    """Write preferences atomically.

    The temporary file is created in the destination directory so the rename
    stays on one filesystem; across filesystems ``os.replace`` is not atomic
    and the guarantee this function exists for would be lost.
    """
    target = paths.settings_file() if path is None else path
    paths.ensure_private_dir(target.parent)
    payload = json.dumps(values, indent=2)

    # Settings must stay private even if an older version created them under
    # a permissive umask. Refuse symlinks/non-regular paths instead of
    # following or replacing a file outside the intended settings path.
    try:
        existing = target.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode):
            raise OSError(f"refusing to replace non-regular settings path: {target}")
        if os.name == "nt":
            paths.ensure_private_file(target)
        elif existing.st_uid != int(os.getuid()):  # type: ignore[attr-defined]
            raise PermissionError(f"settings file is not owned by this user: {target}")

    # mkstemp makes each concurrent write independent and private.
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.tmp.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)  # type: ignore[attr-defined]
        stream = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        paths.ensure_private_file(tmp)
        tmp.replace(target)  # atomic within one filesystem
    except BaseException:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)
        raise


# Serializes concurrent writers in this process. A persistent file lock below
# extends the same guarantee across separate Hearth processes.
_SAVE_LOCK = threading.RLock()


def save(values: dict[str, Any], path: Path | None = None) -> None:
    """Write settings atomically under a thread and cross-process lock."""
    target = paths.settings_file() if path is None else Path(path)
    with _SAVE_LOCK:
        paths.ensure_private_dir(target.parent)
        lock_path = target.with_name(f".{target.name}.lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        if lock_path.exists():
            paths.ensure_private_file(lock_path)
        fd = os.open(lock_path, flags, 0o600)
        try:
            paths.ensure_private_fd(fd, lock_path)
            lock_file(fd)
            try:
                _save_unlocked(values, target)
            finally:
                unlock_file(fd)
        finally:
            os.close(fd)
