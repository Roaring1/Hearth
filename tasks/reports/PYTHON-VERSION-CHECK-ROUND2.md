# Python cross-version check - Hearth Round 2

## Local availability
- Only Python **3.14.4** was discoverable (`python3.14` and `python3`); no Python 3.11, 3.12, or 3.13 interpreter was installed. No interpreter or dependency was installed for this check.

## Python 3.14 result
- Focused signal-quality and transport suite, run from `/tmp` with `PYTHONPATH=src`: **87 passed** (`test_signal_quality.py`, `test_audio_health_transport.py`, `test_audio_health_signal.py`).
- The new watchdog proposal's 3 synthetic/fake-probe tests passed under the project's default `python` earlier in Round 2. Python 3.14 cross-version collection of that script test is not included: its import requires the repository root, which exposes the root `hearth.py` compatibility shim and shadows the `src/hearth` package. This import-path issue is documented and was not changed as part of this cross-version check.

## Gap
- Python 3.11-3.13 compatibility remains unverified locally. The project declares `>=3.11`; hosted CI should cover supported versions, but this local check cannot establish that without those interpreters.
