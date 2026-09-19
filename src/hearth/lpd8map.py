"""Reading and rewriting the LPD8 knob map.

The knob assignments are constants near the top of ``~/bin/lpd8_mixer.sh``
(see :data:`hearth.midi.MAP_SOURCE`). That script is the thing actually
listening to the hardware, so a remap that edited anything else would be a
lie told by the UI: the knob would still move the old bus.

This module therefore edits the script itself, in place, one constant at a
time, and nothing here guesses. If the constant is not in the file the edit
fails loudly rather than appending a line the script never reads.

Knob numbers are the numbers printed on the hardware; the LPD8 sends CC
``knob - 1`` in its default program, which is the only mapping the script
cares about.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: The script whose constants are the truth.
SCRIPT = Path("~/bin/lpd8_mixer.sh").expanduser()

#: The unit that has to be restarted for an edit to take effect.
UNIT = "lpd8-mixer.service"


@dataclass(frozen=True, slots=True)
class Assignment:
    """One shell constant that binds a knob to a bus."""

    const: str
    """The variable name in the script, e.g. ``CC_VM_MUSIC``."""

    sink: str
    """The bus it drives."""

    label: str
    """How to say it out loud."""


#: Every knob binding the script exposes, in the order a person reads them.
#: ``CC_VM_GAME_ALT`` exists because two knobs are deliberately wired to the
#: game bus; both are editable, and neither is hidden.
ASSIGNMENTS: tuple[Assignment, ...] = (
    Assignment("CC_VM_GAME", "vm_game", "Game"),
    Assignment("CC_VM_GAME_ALT", "vm_game", "Game (second knob)"),
    Assignment("CC_VM_CHAT", "vm_chat", "Chat"),
    Assignment("CC_VM_MUSIC", "vm_music", "Music"),
    Assignment("CC_MIC_VOL", "mic_b1", "Mic B1"),
)

#: The knobs that physically exist on an LPD8.
KNOBS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)


def knob_to_cc(knob: int) -> int:
    """CC number the LPD8 sends for the knob printed *knob*."""
    if knob not in KNOBS:
        raise ValueError(f"the LPD8 has knobs 1-8, not {knob}")
    return knob - 1


def cc_to_knob(cc: int) -> int | None:
    """The knob that sends *cc*, or ``None`` if no knob does."""
    knob = cc + 1
    return knob if knob in KNOBS else None


def _pattern(const: str) -> re.Pattern[str]:
    return re.compile(rf"^(\s*{re.escape(const)}=)(\d+)(.*)$", re.MULTILINE)


def read_map(script: Path | None = None) -> dict[str, int]:
    """Current ``constant -> CC`` map, read fresh from the script.

    A missing script is not an error worth crashing a mixer over: it means
    the map cannot be shown, and the caller renders that as unknown rather
    than as "nothing is bound".
    """
    path = script or SCRIPT
    try:
        text = path.read_text()
    except OSError as exc:
        log.warning("cannot read the LPD8 map from %s: %s", path, exc)
        return {}
    found: dict[str, int] = {}
    for assignment in ASSIGNMENTS:
        match = _pattern(assignment.const).search(text)
        if match:
            found[assignment.const] = int(match.group(2))
    return found


def set_knob(const: str, knob: int, script: Path | None = None) -> bool:
    """Point *const* at the knob printed *knob*. True when the file changed.

    Writes through a temporary file in the same directory so an interrupted
    write cannot leave a half-edited script that the service would then fail
    to start with.
    """
    if const not in {a.const for a in ASSIGNMENTS}:
        raise ValueError(f"unknown LPD8 binding {const!r}")
    cc = knob_to_cc(knob)
    path = script or SCRIPT
    text = path.read_text()
    pattern = _pattern(const)
    if not pattern.search(text):
        raise ValueError(f"{const} is not set in {path}")
    new_text, count = pattern.subn(rf"\g<1>{cc}\g<3>", text, count=1)
    if count != 1 or new_text == text:
        return False
    tmp = path.with_name(path.name + ".hearth-tmp")
    tmp.write_text(new_text)
    tmp.chmod(path.stat().st_mode & 0o7777)
    tmp.replace(path)
    log.info("bound %s to knob %s (CC %s)", const, knob, cc)
    return True


def conflicts(mapping: dict[str, int]) -> dict[int, list[str]]:
    """CCs driven by more than one binding.

    Two knobs on one bus is a choice; one knob on two buses is a mistake the
    user should see before they go hunting for why a knob moves two faders.
    """
    by_cc: dict[int, list[str]] = {}
    for const, cc in mapping.items():
        by_cc.setdefault(cc, []).append(const)
    return {cc: names for cc, names in by_cc.items() if len(names) > 1}
