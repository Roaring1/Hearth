# Hearth capture core and discovery adapter

## Summary
Adds capture core and read-only device-discovery adapter interface, fake backend, and recorded synthetic `pw-dump` fixture parsing. Discovery is testable without querying or mutating a live graph.

## Evidence
- 41 focused parser/adapter tests: **PASS**.
- Completeness-guard mutation: expected failure observed.
- Hosted Windows result recorded for integrated stack (45548952071 PASS on `8508374`); verify branch-specific hosted status before merge.
- Commit: `81b9c3f`.

## Risks / limitations
- Read-only adapter is not proof against every live graph/version.
- Fixtures are synthetic/recorded; tests avoid live calls.
- Check branch-specific CI at the exact commit.

## Suggested merge order
1. PR26 fixes (if needed).
2. Signal-quality core (confirm API assumptions).
3. Capture core.
4. Integrated branch last.

## Reviewer checklist
- Inspect protocol/adapter boundary and fake-backend tests.
- Confirm malformed/incomplete fixture parsing fails closed.
- Confirm unit tests do not invoke live PipeWire commands.
