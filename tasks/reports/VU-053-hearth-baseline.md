# VU-053 Hearth baseline audit

Date: 2026-11-24. Baseline repository: `main` at `ce05b9d` (PR #23 merge). Working tree was clean before review. This report is read-only audit evidence; no live audio/UI/service/script changes were made.

## Local gate on main

`./scripts/check.sh` completed all stages:
- Ruff: all checks passed; 151 files already formatted.
- Mypy: success, no issues in 51 source files.
- Pytest: 886 passed, 18 skipped, 27 warnings.
- Legacy module compilation, desktop-entry validation, AppStream validation, ShellCheck: all passed.
- AppStream emits one pedantic warning: app ID contains uppercase letters (`io.github.roaring1.Hearth`); validation still succeeds.
- Skips include Windows-specific tests and opt-in disposable PipeWire integration. GUI visual test was run under Xvfb.

## Documentation / stale references

- `CONTRIBUTING.md` still installs GTK3 and Ayatana appindicator, while README and current app are GTK4. Correcting contributor package guidance is in this branch.
- `CHANGELOG.md` is still headed by Unreleased round 32 dated 2026-09-20 and contains no subsequent dated project checkpoint. Add a documentation-only entry for this correction; no historical entries are rewritten.
- Issue #4, “Resume PC-side Hearth verification after NobaraSupervisor reconnects,” remains open. Its interactive desktop verification is still deferred by the user’s no-focus/no-live-desktop boundary; issue content is historical and must not be treated as current host state.
- No dead code was confidently established from static scan; no speculative deletion is proposed.

## PR #26 review

PR #26 (`32e203d`, feature/midi-routing-grid-2026-11-23) is OPEN and not draft; GitHub reports mergeable. It changes 12 files, +1,837/-39. Linux CI passed lint/types, tests (Python 3.11/3.12/3.13), GTK4 visual, and install smoke. Windows UCRT64 tests + installed-wheel smoke failed in run `45331594556` with 1 failed, 731 passed, 141 skipped, 74 deselected. Failure: `tests/test_collector.py::test_collect_keeps_core_audio_read_failures_distinct_from_empty_graph` - “DID NOT RAISE CommandError”. The test monkeypatches Linux `collector.pactl.raw_checked`, but `collector.collect()` uses the Windows-specific `windows_audio.collect()` on Windows; test lacks a platform skip or Windows-specific assertion. This appears to be a test portability defect rather than a routing-grid production failure, but PR CI is not green.

PR code's local Linux full check was 918 passed, 18 skipped (27 warnings); Ruff check and formatting passed. Mypy must be run with the repo's prescribed `--platform win32`; the Linux-default direct invocation is not valid evidence for this cross-platform tree. No PR edits were made and no PR/merge action was taken.

## Risk notes

- The new collector's optional capture-client inventory adds a bounded `pactl list short source-outputs` subprocess per collector tick (visible poll cadence 0.25–0.5 s); consider caching/independent cadence if profiling shows overhead.
- The Routing Grid remains read-only, consumes snapshots, and visually distinguishes live/configured/unknown evidence. No GUI/hardware acceptance is claimed.
