# Passive microphone quality report timer

This standalone user service captures a 10-second stereo sample from `b1_mic` with `pw-record`, analyzes it offline with `hearth.audio.quality_report`, and stores timestamped WAV/JSON pairs under `~/.local/state/hearth/quality/`. It is report-only: no routing, buffering, mute, or recovery operations. Retention is limited to 48 WAV/JSON pairs.

Install `scripts/hearth_mic_quality_report.py` as `~/bin/hearth_mic_quality_report.py` and the unit pair from `deploy/systemd/user/` under `~/.config/systemd/user/`. Reload the user manager and enable the timer. To roll back, disable the timer and remove the script and units. Reports may correctly return `no_sample` when the processed source is silent.

## Trial evidence (2026-11-27)

- Synthetic 440Hz WAV offline analyzer CLI passed when run from a neutral working directory with `PYTHONPATH=src`; first invocation from repo root was blocked by the `hearth.py` compatibility shim.
- A manual live run initially failed because the shell lacked the user's PipeWire bus environment; explicit `XDG_RUNTIME_DIR=/run/user/1000` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` enabled the user service manager.
- Two manual 9.99-second WAV runs were made. One JSON was malformed due to escaped newline; this was corrected. The first corrected manual report was `no_sample`.
- Temporary 1-minute timer cadence produced two scheduled reports: `20261128T064308Z` (`no_sample`, RMS/peak 0) and `20261128T064410Z` (`healthy_observed`, RMS 0.0007946, peak 0.03571, zero clipped samples, no unidentified tones). Both WAVs were about 9.99 seconds and their JSON parsed. This is not proof of mic signal quality; the second result is very low-level.
- Timer cadence restored to 30 minutes, `AccuracySec=1min`. Timer is enabled and active; next scheduled run was 23:14 PST. No existing watchdog/service/routes changed.
- `./scripts/check.sh` passed (exit 0; “All checks passed”). Commit `263b24f` pushed to `w2/integrated-r3-20261125`.
