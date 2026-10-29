# LPD8 startup state initialization and LED-feedback limitation

## Verified hardware capability

The attached device identifies as USB `09e8:0075`, the original/classic Akai
LPD8 (not the LPD8 MkII). The original Akai LPD8 Quickstart Guide's MIDI
Implementation Chart lists note numbers `000–127` under Transmit/Export and
`n/a` under Recognize/Import. Therefore this unit does not accept incoming MIDI
Note On/Off messages as a means of setting its pad lights.

Reference: [Akai LPD8 Quickstart Guide, MIDI Implementation Chart (page 5)](https://static.bhphotovideo.com/lit_files/84742.pdf).

## Runtime behavior

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
- Logs explicitly that hardware LED feedback is disabled, rather than claiming
  visible LEDs synchronized.
- Still reads the actual Pulse/PipeWire mute states on startup/reconnect and
  seeds Hearth's internal CC/Program toggle state, so the first physical pad
  press is interpreted against real mixer state.
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
- The device's pad LEDs cannot be initialized dynamically through the classic
  LPD8 MIDI Note protocol. Use a controller with inbound LED feedback (such as
  an appropriately configured LPD8 MkII) if the app must show mute state on
  the pad surfaces. Mixer controls continue to work independently of this
  limitation.
