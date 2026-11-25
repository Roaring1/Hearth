# Shared microphone/audio signal-quality engine - design note

Date: 2026-11-24. Design-only proposal; no code, live capture, route, service, plugin, buffer, or configuration changes.

## Goal

Make the scheduled B1 watchdog and `~/bin/mic_analyze.py` agree on what they measure and how they express evidence. Reuse one toolkit-free signal-quality engine, while keeping the watchdog's conservative recovery policy separate from diagnostic scoring. A score alone must never restart Carla.

## Boundaries and responsibilities

- **Capture adapters:** live watchdog and offline analyzer own their capture/read lifecycle, rate/channel conversion, bounds, timestamps, mute/consent gates, and cleanup. They supply short-lived PCM arrays plus transport snapshots. The engine never spawns subprocesses, opens devices, reads configs, queries PipeWire, or owns recovery.
- **Shared engine:** deterministic pure functions over validated PCM and metadata. It emits versioned measurements, event candidates, provenance, confidence/coverage, and explicit limitations. It receives capture-point labels (raw SM7B, processed B1, optional B1 stereo monitor, other future taps) so each metric is localized.
- **Transport adapter:** parse and compare bounded `pw-top`/`pw-metadata` evidence by node identity. Keep driver and follower IDs separate. ERR is cumulative: first observation, node recreation, counter reset/decrease, missing row or failed query means baseline/partial/unknown; only same-node positive deltas are new errors. Record clock/rate/quantum changes independently, not as automatic fault claims.
- **Recovery controller:** watchdog-only, outside the engine. It evaluates a confirmed signal fault, transport evidence, explicit user opt-in, cooldown and rate cap. It may preserve the existing narrowly scoped Carla recovery; ambiguous or transport-conflicted evidence suppresses recovery. Never restart PipeWire/WirePlumber or change buffer/quantum as a signal-classifier side effect.
- **Offline analyzer:** uses the same feature schema/classification helpers, adds full file context and optional manual comparisons, and never triggers recovery.

## Measurement pipeline

1. Validate format/rate/channels and finite samples; count NaN/Inf, sample/true-peak estimates, zero/full-scale plateaus, and read integrity. Invalid or insufficient capture coverage becomes `INCONCLUSIVE` before classification.
2. Align raw and processed taps using bounded correlation/lag estimates only when alignment quality and stability gates pass. Preserve per-tap metrics; never subtract or compare unaligned streams.
3. Compute independent features per capture point and interval: RMS/peak/crest, DC, band energy and high-frequency share/flatness, persistent-tone candidates with stability/prominence, transient/impulse candidates with multiband duration/robust local thresholds, clipping/true-peak diagnostics, channel/phase integrity, stream read cadence/overflow/gaps, and same-node transport counter deltas.
4. Localize by raw-vs-processed feature change and optional stereo/output tap. Report `RAW_ONLY`, `PROCESSING_PATH`, `DOWNSTREAM`, `TRANSPORT_CORRELATED`, `MULTIPLE`, or `UNLOCALIZED` as evidence-backed hypotheses, never root-cause assertions.
5. Emit independent feature facts and bounded event candidates, with event timestamps and capture-point IDs. No unvalidated feature becomes a hard verdict.

## Confidence and outcome vocabulary

Use a typed state rather than a single boolean or numeric score:

- `HEALTHY_OBSERVED`: enough complete, active evidence and no validated abnormal cue.
- `SUSPECT`: one or more candidate cues; review/next sample only.
- `CONFIRMED_SIGNAL_FAULT`: multi-cue conditions validated against the labelled corpus and repeated/stable sampling requirements.
- `TRANSPORT_DEGRADED`: same-node transport evidence exists; independent of signal label.
- `CONFLICT`: signal fault overlaps transport counters or evidence disagrees; suppress restart.
- `NO_SAMPLE`: no meaningful input activity; not a health verdict.
- `INCONCLUSIVE`: low coverage, tap/read error, insufficient alignment, query failure, node identity change, insufficient calibration, or ambiguous data.
- `RECOVERED_SIGNAL_ONLY` / `RECOVERED_WITH_TRANSPORT_DEGRADED`: post-intervention result separates audio evidence from transport health.

Confidence is component-level (signal, transport, localization, classifier/corpus validity) and exposes the exact gates that failed. Avoid a false precision aggregate percentage. Unknown is never converted to healthy or broken.

## Fault families and initial evidence (measurement-only)

| Family | Features/evidence | Localization/use |
|---|---|---|
| Dropout / gate artifacts | RMS/zero-run cadence, raw active/post quiet, window coverage | raw vs post; distinguish intentional silence and NO_SAMPLE |
| Click/crackle/transients | multiband short-window candidates, robust baseline, event times, LPC residual as exploratory | raw/post event match; not a standalone click classifier |
| Broadband hiss/static | band energy/flatness against per-chain baseline, valid lower-energy frames | avoid speech contamination and absolute uncalibrated thresholds |
| Persistent tones/resonance | stable narrow peaks, persistence, drift, local prominence | do not label electrical hum/ground loop by frequency alone |
| Clipping/distortion | sample peak, validated oversampled true peak, plateau counts; plugin/input-stage context | digital peak cannot rule out analog/ADC/plugin distortion |
| Transport underruns | same-node ERR deltas, read gaps, bytes, overflows, clock/quantum | positive comparable deltas are correlation, not causal proof |
| Channel/phase | per-channel RMS/DC/peak, balance, correlation, mono cancellation | optional stereo tap; never inferred from mono verdict tap |
| Frequency loss / processing artifacts | band-wise raw/post delta, gate/compressor cues when available | signal processing hypothesis only; do not infer plugin identity |
| Output/playback failures | downstream graph, endpoint state, loopback/capture evidence, optional controlled tap | separate playback path from mic capture and signal quality |

## Confidence and calibration gates

- Keep the four existing OGGs as labelled listening anchors with metadata: fault-reference, user-confirmed clean, and two non-controlled before/after captures. They are browser/Opus speech recordings and not calibration truth.
- Add a labelled replay corpus with raw PCM and deterministic synthetic cases for silence, speech-like broadband, tone (fixed and drifting), clipping, soft clipping, impulsive clicks, crackle trains, hiss, dropout/gating, channel swap, phase inversion, imbalance, sample corruption, and transport counter sequences (increase/reset/node replacement/missing/failed). Store only consented sample files in test fixtures; live captures default to numeric summaries, not audio persistence.
- Require both unit tests and replay tests with expected feature ranges/outcomes. Include false-positive clean speech/music/silence, encoded references, multiple sample rates/channels, short/partial buffers and corrupt data. Blind event labels and independently reviewed audio are needed before perceptual claims.
- Every proposed threshold must identify its capture point, rate/channel conditions, baseline provenance, corpus size, false-positive/false-negative results and calibration status. Use `candidate` until this evidence exists. Do not treat the current high-frequency heuristic, LPC ratios, or Annex-2 4x true-peak estimate as fully validated.
- Keep analysis resource caps explicit: sample duration, PCM bytes, per-feature windows, event cap, subprocess timeout, memory budget and CPU time. Downsample/feature batching only after validation. No full recording in persistent history; hash/metadata only when needed and privacy reviewed.

## Watchdog cycle and recovery protocol

1. Check mute/active intent and pre-snapshot transport. If muted, close taps and return no verdict.
2. Capture bounded raw and processed taps plus optional stereo B1 monitor. Record read counts, gaps, overflow, and paired coverage.
3. Take post-snapshot transport and compute identity-safe deltas. Keep any failed query partial/unknown.
4. Engine emits measurements/outcome with evidence and localization.
5. Recovery controller acts only on `CONFIRMED_SIGNAL_FAULT` with complete/stable capture, required independent cues and no transport conflict; obey current cooldown/hourly cap. Candidate-only, NO_SAMPLE, INCONCLUSIVE, stale, or conflicted evidence must not restart.
6. If recovery is approved, restart only the existing Carla owner, validate node/link topology, collect one post sample and transport snapshot, and report recovered signal vs degraded transport separately. If verification is incomplete, state it.

## Shared schema and compatibility

Create a versioned `SignalQualityReport` containing capture metadata, per-tap metrics, transport snapshot/deltas, localization hypothesis, component confidence/gates, event candidates, outcome, and `automatic_action: none` from the engine. The watchdog state/history should persist bounded JSON-compatible numeric/enum facts only; the analyzer can render richer text and plots from the same report. Migrations must accept older state/history safely and never trust persisted IDs across node recreation.

## Phased implementation

1. Extract pure feature helpers/schema and port existing measurement code without changing decisions; prove parity by replaying the current corpus and comparing feature outputs.
2. Add labelled fixtures and mutation/false-positive tests; validate true-peak implementation against official reference vectors before calling it compliant.
3. Integrate the offline analyzer to consume the shared report and expose consistent provenance/confidence.
4. Integrate the live watchdog in shadow mode (measure/log only); compare reports over a review period with no notifications or recovery decisions.
5. After corpus validation and independent review, enable only narrowly scoped classifier gates. Keep transport-conflict suppression and conservative recovery as explicit policy.
6. Add user-facing Hearth UI summary only through explicit actions; no analyzer on UI refresh ticks and no new background service.

## Decisions and non-goals

- No aggressive restart behavior, auto-buffer changes, whole-stack restarts, permanent capture streams, always-on graph poller, or audio recording to diagnose quality.
- The unresolved ~97–99 Hz line is not called mains hum; the current refined reference replay did not establish it as a persistent stable tone.
- Carla plugin crash/runtime investigation remains separately blocked by the private PipeWire socket preflight; discovery/static metadata do not prove DSP or crash cause.
- MIDI Controls and Routing Grid are separate from signal-quality work and must remain read-only with the current ownership boundaries.
