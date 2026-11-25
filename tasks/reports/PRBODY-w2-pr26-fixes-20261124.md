# Hearth PR 26 fixes stack

## Summary
Follow-up fixes for PR #26 on branch `w2/pr26-fixes-20261124` (`7ca9d83`), focused on the regression/fix stack while preserving platform and safety boundaries.

## Evidence
- Hosted Windows CI: **45534707201 PASS** on `7ca9d83`.
- Queue item 1 records the hosted result as green.
- Verify exact commit diff and CI run before opening/submitting.

## Risks / limitations
- Hosted Windows result is point-in-time for the recorded commit.
- Queue lacks a detailed test matrix here; confirm individual regression coverage before merge.

## Suggested merge order
1. PR26 fixes if prerequisite.
2. Signal-quality core.
3. Capture core.
4. Integrated branch last, after verifying it is based on and includes intended commits.

## Reviewer checklist
- Verify commit and CI run correspondence.
- Check overlap with signal/capture cores and integrated branch.
- No host service deployment/restart as part of merge.
