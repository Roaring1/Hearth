# Hearth issue status audit - 2026-11-24

Based on the current lane notes and this branch's shipped changes. This is a local audit, not an issue-tracker edit; statuses should be rechecked against live GitHub before posting.

| Issue | Status | Stale/done assessment | Notes |
|---|---|---|---|
| #4 | Open, blocked | **Stale verification record; not done.** | PC-side live audio/session verification remains intentionally blocked pending a safe interactive window. Historical evidence is not current host state.
| #9 | Open parent | **Not stale; active umbrella.** | Keep parent/end-to-end acceptance distinct from overview and component implementation. Manual KDE Wayland/Vesktop acceptance remains blocked.
| #11 | Open, partial | **Foundation now shipped; remaining issue scope active.** | PR #18 merged previously; W2 added pure on-demand model/coordinator in this branch. Production capture adapter and safe route/session integration remain.
| #12 | Open, partial | **Not done.** | UI/production manager injection and capture-adapter integration plus acceptance remain; no live route mutation authorized.
| #13 | Open, partial | **Research/source-review portion likely done; empirical matrix stale/blocked.** | Portal/Vesktop/monitor interaction still needs a safe interactive session; no second desktop/session launched.
| #14 | Open, deferred | **Stale parking lot by design; no action.** | Optional/post-V1 ideas are not prerequisites and should remain parked until a concrete need is scoped.
| #24 | Open, active | **Not done.** | PR #26 remains pending review/CI/integration; Windows check reportedly red at PR head despite a separate W2 validated branch fix. Recheck live tracker and PR status.

## Newly shipped on this branch

- `hearth.audio.capture_core`: pure on-demand device/audio-session coordinator with fake backend, model validation, lifecycle snapshots, explicit discovery/start/stop and retryable stop failure.
- Signal-quality dropout candidate detection and transport-context replay comparison.
- Boolean sample-rate validation fix for direct stable-tone detection.

No live audio/hardware/PipeWire, GUI, route, service, issue tracker, or PR operation was performed.


## Blocker cross-review correction - 2026-11-24

- Fixed `detect_dropouts` to calculate RMS using the same normalized `values` sequence used by validation. Numeric-string samples no longer reach arithmetic as strings and cause TypeError.
- Added regression tests for `detect_dropouts(['0.0'] * 2880, 48000)` and ordinary numeric samples at the same rate. Both assert the expected three-window candidate.
- Focused signal-quality tests: 31 passed; capture-core + signal-quality tests: 40 passed.
- Mutation test reverted the RMS window slice to raw `samples`; numeric-string regression failed with TypeError as expected (mutation confirmed). Restored corrected source.
- Full project check: 926 passed, 18 skipped, 27 warnings in 41.61s; legacy module compile, desktop entry, AppStream and shell checks passed.
- Ruff check/format, mypy, and pre-commit hook passed.
