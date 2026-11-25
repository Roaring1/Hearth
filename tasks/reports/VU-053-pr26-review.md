# PR #26 review - MIDI Controls and Routing Grid

Reviewed 2026-11-24; head `32e203d` (`feature/midi-routing-grid-2026-11-23`), base `ce05b9d`. PR is OPEN, not draft, and reported mergeable. No PR branch edit, PR creation, merge, or worker was performed.


## CI and code review
- Linux hosted CI passed lint/types, GTK4 GUI import, install smoke, and Python 3.11/3.12/3.13 tests.
- Windows UCRT64 run `45331594556` failed at `tests/test_collector.py::test_collect_keeps_core_audio_read_failures_distinct_from_empty_graph`: DID NOT RAISE `CommandError`; total 1 failed, 731 passed, 141 skipped, 74 deselected.
- The test's `os.name == "nt"` skip marker does not fire under the runner's UCRT/MSYS platform identity. The test is Linux/PipeWire-specific, but Windows uses `windows_audio.collect()` instead of the monkeypatched Linux pactl collector. W2 branch fixes this test's skip predicate to `sys.platform.startswith("win")`; Windows behavior still needs hosted confirmation after the fix is carried into PR.
- PR diff check passed (`git diff --check`); 12 files, 1,837 additions and 39 deletions. Linux PR test run: 918 passed, 18 skipped. PR branch Ruff check and format check passed. Mypy must be invoked with the project `--platform win32` command, not Linux-default.

## Observations / follow-up
- The capture-client inventory launches `pactl list short source-outputs` on every collector tick (visible 0.25–0.5 s); this optional query may deserve a measured cadence/cache rather than every refresh.
- Grid evidence provenance and fail-soft optional inventory are generally conservative. GUI/hardware acceptance is not established by synthetic fixtures.
- No definite unused code was proven; no speculative code removal.
