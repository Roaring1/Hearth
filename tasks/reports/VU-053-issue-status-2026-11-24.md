# Hearth issue status audit - refreshed 2026-11-24

Live GitHub state was checked with gh issue list --state all and gh pr view #26 on 2026-11-24. This is an offline audit only; no issue comments, labels, state changes, PR edits, or merges were made.

| Issue | Live state | Current assessment | Evidence / remaining scope |
|---|---|---|---|
| #4 - PC-side verification | OPEN | Blocked, not done | No fresh interactive PC-side audio/session verification was performed. Historical evidence is not current host verification. |
| #9 - Capture Mode parent | OPEN | Active umbrella | End-to-end Capture Mode acceptance remains incomplete. Keep UI, session core, route ownership, and compositor acceptance distinct. |
| #10 - user-managed VM-SHARE routing | CLOSED | Done/deferred scope resolved | GitHub confirms closed. Do not list as an open dependency. |
| #11 - on-demand capture-device/audio-session core | OPEN | Partial implementation | Pure coordinator is integrated into the candidate branch; production capture/audio route adapter, resource lease, rollback, and real session integration remain. No live route mutation. |
| #12 - Capture Mode UI | OPEN | Partial implementation | Read-only Controls/preflight and optional injected snapshot consumer exist. Production manager injection, route-backed Start/Stop, viewer integration, and acceptance remain. No inert controls added. |
| #13 - KDE Wayland/Vesktop spike | OPEN | Source research documented; empirical matrix blocked | New docs define the safe interactive matrix. Installed Vesktop/portal behavior, compositor placement, remote capture, minimize/occlusion, and idle-inhibit remain unverified pending an explicitly approved safe window. |
| #14 - post-V1 parking lot | OPEN | Intentionally deferred | Leave parked; no concrete prerequisite or current action. |
| #16 - novice-facing overview | CLOSED | Done | GitHub confirms closed. Retain existing implementation; no duplicate overview work. |
| #24 - MIDI Controls/Routing Grid | OPEN | Active/deferred | PR #26 remains open and its head is still 32e203d; PR metadata reports mergeStateStatus UNSTABLE. The W2 PR26 fix branch is separate and has not been integrated into PR #26. |

## Current branch evidence

- PR #26 fix branch: w2/pr26-fixes-20261124 at 7ca9d83; hosted Windows run 45534707201 succeeded. The open PR #26 still points at 32e203d and remains UNSTABLE. No PR update or merge performed.
- Capture core branch: w2/capture-core-20261124 at 81b9c3f; hosted Windows run 45544115193 succeeded.
- Signal-quality branch: w2/signal-quality-core-20261124 at ed095a8; hosted Windows run 45539343456 failed because direct stable-tone detection accepted bool sample_rate. W2c fixed this at 72f6e29 on its own branch. The hosted rerun on the later W2c head was still in progress at audit time; see current Actions status before treating that fix as hosted-green.
- Integrated candidate: w2/integrated-20261124. It combines the signal core, capture core, offline WAV report CLI, capture audio discovery adapter, docs, and changelog. Canonical scripts/check.sh: 932 passed, 18 skipped, 27 warnings; legacy compile, desktop entry, AppStream and shell checks passed. Ruff check and format were separately run from the pinned cache and passed (165 files). The canonical gate skipped mypy because mypy is not installed in this environment.
- Offline analyzer tests use synthetic WAV only. The pw-dump adapter tests use a recorded synthetic JSON fixture and fake backend. No live PipeWire or capture device was queried.

## Remaining owner / acceptance decisions

1. Keep PR #26 unmerged until its actual PR head is updated/reviewed and all final-head checks are green; the separate 7ca9d83 branch success is not proof that PR #26 itself is green.
2. Decide when to provide a safe interactive window for issue #13 and #4. Until then, no live Vesktop sharing, monitor switching, display lock/DPMS, or live audio-route tests.
3. Review the offline issue-triage comment draft before any comments are posted.
4. Do not wire the measurement core into the live watchdog or authorize automatic restarts/routes/buffer changes without a separate explicit owner decision.

No live services, audio routes, GUI sessions, home-bin files, GitHub issues, PRs, or main branch were modified.
