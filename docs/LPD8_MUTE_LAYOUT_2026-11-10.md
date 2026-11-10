# LPD8 mute-state layout - 2026-11-10

## Physical layout

The connected controller is the original/classic Akai LPD8 (USB `09e8:0075`),
not the MkII. The drawn control layout follows the hardware: pads 5-8 are the
back row, left to right; pads 1-4 are the front row, left to right.

| Pad | Assignment | Hardware behavior | Light meaning |
|---:|---|---|---|
| 1 | Mute/unmute the processed B1 microphone | Toggle | Lit = muted; dark = normal/unmuted |
| 2 | Mute/unmute the Astro A50 headset | Toggle | Lit = one or both headset outputs muted; dark = both normal |
| 3 | Show/focus Hearth | Momentary | Only lit while physically pressed |
| 4 | Unassigned | Momentary | No action |
| 5 | Unassigned | Momentary | No action |
| 6 | Unassigned | Momentary | No action |
| 7 | Unassigned | Momentary | No action |
| 8 | Toggle Scarlett desktop-speaker mirror | Momentary | Only lit while physically pressed |

Pad 3 is Show Hearth rather than a show/hide toggle. Showing Hearth is
idempotent and easy to recover; hiding it would create a second way to lose
the mixer window. Pad 8 retains the existing `toggle_scarlett_speakers.sh`
action. Pads 4-7 are unassigned in the default layout. The Remap window may
place either momentary action on any of pads 3-8, but it cannot place a mute
toggle on those pads or a momentary action on pads 1-2.

## What the mute pads control

Pad 1 changes the processed B1 path (`mic_b1` sink and `b1_mic` source)
together. It does not mute the raw Scarlett capture device, stop Carla, stop
the microphone router, or stop the normal microphone services.

Pad 2 changes both Astro A50 output endpoints, game and chat, together. The
pad indication is considered muted if either endpoint is muted, so a partial
mute cannot appear completely normal.

The software mirror in Hearth uses the same polarity: a lit mute pad means
muted; a dark pad means normal. The LPD8 control window also shows the live
state it reads from PipeWire, independently of the hardware's local lamp.

## Controller preset behavior

The classic LPD8 does not accept ordinary incoming MIDI Note On/Off messages
to set its pad LED brightness/state. It does, however, support editor-only
SysEx for reading and writing the four stored programs. The reverse-engineered
[LPD8 editor SysEx reference](https://github.com/charlesfleche/lpd8editor/blob/master/doc/SYSEX.md)
defines each pad's behavior byte as `0 = Momentary`, `1 = Toggle`.

All four stored programs were read from the physical controller, updated, and
read back. Pads 1 and 2 are Toggle; pads 3-8 are Momentary. Pad note, program,
and CC assignments, the program MIDI channel, and all eight knob settings
were preserved. The active program remains program 4.

This is not the same as host-driven LED feedback. The original LPD8's normal
Note input is transmit-only: the controller cannot be told to light or darken
pad 1/2 later when a mute is changed through Hearth, a desktop mixer, or another
application. In those cases, Hearth's on-screen indicator remains authoritative
for the live audio state; the physical lamp reflects the LPD8's own local Toggle
latch. Pad presses through the controller keep the local latch and mute action
in step. A later external mute change can make them differ until the pad is
used again.

## Every-login default

`lpd8-boot-defaults.service` runs after PipeWire, the B1 bus, and the audio route
services are available. It explicitly unmutes `mic_b1`, `b1_mic`, the A50 chat
output, and the A50 game output, then reads each state back. It does not restore
old mute bits from a previous login. `lpd8-mixer.service` is ordered after this
initializer so its first software toggle state is seeded from the same known
unmuted state that the controller's freshly-reset Toggle pads display as dark.

The initializer also enables the Scarlett desktop-speaker mirror by ensuring
all six game/chat/music-to-Scarlett links exist. It uses the existing safe
mute-before-link helper and verifies the final links and Scarlett unmute state.
Pad 8 remains a normal user-controlled toggle after login; the next reboot
restores it to ON regardless of the saved marker state.

The initializer does not stop, pause, or restart the capture path, Carla, the
router, or the Moonlight/Vesktop microphone handling.

## Verification

- Original device identity: USB `09e8:0075`, raw MIDI `hw:6,0,0`.
- All four program banks read back with behavior flags `[1, 1, 0, 0, 0, 0, 0, 0]`.
- The SysEx readback comparison verified that note/PC/CC assignments, channel,
  and knob data were unchanged.
- `bash -n` passes for the live LPD8 mixer and boot initializer.
- Hearth's full check gate: 515 tests passed in the final Hearth quality gate.
