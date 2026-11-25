# Draft triage comment - Issue #12

**Suggested status: BLOCKED ON DEPENDENCIES / preflight UI exists; active session flow not ready.**

The current Capture Controls preflight is intentionally read-only and has no production session-manager injection or Start/Stop path. Keep the UI truthful and avoid inert controls until #10's route contract and #11's production session adapters are ready. Remaining acceptance includes real safe route state, Start/Stop/cleanup status, viewer lifecycle, and manual Wayland/Vesktop/physical-card verification. The Share Output window must remain video-only and the Vesktop share action manual. No live session was attempted.
