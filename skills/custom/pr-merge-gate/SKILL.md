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
- explicit canonical Kanban `BOARD_SLUG` and exact same-card `TASK_ID`
- successful `kanban_runtime_cli.sh verify-approval --board <BOARD_SLUG> --task-id <TASK_ID>` on the current bytes

## Block when

- any required SHA is unknown
- `REVIEWED_SHA != PR_HEAD_SHA`
- `VERIFIED_SHA != PR_HEAD_SHA`
- required CI is failing/cancelled/skipped/unknown
- required reviewer/audit is missing or decision unparsable
- credible HIGH/CRITICAL is unresolved
- required evidence is missing
- downstream approval revalidation is absent, fails, observes a live mutation lease, or reports board/seal/HEAD/content drift
- human approval is required by policy and absent

Do not reinterpret `DONE` as `VERIFIED`. Do not merge a new commit under an approval for an older SHA. Never select the board through ambient `kanban/current`; pass the exact board explicitly. Return `MERGE_GATE_OK` only for the exact current PR HEAD and a passing downstream approval revalidation; otherwise `MERGE_GATE_BLOCKED` with explicit blockers.
