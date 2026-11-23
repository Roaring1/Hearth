# Audio quality watchdog: transport observability and safe rollout

## Status

This change adds the first observability/recovery-safety slice to the live B1 microphone watchdog:

- Repository-owned copy of the deployed roaring_carla_healthd.py.
- Dependency-free audio_health_transport.py shared helper.
- Unit tests for pw-top parsing, cumulative ERR counter deltas, resets, node recreation, unavailable queries, clock metadata failures and bounded persisted state.
- Scheduled-cycle integration tests proving pre/post sampling, history persistence and restart suppression when fresh transport errors overlap a confirmed B1 signal fault.

It does **not** yet implement the full signal-quality roadmap in the SiYuan audit. Arbitrary-tone tracking, calibrated broadband noise baseline, click/crackle perceptual detection, true-peak measurement, channel/phase checks and playback/output diagnostics remain subsequent phases.

## Why this change exists

The active timer invokes:

    ~/bin/roaring_carla_healthd.py --sample-seconds 8

The existing daemon already knew how to query pw-top and pw-metadata, but the scheduled path did not call those functions. This meant its normal 10-minute sample history did not contain transport evidence.

The new scheduled path takes bounded transport snapshots immediately before and after each PCM sample, then compares:

1. Current pre-sample counters against the previous scheduled sample's post-sample counters.
2. Current pre-sample counters against current post-sample counters, to find errors that appeared during the microphone sample.

The capture remains short-lived and mute-gated. This helper persists only a small allowlist of bounded graph metadata and never receives PCM buffers.

## Counter semantics

pw-top ERR is cumulative for a node. An increase is meaningful only when the node identity is the same. Therefore:

- First observation: baseline; no delta is invented.
- Same node ID, ERR increases: record a positive delta.
- Same node ID, ERR is unchanged: zero new counter events for that interval.
- Same node ID, ERR decreases: partial; treat as a counter reset, not a negative error count.
- Node ID changes or a node appears/disappears: partial; the old and new counters are not comparable.
- pw-top query failure: partial or unavailable; do not assume zero errors.
- Clock metadata query failure: retain trusted same-node ERR deltas but downgrade the overall transport status to partial.
- Quantum/rate changes: record them independently. They are configuration changes, not automatically XRUNs.

The parser records WAIT, BUSY, W/Q and B/Q as text tokens. This deliberately avoids guessing units or treating placeholder strings such as --- and ??? as numeric values.

## Recovery safety

The original guarded Carla recovery remains the owner of Carla restarts. The added rule is:

- If the B1 raw-to-processed checker confirms a fault **and** same-node PipeWire ERR counters increase during the same 8-second sample, withhold automatic Carla restart.
- Record INCORRECT_TRANSPORT_CONFLICT and notify that the fault overlaps transport errors. A scheduling/driver cause is plausible, and restarting Carla alone could hide the evidence or fail to fix it.
- If Carla recovery is allowed and the subsequent signal sample passes but transport errors increased during verification, record RECOVERED_SIGNAL_TRANSPORT_DEGRADED, not a fully healthy RECOVERED.

This is intentionally conservative. It does not itself change quantum, restart PipeWire, restart WirePlumber, or add a new transport repair controller. Those actions must remain coordinated with the existing quantum manager and route-repair owner.

## Files

- scripts/roaring_carla_healthd.py: repository-owned daemon copy with scheduled transport snapshots.
- scripts/audio_health_transport.py: parser, state sanitizer and identity-safe snapshot comparator.
- tests/test_audio_health_transport.py: transport helper tests.
- tests/test_roaring_carla_healthd_scheduled.py: scheduled-cycle integration and restart-conflict tests.

The active systemd unit still points to ~/bin/roaring_carla_healthd.py. That external file has **not** been updated. The active timer and service were read-only checked; no service restart or routing change was performed.

## Deployment contract - not yet performed

The daemon and its helper must be installed together into the same directory, because the daemon imports audio_health_transport as a sibling module. Installing only the daemon will fail at import time.

Before a later deployment:

1. Back up both existing destination paths:
   - ~/bin/roaring_carla_healthd.py
   - ~/bin/audio_health_transport.py (if it already exists).
2. Copy both repository files atomically or via a temporary staging directory, preserve executable mode for the daemon, and ensure user ownership.
3. Run Python byte-compilation and --help from the target directory as a non-capturing smoke test.
4. Initially run a shadow/diagnostic cycle with AUTO_RECOVER=0; do not enable restart action while validating new transport classifications.
5. Inspect JSON history and logs. Confirm same-node deltas, resets, node recreation, unavailable queries and metadata failures are classified as designed.
6. Only after review, consider restoring the existing AUTO_RECOVER policy. The transport-conflict guard still applies.
7. Verify the next scheduled cycle and inspect its persisted transport fields before considering deployment complete.

Never replace the active script without the helper alongside it. Keep the old pair available for immediate file-level rollback. Do not restart PipeWire or WirePlumber as part of installing this detector.

## Validation

The change is tested without requiring a live PipeWire graph. Tests mock the scheduled sample and command snapshots. The production daemon's --help can be run without opening microphone taps. The live pw-top output was separately inspected through the user session bus and its column layout was used to validate the parser's field positions.

Run:

    ruff check scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    ruff format --check scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    python -m py_compile scripts/audio_health_transport.py scripts/roaring_carla_healthd.py
    pytest -q tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py

Run the full Hearth tests from outside the repository root with PYTHONPATH set to src, so the compatibility hearth.py CLI shim at the root cannot shadow the package import.

## Remaining engineering work

The implementation roadmap and wider fault taxonomy are in SiYuan: **Microphone Audio Quality Monitoring - Full Diagnostic Audit and Fix Plan (2026-11-22)**.

The next engineering slice should add arbitrary persistent spectral-line tracking and quiet-window broadband noise baselining, then build multiscale click/crackle analysis with raw/post localization and a synthetic + human-labelled corpus. None of those classifiers should trigger automatic repair until false-positive rates and source localization are validated.\n
