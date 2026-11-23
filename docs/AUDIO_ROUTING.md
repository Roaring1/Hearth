# Audio routing behavior

Hearth's route checkboxes describe the **live PipeWire/PulseAudio graph**, not a
separate saved layout. The mixer uses `pactl` from PipeWire's PulseAudio
compatibility layer to create and remove `module-loopback` routes.

## Output destinations

Selecting an output requests a loopback from that strip's exact monitor source
to the selected sink. A small set of headset endpoints is exclusive: selecting
one removes its paired endpoint after the requested route has loaded.

The collector reads sink mute state and the route module table away from
GTK's main thread and includes the route graph in each snapshot. The mixer
therefore does not launch a module query, a sink query, and several per-output
mute queries from its GTK repaint callback. Core sink/stream/source reads are
checked; a failed query skips that poll instead of masquerading as an empty
graph and making strips look unplugged. If only the optional route query fails,
the snapshot marks routes unknown and the UI keeps the last verified route
graph.

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
overwriting each other. The writer accepts only the route and boolean keys it
owns, with fixed value choices; unknown keys and shell-unsafe values are
refused because the config is sourced by the daemon. If an owned key appears
more than once, every occurrence is rewritten so a later shell assignment
cannot silently override the UI choice. Symlinked/non-regular
config paths and invalid UTF-8 are treated as unreadable; original bytes are
preserved and edits fail closed. If the lock, read, or
replacement cannot be completed, the edit fails closed. Hearth does not apply a
Carla live-link change when its config update failed. If the config is saved but
the pw-link executable cannot start or Carla is unavailable, the saved choice is
retained and a warning is logged so the next patch launch can apply it. A saved route may take the router daemon up
to one polling interval to converge; the UI schedules a refresh after that
interval.

## Volume and mute controls

Bus and per-app fader values are coalesced on a shared background worker:
it keeps the latest pending value per sink or stream and dispatches at most one
batch about every 30 ms. Rapid pointer movement therefore doesn't spawn one
`pactl` process per GTK event, and the process-launch path never blocks GTK.
Mute, app-move and default-sink changes remain fire-and-forget. The audio
wrapper returns whether `pactl` could be started; that is **not** a guarantee
that the child later exited successfully. If a worker or executable cannot be
started, Hearth refreshes the displayed value from the collector and shows an
explanatory tooltip. Route creation and removal use a checked, bounded command
because those operations need explicit ordering and a live-graph verification
step.

## Setup panel: headset target and latency

The Setup panel saves the headset endpoint and LATENCY_MSEC together in
the mixer configuration file under ~/.config/hearth/roaring_mixer.conf (or
the legacy path selected when the new file is absent). Both assignments are
edited in one locked, atomic transaction on a background worker; comments and
unrelated keys are preserved. The click handler does not wait for the lock or
fsync, and the target/latency controls remain disabled until completion. A
missing, malformed, unreadable, or symlinked config is not silently recreated
or overwritten. If the write fails, Setup reports **Save failed** and does
not restart the routing service.

After a successful save, Setup requests a routing-service restart. A Saved*
label means the file was persisted but systemctl could not be started; the
tooltip explains that the service must be restarted manually. A plain Saved
means the config was written and the restart command was successfully launched,
not that systemd has finished the transition. Hearth refreshes the status
after the asynchronous operation.

## Moonlight microphone source

The Setup panel's Moonlight Stream/Chat choice updates the
roaring-moonlight-mic.service unit's Environment=MIC_SOURCE= assignment.
Hearth validates the choice against the two supported bus names, takes a
private advisory lock, preserves the unit's other lines and permissions, and
replaces the file atomically. If the unit file is missing, malformed,
unreadable, symlinked, or lacks the expected assignment, the write fails closed
and systemd is not reloaded or restarted.

When a save fails, the Setup radio buttons revert to the previous choice and
the error remains visible. If the file is saved but Hearth cannot queue the
background restart task, the new choice remains selected and the note requests
a manual service restart. Otherwise Hearth reports that the setting was saved
and the routing restart was requested. This is not a claim that the
asynchronous systemd transition has finished.

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
