# LPD8 startup and reconnect LED synchronization

## Runtime change

The installed control script is `~/bin/lpd8_mixer.sh` and is invoked by the
user unit `lpd8-mixer.service`. That script is intentionally outside the Hearth
source tree because the existing remap UI edits its mapping constants in
place. The exact host-script delta is preserved in
[`patches/lpd8-startup-led-sync.patch`](patches/lpd8-startup-led-sync.patch).

The bug was a false-success path: `lpd8_led_set` swallowed every `amidi` error,
`sync_leds_to_pulse_state` therefore looked successful, and the connection loop
set `led_synced=1` after its first attempt regardless of whether the raw port
was available or all pad writes had succeeded. A brief USB/ALSA timing gap at
startup/reconnect could leave LEDs dark until the user physically pressed a
pad.

The patch makes MIDI writes return their true status and places each write and
raw-port enumeration behind a timeout. Startup initialization validates each
mute-state query, sends the five state-pad values plus explicit OFF messages to
action pads 3, 5, and 8, and only marks the connection initialized after all
eight writes succeed. On a missing port, failed state query, timeout, or failed
write, it retries once per second with retry logging rate-limited to avoid log
flooding. The same logic runs in every new `run_port_loop`, so an ALSA
sequencer disconnect/reconnect gets a fresh sync as well. Individual pad-event
LED failures remain non-fatal to the mixer loop.

## Verification recorded on 2026-10-28

- `bash -n ~/bin/lpd8_mixer.sh` passes.
- A fake-device test exercised an empty raw-port listing on the first attempt,
  a failed first MIDI write on the second attempt, and a successful third
  attempt. The third attempt emitted all eight expected messages on channel 10:
  pads 1, 2, 4, 6, and 7 lit for the current unmuted buses/devices; pads 3, 5,
  and 8 explicitly dark. No real device was touched by this fake-device test.
- A direct, state-only sync to the real LPD8 at `hw:6,0,0` returned success for
  all eight writes. The `lpd8-mixer.service` was restarted once to load the
  patch; the new log reports `LED sync complete: music=1 chat=1 astro=1 mic=1
game=1 (action pads dark)`. The service is active/running afterwards.
- The Game/Chat monitor routes, all four VM-bus volume/mute states, and the
  audio-routing service were checked after restart and remained at baseline.

The MIDI command's successful exit and the service log prove that the host sent
the initialization messages; they cannot optically verify the physical LEDs.
If the pads are still dark despite the new `LED sync complete` line, verify the
LPD8's active pad mode/preset accepts incoming MIDI Note On feedback. The
script cannot infer a pad's visible color from a successful MIDI write alone.
