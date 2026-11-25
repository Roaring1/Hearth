# Hearth integrated R3 candidate - 2026-11-25

## Summary
R3 is an isolated candidate consolidating the W2c integrated signal-quality/capture foundation and W2d queue work. It includes the Capture Mode presentation model, issue #9/#11/#12 acceptance roll-up, synthetic watchdog replay fixtures and confidence/threshold docs, plus the PR #26 safe rebase recipe.

## Evidence
- Base: w2/integrated-20261124 (8508374), hosted Windows run 45548952071 passed; prior local gate 932 passed, 18 skipped, 27 warnings.
- R3 local full gate: **954 passed, 18 skipped, 30 warnings in 33.23s**; scripts/check.sh reported All checks passed.
- Ruff check: All checks passed; Ruff format: 188 files already formatted (manual invocation because tools were not on scripts/check.sh PATH).
- Local mypy skipped (not installed).
- Hosted R3 CI run **45566497898 PASS** on code/doc head 5f6f854: lint/types, Python 3.11/3.12/3.13, GTK smoke and install/package jobs passed.
- Hosted R3 Windows run **45566497892 PASS** on 5f6f854 (UCRT64 tests and installed-wheel smoke).
- Item 2 view-model: 11 new tests; focused core+model suite 20 passed; removal mutation failed as expected.
- Item 4 watchdog: 42 focused tests passed; confidence, stable-tone and dropout-threshold mutations all failed as expected.

## Risks / limitations
- The view-model is not wired to GTK.
- Signal-quality features remain heuristic/measurement-only.
- Production CaptureAudioAdapter and active-session route ownership are unresolved.
- No live services, routes, capture hardware, GUI, Wayland/Vesktop portal, or dormant-resource audit.
- PR #26 is unchanged and remains UNSTABLE until owner-approved fix application and fresh checks.

## Review sequence
1. PR #26 fixes branch (only after owner approval and fresh combined-head CI).
2. Signal-quality core.
3. Capture core.
4. Integrated candidate last; verify ancestry and CI.

## Reviewer checklist
- Verify exact R3 commit and hosted CI result.
- Review issues #9/#11/#12 acceptance roll-up and thresholds/confidence documentation.
- Confirm no live service, route, hardware, GUI, PR, merge or release action occurred.
