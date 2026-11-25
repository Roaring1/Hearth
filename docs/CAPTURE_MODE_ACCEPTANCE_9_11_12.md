# Capture Mode acceptance roll-up - issues #9, #11, #12

Status snapshot: 2026-11-24, based on w2/integrated-20261124 plus W2d's isolated presentation-model addition.

This is an acceptance checklist, not a claim that Capture Mode is ready for live use. Hardware, live PipeWire routes, active capture, KDE Wayland portal behavior, and Vesktop receiver behavior have not been verified.

## Issue #9 - parent contract

| Acceptance | Status | Evidence / gap |
|---|---|---|
| Capture Mode is on-demand and dormant by default | PARTIAL / NOT LIVE-VERIFIED | Preflight window is one-shot. Production active session is not wired; no dormant-state live audit. |
| Explicit transactional Start and honest Stop/cleanup | CORE IMPLEMENTED; APP INTEGRATION MISSING | CaptureSessionManager transaction and rollback/retry fixtures exist. No production audio-route adapter or GTK Start/Stop. |
| Share Output is video-only, clean and fullscreen by default | PROTOTYPE / SYNTHETIC ONLY | Owned mpv viewer and command builder exist; no physical capture-card stream or actual Vesktop receiver verification. |
| Separate Capture Controls and Share Output windows | PARTIAL | Read-only preflight Controls exists; no production Start path/session injection. |
| Manual Vesktop window selection and manual share start | DOCUMENTED; NOT EMPIRICALLY VERIFIED | UI/help does not automate Go Live. Wayland/portal matrix deferred. |
| Share-audio selector Nobara/Xbox/Both and Mute, delivered exactly once | NOT IMPLEMENTED | Production route adapter, destination mapping and guarded overlay are unresolved. |
| No Discord microphone/webcam controls; prevent B1/B2/VM-CHAT/local monitor leakage | SAFETY CONTRACT DOCUMENTED; LIVE ROUTE NOT VERIFIED | No route mutation or live leakage test. |
| Optional local monitoring default-off and isolated from VM-SHARE | NOT IMPLEMENTED / NOT VERIFIED | No production audio-route integration. |
| Generic UVC device, stable identity, mode discovery | IMPLEMENTED IN FIXTURE/PROVIDER LAYER; HARDWARE UNKNOWN | Bounded read-only inventory and identity resolvers exist; no physical card inventory. |
| Stop leaves zero capture-specific resources | UNIT-TESTED CONTRACT; NOT LIVE AUDITED | Cleanup leases and retries are modeled; production adapters not wired. |
| Parent issue closure checklist (all children, card checklist, help reconciled, dormant audit) | NOT MET | Children #11/#12 remain incomplete; hardware, live routing, UI integration and dormant audit outstanding. |

## Issue #11 - capture core and transaction

| Acceptance | Status | Evidence / gap |
|---|---|---|
| Pure toolkit-free core and injected I/O seams | IMPLEMENTED | hearth.audio.capture_core and session/runtime contracts. |
| Explicit one-shot discovery; stable USB identity; no volatile node persistence | IMPLEMENTED / SYNTHETIC | V4L2 and PipeWire providers, parsing, stable USB matching and fail-closed ambiguity. Not run on host hardware/live graph. |
| Device/mode selection, Start-time revalidation | IMPLEMENTED AS CONTRACT | Resolver rechecks inventory and exact mode at explicit Start; not wired to production Controls. |
| Transactional startup order and reverse rollback | IMPLEMENTED / SYNTHETIC | Viewer, route lease, idle-inhibit and first-frame/route validation seams; failure injection/rollback tests. |
| Session-owned route lease with graph verification | FOUNDATION ONLY | Graph parser, command builder and process lease exist. Production source-to-Venmic destination and adapter ownership remain unresolved. |
| Owned video process, first-frame readiness, event-driven progress/stall | IMPLEMENTED / SYNTHETIC | mpv process/IPC tests. Actual card input is not opened. Timestamp stall and pixel health are advisory, not definitive signal diagnosis. |
| Unexpected viewer exit, typed failure and retryable cleanup | IMPLEMENTED / SYNTHETIC | Manager and fake viewer/process tests; production composition absent. |
| Idle/sleep inhibitor lease | IMPLEMENTED AS ADAPTER / NOT WIRED | Fake-FD tests only; no live logind call. |
| Local monitoring isolated from VM-SHARE | NOT IMPLEMENTED | Route integration not complete. |
| Crash recovery / stale session cleanup and hardware verification | PARTIAL / NOT ACCEPTED | Bounded stale-artifact sweep exists for private viewer IPC; full session route/process crash-recovery and physical-card matrix remain. |
| Capture Mode view-model/action logic | IMPLEMENTED BY W2d, PURE | hearth.capture_ui_model maps core snapshot to safe status/options and action availability. 11 presentation assertions; mutation check confirms removing module breaks tests. Not wired to GTK. |

## Issue #12 - active Controls and Share Output UI

| Acceptance | Status | Evidence / gap |
|---|---|---|
| Use Capture Card opens a separate Controls window | IMPLEMENTED | Launcher and single-instance routing exist. |
| Opening/Refresh performs bounded one-shot video inventory only | IMPLEMENTED / FIXTURE-VERIFIED | No streaming handle, session, route, viewer, timer or watcher. |
| Device labels and supported modes; no volatile path leakage | IMPLEMENTED | Fixture-backed read-only inventory UI. |
| Saved Vesktop audio selection clearly labelled as intent | IMPLEMENTED | UI states saved selection, not a live route. |
| Session snapshots optionally injected; safe lifecycle/error/stall copy | UI SUPPORT IMPLEMENTED; production source NOT WIRED | Optional snapshot source exists; app does not construct the session manager. |
| New W2d view-model maps core states/actions | IMPLEMENTED / TESTED | Pure model exists separately; UI has not consumed it yet. |
| Real Start/Stop, session audio selector/mute and route cleanup | NOT IMPLEMENTED | No inert controls are exposed, by design. |
| Clean Share Output integrated with active Controls | PROTOTYPE ONLY | mpv synthetic tests; no real capture card or Vesktop window sharing. |
| GTK performance/resource/dormant audit with active capture | NOT COMPLETE | No live integration or activity test. |

## Acceptance gates before parent #9 can close

- [ ] Resolve the production session-owned VM-SHARE route adapter and exact destination; test guarded overlay and single-path delivery.
- [ ] Wire the production runtime/session manager to GTK Controls and the owned Share Output viewer. Do not expose Start/Stop until the route lease, cleanup, inhibitor and first-frame paths are real.
- [ ] Implement the selector (Nobara / Xbox / Both) and Mute only against verified route ownership; local monitoring remains separate and default-off.
- [ ] Verify device/mode and audio association on the physical GUV302G; record actual negotiated USB speed, Linux modes, busy/exclusive-open behavior, input-loss behavior and supported audio endpoints. Keep analog headset jacks unplugged for V1.
- [ ] Run the explicitly approved KDE Wayland/Vesktop portal matrix and confirm the receiver sees only Share Output, not the desktop or Controls. This is a user-visible/interactive test and remains deferred.
- [ ] Verify failure injection at every startup phase and cleanup retry with real adapters where safe.
- [ ] Audit Stop and dormant state for zero capture-specific process, device FD, audio stream, session-owned link, watcher, timer, unit or autostart; compare overhead with the existing Hearth baseline.
- [ ] Reconcile README and in-app help with final behavior and ownership (B1/B2 vs VM-SHARE).
- [ ] Update all child issues and evidence; only then revisit parent #9 closure.

## Safety boundary

Current development is synthetic/offline. No live PipeWire/WirePlumber/Carla services, audio routes, buffer settings, GUI, Wayland/Vesktop portal, physical capture device, or ~/bin helper has been changed or exercised. No PR, merge, release, or GitHub issue comment was made.
