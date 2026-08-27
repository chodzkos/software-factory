# Software Factory Skills v0.8 review stage

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review. They are subordinate procedures used inside an already assigned task/profile.

## v0.8 review scope

The v0.7 pinned-upstream model remains active for installable skills. This review stage adds `repo-map` as the first pinned **multi-file upstream reference** so its executable helper can be independently reviewed before any runtime activation.

`repo-map` is intentionally:

- absent from `manifest.skills`,
- `installable=false`,
- `vetted=false`,
- `review_status=pending-helper-review`,
- granted to no profile.

The vendored reference pins the exact upstream repository, full immutable commit SHA, exact file allowlist, and SHA-256 for both `SKILL.md` and `scripts/repo_map.py`.

Testing the exact upstream helper found a real defect: when `.git` or `node_modules` is itself the current `os.walk` directory, its files are still mapped even though the skill description claims those directories are skipped. This defect is documented and pinned by tests; it is not treated as fixed. Runtime activation requires a later reviewed Factory fix/wrapper.

## Existing v0.7 runtime scope

Installable upstream content is never fetched at runtime. It must be committed under `skills/upstream/`, tied to an allowlisted repository and an exact 40-character upstream commit, carry an exact upstream path and SHA-256 digest, and be marked `vetted=true` in `manifest.yaml`.

Batch 1 installs `bug-diagnosis` directly as vetted upstream content for the coder profile. Raw upstream `tdd-workflow` and `ai-code-review` are retained byte-identical as non-installable audit references because their original procedures conflict with Factory workflow contracts. Runtime profiles receive Factory-owned adapters instead: `factory-tdd-workflow` and `factory-ai-code-review`.

## Layout

- `manifest.yaml` — machine-readable inventory, profile grants, vendored-upstream provenance, exact commit/digest pins, and non-installable upstream references.
- `profiles.yaml` — minimum profile→skill policy.
- `custom/` — factory-owned skills and Factory-safe adapters.
- `upstream/` — pinned upstream source material plus vetting/review records. Reference-only files may live here without being installer-visible.
- `tests/` — manifest/profile/routing and supply-chain/helper regression tests.
- `../hermes/install_factory_skills.sh` — fail-closed profile-aware installer for manifest-declared custom and vetted vendored skills.
- `../hermes/verify_factory_skills.sh` — repository and installed-state verification.

## Vendored upstream policy

- Runtime installation performs no network acquisition (`network_install=false`).
- Upstream repositories must be explicitly allowlisted.
- Upstream identity uses an immutable full commit SHA, never a moving branch/tag such as `main` or `latest`.
- Installable upstream directories must satisfy their reviewed source-shape contract.
- SHA-256 is verified before the first install write.
- Installed upstream symlinks, extra files, missing files, and digest drift fail closed.
- Upstream text or helpers that conflict with higher Factory authority or fail security review stay reference-only until a Factory-owned adapter/wrapper exists.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree when the task already has `workspace_kind=worktree` / `workspace_path`.
- SHA-sensitive claims use exact SHAs, never branch-name assumptions.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Upstream procedures cannot override the canonical standard, Kanban contract, profile/task contract, native same-card review/rework lifecycle, or exact-SHA merge gates.
- Installer never fetches moving upstream content and never blindly `rm -rf`s an unknown installed skill.
