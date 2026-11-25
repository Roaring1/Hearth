# Hearth signal-quality core

## Summary
Adds pure signal-analysis helpers for synthetic PCM, including diagnostic feature measurements and classification/state outputs. Core is separated from capture/device operations and does not trigger automatic recovery.

## Evidence
- Initial hosted run failed on boolean `sample_rate` validation; fixed in `72f6e29`.
- Hosted run **45548618808 PASS** on corrected commit `5a303c4`.
- Integrated hosted run **45548952071 PASS** on `8508374`.
- Integrated canonical gate: 932 passed, 18 skipped, 27 warnings; Ruff check/format PASS.

## Risks / limitations
- Diagnostic metrics are not perceptual click/crackle classification or automatic-repair criteria.
- True-peak implementation needs full reference-vector/conformance validation.
- Confirm branch/head and hosted run mapping before merge.

## Suggested merge order
1. PR26 fixes (if dependency history requires them).
2. Signal-quality core.
3. Capture core.
4. Integrated branch last.

## Reviewer checklist
- Verify strict numeric validation and regression coverage for bool/non-finite/invalid inputs.
- Check synthetic tests and mutation evidence.
- Confirm no live audio capture or recovery side effects.
