# Passive microphone quality report timer

This standalone user service captures a 10-second stereo sample from `b1_mic` with `pw-record`, analyzes it offline with `hearth.audio.quality_report`, and stores timestamped WAV/JSON pairs under `~/.local/state/hearth/quality/`. It is report-only: no routing, buffering, mute, or recovery operations. Retention is limited to 48 WAV/JSON pairs.

Install `scripts/hearth_mic_quality_report.py` as `~/bin/hearth_mic_quality_report.py` and the unit pair from `deploy/systemd/user/` under `~/.config/systemd/user/`. Reload the user manager and enable the timer. To roll back, disable the timer and remove the script and units. The live trial requires a functioning user PipeWire session; the report may correctly return `no_sample` when the processed source is silent.

## Trial evidence (2026-11-27)

- One synthetic WAV offline analyzer CLI test passed from neutral working directory with `PYTHONPATH=src`.
- Two manual live report runs produced valid 9.99-second WAVs after correcting the script's JSON newline encoding.
- First run failed to connect to PipeWire because the shell lacked the user bus environment. Explicit user bus environment enabled systemd; subsequent capture returned `no_sample`, RMS 0, peak 0. A read-only mute/source inspection showed `b1_mic` unmuted and RUNNING. This is not proof of microphone signal quality.
- Timer scheduling is being verified with a temporarily accelerated 1-minute interval; restore the intended 30-minute interval after cycle verification.
