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


The scheduled capture also records stream-read metadata: read calls and bytes per tap, maximum inter-read gap, gaps over 250 ms, bytes discarded when the bounded capture buffer overflows, unmatched raw/post buffer tails at sample end, exact repeated aligned 100 ms window fingerprints and repeat streaks. Only short BLAKE2s digests and counters are retained; PCM itself is not written or persisted. Identical audio windows can occur legitimately (periodic tones, silence or steady signals), so repeat counters are evidence for review, not a standalone frozen-stream verdict.

The classifier also requires **at least 90% paired raw/post PCM coverage** of the requested sample duration. Coverage uses the lesser of the raw and processed sample counts. If either tap provides too little data, the result is INCONCLUSIVE before evaluating a fault or healthy verdict. At the configured 8-second sample duration, this limits the unobserved portion to at most 0.8 seconds. This prevents a small set of valid received windows from making a badly truncated sample look healthy, and prevents incomplete evidence from triggering Carla recovery.

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
- scripts/audio_health_stream.py: bounded per-tap read cadence, overflow and repeated-window diagnostics.
- tests/test_audio_health_transport.py: transport helper tests.
- tests/test_audio_health_stream.py: read cadence, overflow, repeated-window and no-fake-data tests.
- tests/test_audio_health_signal.py: synthetic tone, noise, impulse, LPC residual, integrity and localization tests.
- tests/test_roaring_carla_healthd_scheduled.py: scheduled-cycle integration and restart-conflict tests.

- scripts/audio_health_signal.py: measurement-only spectral, transient, broadband-impulse and sample-integrity features.

The active systemd unit still points to ~/bin/roaring_carla_healthd.py. That external file has **not** been updated. The active timer and service were read-only checked; no service restart or routing change was performed.

## Signal-feature measurements added in the current branch

The scheduled sample now also runs audio_health_signal.py on aligned raw SM7B and processed B1 PCM held in bounded memory for that sample. The arrays are discarded when the function returns. No waveform is written to the state file or history.

The measurement-only summary contains:

- Band-limited RMS estimates for 20-80 Hz, 80-250 Hz, 250-500 Hz, 500 Hz-2 kHz, 2-5 kHz, 5-12 kHz and 12-20 kHz.
- High-frequency energy share and 2-12 kHz spectral flatness.
- A lower-energy-frame RMS / 5-12 kHz estimate. This is **not** a calibrated noise floor and the frames are not guaranteed speech-free; it must be compared to a profile-specific baseline before a warning.
- Candidate persistent spectral lines, using non-overlapping 2-second windows. Lower-energy windows nominate candidates, then every frame in the sample is checked for persistence and frequency stability; estimates include sub-bin peak position and local prominence. The detector does not call a line "mains hum" or "ground loop".
- 5 kHz+ high-pass transient descriptors: RMS, peak, crest factor, excess kurtosis and 10 ms outlier-frame count/rate.
- Short broadband impulse candidates from overlapping 5 ms windows at 2.5 ms hop. Three bands (2.5-5 kHz, 5-10 kHz, 10-20 kHz) are compared with a local median/MAD baseline. A candidate must elevate at least two bands and last no more than 15 ms. Event count, rate, bounded timestamps and threshold ratios are retained. Long high-frequency sibilance-like spans are excluded by duration.
- For at most the 24 strongest retained impulse examples, calculate order-12 Yule-Walker LPC prediction-error RMS/peak ratios against the preceding 25 ms of context. Ratios are stored as descriptive values only; no binary 'click' or 'unpredictable' label is emitted and no recovery action uses them.
The bounded LPC stage fits its predictor using preceding context, then compares prediction-error RMS and peak with that context. These ratios are not a perceptual click score: the labelled clean and post-restart OGGs produce more high-ratio events than the fault/pre-restart takes in this tiny corpus, so they are retained only as context for later human review.
- Raw-to-processed deltas for high-frequency transient descriptors, to show whether the measured change first appears before or after the processing chain.
- A heuristic multi-cue high-frequency impulsive-event candidate. It requires all of: HF RMS at least -55 dBFS, HF peak at least -35 dBFS, HF crest at least 27 dB, 5 kHz+ excess kurtosis at least 80 and high-frequency energy share at least 0.5%. The candidate is separately reported for raw and B1 and logged with its cue evidence. It is **not a click/crackle classifier** and cannot trigger a restart or notification.
- Sample peak and near-full-scale fraction, separate from true-peak or analog-overload claims.
- Sample-integrity counts for NaN/Inf values, long exact-zero runs, repeated nonzero sample plateaus and full-scale sample plateaus. The integrity pass preserves timing positions across non-finite samples. Long zero runs may be ordinary silence or an intentional gate; these are measurements, not faults.
- Stream-read diagnostics: per-source read-call and byte counts, maximum time between successful reads, gaps exceeding 250 ms, bytes discarded when the aligned PCM buffer exceeds its 600 ms cap, and exact repeated adjacent 100 ms block fingerprints for raw and processed streams. Only 8-byte digests and counters are retained, not audio data. Repeating identical blocks can be normal for periodic tones or steady signals, so these are indicators only and never a verdict or repair trigger.
- Raw-to-processed band-level changes, which quantify frequency attenuation or boosts within each measured band.

All these results currently have automatic_action: none. They are measurements only. Zero-run and full-scale plateau counts are not automatic faults either: digital silence may be intentional, and a plateau requires context from the preceding/following samples and capture point. The transient frame event count is not a crackle verdict: on the actual labelled recordings it did not consistently rank the bad reference above the good one. The old user-described scratch/crackle reference did have much higher 5 kHz+ excess kurtosis (196.84 vs 34.96) and high-frequency energy share (0.856% vs 0.336%) than the user-confirmed clean reference, but the browser Opus takes differ. These are useful calibration evidence, not universal thresholds.

The whole-recording 97-99 Hz peak in the November 22 OGGs did not persist as a stable low-energy-window tone under the refined criterion. Therefore the source of that earlier line remains unresolved; do not report it as confirmed electrical hum. Synthetic stable 97.5 Hz signals remain detectable in unit tests, while drifting voice-like tonal bursts are rejected.

The five-cue 8-second watchdog heuristic was also replayed in consecutive complete 8-second windows (the incomplete trailing window was ignored):

| Recording | Positive 8-second windows | Complete 8-second windows |
|---|---:|---:|
| November 9 fault reference, Zo2N_NCo.ogg | 2 | 3 |
| November 9 confirmed-clean reference, 3fL9fHB_.ogg | 0 | 3 |
| November 22 before, test.ogg | 1 | 3 |
| November 22 after, fixed_test.ogg | 0 | 3 |

This reproduces the single labelled bad/clean distinction and the user's before/after observation at the watchdog's actual sampling duration. It is not an independently validated classifier: there are only three complete non-overlapping windows per file, the recordings are different browser-Opus speech takes, and no blind human event labels exist. The heuristic stays diagnostic-only and cannot restart or change the audio chain.

The impulse-candidate replay against the four supplied browser-Opus references produced:

| Recording | Candidate events | Events/second | 5 kHz+ excess kurtosis | High-frequency power share |
|---|---:|---:|---:|---:|
| November 9 fault reference, Zo2N_NCo.ogg | 329 | 10.965 | 196.84 | 0.8555% |
| November 9 confirmed-clean reference, 3fL9fHB_.ogg | 41 | 1.367 | 34.96 | 0.3361% |
| November 22 before, test.ogg | 309 | 10.302 | 142.72 | 0.4644% |
| November 22 after, fixed_test.ogg | 60 | 2.000 | 86.22 | 0.3997% |

The LPC ratio replay on the 24 strongest retained candidates per file yielded:

| Recording | Events with RMS ratio >4 and peak ratio >8 | Maximum RMS ratio | Maximum peak ratio |
|---|---:|---:|---:|
| November 9 fault reference | 2/24 | 9.59 | 78.82 |
| November 9 confirmed-clean reference | 7/24 | 16.75 | 108.67 |
| November 22 before | 8/24 | 814.40 | 5047.86 |
| November 22 after | 13/24 | 32.82 | 172.27 |

The replay confirms the LPC result must remain a pair of numeric features, not a boolean click verdict: normal speech transitions can be less predictable than the preceding context, and the apparently better files can score higher.

Each 30-second file took approximately 0.85-0.95 seconds for the complete feature pass in the local offline replay. This suggests the scheduled 8-second sample should fit the existing oneshot budget, but a live resource measurement is still required. Candidate counts are not subjective click labels: the samples differ in spoken content and the test files use browser Opus. The detector's purpose at this stage is to expose repeatable features and locations, not to declare crackle or drive restart.

## Deployment contract - not yet performed

The daemon and its three helpers must be installed together into the same directory, because the daemon imports audio_health_transport, audio_health_signal and audio_health_stream as sibling modules. Installing only the daemon will fail at import time.

Before a later deployment:

1. Back up all existing destination paths:
   - ~/bin/roaring_carla_healthd.py
   - ~/bin/audio_health_transport.py (if it already exists).
   - ~/bin/audio_health_signal.py (if it already exists).
   - ~/bin/audio_health_stream.py (if it already exists).
2. Copy the daemon and all three helper modules atomically or via a temporary staging directory, preserve executable mode for the daemon, and ensure user ownership.
3. Run Python byte-compilation and --help from the target directory as a non-capturing smoke test.
4. Initially run a shadow/diagnostic cycle with AUTO_RECOVER=0; do not enable restart action while validating new transport classifications.
5. Inspect JSON history and logs. Confirm same-node deltas, resets, node recreation, unavailable queries, metadata failures, stream-read summaries, paired-frame coverage and signal-feature fields are classified as designed.
6. Only after review, consider restoring the existing AUTO_RECOVER policy. The transport-conflict guard still applies.
7. Verify the next scheduled cycle and inspect its persisted transport fields before considering deployment complete.

Never replace the active script without all three helpers alongside it. Keep all old files available for immediate file-level rollback. Do not restart PipeWire or WirePlumber as part of installing this detector.

## Validation

The change is tested without requiring a live PipeWire graph. Tests mock the scheduled sample and command snapshots. The production daemon's --help can be run without opening microphone taps. The live pw-top output was separately inspected through the user session bus and its column layout was used to validate the parser's field positions.

Run:

    ruff check scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    ruff format --check scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    python -m py_compile scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py
    pytest -q tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py

Run the full Hearth tests from outside the repository root with PYTHONPATH set to src, so the compatibility hearth.py CLI shim at the root cannot shadow the package import.

Current full-suite result after the stream-read and paired-coverage tranche: **696 passed, 75 skipped**. The skips are display/GUI and optional media-integration tests requiring a display or external media tools. The signal-feature tests use pytest.importorskip("numpy") so minimal GUI-only CI collection environments without NumPy do not fail while collecting unrelated GUI tests. Repository-wide Ruff lint and format checks pass across 129 files, as do Python byte-compilation checks. New coverage tests prove that missing or sub-90% coverage cannot be CORRECT or INCORRECT and that exactly 90% is the minimum accepted level. Stream diagnostics tests cover read gaps, overflow, exact repeated windows, unknown devices, empty reads and unmatched raw/post tails.

A non-capturing live --check run against the current user session also succeeded: Carla node present, B1 expected active, expected links present, pw-top and clock metadata queries successful. This --check does not open microphone taps. It is not a live audio-quality sample or a deployment verification.

## Remaining engineering work

The implementation roadmap and wider fault taxonomy are in SiYuan: **Microphone Audio Quality Monitoring - Full Diagnostic Audit and Fix Plan (2026-11-22)**. The current branch implements transport observability, measurement-only signal features, bounded read-cadence/overflow/repeat metadata and a 90% paired-frame coverage gate. It does not yet implement the perceptual LPC/ERB click classifier, calibrated persistent-noise baselines, complete timestamp/frame-gap and repeated-block corruption analysis, oversampled true peak, full multi-stage tap localization or opt-in playback/output monitoring.

The next engineering slice should build multiscale perceptual click/crackle analysis, profile-specific noise baselines, sample integrity checks and stage-by-stage capture localization, with a synthetic + human-labelled corpus. None of those classifiers should trigger automatic repair until false-positive rates and source localization are validated.
