# Audio incident: playback lost after WirePlumber restart

**Date:** 2026-11-19 (Pacific)
**Status (2026-11-23):** The temporary Game-endpoint workaround remains removed and the original target/VM-GAME level restoration was verified at the time of the incident. A repository implementation of the graph verifier, conservative one-shot route guardian, replacement daemon script and isolated PipeWire integration test is now staged and locally validated. **It has not been deployed.** No live unit, service, route, gain, mute, default sink, PipeWire, WirePlumber or Carla state was changed as part of that implementation.

## Summary

During a Microsoft Teams meeting in Firefox, playback became inaudible after the user ran:

```bash
systemctl --user restart wireplumber.service
```

PipeWire and PipeWire-Pulse remained running. WirePlumber restarted successfully, but playback did not recover for the user. The emergency workaround duplicated `vm_game.monitor` to the Astro A50 **Game** endpoint. The user reported that this sounded spatial/"3D" and was disorienting. That was the wrong endpoint for normal desktop and meeting audio.

The intended headset destination is **Astro A50 Chat**. The persistent mixer configuration already selects `stereo-chat`; `stereo-game` must not be used as an automatic recovery destination.

## What happened (verified timeline)

- PipeWire and PipeWire-Pulse had been running since 17:10:10.
- WirePlumber logged graph/link warnings at 18:29:46 and 18:29:49:
  - `Object activation aborted: proxy destroyed`
  - `link failed: some node was destroyed before the link was created`
- WirePlumber was stopped and started at 18:34:16. PipeWire and PipeWire-Pulse were not restarted.
- During recovery inspection, the default sink was still `vm_game`; the Teams-related Firefox playback streams were assigned to the virtual game/music buses. The virtual game bus was at 35%, and the Astro Chat sink was unmuted at 100%.
- Moving the Firefox stream directly to an Astro hardware sink with `pactl move-sink-input` failed with `Invalid argument`. This was not a valid recovery path and did not establish the cause.
- A temporary loopback was added from `vm_game.monitor` to `alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game` (module ID `536870930`). `vm_game` was raised to 80% for the workaround.
- After the meeting, module `536870930` was explicitly unloaded. `vm_game` was restored to 35% and remains unmuted.
- Verification after cleanup: the Game endpoint is `SUSPENDED`; the three normal bus routes remain aimed at Astro Chat; the Astro Chat endpoint is unmuted at 100%. The live PipeWire graph currently shows both channel links from each of `vm_game`, `vm_chat`, and `vm_music` through its loopback to Astro Chat.

## Cause assessment

### Confirmed

1. The user action immediately preceding the incident was a WirePlumber restart.
2. WirePlumber emitted link/activation errors shortly before that restart.
3. The route daemon (`roaring-audio-routesd.service`) remained running across the WirePlumber restart. It started at 17:10:10, had zero systemd restarts, and its unit has `After=`/`Wants=` ordering but no restart coupling such as `PartOf=wireplumber.service`.
4. The route daemon checks the Pulse module table and recreates a route only when the `module-loopback` is absent. It does not verify the PipeWire port-to-port links for an existing module. Its log contains no incident-time detection or repair event.
5. The intended Astro target is explicitly `alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat`, not the Game endpoint.

### Most likely explanation (not proven)

The WirePlumber restart interrupted or invalidated one or more session-manager graph links. A loopback module can still exist in PipeWire-Pulse while its effective port links are missing or stale. The route daemon's module-existence check could therefore report the route as present without proving that audio can reach the headset.

This fits the pre-restart link warnings and the gap in the route daemon's health check. However, the graph was not captured during the silent interval. The post-incident graph is healthy, so the exact missing edge cannot now be identified with certainty.

### Not established

- We have not proven whether the failed edge was source-monitor to loopback input, loopback output to Astro Chat playback, or a hardware/profile/output issue downstream.
- We have not established that the 35% VM-GAME level caused silence. It was the previously observed setting and has been restored.
- The direct stream-move error is not evidence of the root cause.
- The separate Focusrite/USB disconnect observed earlier that afternoon concerns device enumeration and microphone input. It should not be conflated with this playback incident without further evidence.

## What was repaired immediately

- Removed the temporary Game-channel loopback, module `536870930`.
- Restored `vm_game` to 35%, unmuted.
- Left the normal Game, Chat, and Music bus routes directed to Astro Chat.
- Verified Astro Chat is unmuted at 100% and Astro Game is suspended after removal of the temporary route.
- Did not restart PipeWire, PipeWire-Pulse, Carla, or microphone services during the recovery/cleanup.

## Recommended fixes, in priority order

### P0: Make Astro Chat the only automatic headset recovery target

- Keep `ASTRO_TARGET=...stereo-chat` as the single normal target.
- Never fall back automatically to `stereo-game`, even when Chat is unavailable.
- If Chat is missing or cannot be linked, preserve the virtual buses, report an explicit unavailable/degraded state, and ask for an intentional user choice. Do not silently send meeting audio to the Game endpoint.
- Do not move application streams directly from their virtual buses to the hardware headset. That bypasses the mixer architecture and can undermine recording, sharing, and per-app routing.

### P1: Verify the actual graph, not just module existence

For each required route (`vm_game`, `vm_chat`, `vm_music` to Astro Chat), verify all four stereo links:

1. source monitor FL -> that loopback's input FL
2. source monitor FR -> that loopback's input FR
3. that loopback's output FL -> Astro Chat playback FL
4. that loopback's output FR -> Astro Chat playback FR

Treat a failed graph query as **unknown**, not as an empty graph or a confirmed broken route. Distinguish these states:

- module missing
- module present but graph links incomplete
- graph links present but sink unavailable/suspended unexpectedly
- graph healthy
- inspection unavailable

When a specific route is broken, repair only that route. Prefer idempotent relinking if the nodes and ports still exist. If that is not safe, unload and recreate only the affected loopback, then verify all four links. Do not unload all audio modules or restart the whole audio stack.

### P1: Couple route recovery to WirePlumber lifecycle

- Evaluate adding `PartOf=wireplumber.service` to `roaring-audio-routesd.service`, with explicit ordering so the route daemon comes back after WirePlumber.
- Confirm the exact systemd restart-propagation behavior in a disposable test unit before applying it to the live audio graph.
- Regardless of unit coupling, the route daemon must verify the graph after WirePlumber returns. Merely restarting the daemon is not enough if it checks only module IDs.

### P2: Add bounded, low-noise recovery and alerting

- Keep the existing lightweight polling philosophy; do not add a high-frequency `pactl`/`pw-link` storm.
- Prefer reacting to PipeWire/WirePlumber node/link changes, with a modest periodic verification as a fallback.
- Require a short debounce/stability window before repair so normal graph churn does not cause needless reloads.
- Add per-route cooldowns and a small retry limit. If repair repeatedly fails, stop retrying and surface the precise failed edge.
- Log detection, repair attempt, before/after graph state, and outcome. The current route log should not remain silent while a link is missing.
- Never alter bus gain, mute, or the default virtual sink as a side effect of route repair.

### P2: Make the mixer report effective playback health

Show the selected destination as Astro A50 Chat and distinguish configured intent from live graph health. A checkbox or module row alone must not imply audible playback. Useful states include `Connected`, `Link missing`, `Headset unavailable`, and `Status unknown`. Include a one-click safe repair that targets only the missing Astro Chat route.

### P3: Add regression coverage

Add recorded-graph/unit tests and a safe integration harness for:

- all three expected bus routes healthy
- loopback module exists but one or more PipeWire links are missing
- one channel missing while the other remains linked
- Astro Chat disappears and later returns
- WirePlumber restarts while PipeWire-Pulse and route modules remain alive
- route module IDs change after recovery
- duplicate loopback prevention
- failed graph inspection reports unknown and causes no destructive mutation
- automatic recovery never chooses Astro Game
- recovery leaves bus volumes, mute state, default sink, mic routes, Carla, and screen-share routes untouched

Test recovery against disposable virtual nodes before testing against the live headset.

## Operating guidance until the permanent fix lands

- Do not use Astro Game as a generic fallback for calls or desktop audio. The user found that route spatialized/disorienting.
- If playback fails after a WirePlumber restart, inspect the PipeWire graph and the Astro Chat route first. Avoid broad audio-stack restarts during a live call.
- Keep applications on their intended virtual buses; do not bypass them by moving the application stream straight to the physical headset.
- Do not change the user's stored gain levels as part of a route repair.

## Implementation checkpoint (2026-11-23)

The repository now contains the graph-level verifier, conservative exact-route planner, one-shot `hearth.audio.route_guard` and a proposed `scripts/roaring_audio_routesd.sh` replacement. The unit tests cover healthy, degraded, ambiguous, missing-endpoint, inventory-failure, cooldown and exact-edge repair cases. A private-runtime PipeWire integration test creates a real virtual loopback, removes both a capture-side and playback-side edge in turn, repairs each exact missing edge, and verifies the result is healthy and idempotent. It passed locally on 2026-11-23.

The code also hardens cooldown-state and lock files against symlinks, hard links, non-regular files and foreign ownership. Corrupt cooldown state now fails closed instead of silently resetting the retry guard.

**Nothing from this checkpoint is installed.** The current `roaring-audio-routesd.service` unit and its live script/configuration were not changed. The new shell script has not replaced the live daemon. Do not infer deployment from passing tests or a committed PR.

## Remaining implementation and deployment work

1. Review the current installed unit, ExecStart path, environment and permissions without changing them.
2. In a disposable systemd user-manager test, verify the proposed WirePlumber lifecycle ordering and restart propagation. Do not add `PartOf=` to the live unit based only on static reasoning.
3. Review the five-second polling cadence, duplicate route behavior, debounce and cooldown as a whole. The current guardian has per-route cooldowns but does not yet implement the full debounce/retry/alerting policy described above.
4. Prepare a staged, rollback-capable installer or deployment path only after the branch is merged and unit behavior is independently verified.
5. Keep Astro Chat as the only automatic target. Astro Game fallback remains explicitly prohibited.

The implementation and integration test do not prove the exact missing edge from the November 19 incident because that failed graph was not captured at the time.
