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

## Signal-feature measurements added in the current branch

The scheduled sample now also runs audio_health_signal.py on aligned raw SM7B and processed B1 PCM held in bounded memory for that sample. The arrays are discarded when the function returns. No waveform is written to the state file or history.

The measurement-only summary contains:

- Band-limited RMS estimates for 20-80 Hz, 80-250 Hz, 250-500 Hz, 500 Hz-2 kHz, 2-5 kHz, 5-12 kHz and 12-20 kHz.
- High-frequency energy share and 2-12 kHz spectral flatness.
- A lower-energy-frame RMS / 5-12 kHz estimate. This is **not** a calibrated noise floor and the frames are not guaranteed speech-free; it must be compared to a profile-specific baseline before a warning.
- Candidate persistent spectral lines, using non-overlapping 2-second windows. Lower-energy windows nominate candidates, then every frame in the sample is checked for persistence and frequency stability; estimates include sub-bin peak position and local prominence. The detector does not call a line "mains hum" or "ground loop".
- 5 kHz+ high-pass transient descriptors: RMS, peak, crest factor, excess kurtosis and 10 ms outlier-frame count/rate.
- Raw-to-processed deltas for high-frequency transient descriptors, to show whether the measured change first appears before or after the processing chain.
- Sample peak and near-full-scale fraction, separate from true-peak or analog-overload claims.
- Sample-integrity counts for NaN/Inf values, long exact-zero runs, repeated nonzero sample plateaus and full-scale sample plateaus. The integrity pass preserves timing positions across non-finite samples. Long zero runs may be ordinary silence or an intentional gate; these are measurements, not faults.
- Raw-to-processed band-level changes, which quantify frequency attenuation or boosts within each measured band.

All these results currently have automatic_action: none. They are measurements only. Zero-run and full-scale plateau counts are not automatic faults either: digital silence may be intentional, and a plateau requires context from the preceding/following samples and capture point. The transient frame event count is not a crackle verdict: on the actual labelled recordings it did not consistently rank the bad reference above the good one. The old user-described scratch/crackle reference did have much higher 5 kHz+ excess kurtosis (196.84 vs 34.96) and high-frequency energy share (0.856% vs 0.336%) than the user-confirmed clean reference, but the browser Opus takes differ. These are useful calibration evidence, not universal thresholds.

The whole-recording 97-99 Hz peak in the November 22 OGGs did not persist as a stable low-energy-window tone under the refined criterion. Therefore the source of that earlier line remains unresolved; do not report it as confirmed electrical hum. Synthetic stable 97.5 Hz signals remain detectable in unit tests, while drifting voice-like tonal bursts are rejected.

## Deployment contract - not yet performed

The daemon and its two helpers must be installed together into the same directory, because the daemon imports audio_health_transport and audio_health_signal as sibling modules. Installing only the daemon will fail at import time.

Before a later deployment:

1. Back up all existing destination paths:
   - ~/bin/roaring_carla_healthd.py
   - ~/bin/audio_health_transport.py (if it already exists).
   - ~/bin/audio_health_signal.py (if it already exists).
2. Copy the daemon and both helper modules atomically or via a temporary staging directory, preserve executable mode for the daemon, and ensure user ownership.
3. Run Python byte-compilation and --help from the target directory as a non-capturing smoke test.
4. Initially run a shadow/diagnostic cycle with AUTO_RECOVER=0; do not enable restart action while validating new transport classifications.
5. Inspect JSON history and logs. Confirm same-node deltas, resets, node recreation, unavailable queries and metadata failures are classified as designed.
6. Only after review, consider restoring the existing AUTO_RECOVER policy. The transport-conflict guard still applies.
7. Verify the next scheduled cycle and inspect its persisted transport fields before considering deployment complete.

Never replace the active script without both helpers alongside it. Keep all old files available for immediate file-level rollback. Do not restart PipeWire or WirePlumber as part of installing this detector.

## Validation

The change is tested without requiring a live PipeWire graph. Tests mock the scheduled sample and command snapshots. The production daemon's --help can be run without opening microphone taps. The live pw-top output was separately inspected through the user session bus and its column layout was used to validate the parser's field positions.

Run:

    ruff check scripts/audio_health_signal.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    ruff format --check scripts/audio_health_signal.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    python -m py_compile scripts/audio_health_signal.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py
    pytest -q tests/test_audio_health_signal.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py

Run the full Hearth tests from outside the repository root with PYTHONPATH set to src, so the compatibility hearth.py CLI shim at the root cannot shadow the package import.

Current full-suite result for this branch: **676 passed, 75 skipped**. The skips are display/GUI and optional media-integration tests requiring a display or external media tools. Repository-wide Ruff lint and format checks pass, as do Python byte-compilation checks.

A non-capturing live --check run against the current user session also succeeded: Carla node present, B1 expected active, expected links present, pw-top and clock metadata queries successful. This --check does not open microphone taps. It is not a live audio-quality sample or a deployment verification.

## Remaining engineering work

The implementation roadmap and wider fault taxonomy are in SiYuan: **Microphone Audio Quality Monitoring - Full Diagnostic Audit and Fix Plan (2026-11-22)**. The current branch implements transport observability and measurement-only signal features. It does not yet implement the perceptual LPC/ERB click classifier, calibrated persistent-noise baselines, repeated-block/timestamp corruption checks, oversampled true peak, full multi-stage tap localization or opt-in playback/output monitoring.

The next engineering slice should build multiscale perceptual click/crackle analysis, profile-specific noise baselines, sample integrity checks and stage-by-stage capture localization, with a synthetic + human-labelled corpus. None of those classifiers should trigger automatic repair until false-positive rates and source localization are validated.
