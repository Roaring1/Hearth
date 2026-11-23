# Audio quality watchdog: transport observability and safe rollout

## Status

This change adds the first observability/recovery-safety slice to the live B1 microphone watchdog:

- Repository-owned copy of the deployed roaring_carla_healthd.py.
- Dependency-free audio_health_transport.py shared helper.
- Unit tests for pw-top parsing, cumulative ERR counter deltas, resets, node recreation, unavailable queries, clock metadata failures and bounded persisted state.
- Scheduled-cycle integration tests proving pre/post sampling, history persistence and restart suppression when fresh transport errors overlap a confirmed B1 signal fault.

It does **not** yet implement the full signal-quality roadmap in the SiYuan audit. It now also performs lag-corrected matching of the strongest retained raw/post transient event timestamps (5-observation and 5-ms-MAD stability gates, 5-ms matching tolerance; measurement-only), and records bounded NO_SAMPLE/INCONCLUSIVE streak counters without notifications. Arbitrary-tone tracking, measurement-only true-peak estimation using the published BS.1770-5 Annex 2 four-phase FIR coefficient set and sample-integrity checks are now present. The short-impulse FFT pass is batched in 256-frame chunks to reduce temporary allocations under the 96 MiB service memory cap. Manual numeric low-energy baseline comparison is now available (see below); calibrated acoustic SPL/true VAD, validated perceptual click/crackle classification, multi-stage tap localization and playback/output diagnostics remain subsequent phases. Basic stereo channel/phase integrity metrics are now present as a separate optional tap.

## Why this change exists

The active timer invokes:

    ~/bin/roaring_carla_healthd.py --sample-seconds 8

The existing daemon already knew how to query pw-top and pw-metadata, but the scheduled path did not call those functions. This meant its normal 10-minute sample history did not contain transport evidence.

The new scheduled path takes bounded transport snapshots immediately before and after each PCM sample, then compares:

1. Current pre-sample counters against the previous scheduled sample's post-sample counters.
2. Current pre-sample counters against current post-sample counters, to find errors that appeared during the microphone sample.

The capture remains short-lived and mute-gated. This helper persists only a small allowlist of bounded graph metadata and never receives PCM buffers.


The scheduled capture also records stream-read metadata: read calls and bytes per tap, maximum inter-read gap, gaps over 250 ms, bytes discarded when the bounded capture buffer overflows, unmatched raw/post buffer tails at sample end, exact repeated aligned 100 ms window fingerprints and repeat streaks. Only short BLAKE2s digests and counters are retained; PCM itself is not written or persisted. Identical audio windows can occur legitimately (periodic tones, silence or steady signals), so repeat counters are evidence for review, not a standalone frozen-stream verdict.

The scheduled healthd state/history now carries bounded consecutive `NO_SAMPLE` (no raw activity) and `INCONCLUSIVE` streak counters. These make long-running unverified periods visible in `--status` and persisted history; they do not notify, restart, or mark the microphone unhealthy. Counters reset on activity/other terminal states, saturate at 255, and sanitize malformed persisted values.

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

The scheduled sample now also runs audio_health_signal.py on aligned raw SM7B and processed B1 PCM held in bounded memory for that sample. The arrays are discarded when the function returns. No waveform is written to the state file or history. The scheduled sample additionally opens an optional third tap on the B1 stereo monitor source (b1_mic, 48 kHz FL/FR) while preserving the existing mono raw and mono processed verdict taps. Read-only live PipeWire inspection verified the B1 virtual sink/source is 2-channel FL/FR at 48 kHz. Read-only live PipeWire inspection confirmed the B1 virtual sink is 2-channel FL/FR at 48 kHz and its monitor source b1_mic exposes 2 channels. The optional tap is 2-channel float32, separately bounded to 600 ms of buffered data, and reduced block-by-block to numeric metrics; it never stores a full PCM recording. Metrics are per-channel RMS/peak/DC, L/R correlation, balance, mono-mix cancellation and flags for strong phase opposition, cancellation, imbalance or one-channel silence. These are candidates only, not actions. If the optional tap fails or exits, the mono capture continues and records stereo_capture_error. The read loop labels source reads correctly and reports stereo frame counts using 8 bytes/frame, not the mono 4-byte sample width. Stereo data does not participate in verdict or restart decisions. The capture loop isolates optional-tap failures, drains only complete 100-ms stereo frames, reports frame accounting using 8 bytes/frame (not the mono 4-byte width), and explicitly terminates/waits/closes all capture processes and stdout pipes.

The measurement-only summary contains:

- Band-limited RMS estimates for 20-80 Hz, 80-250 Hz, 250-500 Hz, 500 Hz-2 kHz, 2-5 kHz, 5-12 kHz and 12-20 kHz.
- High-frequency energy share and 2-12 kHz spectral flatness.
- A lower-energy-frame RMS / 5-12 kHz estimate. This is **not** a calibrated noise floor and the frames are not guaranteed speech-free; it must be compared to a profile-specific baseline before a warning.
- Candidate persistent spectral lines, using non-overlapping 2-second windows. Lower-energy windows nominate candidates, then every frame in the sample is checked for persistence and frequency stability; estimates include sub-bin peak position and local prominence. The detector does not call a line "mains hum" or "ground loop".
- 5 kHz+ high-pass transient descriptors: RMS, peak, crest factor, excess kurtosis and 10 ms outlier-frame count/rate.
- Short broadband impulse candidates from overlapping 5 ms windows at 2.5 ms hop. Three bands (2.5-5 kHz, 5-10 kHz, 10-20 kHz) are compared with a local median/MAD baseline. A candidate must elevate at least two bands and last no more than 15 ms. Event count, rate, bounded timestamps and threshold ratios are retained. Long high-frequency sibilance-like spans are excluded by duration. Raw/post event-time matching is now also reported for the strongest retained candidate events: the scheduled sampler collects median lag and median absolute deviation from valid 100 ms paired windows, shifts processed event times into raw-time coordinates, and greedily matches one-to-one within 5 ms. Matching is withheld unless at least five valid lag observations exist and lag MAD is <=5 ms. The list is capped at 24 events per tap, so this is not exhaustive or a perceptual classifier; unmatched retained events are measurement evidence only.
- For at most the 24 strongest retained impulse examples, calculate order-12 Yule-Walker LPC prediction-error RMS/peak ratios against the preceding 25 ms of context. Ratios are stored as descriptive values only; no binary 'click' or 'unpredictable' label is emitted and no recovery action uses them.
The bounded LPC stage fits its predictor using preceding context, then compares prediction-error RMS and peak with that context. These ratios are not a perceptual click score: the labelled clean and post-restart OGGs produce more high-ratio events than the fault/pre-restart takes in this tiny corpus, so they are retained only as context for later human review.
- Raw-to-processed deltas for high-frequency transient descriptors, to show whether the measured change first appears before or after the processing chain.
- A heuristic multi-cue high-frequency impulsive-event candidate. It requires all of: HF RMS at least -55 dBFS, HF peak at least -35 dBFS, HF crest at least 27 dB, 5 kHz+ excess kurtosis at least 80 and high-frequency energy share at least 0.5%. The candidate is separately reported for raw and B1 and logged with its cue evidence. It is **not a click/crackle classifier** and cannot trigger a restart or notification.
- Per-point sample peak, a 4x true-peak estimate using the BS.1770-5 Annex 2 four-phase FIR coefficients (dBTP), inter-sample increase and over-full-scale estimate. This is a diagnostic estimate, not yet validated as fully conformant to ITU-R BS.1770-5; it does not measure analog ADC overload.
- Sample-integrity counts for NaN/Inf values, long exact-zero runs, repeated nonzero sample plateaus and full-scale sample plateaus. The integrity pass preserves timing positions across non-finite samples. Long zero runs may be ordinary silence or an intentional gate; these are measurements, not faults.
- Stream-read diagnostics: per-source read-call and byte counts, maximum time between successful reads, gaps exceeding 250 ms, bytes discarded when the aligned PCM buffer exceeds its 600 ms cap, and exact repeated adjacent 100 ms block fingerprints for raw and processed streams. Only 8-byte digests and counters are retained, not audio data. Repeating identical blocks can be normal for periodic tones or steady signals, so these are indicators only and never a verdict or repair trigger.
- Optional stereo B1 channel integrity: per-channel RMS/peak/DC, L/R correlation, channel balance, mono-mix cancellation, and measurement-only phase-opposition, imbalance and channel-silence candidates. The currently live B1 virtual sink/source is stereo FL/FR; the previous health monitor requested only mono from b1_mic, which could hide channel/phase problems. The new stereo tap is additive and fail-soft, not a fault verdict.
- Raw-to-processed band-level changes, which quantify frequency attenuation or boosts within each measured band.

All these results currently have automatic_action: none. They are measurements only. Zero-run and full-scale plateau counts are not automatic faults either: digital silence may be intentional, and a plateau requires context from the preceding/following samples and capture point. The transient frame event count is not a crackle verdict: on the actual labelled recordings it did not consistently rank the bad reference above the good one. The old user-described scratch/crackle reference did have much higher 5 kHz+ excess kurtosis (196.84 vs 34.96) and high-frequency energy share (0.856% vs 0.336%) than the user-confirmed clean reference, but the browser Opus takes differ. These are useful calibration evidence, not universal thresholds.

A one-time 8-second live shadow capture using the branch code (direct capture_sample, not run_sample_cycle) completed with no capture errors and no recovery/notification/state-write path. It had 98.75% paired mono coverage, zero active raw windows and no confirmed fault, so the result was NO_SAMPLE: no voice/activity during that sample, not a microphone fault. The optional stereo tap received 379,200 frames (~7.9 s); both channels measured -600 dBFS RMS/peak (digital silence), correlation was undefined, and no stereo candidates were flagged. This is consistent with an idle/no-speech interval but does not prove the microphone works. The same live sample exposed a bookkeeping bug: raw/processed read counters were zero despite data capture. The tap labels were corrected and the capture integration test now asserts all three frame counters; this shadow sample preceded that fix and has not been repeated. No live audio was written or saved.

Offline stereo analysis of the four reference OGGs confirmed dual-mono content: each file had L/R correlation 1.0, 0.0 dB channel balance, 0.0 dB mono-mix cancellation and no phase-opposition, imbalance or channel-silence candidates. A live 8-second shadow capture before the raw/processed stream-counter fix had 98.75% paired coverage, zero active raw windows and no confirmed fault (NO_SAMPLE). Its stereo tap read 379,200 frames (~7.9 s) and both channels were digital silence (-600 dBFS floor); this cannot validate microphone function without voice. The live capture exposed that raw/processed stream read counters were keyed by source names instead of diagnostic labels; this mapping is now fixed and the mock capture integration asserts frame counts for all three taps. The live shadow run did not write PCM, trigger recovery, send notifications, persist state or change routes.

Reference replay of the original before/after OGG pair after this correction: before (test.ogg) had 65/300 digital-silence frames (21.7%), only 10 selected non-silent lower-energy frames, and its noise-floor value is now invalid; the unverified estimate is -137.07 dBFS. After (fixed_test.ogg) had 30/300 digital-silence frames (10%), 45 selected non-silent lower-energy frames and a valid estimate of -98.13 dBFS. Do not compare these as a reliable before/after noise-floor change because the before sample lacks the required 20 frames and both files are browser/Opus captures rather than calibrated noise recordings.

The four supplied OGGs had sample peaks of -2.925, -4.712, -1.798 and -0.667 dBFS; the current 4x Annex 2 FIR true-peak estimates were -2.911, -4.698, -1.787 and -0.643 dBTP respectively. None exceeded 0 dBTP. These values use the published Annex 2 coefficient set, but the implementation has not yet been checked against the recommendation's full reference-vector suite and should be treated as a diagnostic meter pending that validation. The whole-recording 97-99 Hz peak in the November 22 OGGs did not persist as a stable low-energy-window tone under the refined criterion. Therefore the source of that earlier line remains unresolved; do not report it as confirmed electrical hum. Synthetic stable 97.5 Hz signals remain detectable in unit tests, while drifting voice-like tonal bursts are rejected.

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

With the Annex 2 FIR true-peak calculation enabled, each 30-second file took approximately 0.857-0.897 seconds for the complete feature pass in the local offline replay. This suggests the scheduled 8-second sample should fit the existing oneshot budget, but a live resource measurement is still required. On an isolated 8-second synthetic raw/post sample after FFT batching and the Annex 2 FIR change, the full feature pass took 0.324 seconds; process peak RSS rose from 61.11 MiB to 80.53 MiB (+19.42 MiB). This is approximately 15.5 MiB below the service's configured 96 MiB MemoryMax in that isolated run. It is not yet a live systemd cgroup measurement and should be rechecked in shadow deployment before considering the resource budget verified. Candidate counts are not subjective click labels: the samples differ in spoken content and the test files use browser Opus. The detector's purpose at this stage is to expose repeatable features and locations, not to declare crackle or drive restart.

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

The low-energy RMS dBFS estimate previously used 10log10 on RMS amplitude (half the correct dBFS scale) and included digital-silence frames. It now requires 20 selected non-silent 100-ms frames (2 seconds) from the lower-energy quartile to mark a valid estimate; fallback lowest-non-silent values remain explicitly unverified and are not exposed as a valid noise floor. It now uses squared RMS for dBFS, excludes frames with peak <=1e-8 from the noise-floor candidate, and requires at least 20 selected non-silent 100-ms frames (2 seconds) before labeling the floor valid. Fallback values are retained under unverified fields, not treated as a valid noise floor.

## Optional manually enrolled noise baseline

A noise profile can now be enrolled only with explicit user confirmation. Run the installed health daemon manually with: Enrollment requires exactly three eligible CORRECT/NO_SAMPLE captures, each 8 seconds, at least 90% paired coverage, and valid low-energy estimates on both raw and processed taps. The profile builder also requires an explicit confirmed_quiet argument, stores user_confirmed_quiet=true, and save refuses profiles lacking those safety flags.

    roaring_carla_healthd.py --noise-baseline-enroll --confirm-quiet

This captures three 8-second samples and requires >=90% capture coverage plus valid non-silent low-energy estimates for both raw and processed taps. It refuses to overwrite the baseline if any sample fails. The profile contains only numeric medians for raw/processed low-energy RMS, 5k-12k level, flatness, and consensus persistent-tone frequencies; no PCM is saved. It is stored atomically at ~/.cache/roaring/carla-healthd-noise-baseline.json with file mode 0600. It is never automatically updated. Show the profile with:

    roaring_carla_healthd.py --noise-baseline-show

During scheduled samples, a fixed profile enables measurement-only candidates for >=8 dB rises in raw/processed low-energy RMS or 5k-12k level, and new persistent tones outside a 2.5 Hz matching tolerance. The profile must pass schema, profile-ID, sample-count, metrics and non-auto-update validation before comparison. These candidates are stored in state/history and journaled; they do **not** notify or restart Carla. If the profile is missing, the sample records baseline_missing. Enrollment is not run during this audit because a user-confirmed clean/quiet state has not been provided. Re-enroll after mic gain, routing, Carla plugin/settings or processing-chain changes because the profile does not hash plugin parameters.

## Safe installation helper

\`scripts/install_carla_healthd.sh\` stages the main daemon and all runtime modules, compiles and imports them before installation, backs up existing target files under ~/.local/share/roaring/carla-healthd-backups, and then atomically moves staged files into ~/bin. It does not change systemd unit/timer files or restart any service. A temp-HOME integration test verifies module installation, backup and no-restart messaging. This installer has not been run against the live user home.

## True-peak method reference

[ITU-R Recommendation BS.1770-5 (11/2023), Annex 2](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I/en) defines true peak as the maximum of the continuous-time reconstructed waveform, which may exceed the largest stored sample. Its prescribed workflow at 48 kHz uses 4x oversampling and low-pass interpolation; it provides example four-phase FIR coefficients. Hearth now uses the published 48-tap, four-phase FIR coefficient set from Annex 2 in a block-bounded floating-point implementation. The helper now refuses to estimate dBTP at non-48-kHz rates (while still reporting sample peak) so the 48-kHz coefficient set cannot be silently misapplied to 44.1/96 kHz inputs. The optional 12.04 dB integer-arithmetic attenuation is not applied. Exact conformance still needs reference-vector validation.

## Validation

The change is tested without requiring a live PipeWire graph. Tests mock the scheduled sample and command snapshots. The production daemon's --help can be run without opening microphone taps. The live pw-top output was separately inspected through the user session bus and its column layout was used to validate the parser's field positions.

Run:

    ruff check scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    ruff format --check scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py
    python -m py_compile scripts/audio_health_baseline.py scripts/audio_health_signal.py scripts/audio_health_stream.py scripts/audio_health_transport.py scripts/roaring_carla_healthd.py
    bash -n scripts/install_carla_healthd.sh
    pytest -q tests/test_audio_health_signal.py tests/test_audio_health_stream.py tests/test_audio_health_transport.py tests/test_roaring_carla_healthd_scheduled.py

Run the full Hearth tests from outside the repository root with PYTHONPATH set to src, so the compatibility hearth.py CLI shim at the root cannot shadow the package import.

Current full-suite result after true-peak, chunked FFT, sample-rate guard, event-time localization, unverified streak tracking, stereo diagnostics, frame accounting, pipe cleanup, noise-floor validity, DC offset, manual baseline comparison and safe installer: **750 passed, 75 skipped**. The skips are display/GUI and optional media-integration tests requiring a display or external media tools. The signal-feature tests use pytest.importorskip("numpy") so minimal GUI-only CI collection environments without NumPy do not fail while collecting unrelated GUI tests. Repository-wide Ruff lint and format checks pass across 132 files, as do Python byte-compilation checks. The safe installer is separately smoke-tested in a temporary HOME. New coverage tests prove that missing or sub-90% coverage cannot be CORRECT or INCORRECT and that exactly 90% is the minimum accepted level. Stream diagnostics tests cover read gaps, overflow, exact repeated windows, unknown devices, empty reads and unmatched raw/post tails. True-peak tests cover a controlled inter-sample over, sample-aligned sinusoid, non-finite values, invalid configurations and raw/post dBTP differences. Event-time localization tests cover stable-lag matching, sparse/unstable refusal, and a detector-to-matcher synthetic scenario with four shared clicks plus two processing-only clicks (4 matches, 0 raw-only, 2 post-only).

A non-capturing live --check run against the current user session also succeeded: Carla node present, B1 expected active, expected links present, pw-top and clock metadata queries successful. This --check does not open microphone taps. It is not a live audio-quality sample or a deployment verification.

## Remaining engineering work

The implementation roadmap and wider fault taxonomy are in SiYuan: **Microphone Audio Quality Monitoring - Full Diagnostic Audit and Fix Plan (2026-11-22)**. The current branch implements transport observability, measurement-only signal features including a true-peak estimate using the Annex 2 FIR coefficient set and lag-corrected raw/post candidate event timing, bounded read-cadence/overflow/repeat metadata and a 90% paired-frame coverage gate. The true-peak estimator now uses the published Annex 2 four-phase FIR coefficient set. Controlled validation covers a sample-aligned sine (no inter-sample increase) and an inter-sample-over sine (+3.055 dBTP, correctly above 0 dBTP); full reference-vector conformance testing remains outstanding. It does not yet implement the perceptual LPC/ERB click classifier, calibrated persistent-noise baselines, complete timestamp/frame-gap and repeated-block corruption analysis, a fully reference-vector-validated BS.1770-5 true-peak meter, full multi-stage tap localization or opt-in playback/output monitoring.

The next engineering slice should build multiscale perceptual click/crackle analysis, profile-specific noise baselines, sample integrity checks and stage-by-stage capture localization, with a synthetic + human-labelled corpus. None of those classifiers should trigger automatic repair until false-positive rates and source localization are validated.

## Optional manually enrolled noise baseline

The current scheduled sample can compare low-energy noise measurements against an optional, user-confirmed numeric baseline. It never learns or updates the baseline automatically. Enrollment requires explicit `--noise-baseline-enroll --confirm-quiet`, exactly three eligible 8-second samples, >=90% capture coverage, and valid non-silent low-energy estimates on both raw and processed taps. The profile contains only numeric medians for RMS, 5k-12k level, flatness, and consensus persistent-tone frequencies. PCM is not saved. Profile writes are atomic, with file mode 0600; the fixed path is `~/.cache/roaring/carla-healthd-noise-baseline.json`. Show it read-only using `--noise-baseline-show`.


Scheduled comparison is measurement-only: >=8 dB rise in raw or processed low-energy RMS/high-band level, or a newly detected persistent tone outside 2.5 Hz matching tolerance. It stores/logs candidates but does not notify, restart Carla, change routes, or alter PipeWire. Missing or invalid profiles are fail-closed. Re-enroll after microphone gain, route, plugin, or processing-chain changes; the profile does not fingerprint plugin settings. Enrollment was not run because no user-confirmed quiet/clean baseline condition was available.

Baseline loading now validates the schema, profile ID, finite timestamp/metrics, flatness range, bounded tone list, tone frequency bounds (20-1000 Hz), sample-count bounds, and a 64 KiB maximum profile size before comparison. Malformed profiles are ignored rather than trusted.


## Current checkpoint (2026-11-23)

- Branch: \`feature/audio-quality-health-2026-11-22\`; latest pushed commit: \`14e227a\` (manual noise baseline + safe installer).
- PR remains draft; no live install/deployment, no service restart from this work.
- Latest local validation: **750 passed, 75 skipped**, Ruff clean, formatting clean (132 files), byte compilation clean, installer Bash syntax and temp-HOME install test pass.
- Hosted CI for latest commit is in progress.
- No noise baseline has been enrolled; manual enrollment requires user confirmation that the mic is quiet/clean and B1 is active/unmuted.
- One direct 8-second shadow capture was performed earlier with no recovery/state write; it found no activity and stereo digital silence. No live capture was repeated after the mono stream-counter correction.

## Route-selection capture guard

The B1 health capture gate now also requires `B1_ROUTE` to be one of `sm7b`, `astro`, or `both`. `B1_ACTIVE=true` and `CARLA_TO_B1=true` alone are not sufficient; `B1_ROUTE=none`, empty or invalid route values fail closed. This prevents the periodic checker and manual baseline enrollment from sampling a raw mic that is not routed to B1. The gate still also verifies both B1 endpoints are unmuted, and query failures deny sampling.


Read-only live check on 2026-11-22 found `B1_ROUTE="none"`, `B2_ROUTE="astro"`, with B1 and Carla-to-B1 flags enabled. Recent deployed timer samples were `NO_SAMPLE` with zero active windows and processed p50/p90 exactly 0, indicating the B1 chain had no signal. A shadow-only `capture_sample(8)` returned `MUTED` after the capture gate refused the route/mute state. `pactl` queries could not connect at that time, so mute state itself was not confirmed. The route-none capture gate fix is not installed live; it is on the isolated feature branch only.
