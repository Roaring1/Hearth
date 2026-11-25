# Hearth signal-quality core

## Summary
Pure bounded signal-quality analysis for normalized mono PCM, plus tone/stereo/true-peak metrics, dropout candidates and transport-context replay. Candidate results remain diagnostic; no live capture or automatic recovery is performed.

## Evidence
- Corrected bool numeric-threshold validation: commit 72f6e29; hosted CI 45548618808 PASS on W2c head 5a303c4.
- Integrated hosted CI 45548952071 PASS on integrated head 8508374.
- Integrated gate: 932 passed, 18 skipped, 27 warnings; Ruff check/format PASS; mypy unavailable in that environment.
- W2d extends synthetic replay coverage with 50/60 Hz and 97-99 Hz lines (all low-confidence UNIDENTIFIED_TONE), clipping, dropout and silence; confidence output is explicit and transport remains unknown without a baseline.

## Risks / limitations
- Heuristic metrics are not calibrated/perceptual fault labels.
- 97-99 Hz remains unidentified, never mains hum.
- True-peak estimator still needs reference-vector validation.
- No live integration into the watchdog or audio analyzer.

## Reviewer checklist
- Verify strict validation and fail-closed outcomes.
- Confirm mutation tests for threshold/tone/dropout behavior.
- Keep measurement-only boundary and no automatic repair.
