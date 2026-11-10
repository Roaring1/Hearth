# LPD8 remapping

The LPD8 Controls window edits the constants in the script the running
`lpd8-mixer.service` actually reads. A knob assignment changes one `CC_*`
constant; a pad assignment updates the corresponding note/CC/program values
as one script edit so its mapping stays consistent across the controller modes.

## Safe edits

Remapping holds a private advisory lock across the complete read/modify/write
transaction. This prevents simultaneous edits to different controls from
silently overwriting each other. The updated script is written to a unique
temporary file in the same directory, flushed, synced, given the original file
mode, and atomically renamed over the script. The previous fixed
`.hearth-tmp` file is no longer used, so an old temp-path symlink cannot redirect
the write. The lock path is also protected against symlinks.

Missing assignments remain errors instead of being appended somewhere the
service never reads. The map write runs on a background worker, and the pad/knob
controls are disabled until it finishes so concurrent edits cannot race. If
the write or worker startup cannot complete, the Remap window shows an error
and does not queue a restart. When the script changes successfully, Hearth
requests a service restart; this reports that the request was launched, not
that systemd has completed the transition. The Setup/Remap status refresh then
reads back the actual state.

## Current pad layout

The available pad actions now match the 2026-11-10 physical layout:

- Pad 1: mute/unmute the processed B1 microphone.
- Pad 2: mute/unmute both Astro A50 output endpoints.
- Pad 3: Show Hearth (one-shot action).
- Pads 4-7: unassigned.
- Pad 8: toggle the Scarlett desktop-speaker mirror.

The mute-pad polarity is lit = muted and dark = normal/unmuted. Pads 1/2 use
hardware Toggle behavior; pads 3-8 use Momentary behavior. The four hardware
banks were set through the original LPD8's editor-only SysEx protocol and read
back. The Remap choices are filtered by those stored modes: mute actions can
only be placed on pads 1/2, and one-shot actions can only be placed on pads
3-8. The script remap itself still edits only note/CC/program mapping values;
it does not change the controller's stored Toggle/Momentary mode. See
[`LPD8_MUTE_LAYOUT_2026-11-10.md`](LPD8_MUTE_LAYOUT_2026-11-10.md) for the
verified assignments, hardware limitation, and boot initialization.

## Read-only checks

To inspect the configured mapping without changing it, open LPD8 Controls and
choose Refresh. The UI reads the script's current knob/pad values and shows
unknown mappings as unknown rather than assuming that a missing binding is
unbound. The systemd unit state is queried on a background worker when the
map is refreshed; if systemd is unreachable, Hearth does not report a
confident "service stopped" state from a failed query.
