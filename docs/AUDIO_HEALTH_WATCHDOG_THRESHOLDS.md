# Audio-health watchdog proposal - thresholds and confidence

The watchdog proposal remains offline/observational. These are heuristic replay thresholds, not calibrated microphone fault limits. The proposal does not capture live PCM, persist sample history, notify, change routes, or restart services.

## Current numeric thresholds

| Measurement | Default | Meaning / limitation |
|---|---:|---|
| WAV input limit | 480,000 frames | Up to 10 s at 48 kHz; file size is bounded by PCM format and channels. |
| Sample minimum | 256 samples | Smaller signal buffers are inconclusive. |
| Clip candidate | absolute sample >= 0.995 normalized full scale | Counts samples/runs only; cannot prove analog clipping or audible distortion. |
| Near-zero sample | absolute sample <= 1e-6 | Descriptive run count, not a dropout verdict. |
| Transient candidate baseline floor | 0.05 absolute sample-step | Robust MAD floor; avoids declaring small changes as clicks. |
| Transient multiplier | 8 x robust MAD scale | Candidate threshold; uncalibrated and content-sensitive. Candidate list capped at 32. |
| Stable-tone minimum RMS | 0.005 | Below this, stable-tone search returns no candidate. |
| Stable-tone split | two halves of the sample | Rising zero-crossing estimate per half; both must be valid. |
| Tone consistency | <= 2.5% frequency variation | A stable line is reported as UNIDENTIFIED_TONE with low confidence. It is not called mains hum or assigned a cause. |
| Dropout window | 20 ms | Fixed complete windows; trailing partial window ignored. |
| Dropout minimum run | 3 windows (60 ms) | Low RMS candidate only. |
| Dropout RMS threshold | 0.0001 | Very-low amplitude; quiet speech, gate, mute or intentional silence can mimic a dropout. |
| Transport pw-top timeout | 8 s | Bounded read-only command. |
| Transport metadata timeout | 4 s | Bounded read-only command. |

## Synthetic replay corpus

Tests create temporary mono 16-bit PCM WAVs and synthetic in-memory samples:

- 50 Hz and 60 Hz stable lines: classified as UNIDENTIFIED_TONE, low confidence; no mains/hum label.
- 97, 98 and 99 Hz lines: same UNIDENTIFIED_TONE classification. In particular, 97-99 Hz remains unidentified, not mains hum.
- Full-scale sine with clipped sample candidates: SUSPECT plus sample counts. This is not a validated clipping fault.
- A 80 ms zero-amplitude span in a 440 Hz signal: dropout candidate from four complete 20 ms windows; sample indices are reported. This does not establish a real interruption or trigger recovery.
- Silence: NO_SAMPLE, not SUSPECT.

## Confidence states

- **low**: the bounded signal buffer is available, but the result comes from uncalibrated heuristics. Tone candidates and transient/dropout flags stay low confidence.
- **unknown**: input metrics are absent or incomplete. Transport remains unknown in the proposal because a single snapshot cannot establish a comparable same-node ERR delta or continuity.
- **healthy_observed / suspect / no_sample / inconclusive** are signal states, not confidence grades. A healthy_observed result means no current heuristic flag, not certified healthy audio. SUSPECT means one or more candidates, not a confirmed fault.
- No **high** confidence or automatic-recovery state is produced by this proposal. There is no live watchdog integration, temporal baseline, calibrated labelled corpus, or validated causal localization.

## Required interpretation

Do not restart Carla or other services from this proposal. A 50/60 Hz tone is a frequency observation, not proof of mains coupling or ground loop. A 97-99 Hz line remains unidentified. Transport probe failure, missing data, node replacement, counter reset, or non-comparable snapshots must remain unknown, never be converted to zero errors or healthy. Recovery ownership remains outside this measurement-only proposal.
