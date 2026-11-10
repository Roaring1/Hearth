# LPD8 startup state initialization and LED-feedback limitation

## Verified hardware capability

The attached device identifies as USB `09e8:0075`, the original/classic Akai
LPD8 (not the LPD8 MkII). The original Akai LPD8 Quickstart Guide's MIDI
Implementation Chart lists note numbers `000–127` under Transmit/Export and
`n/a` under Recognize/Import. Therefore this unit does not accept incoming MIDI
Note On/Off messages as a means of setting its pad lights. That restriction is
specific to ordinary Note feedback; the original unit does support editor-only
SysEx for reading and writing its stored pad/knob programs, including each
pad's Toggle/Momentary behavior byte.

Reference: [Akai LPD8 Quickstart Guide, MIDI Implementation Chart (page 5)](https://static.bhphotovideo.com/lit_files/84742.pdf).

## Runtime behavior

The per-pad behavior configuration is documented in the
[reverse-engineered LPD8 editor SysEx reference](https://github.com/charlesfleche/lpd8editor/blob/master/doc/SYSEX.md).
The active hardware was read as program 4. All four stored programs now use
Toggle for pads 1/2 and Momentary for pads 3-8; readback confirmed that the
pad assignments, channel and knob values were preserved.

The installed script is `~/bin/lpd8_mixer.sh`, invoked by `lpd8-mixer.service`.
It remains outside the Hearth source tree because the remap UI edits mapping
constants in place. Its complete host-side delta is preserved in
[`patches/lpd8-startup-led-sync.patch`](patches/lpd8-startup-led-sync.patch).
Hearth's separate Python LED worker also now ignores raw ports unless their
reported model explicitly identifies the feedback-capable LPD8 MkII; this
prevents the GTK mixer from repeatedly trying unsupported feedback on the
classic unit. The controller drawing in the app is a software indicator, not
physical LED readback.

The earlier startup patch improved timeout/retry handling but incorrectly
assumed the attached hardware could accept Note On/Off feedback. The MIDI
command's successful exit only showed that the host-side MIDI utility accepted
the write; it did not prove the device changed its LEDs. The user confirmed that
all eight pads remained dark and only the hardware `PAD` mode indicator was lit.

The current script now:

- Defaults `LED_FEEDBACK_SUPPORTED=0` for this classic LPD8 and does not send
  futile Note On/Off messages to it.
- Uses lit = muted and dark = normal for the two hardware mute toggles.
- Maps pad 1 to the processed B1 microphone, pad 2 to both A50 outputs, pad 3
  to Show Hearth, pads 4-7 to no action, and pad 8 to the existing desktop
  speaker-mirror toggle.
- Ignores Note-Off releases for momentary action pads 3 and 8, so a long press
  cannot fire Show Hearth or the speaker toggle twice.
- Reads the actual Pulse/PipeWire mute states on startup/reconnect and seeds
  Hearth's internal CC/Program toggle state after the boot-default unit has
  unmuted B1 and both A50 endpoints.
- Retains the timeout/retry/write-validation path behind an explicit
  `LED_FEEDBACK_SUPPORTED=1` opt-in for a different controller verified to
  support incoming Note On/Off pad-light control. Do not enable this setting for
  USB device `09e8:0075`.

## Verification

- `bash -n ~/bin/lpd8_mixer.sh` and ShellCheck pass after the capability correction.
- Three regression tests verify that a classic `LPD8 MIDI 1` port is not selected for LED feedback, the supported-port parser requires an explicit MkII label, and `find_port()` skips the classic controller. The original generic `parse_port()` remains available for connection diagnostics.
- The earlier fake-device test validated recovery from a missing raw MIDI port
  and transient write failure. That proves retry behavior only, not physical LED
  support, and is not evidence of actual LEDs illuminating.
- The actual device is detected by `lsusb` as `09e8:0075`; `amidi -l` lists
  `hw:6,0,0 LPD8 MIDI 1`.
- The device's pad LEDs cannot be updated dynamically through the classic
  LPD8 MIDI Note protocol. The stored Toggle behavior still lets the physical
  pad light follow pad presses; it cannot reflect a later mute change made by
  another PC application. The Hearth window remains the live-state indicator.
  A boot-default service starts the B1 microphone and both headset outputs
  unmuted so the fresh dark pads and PC state agree at login. It also enables
  and verifies the six desktop-speaker mirror links to Scarlett on every login;
  pad 8 remains a manual toggle during the session.
