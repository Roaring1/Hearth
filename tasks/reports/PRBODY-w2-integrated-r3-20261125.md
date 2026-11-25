# Hearth integrated R3 candidate - 2026-11-25

## Summary
R3 is an isolated candidate consolidating the W2c integrated signal-quality/capture foundation and W2d queue work. It includes the Capture Mode presentation model, issue #9/#11/#12 acceptance roll-up, synthetic watchdog replay fixtures and confidence/threshold docs, plus the PR #26 safe rebase recipe.

## Evidence
- Base: w2/integrated-20261124 (8508374), whose hosted Windows run 45548952071 passed and prior local gate reported 932 passed, 18 skipped, 27 warnings.
- R3 queue branch commits include CI-on-branch push trigger, Capture Mode view-model, acceptance roll-up, synthetic watchdog coverage and PR26 rebase recipe.
- Full R3 local gate: PENDING.
- Hosted R3 CI (Linux Python 3.11/3.12/3.13, GUI smoke, packaging, Windows UCRT64): PENDING.

## Risks / limitations
- No live services, audio routes, capture device, GUI, Wayland/Vesktop portal, or desktop session actions.
- Capture Mode model is pure and not connected to GTK; operational Start/Stop and production audio-route adapter remain missing.
- Signal-quality results are heuristics, not fault verdicts or recovery triggers.
- PR #26 is unchanged and remains UNSTABLE until owner-approved fix application and fresh checks.

## Reviewer checklist
- Verify exact R3 commit and all gate logs.
- Confirm branch and hosted checks match exact R3 head.
- Reconcile PR #26 recipe with current base/head before use.
- No PR/merge/release is authorized by this draft.
