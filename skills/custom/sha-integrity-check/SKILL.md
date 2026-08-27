---
name: sha-integrity-check
description: Verify exact Git state for SHA-sensitive work and fail closed on mismatch or unknown required SHA.
---

# SHA Integrity Check

Use inside an already assigned Software Factory task. Kanban/profile/task contract remain authoritative.

## Record

- repository / remote
- branch
- working-tree state
- BASE_SHA
- CURRENT_SHA for the implementation HEAD currently being inspected
- REVIEWED_SHA when review/audit occurs
- VERIFIED_SHA when verification occurs
- PR_HEAD_SHA when a PR exists

Refresh remote state before relying on `origin/*`. Copy SHAs from Git/GitHub output; never infer them from branch names.

## Fail closed

- expected SHA != actual SHA → `SHA_MISMATCH`
- required SHA unknown → `SHA_UNVERIFIED`
- dirty/unknown workspace → preserve it; never reset/clean destructively
- merge candidate with `REVIEWED_SHA != PR_HEAD_SHA` → block
- merge candidate with `VERIFIED_SHA != PR_HEAD_SHA` → block

Do not silently upgrade a requested exact SHA to a newer branch tip. A review is valid only for the SHA actually reviewed.
