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
import os
from pathlib import Path
from typing import Any

from hearth import paths

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
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, int | float) and not isinstance(value, bool)
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


def save(values: dict[str, Any], path: Path | None = None) -> None:
    """Write preferences atomically.

    The temporary file is created in the destination directory so the rename
    stays on one filesystem; across filesystems ``os.replace`` is not atomic
    and the guarantee this function exists for would be lost.
    """
    target = paths.settings_file() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(values, indent=2))
        tmp.replace(target)  # atomic within one filesystem
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
