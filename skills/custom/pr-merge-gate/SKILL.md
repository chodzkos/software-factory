---
name: pr-merge-gate
description: Release-manager procedure for deciding whether the current PR HEAD is eligible to merge using exact-SHA, review, verification, CI and evidence gates.
---

# PR Merge Gate

Use primarily by `release-manager`. This skill explains the procedure; deterministic validators/policy remain authoritative.

## Required state

- current `PR_HEAD_SHA`
- `REVIEWED_SHA`
- `VERIFIED_SHA`
- required CI checks and their status for PR HEAD
- required independent reviews/audits
- unresolved findings and severity
- task `REQUIRED_EVIDENCE`

## Block when

- any required SHA is unknown
- `REVIEWED_SHA != PR_HEAD_SHA`
- `VERIFIED_SHA != PR_HEAD_SHA`
- required CI is failing/cancelled/skipped/unknown
- required reviewer/audit is missing or decision unparsable
- credible HIGH/CRITICAL is unresolved
- required evidence is missing
- human approval is required by policy and absent

Do not reinterpret `DONE` as `VERIFIED`. Do not merge a new commit under an approval for an older SHA. Return `MERGE_GATE_OK` only for the exact current PR HEAD; otherwise `MERGE_GATE_BLOCKED` with explicit blockers.
