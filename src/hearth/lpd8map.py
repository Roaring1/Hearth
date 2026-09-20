"""Reading and rewriting the LPD8 knob and pad map.

The assignments are constants near the top of ``~/bin/lpd8_mixer.sh``
(see :data:`hearth.midi.MAP_SOURCE`). That script is the thing actually
listening to the hardware, so a remap that edited anything else would be a
lie told by the UI: the knob would still move the old bus.

This module therefore edits the script itself, in place, one constant at a
time, and nothing here guesses. If the constant is not in the file the edit
fails loudly rather than appending a line the script never reads.

Knob numbers are the numbers printed on the hardware; the LPD8 sends CC
``knob - 1`` in its default program, which is the only mapping the script
cares about.

Pads work the other way round. What each *slot* does -- slot 1 mutes music,
slot 5 saves a report -- is a ``case`` in the script and is not data. What is
data is which physical pad feeds which slot, and the script says that three
times, once per pad mode: ``NOTE_PADn`` for note mode, ``CC_PADn`` for CC
mode, ``PROG_n`` for program mode. Remapping a pad rewrites all three, or the
pad would move only in whichever mode the hardware preset happens to be in.
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

#: The pads that physically exist on an LPD8.
PADS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)


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


def _write(path: Path, text: str) -> None:
    """Replace *path* atomically, keeping its mode.

    Through a temporary file in the same directory, so an interrupted write
    cannot leave a half-edited script that the service would then fail to
    start with.
    """
    tmp = path.with_name(path.name + ".hearth-tmp")
    tmp.write_text(text)
    tmp.chmod(path.stat().st_mode & 0o7777)
    tmp.replace(path)


def _set_const(text: str, const: str, value: int, *, required: bool) -> str | None:
    """*text* with ``const`` set to *value*, or ``None`` when nothing changed.

    A missing optional constant is skipped: the script carries one pad-mode
    table that some forks do not, and refusing the whole edit over it would
    leave the pad half-moved.
    """
    pattern = _pattern(const)
    if not pattern.search(text):
        if required:
            raise ValueError(f"{const} is not set in the LPD8 script")
        return None
    new_text, count = pattern.subn(rf"\g<1>{value}\g<3>", text, count=1)
    if count != 1 or new_text == text:
        return None
    return new_text


def set_knob(const: str, knob: int, script: Path | None = None) -> bool:
    """Point *const* at the knob printed *knob*. True when the file changed."""
    if const not in {a.const for a in ASSIGNMENTS}:
        raise ValueError(f"unknown LPD8 binding {const!r}")
    cc = knob_to_cc(knob)
    path = script or SCRIPT
    text = path.read_text()
    new_text = _set_const(text, const, cc, required=True)
    if new_text is None:
        return False
    _write(path, new_text)
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


# -- pads ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PadSlot:
    """One pad slot: a job the script does, and the pad that triggers it."""

    slot: int
    """The slot number the script's ``case`` dispatches on."""

    label: str
    """What pressing it does, in plain words."""

    latching: bool
    """True when the pad holds a state, so its light means something."""

    @property
    def note_const(self) -> str:
        return f"NOTE_PAD{self.slot}"

    @property
    def cc_const(self) -> str:
        return f"CC_PAD{self.slot}"

    @property
    def prog_const(self) -> str:
        return f"PROG_{self.slot}"


#: Every pad job the script has, read off its dispatch ``case``. Slots the
#: script ignores are not listed, because offering to remap a pad that does
#: nothing is offering a setting with no effect.
PAD_SLOTS: tuple[PadSlot, ...] = (
    PadSlot(1, "Mute or unmute music", True),
    PadSlot(2, "Mute or unmute chat", True),
    PadSlot(3, "Switch the headset between chat and game", False),
    PadSlot(4, "Mute or unmute the headset", True),
    PadSlot(5, "Save a diagnostic report", False),
    PadSlot(6, "Mute or unmute the microphone", True),
    PadSlot(7, "Mute or unmute game audio", True),
    PadSlot(8, "Switch the desk speakers on or off", False),
)

#: The note the first pad sends, minus one: pad *n* sends ``59 + n``.
PAD_NOTE_BASE = 59


def pad_to_note(pad: int) -> int:
    """Note number the pad printed *pad* sends in note mode."""
    if pad not in PADS:
        raise ValueError(f"the LPD8 has pads 1-8, not {pad}")
    return PAD_NOTE_BASE + pad


def note_to_pad(note: int) -> int | None:
    """The pad that sends *note*, or ``None`` if no pad does."""
    pad = note - PAD_NOTE_BASE
    return pad if pad in PADS else None


def pad_to_cc(pad: int) -> int:
    """CC number the pad printed *pad* sends in CC mode.

    The LPD8 numbers these 9, 10 ... 15, 8 -- pad 8 wraps back to 8, which
    is why this is a formula and not ``7 + pad``.
    """
    if pad not in PADS:
        raise ValueError(f"the LPD8 has pads 1-8, not {pad}")
    return 8 + (pad % 8)


def pad_to_prog(pad: int) -> int:
    """Program number the pad printed *pad* sends in program mode."""
    if pad not in PADS:
        raise ValueError(f"the LPD8 has pads 1-8, not {pad}")
    return pad - 1


def read_pad_map(script: Path | None = None) -> dict[int, int]:
    """Current ``slot -> physical pad`` map, read fresh from the script.

    Note mode is the source of truth because it is the mode the rig runs in
    and the only one whose lights Hearth can drive. A slot whose note is not
    a pad's note is left out rather than guessed at.
    """
    path = script or SCRIPT
    try:
        text = path.read_text()
    except OSError as exc:
        log.warning("cannot read the LPD8 pad map from %s: %s", path, exc)
        return {}
    found: dict[int, int] = {}
    for pad_slot in PAD_SLOTS:
        match = _pattern(pad_slot.note_const).search(text)
        if not match:
            continue
        pad = note_to_pad(int(match.group(2)))
        if pad is not None:
            found[pad_slot.slot] = pad
    return found


def set_pad(slot: int, pad: int, script: Path | None = None) -> bool:
    """Make the pad printed *pad* drive *slot*. True when the file changed.

    All three mode tables move together. Note mode is required -- if it is
    missing the script is not one this function understands -- and the CC
    and program tables are updated when present.
    """
    slots = {s.slot: s for s in PAD_SLOTS}
    if slot not in slots:
        raise ValueError(f"unknown LPD8 pad slot {slot!r}")
    pad_slot = slots[slot]
    path = script or SCRIPT
    text = path.read_text()
    changed = False
    for const, value, required in (
        (pad_slot.note_const, pad_to_note(pad), True),
        (pad_slot.cc_const, pad_to_cc(pad), False),
        (pad_slot.prog_const, pad_to_prog(pad), False),
    ):
        new_text = _set_const(text, const, value, required=required)
        if new_text is not None:
            text = new_text
            changed = True
    if not changed:
        return False
    _write(path, text)
    log.info("bound pad slot %s to pad %s", slot, pad)
    return True


def pad_conflicts(mapping: dict[int, int]) -> dict[int, list[int]]:
    """Physical pads wired to more than one slot.

    One pad doing two jobs is never intended: the script dispatches on the
    first match, so the second job silently never fires.
    """
    by_pad: dict[int, list[int]] = {}
    for slot, pad in mapping.items():
        by_pad.setdefault(pad, []).append(slot)
    return {pad: sorted(slots) for pad, slots in by_pad.items() if len(slots) > 1}
