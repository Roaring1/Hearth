"""Pad LEDs on the LPD8, pushed from Hearth.

``lpd8_mixer.sh`` already lights a pad when *it* handles a press, and syncs
every LED once at connect time. What it cannot do is notice a mute that
happened somewhere else -- clicking MUTE in this window, or another app
muting a sink -- so the pad stayed lit over a silent bus and the surface
quietly started lying about the state of the rig.

This module writes the same bytes the shell script writes, so both paths
agree:

* pad *n* (1-8) is note ``59 + n``;
* the message is a Note On on :data:`DEFAULT_CHANNEL` (``LED_MIDI_CHANNEL``
  in ``roaring_mixer.conf``);
* velocity 127 lights the pad, velocity 0 clears it;
* **lit means audible.** A muted channel is a dark pad, which matches the
  script's own ``sync_leds_to_pulse_state``.

Two hardware truths are deliberately not hidden. The LPD8 only accepts LED
writes in NOTE TOGGLE mode -- in CC mode the device owns its lights and
these messages are ignored -- and the pad may simply be unplugged. Neither
is an error worth interrupting a mixer for, so every failure here is
swallowed and logged.
"""

from __future__ import annotations

import logging

from hearth import proc

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


def find_port(timeout: float = 2.0) -> str | None:
    """Ask ALSA where the LPD8 is, or ``None`` if it cannot be asked.

    ``None`` covers both "unplugged" and "``amidi`` is not installed". The
    caller treats them the same -- there is nothing to light either way --
    but they are not reported to the user as a fault.
    """
    if not proc.have("amidi"):
        return None
    result = proc.run(["amidi", "-l"], timeout=timeout, check=False)
    if not result.ok:
        return None
    return parse_port(result.out)


def set_pad(port: str, pad: int, lit: bool, channel: int = DEFAULT_CHANNEL) -> bool:
    """Light or clear one pad. False when the write did not land."""
    if not port or not proc.have("amidi"):
        return False
    try:
        payload = message(pad, lit, channel)
    except ValueError:
        log.debug("refusing to write LED for pad %s on channel %s", pad, channel)
        return False
    result = proc.run(["amidi", "-p", port, "-S", payload], timeout=2.0, check=False)
    if not result.ok:
        # NOTE TOGGLE mode, or the pad went away mid-session. Either way
        # the mixer keeps working; only the lamp is wrong.
        log.debug("LED write failed on %s: %s", port, payload)
    return result.ok


def set_sink_mute(port: str, sink: str, muted: bool, channel: int = DEFAULT_CHANNEL) -> bool:
    """Push one bus's mute state to its pad. False when nothing was sent."""
    pad = pad_for_sink(sink)
    if pad is None:
        return False
    return set_pad(port, pad, not muted, channel)
