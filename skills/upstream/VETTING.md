# Upstream skill vetting — batch 1

Upstream repository: `mohitagw15856/pm-claude-skills`

Pinned commit: `aa71bee8d20b7febdfd49f3aa96f26f316344628`

Policy: vendored-only. Runtime installation must not fetch remote content. Installable upstream content must be a regular, non-symlink directory containing exactly one regular `SKILL.md`; the committed bytes must match the manifest SHA-256 before any write to `HERMES_SKILLS_DIR`.

## Accepted installable upstream

### `bug-diagnosis`

- upstream path: `skills/bug-diagnosis/SKILL.md`
- assigned profile: `coder` (optional)
- purpose: reproduce → isolate → hypothesize → verify before changing code
- executable helper: none
- remote/network acquisition: none
- compatibility: works inside the existing Kanban-assigned workspace; it creates no cards, branches or worktrees
- caveat: `git bisect` / temporary comment-outs may mutate the assigned worktree and remain subordinate to `workspace-integrity`, the task contract and exact-SHA handoff

Decision: **VETTED AND INSTALLABLE**.

## Vetted references requiring Factory adapters

The following upstream files remain byte-identical vendored references for provenance/audit, but are **not installer-visible skills**. They are absent from `manifest.skills`; Factory profiles receive only custom adapters.

### `tdd-workflow` → `factory-tdd-workflow`

- upstream path: `skills/tdd-workflow/SKILL.md`
- upstream SHA-256: `55ddcbf38feff891b811e5b7027c0b5efebc65831bc5f7d599c62cbd19561e1a`
- upstream text is retained byte-identical under `skills/upstream/tdd-workflow/SKILL.md`
- conflict found by adversarial review: upstream says to commit at each green cycle
- Factory conflict: commit-per-cycle can move HEAD, fragment a logical task, and invalidate exact-SHA verification/review
- runtime grant: upstream `tdd-workflow` is **not installable**
- adapter grant: `factory-tdd-workflow` is optional for `coder`
- adapter override: stay in the Kanban-assigned worktree; no branch/worktree creation; no commit-per-green; commit/push only per task contract/coder SOUL; preserve one logical task/PR and exact-SHA handoff

Decision: **VETTED AS REFERENCE; FACTORY ADAPTER REQUIRED**.

### `ai-code-review` → `factory-ai-code-review`

- upstream path: `skills/ai-code-review/SKILL.md`
- upstream SHA-256: `2bb60f6f1ef619a6b48390b320cbf30a85fb233686d2a64c8e4b90e8d521a7ba`
- upstream text is retained byte-identical under `skills/upstream/ai-code-review/SKILL.md`
- conflict found by adversarial review: competing verdict vocabulary and Severity-column format do not satisfy the native review parser contract
- observed risk: a canonical `DECISION: APPROVE` could coexist with an upstream table containing HIGH without the parser recognizing that column as blocking; skill-only verdicts can also stall as `REVIEW_PENDING`
- runtime grant: upstream `ai-code-review` is **not installable**
- adapter grant: `factory-ai-code-review` is optional for `quick-reviewer` and `critic`
- adapter override: canonical `severity:` fields; exactly one `DECISION: APPROVE` or `DECISION: CHANGES_REQUIRED`; required fixes map to CHANGES_REQUIRED; native same-card `kanban_request_changes`; no merge-authority escalation

Decision: **VETTED AS REFERENCE; FACTORY ADAPTER REQUIRED**.

## Supply-chain hardening from adversarial review

The first review found that hashing only `SKILL.md` while copying an unrestricted directory allowed a symlink/extra-file bypass. Remediation in this batch now requires for every installable `upstream-vendored` source:

- exact repository allowlist match
- full 40-character commit SHA
- exact upstream and local paths
- `vetted=true`
- regular non-symlink source directory
- regular non-symlink `SKILL.md`
- directory contents exactly `SKILL.md` and nothing else
- SHA-256 match before writes
- install only the validated `SKILL.md` bytes, not an arbitrary source tree
- installed target directory and installed `SKILL.md` must not be symlinks
- installed upstream `SKILL.md` digest is rechecked by the verifier

Adversarial tests cover digest tamper, repository mismatch, source-directory symlink, extra source file and installed `SKILL.md` symlink.

## Deferred

### `repo-map`

Deferred to a separate PR because it includes executable helper `scripts/repo_map.py`. That helper needs its own code/security review, path-boundary tests and multi-file digest policy before activation.

### `verification-before-completion`

Deferred because the current Factory already has `evidence-ledger`, exact-SHA verification and merge-gate semantics. Import only after checking for duplication/conflicting definitions of DONE vs VERIFIED.

## Batch invariants

- no skill is downloaded during installation
- pinned upstream identity is exact repository + immutable 40-character commit + path + SHA-256
- only conflict-free vetted upstream material is directly installable
- conflicting upstream text is retained byte-identical for audit but exposed only through Factory-safe adapters
- adapters are subordinate to `SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract`
- no batch skill is granted to `orchestrator`, `runtime-controller`, `task-decomposer`, `release-manager` or auditors
