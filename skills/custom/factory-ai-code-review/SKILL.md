---
name: factory-ai-code-review
description: "Factory-safe adapter for the vendored upstream ai-code-review checklist. Keeps AI-specific review heuristics while enforcing canonical severities, native same-card request_changes, and exactly one parseable DECISION line."
---

# Factory AI Code Review Adapter

Use the AI-specific failure-mode checklist from the pinned upstream `ai-code-review` skill only as a subordinate review checklist. Factory review semantics are canonical and override upstream verdict wording.

Authority:

`SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract -> skill`.

## Required Factory review contract

- Reviewer must be independent from the implementer where required by the task contract.
- Review the exact PR HEAD SHA and record it as `REVIEWED_SHA`.
- Findings must use canonical fields, including `severity: LOW|MEDIUM|HIGH|CRITICAL`.
- A credible MEDIUM/HIGH/CRITICAL finding requires changes; do not approve with required fixes.
- Do not use upstream verdict phrases as machine decisions.
- Final output must contain exactly one of:
  - `DECISION: APPROVE`
  - `DECISION: CHANGES_REQUIRED`
- `approve with required fixes` maps to `DECISION: CHANGES_REQUIRED`.
- `request changes` maps to `DECISION: CHANGES_REQUIRED`.
- When changes are required, use the native same-card `kanban_request_changes` flow; do not create a replacement review/task card.
- `DONE` is not `VERIFIED`; approval does not grant merge authority.
- Release-manager / merge gate remains authoritative for merge.

## AI-specific checklist

Check, with evidence where applicable:

1. plausible-but-wrong logic;
2. hallucinated or version-mismatched APIs;
3. tests that would stay green under broken behavior;
4. reinvention or convention drift;
5. speculative over-engineering;
6. dead scaffolding;
7. silent security shortcuts.

## Output shape

For each finding use:

`severity: <LEVEL>`
`location: <file:line or scope>`
`evidence: <what proves it>`
`impact: <why it matters>`
`proposed fix: <required remediation>`

End with exactly one canonical `DECISION:` line. Never emit a second competing verdict marker.
