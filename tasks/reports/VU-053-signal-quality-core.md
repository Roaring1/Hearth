# VU-053 shared signal-quality core implementation

Date: 2026-11-25. Branch `w2/signal-quality-core-20261124`, worktree `/home/roaring/Hearth-W2-signal-core-20261124`, base `ce05b9d`. Implementation is pure Python and measurement-only. No production capture integration, services, GUI, routes, plugins, buffers, or `~/bin` scripts changed.


## Files

- `src/hearth/audio/signal_quality.py`
- `tests/test_signal_quality.py`


## Core API and guarantees

- `analyze_pcm(samples, sample_rate, config=None)` accepts bounded normalized mono PCM; caps buffers at 480,000 samples (10 seconds at 48 kHz), rejects short/oversized, invalid sample rates/config, booleans, nonnumeric and non-finite samples with `INCONCLUSIVE`.
- Reports immutable dataclasses/enums: sample count/rate/duration, RMS/peak/DC/crest factor, adjacent-difference RMS/ratio, clip sample/run stats, near-zero stats and bounded transient candidate indices. Outcomes: `HEALTHY_OBSERVED`, `SUSPECT`, `NO_SAMPLE`, `INCONCLUSIVE`.
- Transient and clip flags are candidates only; thresholds are uncalibrated. No automatic fault-confirmed state, no classifier, no persistence, and no recovery.
- `compare_taps()` refuses missing or unequal sample/rate coverage, reports low-confidence RAW_ONLY/PROCESSING_PATH/BOTH_TAPS/NO_CANDIDATE hypotheses, and explicitly notes content/time alignment is not verified.
- `compare_transport()` emits ERR delta only for same node and role with nondecreasing valid counters. Missing/invalid evidence, identity change, and reset are separate states. Rate/quantum changes are independent metadata flags.
- No third-party dependency is introduced. The core performs no capture, subprocess, PipeWire query, file I/O, or audio state change.

## Replay tests

13 focused tests pass: silence/no-sample, clean tone metrics, clipping candidate, impulse candidate, non-finite rejection, invalid rate/short/oversized bounds, raw/post candidate localization, unequal tap coverage, valid transport delta, missing/invalid transport, metadata change, invalid configuration, and boolean/float transport values.

The impulse replay initially failed to produce a transient candidate with the first floor; the candidate detector floor was adjusted and the replay now passes. It remains explicitly heuristic and not a perceptual or confirmed-fault label.


## Verification

- Focused pytest after final validation edits: 14 passed; strict mypy Windows-platform check: success, no issues in 1 source file.
- Strict mypy Windows platform: success, no issues in 1 source file.
- Ruff check and format: pass.
- Final full project gate passed with the final 14 replay tests; exact output is copied below.
- Performance sanity check on 480,000 synthetic samples (10 seconds at 48 kHz): 0.207 seconds elapsed; 0 transient candidates on a clean 440 Hz sine. This is a synthetic microbenchmark, not live capture.


## Not implemented / deliberately deferred

The `af7d76e` signal-quality extension adds:

- Stable-tone candidate detection using interpolated rising zero crossings across two halves of a buffer. It reports a frequency and variation only as `UNIDENTIFIED_TONE` at low confidence; it does not label mains hum.
- Stereo analysis with correlation, a low-confidence tone phase-difference estimate when both channels have comparable tone frequencies, per-channel true-peak estimates, and relative peak difference.
- A four-times Catmull-Rom inter-sample true-peak estimate. It is an estimate, not a certified oversampled true-peak measurement.

These additions are synthetic replay tested but remain heuristic and uncalibrated. They are not yet wired into the watchdog or offline analyzer; integration requires a separate phase and validated replay corpus before they can influence recovery. Band-energy, dropout/gate context, and broader transport-parser integration remain deferred.

## Final full-gate output (verbatim)

```text

[1m== ruff check[0m
All checks passed!
155 files already formatted

[1m== mypy[0m
Success: no issues found in 52 source files

[1m== pytest[0m
s....................................................................... [  7%]
........................................................................ [ 15%]
........................................................................ [ 23%]
........................................................................ [ 31%]
........................................................................ [ 39%]
........................................................................ [ 47%]
........................................................................ [ 55%]
........................................................................ [ 62%]
........................................................................ [ 70%]
........................................................................ [ 78%]
........................................................................ [ 86%]
........................................................................ [ 94%]
....................................ssssssssssssssss                     [100%]
=============================== warnings summary ===============================
tests/test_gui_visual.py::test_production_windows_render_and_capture
  /usr/lib64/python3.14/site-packages/gi/overrides/Gtk.py:1801: DeprecationWarning: Gtk.CssProvider.load_from_data is deprecated
    super().load_from_data(text, length)

tests/test_gui_visual.py: 25 warnings
  /usr/lib64/python3.14/site-packages/gi/_ossighelper.py:266: DeprecationWarning: 'asyncio.get_event_loop_policy' is deprecated and slated for removal in Python 3.16
    policy = asyncio.get_event_loop_policy()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_audio.py:16: Windows Core Audio backend
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/integration/test_pipewire_route_recovery.py:287: set HEARTH_RUN_PIPEWIRE_INTEGRATION=1 to run disposable PipeWire tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:23: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:32: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:41: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:59: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:81: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:113: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:141: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:202: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:240: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:263: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:272: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:285: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:322: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:356: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:364: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:374: Windows port regression tests
899 passed, 18 skipped, 26 warnings in 42.71s

[1m== legacy module compiles[0m

[1m== desktop entry[0m

[1m== appstream metadata[0m
P: io.github.roaring1.Hearth:4: cid-contains-uppercase-letter io.github.roaring1.Hearth

✔ Validation was successful: pedantic: 1

[1m== shell scripts[0m

[32mAll checks passed[0m

```


## Final full-gate output (verbatim)

```text

[1m== ruff check[0m
All checks passed!
156 files already formatted

[1m== mypy[0m
Success: no issues found in 52 source files

[1m== pytest[0m
s....................................................................... [  7%]
........................................................................ [ 15%]
........................................................................ [ 23%]
........................................................................ [ 31%]
........................................................................ [ 39%]
........................................................................ [ 47%]
........................................................................ [ 54%]
........................................................................ [ 62%]
........................................................................ [ 70%]
........................................................................ [ 78%]
........................................................................ [ 86%]
........................................................................ [ 94%]
.....................................ssssssssssssssss                    [100%]
=============================== warnings summary ===============================
tests/test_gui_visual.py::test_production_windows_render_and_capture
  /usr/lib64/python3.14/site-packages/gi/overrides/Gtk.py:1801: DeprecationWarning: Gtk.CssProvider.load_from_data is deprecated
    super().load_from_data(text, length)

tests/test_gui_visual.py: 42 warnings
  /usr/lib64/python3.14/site-packages/gi/_ossighelper.py:266: DeprecationWarning: 'asyncio.get_event_loop_policy' is deprecated and slated for removal in Python 3.16
    policy = asyncio.get_event_loop_policy()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_audio.py:16: Windows Core Audio backend
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/integration/test_pipewire_route_recovery.py:287: set HEARTH_RUN_PIPEWIRE_INTEGRATION=1 to run disposable PipeWire tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:23: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:32: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:41: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:59: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:81: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:113: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:141: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:202: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:240: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:263: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:272: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:285: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:322: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:356: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:364: Windows port regression tests
SKIPPED [1] ../../home/roaring/Hearth-W2-signal-core-20261124/tests/test_windows_port.py:374: Windows port regression tests
900 passed, 18 skipped, 43 warnings in 43.78s

[1m== legacy module compiles[0m

[1m== desktop entry[0m

[1m== appstream metadata[0m
P: io.github.roaring1.Hearth:4: cid-contains-uppercase-letter io.github.roaring1.Hearth

✔ Validation was successful: pedantic: 1

[1m== shell scripts[0m

[32mAll checks passed[0m

```

## Follow-up correction and validation (boolean thresholds)

A follow-up fix on branch `w2/signal-quality-core-20261124` rejects booleans for each numeric configuration threshold (`clip_threshold`, `near_zero_threshold`, `transient_mad_multiplier`, `transient_floor`) and rejects a boolean sample rate when `detect_stable_tone()` is called directly. Existing numeric metadata guards also reject booleans for transport `err_count`, `sample_rate`, and `quantum`; dedicated tests now cover sample-rate/quantum metadata. Focused tests: **25 passed**.

Mutation evidence: removed the `clip_threshold` bool guard temporarily. The targeted boolean-threshold test failed for `clip_threshold` (1 failed, 3 passed), proving sensitivity; exact source backup restored afterward. Full project gate is currently running; its final result will be appended.

The earlier `Not implemented / deliberately deferred` section has been corrected to reflect `af7d76e`: stable-tone candidate detection (`UNIDENTIFIED_TONE`, low confidence), stereo correlation/phase estimate, and 4x Catmull-Rom inter-sample true-peak estimate are implemented. These remain synthetic/heuristic measurements and are not integrated into live watchdog/offline analyzer or recovery. Band-energy and dropout/gate context remain deferred.
