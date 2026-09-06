---
name: pr-merge-gate
description: Read-only evidence checklist for a release decision; never a mechanical merge enforcement layer.
---

# PR Merge Gate

Use only as a read-only decision procedure for `release-manager`. This skill is not a mechanical merge enforcement layer and grants no merge, push, PR-write, ready-for-review, publication, shell, Git, GitHub API, or credential authority.

Canonical model work starts only through `hermes/release_manager_start.py`, after its exact actual-surface verifier passes. The isolated home is outside `~/.hermes/profiles`, so it is not a generic Kanban assignee; the launcher removes every `HERMES_KANBAN_*` value. The profile receives only the three bounded repository-read tools and no Kanban tool.

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

Do not reinterpret `DONE` as `VERIFIED`. Never select the board through ambient `kanban/current`; pass the exact board explicitly. Return the decision `RELEASE_APPROVED` only when the evidence checklist is satisfied; otherwise return `RELEASE_BLOCKED` with explicit blockers.

Immediately before the final merge, the repository owner—not this profile—runs `kanban_runtime_cli.sh verify-approval --board <BOARD_SLUG> --task-id <TASK_ID>` on the exact current workspace bytes and exact current PR HEAD, then performs the GitHub merge manually. That final merge is a trusted human action outside the Hermes model's mechanical capability boundary. No agent decision alone constitutes merge authority. This policy does not claim to constrain a malicious host owner/root or an unrelated process holding the user's GitHub credentials.
