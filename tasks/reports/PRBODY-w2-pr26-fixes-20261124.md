# Hearth PR #26 fixes candidate

## Summary
Separate follow-up branch w2/pr26-fixes-20261124 applies the Linux-only test skip for the PipeWire collector on Windows and reduces optional source-output refresh cadence with a five-second cache/remapping.

## Evidence
- Head: 7ca9d83 (based on PR #26 head 32e203d).
- Hosted Windows UCRT64 run 45534707201: PASS.
- Collector focused suite: 30 passed.
- Local full gate previously recorded: 920 passed, 18 skipped. Local environment omitted Ruff/mypy from PATH, so focused Ruff checks and hooks were used; hosted Windows run covers them.
- PR #26 itself remains OPEN/UNSTABLE at head 32e203d; two Windows runs failed (45331506913, 45331594556), while Linux CI 45331594463 passed.

## Risks / limitations
- Candidate fix branch is not the PR head and has not been integrated into PR #26.
- Require fresh combined-head CI after rebasing and applying both commits.
- No PR update, comment, merge, or live deployment performed.

## Reviewer checklist
- Verify the test skip applies only to the Linux-specific PipeWire test.
- Review source-output cache age, source-name remapping, and optional query failure semantics.
- Confirm exact branch/head and CI mapping before any owner-approved PR update.
