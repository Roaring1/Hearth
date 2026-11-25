# Hearth integrated signal-quality and Capture Mode foundation

## Summary
Superseded by R3 branch w2/integrated-r3-20261125. The earlier integrated head remains a reference candidate.

## Evidence
- Original integrated head 8508374: 932 passed, 18 skipped, 27 warnings; Ruff check/format PASS; hosted Windows 45548952071 PASS.
- R3 local full gate: 954 passed, 18 skipped, 30 warnings in 33.23s; all checks passed.
- R3 hosted Linux CI 45566497898 PASS on code/doc head 5f6f854; lint/types, Python 3.11/3.12/3.13, GTK smoke and package/install smoke all green.
- R3 hosted Windows run 45566497892 PASS on 5f6f854.
- R3 adds a pure Capture Mode view-model, acceptance roll-up for issues #9/#11/#12, watchdog synthetic corpus/threshold-confidence docs, and PR #26 rebase recipe.

## Risks / limitations
- View-model is not wired to GTK; active Start/Stop remains unavailable.
- Production audio-route adapter/session ownership unresolved; no real Capture Mode.
- Signal-quality outputs remain heuristic, not fault verdicts or recovery triggers.
- No live capture, PipeWire routes, services, Wayland portal, Vesktop or physical card validation.

## Suggested review sequence
1. PR #26 fixes branch after owner approval and fresh checks.
2. Signal-quality core.
3. Capture core.
4. Integrated R3 candidate last.
