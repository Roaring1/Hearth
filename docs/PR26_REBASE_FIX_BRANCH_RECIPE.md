# PR #26 rebase and fix-branch recipe

## Why the PR is UNSTABLE

As checked on 2026-11-24:

- PR #26 is OPEN, head `feature/midi-routing-grid-2026-11-23` at `32e203d322d34b351e0de1ad48b3481dfda8170e`, base `main` at `ce05b9dc6dc07f7c0c9db94d1f849f0df0d431ce`.
- Linux CI run `45331594463` passed its lint/types, Python 3.11/3.12/3.13, GTK4 smoke and install-smoke jobs.
- Windows UCRT64 failed twice on the same PR head: `45331506913` and `45331594556`. This is why mergeStateStatus is UNSTABLE, not a merge conflict.
- A read-only merge-tree simulation of current main against the PR head returned a clean tree. Main is already the PR branch's merge-base; rebasing on today's main is a no-op.
- W2's fix branch `w2/pr26-fixes-20261124` is based directly on PR head and contains these ordered commits:
  1. `e3908f2d21db623c8bde7829059f26ce969bbe84` - skip the Linux-only PipeWire collector test on Windows.
  2. `7ca9d8356d1d8632f7ba8b2124ad785afdbff9ae` - cache optional capture-client inventory for five seconds.
- The fix branch's hosted Windows run `45534707201` passed, but that is not the same as a combined-head run on a newly rebased PR branch. Re-run all required checks after application.

## Apply in a disposable worktree (do not work in the PR branch checkout)

This recipe intentionally creates a new local branch/worktree and does not modify or push the existing PR head.

```bash
cd /home/roaring/Documents/GitHub/Hearth
git fetch origin

git worktree add -b fix/pr26-rebased-fixes-20261125 \
  ../Hearth-pr26-rebased-fixes \
  origin/feature/midi-routing-grid-2026-11-23

cd ../Hearth-pr26-rebased-fixes

# Rebase against current main. Today this is expected to be a no-op.
git rebase origin/main

# Apply W2's fixes in parent order, preserving their distinct commit scopes.
git cherry-pick e3908f2d21db623c8bde7829059f26ce969bbe84
git cherry-pick 7ca9d8356d1d8632f7ba8b2124ad785afdbff9ae

# Inspect and run all local gates before pushing this candidate branch.
git diff origin/main...HEAD
git diff --check
./scripts/check.sh

# Push only the candidate fix branch, not feature/midi-routing-grid-2026-11-23.
git push -u origin fix/pr26-rebased-fixes-20261125
```

If a later main update introduces rebase/cherry-pick conflicts, stop and resolve them in this disposable candidate worktree only. Re-run focused collector tests, full local gate, and hosted CI for the exact candidate head. Do not force-push, update PR #26, or merge without owner approval.

## No action taken

This file is the recipe only. PR #26, its branch, its base, and all PR metadata were left unchanged. No PR comment, push to the PR branch, merge, or release was performed.
