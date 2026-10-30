"""Optional MIDI pad-light feedback for compatible control surfaces.

This module can send Note On/Off messages on controllers that explicitly
support incoming MIDI notes for pad-light control (such as a properly
configured LPD8 MkII). The attached classic LPD8, USB ID ``09e8:0075``, is
transmit-only for notes according to Akai's original MIDI implementation
chart. It cannot mirror Hearth's mute state on its physical pad LEDs, even
when ``amidi`` exits successfully. ``find_port`` therefore opts in only when
the device label explicitly identifies the feedback-capable MkII; ordinary
classic ``LPD8 MIDI 1`` ports are ignored.

For compatible hardware:

* pad *n* (1-8) is note ``59 + n``;
* the message is a Note On on :data:`DEFAULT_CHANNEL`;
* velocity 127 requests a lit pad, velocity 0 requests a dark pad.

A failed/unavailable feedback write must never interrupt audio mixing. The
mixer UI is the source of truth for mute state regardless of physical LEDs.
"""

from __future__ import annotations

import logging

from hearth import proc
from hearth.errors import CommandError

log = logging.getLogger(__name__)

#: Note On, channel 10 (0x99). The shell script's default, and its conf key.
DEFAULT_CHANNEL = 9

#: Pad 1 is note 60. Kept as an offset rather than a table because the
#: device is contiguous and a table would just be eight ways to be wrong.
FIRST_NOTE = 60

#: Which pad watches which bus, mirroring the pad handlers in
#: ``~/bin/lpd8_mixer.sh``. Pads 3, 5 and 8 are action pads -- they fire
#: something rather than holding a state -- so nothing maps to them.
PAD_FOR_SINK: dict[str, int] = {
    "vm_music": 1,
    "vm_chat": 2,
    "vm_game": 7,
}

#: Where the truth lives, so a later edit to the script is known to need a
#: matching edit here.
MAP_SOURCE = "~/bin/lpd8_mixer.sh"


def pad_for_sink(sink: str) -> int | None:
    """The pad that shows this sink's mute state, if any."""
    return PAD_FOR_SINK.get(sink)


def note_for_pad(pad: int) -> int:
    """MIDI note for pad ``1``-``8``."""
    if not 1 <= pad <= 8:
        raise ValueError(f"pad out of range: {pad}")
    return FIRST_NOTE + pad - 1


def message(pad: int, lit: bool, channel: int = DEFAULT_CHANNEL) -> str:
    """The ``amidi -S`` hex string that sets one pad LED.

    Formatted exactly like the shell script's ``printf '%02X %02X %02X'``
    so the two implementations can be compared by eye.
    """
    if not 0 <= channel <= 15:
        raise ValueError(f"midi channel out of range: {channel}")
    status = 0x90 | channel
    velocity = 127 if lit else 0
    return f"{status:02X} {note_for_pad(pad):02X} {velocity:02X}"


def parse_port(listing: str) -> str | None:
    """Pull the LPD8's raw ALSA port out of ``amidi -l`` output.

    ``amidi -l`` prints ``IO  hw:6,0,0  LPD8``; the script takes field two
    of the first LPD8 line and so does this.
    """
    for line in listing.splitlines():
        if "LPD8" not in line:
            continue
        fields = line.split()
        if len(fields) >= 2:
            return fields[1]
    return None


def parse_feedback_port(listing: str) -> str | None:
    """Return only a raw LPD8 port whose label identifies the LED-capable MkII.

    The classic LPD8's MIDI implementation is transmit-only for Note messages;
    it must not be mistaken for a feedback-capable device just because its raw
    ALSA port is writable. Keep :func:`parse_port` separately available for
    diagnostics that need to identify any LPD8 generation.
    """
    for line in listing.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3 or "lpd8" not in fields[2].lower():
            continue
        model = fields[2].lower().replace("-", " ").replace("_", " ")
        if "mk2" in model or "mkii" in model:
            return fields[1]
        log.debug(
            "not enabling pad LED feedback for unsupported/unverified controller: %s", fields[2]
        )
    return None


def find_port(timeout: float = 2.0) -> str | None:
    """Find a known LED-feedback-capable LPD8, or ``None`` if unavailable.

    A classic ``LPD8 MIDI 1`` port is intentionally not returned: the original
    hardware does not recognize incoming Note On/Off messages, so issuing writes
    to it would create a false impression that physical pad lights can sync.
    """
    if not proc.have("amidi"):
        return None
    try:
        result = proc.run(["amidi", "-l"], timeout=timeout, check=False)
    except CommandError as exc:
        # A slow/unavailable controller is not a mixer failure. Don't let a
        # timeout escape into the UI worker.
        log.debug("could not list LPD8 ports: %s", exc)
        return None
    if not result.ok:
        return None
    return parse_feedback_port(result.out)


def set_pad(port: str, pad: int, lit: bool, channel: int = DEFAULT_CHANNEL) -> bool:
    """Light or clear one pad. False when the write did not land."""
    if not port or not proc.have("amidi"):
        return False
    try:
        payload = message(pad, lit, channel)
    except ValueError:
        log.debug("refusing to write LED for pad %s on channel %s", pad, channel)
        return False
    try:
        result = proc.run(["amidi", "-p", port, "-S", payload], timeout=2.0, check=False)
    except CommandError as exc:
        # NOTE TOGGLE mode, or the pad went away mid-session. A timed-out or
        # unavailable MIDI write only loses LED feedback; audio must keep
        # working and the worker must be able to invalidate this port.
        log.debug("LED write failed on %s: %s", port, exc)
        return False
    if not result.ok:
        log.debug("LED write failed on %s: %s", port, payload)
    return result.ok


def set_sink_mute(port: str, sink: str, muted: bool, channel: int = DEFAULT_CHANNEL) -> bool:
    """Push one bus's mute state to its pad. False when nothing was sent."""
    pad = pad_for_sink(sink)
    if pad is None:
        return False
    return set_pad(port, pad, not muted, channel)
