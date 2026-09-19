"""Machine-specific configuration, read from ``config.json``.

Everything that identifies *this* rig lives in
``$XDG_CONFIG_HOME/hearth/config.json``, never in the source. This file is
published, so it must not contain a device serial number, a hostname or an
IP address. That rule is why the Focusrite's serial is no longer a literal
and why the monitor sink can be discovered by substring instead.

Every key is optional and every value is validated. A config file is
hand-edited by definition, so the interesting cases are all the wrong ones:

* **missing, empty, or not JSON** -- use defaults, log once, never raise;
* **not an object** (a list, a bare string) -- same;
* **a value of the wrong type** -- keep the default for that one key and
  keep every other key the user got right;
* **an unknown key** -- reported through :func:`unknown_keys` so the UI can
  say "this line does nothing", because a silently ignored typo in a config
  file is indistinguishable from a bug in the program.

The old implementation did none of this: it returned the raw dictionary, so
``"laptop_rtp_port": "forty-six thousand"`` travelled all the way to the
command that used it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from hearth import paths

log = logging.getLogger(__name__)

#: Fallback sinks for the headset's two targets. These are defaults, not
#: facts about anyone's hardware: any rig without an A50 overrides them.
DEFAULT_ASTRO_CHAT = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
DEFAULT_ASTRO_GAME = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"


@dataclass(frozen=True, slots=True)
class Config:
    """The resolved machine configuration. Every field always has a value."""

    #: Sink names for the headset's chat and game targets.
    astro_chat_sink: str = DEFAULT_ASTRO_CHAT
    astro_game_sink: str = DEFAULT_ASTRO_GAME
    #: Monitor interface sink. Blank means "discover it by substring".
    monitor_sink: str = ""
    #: The substring used for that discovery.
    monitor_sink_match: str = "Focusrite"
    #: Labels for the network-audio target shown in the LAPTOP bus.
    laptop_host: str = "laptop"
    laptop_rtp_port: int = 46000
    #: Target for the "SSH to laptop" shortcut.
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_key: str = ""

    @property
    def ssh_key_path(self) -> Path | None:
        """``ssh_key`` as a path with ``~`` expanded, or ``None`` if unset."""
        if not self.ssh_key:
            return None
        return Path(self.ssh_key).expanduser()


def _field_types() -> dict[str, type]:
    return {f.name: str if f.type == "str" else int for f in fields(Config)}


def _coerce(key: str, value: Any, want: type) -> Any | None:
    """Return ``value`` as ``want``, or ``None`` when it cannot be.

    ``bool`` is rejected for an int field on purpose: ``True`` is a valid
    ``int`` in Python, and silently accepting it as a port number would be
    a confusing way to fail later.
    """
    if want is str and isinstance(value, str):
        return value
    if want is int and isinstance(value, int) and not isinstance(value, bool):
        return value
    log.warning(
        "config: ignoring %r for %r; expected %s, got %s",
        value,
        key,
        want.__name__,
        type(value).__name__,
    )
    return None


def parse(raw: object) -> Config:
    """Build a :class:`Config` from already-decoded JSON, degrading as needed."""
    if not isinstance(raw, dict):
        if raw is not None:
            log.warning("config: expected an object, got %s; using defaults", type(raw).__name__)
        return Config()

    types = _field_types()
    values: dict[str, Any] = {}
    for key, value in raw.items():
        want = types.get(key)
        if want is None:
            continue  # surfaced by unknown_keys(), not silently fatal
        coerced = _coerce(key, value, want)
        if coerced is not None:
            values[key] = coerced
    return Config(**values)


def unknown_keys(raw: object) -> list[str]:
    """Keys present in the file that Hearth does not understand.

    A typo in a config file is otherwise invisible: the setting simply has
    no effect, and the user concludes the feature is broken.
    """
    if not isinstance(raw, dict):
        return []
    known = _field_types()
    return sorted(key for key in raw if key not in known)


def read_raw(path: Path | None = None) -> object:
    """Decode the config file, returning ``None`` when it cannot be read."""
    target = paths.config_file() if path is None else path
    try:
        return json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("config: %s is unreadable (%s); using defaults", target, exc)
        return None


def load(path: Path | None = None) -> Config:
    """The machine configuration, always usable."""
    return parse(read_raw(path))
