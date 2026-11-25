# Hearth issue-triage comments (DRAFT ONLY - NOT POSTED)

Prepared 2026-11-25 from current issue metadata and lane findings. No GitHub issue write/comment action was used. Re-check statuses and linked PRs before posting.

## Issue #4 - Resume PC-side Hearth verification after NobaraSupervisor reconnects

> The prior PC-side verification checklist remains historical, not a current host-state claim. The desktop/live-audio verification remains deferred under the focus/session safety boundary. Code/isolated GTK tests are not a substitute for explicit safe-session verification. Keep open until the user authorizes that window and the checklist is freshly completed; do not infer current services or routing from the November 10 notes.

Triage: Keep open, stale-but-intentionally-blocked; no live verification performed.

## Issue #9 - Capture Mode parent

> The parent still appears to be a useful umbrella for the Capture Mode workflow. The novice-facing overview is tracked separately (#16); device/session core (#11) and controls UI (#12) remain distinct implementation/acceptance layers. Keep the parent focused on end-to-end user acceptance, and avoid duplicating the detailed component checklists. The real KDE Wayland/Vesktop matrix remains manual and must wait for an explicitly safe desktop session.

Triage: Keep open; review child/parent linkage and acceptance criteria rather than splitting further now.

## Issue #11 - On-demand capture-device/audio-session core

> The existing checkpoint describes PR #18 stacked on #17, but this is not re-verified in this triage pass. Please refresh the stack/head/CI status before calling the core complete. Preserve bounded discovery, explicit incomplete inventory, no-card behavior, and no live device access unless an approved test plan is run.

Triage: Keep open pending current PR/CI verification; comment intentionally avoids treating historical checkpoint as current.

## Issue #12 - Capture Mode UI

> The prior checkpoint describes the separate Controls window and its read-only discovery semantics. Please confirm the current implementation head and CI before closure. Keep the UI and the overview (#16) separate; controls must not gain unrequested route mutation or start/stop behavior through this issue.

Triage: Keep open pending verified PR/head and acceptance evidence.

## Issue #13 - KDE Wayland + Vesktop capture behavior spike

> The upstream source-review portion appears complete, but the actual portal/Vesktop/monitor-interaction matrix is still not live-tested. Preserve the distinction between source review and empirical validation. Do not launch a second desktop/session or change monitor focus to perform the matrix; it needs an explicitly safe interactive window.

Triage: Keep open or mark research portion complete only if the tracker supports partial completion; manual test remains blocked.

## Issue #14 - Deferred/post-V1 Capture Mode parking lot

> This remains a parking lot and none of its optional ideas are prerequisites for the focused Capture Mode flow. Please keep proposals separate until there is a concrete user need and a ready-to-build scope. No implementation is recommended from this triage pass.

Triage: Leave open as a parking lot; no action.

## Issue #24 - Deferred MIDI Controls and read-only Routing Grid

> PR #26 is now the active implementation candidate for MIDI Controls and the read-only Routing Grid. Linux jobs pass. Windows UCRT64 tests/installed-wheel smoke failed on the PR head, but the platform-marker correction is now validated on W2's pushed branch: Windows UCRT64 tests and installed-wheel smoke passed in run 45527113279 (head d2f6340). The fix has not been applied to PR #26 head, so its required Windows check remains red. Keep this issue open until PR review/CI and integration are complete. This work has not touched live audio or hardware.

Triage: Keep open; track PR #26, require green Windows job and integration/acceptance review.


## Posting policy

These are proposals only. They must not be posted automatically. They deliberately do not ask for live PC testing, merge, close, or scope expansion.
