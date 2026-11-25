# Hearth Capture Mode core and discovery foundation

## Summary
Toolkit-free capture device/audio session models, bounded read-only V4L2 and PipeWire inventory adapters, stable USB identity matching, injected backend lifecycle, transactional session/viewer foundation, and pure Capture Mode presentation model.

## Evidence
- Capture-core branch 81b9c3f; hosted Windows CI 45544115193 PASS.
- Synthetic/fake-backend tests cover discovery, identity, session lifecycle and cleanup failure retention.
- W2d adds hearth.capture_ui_model mapping core snapshots to safe status/device choices and refresh/select/start/stop availability; 11 new assertions.
- Mutation check removing the view-model module failed at import as expected; restored tests passed.
- Full project gate and hosted CI evidence are recorded on the integrated candidate.

## Risks / limitations
- No production route adapter, GTK Start/Stop injection, physical capture-card validation, or live graph mutation.
- The session core is not an operational capture path.

## Reviewer checklist
- Confirm one-shot discovery, stable identity and fail-closed unknown/ambiguous states.
- Review rollback/cleanup ownership and retained retry handles.
- Ensure no live audio services, routes, devices or UI were touched.
