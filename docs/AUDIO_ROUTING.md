# Audio routing behavior

Hearth's route checkboxes describe the **live PipeWire/PulseAudio graph**, not a
separate saved layout. The mixer uses `pactl` from PipeWire's PulseAudio
compatibility layer to create and remove `module-loopback` routes.

## Output destinations

Selecting an output requests a loopback from that strip's exact monitor source
to the selected sink. A small set of headset endpoints is exclusive: selecting
one removes its paired endpoint after the requested route has loaded.

Route mutations and the follow-up module-table query run away from GTK's main
thread, with bounded command timeouts. While a mutation is pending, the
initiating checkbox and output destination controls on that strip are disabled;
other input-route toggles are rejected and restored while the strip is busy.
This prevents overlapping load/unload requests without freezing the mixer.
If `pactl` rejects the operation, Hearth reports the error in the checkbox
tooltip and still reconciles the visible state with the graph. Each asynchronous
query carries a generation number, so a late result cannot overwrite a newer
route request. If the module query itself fails or returns an empty table,
Hearth keeps the last known checkbox state instead of claiming that the route
is gone.

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
shell config. Writers serialize the full read/merge/write transaction with a
private advisory lock, then use a unique same-directory temporary file and
atomic replacement. That prevents simultaneous edits to different keys from
overwriting each other. If the lock, read, or replacement cannot be completed,
the edit fails closed. Hearth does not apply a Carla live-link change
when its config update failed, and the relevant checkbox returns to its previous
state with an explanatory tooltip. A saved route may take the router daemon up
to one polling interval to converge; the UI schedules a refresh after that
interval.

## Volume and mute controls

Fader, bus-mute, stream-mute, app-move and default-sink changes stay
fire-and-forget so frequent input events do not stall GTK. The audio wrapper
returns whether `pactl` could be started; that is **not** a guarantee that the
child later exited successfully. If the executable is missing or cannot be
started, Hearth keeps the previous mute state or refreshes the displayed
volume from the collector and shows an explanatory tooltip. Route creation and
removal use a checked, bounded command because those operations need explicit
ordering and a live-graph verification step.

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
