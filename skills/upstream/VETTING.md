# Upstream skill vetting — batch 1

Upstream repository: `mohitagw15856/pm-claude-skills`

Pinned commit: `aa71bee8d20b7febdfd49f3aa96f26f316344628`

Policy: vendored-only. Runtime installation must not fetch remote content. The committed vendored `SKILL.md` must match the SHA-256 recorded in `skills/manifest.yaml`.

## Accepted

### `bug-diagnosis`

- upstream path: `skills/bug-diagnosis/SKILL.md`
- assigned profile: `coder` (optional)
- purpose: reproduce → isolate → hypothesize → verify before changing code
- executable helper: none
- remote/network action required by the skill: none
- destructive workflow authority: none
- compatibility note: subordinate to the existing task/Kanban contract; its regression-test guidance complements `evidence-ledger` and does not create cards, branches or worktrees

Decision: **VETTED**.

### `tdd-workflow`

- upstream path: `skills/tdd-workflow/SKILL.md`
- assigned profile: `coder` (optional)
- purpose: small red → green → refactor cycles
- executable helper: none
- remote/network action required by the skill: none
- destructive workflow authority: none
- compatibility note: the upstream text suggests committing at each green; Software Factory repository/branch/PR rules remain authoritative, so this is procedural advice and cannot override the canonical standard or task contract

Decision: **VETTED**.

### `ai-code-review`

- upstream path: `skills/ai-code-review/SKILL.md`
- assigned profiles: `quick-reviewer`, `critic` (optional)
- purpose: AI-specific review failure modes and verification prompts
- executable helper: none
- remote/network action required by the skill: none; checking dependency documentation may use tools already authorized for the active reviewer
- destructive workflow authority: none
- compatibility note: its textual verdict format does not replace native same-card `kanban_complete` / `kanban_request_changes`; reviewer SOUL and Kanban lifecycle remain authoritative

Decision: **VETTED**.

## Deferred

### `repo-map`

Deferred to a separate PR because it includes executable helper `scripts/repo_map.py`. That helper needs its own code/security review, path-boundary tests and multi-file digest policy before activation.

### `verification-before-completion`

Deferred because the current Factory already has `evidence-ledger`, exact-SHA verification and merge-gate semantics. Import only after checking for duplication/conflicting definitions of DONE vs VERIFIED.

## Batch invariants

- no skill is downloaded during installation
- every upstream commit is a full immutable 40-character SHA
- every vendored skill is SHA-256 pinned
- `vetted=true` is mandatory
- upstream skills remain optional procedures under profile/task/Kanban authority
- no upstream skill is granted to `orchestrator`, `runtime-controller`, `task-decomposer` or `release-manager` in this batch
