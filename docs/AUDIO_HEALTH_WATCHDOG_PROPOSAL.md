# Audio health watchdog integration proposal (not installed)

This repository-only spike demonstrates the intended boundary: the existing read-only WAV analyzer supplies a signal-quality report, and a separate bounded PipeWire transport probe supplies graph/counter observability. The output is explicitly `measurement_only` with `automatic_action: none`.

## Prototype

`scripts/audio_health_watchdog_proposal.py WAV_FILE` prints one JSON object. It analyzes an existing WAV file offline, then runs only read-only `pw-top -b -n 6` and `pw-metadata -n settings` probes. It does not capture microphone PCM, persist state, change routes, notify, or restart services.

## Test scope

`tests/test_audio_health_watchdog_proposal.py` creates synthetic PCM WAV and injects a fake transport runner. Tests do not invoke PipeWire commands or access live services.

## Installation/rollout notes

- Do not install this spike. The live watchdog remains the existing separately managed script/unit.
- Before any deployment, review privacy/retention (offline WAV is user-provided; no waveform is written by the tool), bounded command timeouts, failure-state handling, and integration with the existing scheduled capture.
- A production adaptation must not infer health from unavailable transport metrics, and must not restart on transport/signal conflicts.
- Rollback: remove the proposal script/test/docs from the repository candidate; no live system changes exist to revert.
