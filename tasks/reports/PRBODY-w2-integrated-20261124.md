# Hearth integrated audio-quality and capture tooling

## Summary
Integrates the signal-quality core, capture core, read-only WAV/PCM JSON analyzer, capture-device discovery adapter (including recorded `pw-dump` parsing), and associated documentation/CHANGELOG updates. Repository candidate only; no host watchdog installation or live capture was performed.

## Evidence
- Canonical gate: **932 passed, 18 skipped, 27 warnings**.
- Ruff check and format: PASS across 165 files.
- Hosted Windows run: **45548952071 PASS**.
- `mypy`: not run (not installed).
- Synthetic fixtures only for capture discovery and audio-analysis tests; no live PipeWire queries in test runs.

## Risks / limitations
- Signal-quality metrics are diagnostic/measurement-only; no automatic repair action is introduced.
- True-peak conformance still needs reference-vector validation.
- GTK/display and optional media tests remain skipped in headless gates.
- Revalidate after any upstream merge/rebase.

## Suggested merge order
1. `w2/pr26-fixes-20261124` (if fixes are a prerequisite)
2. `w2/signal-quality-core-20261124`
3. `w2/capture-core-20261124`
4. `w2/integrated-20261124` last, after verifying integration base and resolving overlap

## Reviewer checklist
- Confirm dependency order against actual target history before merging.
- Inspect skipped GUI/media tests and mypy gap.
- Keep live installation and service restart outside this PR.
