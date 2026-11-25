# Hearth integrated signal-quality and Capture Mode foundation

## Summary
Combines signal-quality core, dropout/transport-context primitives, bounded WAV analysis CLI, capture-core/session foundation, read-only discovery adapter and Capture Controls design. This is a candidate branch only.

## Evidence
- Original integrated head 8508374: 932 passed, 18 skipped, 27 warnings; Ruff check/format PASS; mypy not installed.
- Hosted Windows run 45548952071 PASS on exact original integrated head 8508374.
- W2d additions: pure Capture Mode view-model (11 assertions), acceptance roll-up for issues #9/#11/#12, watchdog synthetic fixtures for 50/60/97/98/99 Hz, clipping, dropout, silence, thresholds/confidence doc, PR #26 rebase/fix recipe.
- W2d hosted CI matrix run 45565337392 PASS on e8b0141 before the later changes. R3 full gate and hosted runs are pending.

## Risks / limitations
- The view-model is not wired to GTK.
- Signal-quality features remain heuristic/measurement-only.
- Production CaptureAudioAdapter and active-session route ownership are unresolved.
- No physical capture-card, live PipeWire, KDE Wayland/Vesktop or dormant-resource audit.
- PR #26 remains OPEN/UNSTABLE; W2 fixes are not applied to its PR head.

## Suggested review sequence
1. PR #26 fixes branch (only after owner approval and fresh combined-head CI).
2. Signal-quality core.
3. Capture core.
4. Integrated candidate last; verify ancestry and CI.

## Reviewer checklist
- Reverify branch/head/CI against current remote.
- Review issues #9/#11/#12 acceptance roll-up and thresholds/confidence documentation.
- Confirm no live service, route, hardware, GUI, PR, merge or release action occurred.
