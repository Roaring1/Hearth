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
service never reads. If a write cannot complete, the Remap window shows an
error and does not queue a restart. When the script changes successfully,
Hearth requests a service restart; this reports that the request was launched,
not that systemd has completed the transition. The Setup/Remap status refresh
then reads back the actual state.

## Read-only checks

To inspect the configured mapping without changing it, open LPD8 Controls and
choose Refresh. The UI reads the script's current knob/pad values and shows
unknown mappings as unknown rather than assuming that a missing binding is
unbound. The controller's live LEDs still depend on the LPD8 being in Note
Toggle mode; the mapping file alone cannot change the device's hardware mode.
