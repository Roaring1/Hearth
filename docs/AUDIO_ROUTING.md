# Audio routing behavior

Hearth's route checkboxes describe the **live PipeWire/PulseAudio graph**, not a
separate saved layout. The mixer uses `pactl` from PipeWire's PulseAudio
compatibility layer to create and remove `module-loopback` routes.

## Output destinations

Selecting an output requests a loopback from that strip's exact monitor source
to the selected sink. A small set of headset endpoints is exclusive: selecting
one removes its paired endpoint after the requested route has loaded.

Route mutations run away from GTK's main thread and have a bounded command
timeout. While a mutation is pending, route controls on that strip are disabled
to prevent overlapping load/unload requests. When the command completes,
Hearth reads the module table again and sets open checkboxes from the live graph.
If `pactl` rejects the operation, Hearth reports the error in the checkbox
tooltip and still reconciles the visible state with the graph. If the module
query itself fails, Hearth keeps the last known checkbox state instead of
claiming that an empty result proves the route is gone.

When removing a route, Hearth matches both the exact source name and requested
sink. It does not use a substring match for destructive operations; for example,
`vm_game.monitor-extra` is not considered the same source as
`vm_game.monitor`.

## Microphone inputs

The raw microphone router daemon reads
`~/.config/hearth/roaring_mic_router.conf`. Its `B1_ROUTE` / `B2_ROUTE` values
and `B1_ACTIVE` / `B2_ACTIVE` gates are authoritative for the raw sources it
manages. Carla's `CARLA_TO_B1` / `CARLA_TO_B2` selections live in the same file.

Hearth preserves unknown keys, ordering, and comments when editing this shared
shell config. Writes use a unique same-directory temporary file and atomic
replacement. If an existing file cannot be read or the replacement cannot be
written, the edit fails closed. Hearth does not apply a Carla live-link change
when its config update failed, and the relevant checkbox returns to its previous
state with an explanatory tooltip. A saved route may take the router daemon up
to one polling interval to converge; the UI schedules a refresh after that
interval.

## Read-only troubleshooting

These commands inspect the session without changing audio routing:

```bash
pactl info
pactl list short modules
hearth --status
```

If a route looks wrong, compare the `source=` and `sink=` arguments in the live
`module-loopback` rows with the names in the checkbox tooltip. Do not unload all
loopbacks or restart the audio session as a first diagnostic step; Hearth may
be sharing the graph with other audio applications.
