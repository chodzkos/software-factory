# Software Factory Skills v0.7

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review. They are subordinate procedures used inside an already assigned task/profile.

## v0.7 scope

This release keeps the factory-owned skill foundation and adds the first pinned upstream batch using a vendored-only supply-chain model.

Installable upstream content is never fetched at runtime. It must be committed under `skills/upstream/`, tied to an allowlisted repository and an exact 40-character upstream commit, carry an exact upstream path and SHA-256 digest, and be marked `vetted=true` in `manifest.yaml`.

Batch 1 installs `bug-diagnosis` directly as vetted upstream content for the coder profile. Raw upstream `tdd-workflow` and `ai-code-review` are retained byte-identical as non-installable audit references because their original procedures conflict with Factory workflow contracts. Runtime profiles receive Factory-owned adapters instead: `factory-tdd-workflow` and `factory-ai-code-review`.

## Layout

- `manifest.yaml` — machine-readable inventory, profile grants, vendored-upstream provenance, exact commit/digest pins, and non-installable upstream references. JSON-compatible YAML keeps validation Python-stdlib-only.
- `profiles.yaml` — minimum profile→skill policy.
- `custom/` — factory-owned skills and Factory-safe adapters.
- `upstream/` — pinned upstream source material plus `VETTING.md`. Reference-only files may live here without being installer-visible.
- `tests/` — manifest/profile/routing and supply-chain regression tests.
- `../hermes/install_factory_skills.sh` — fail-closed profile-aware installer for manifest-declared custom and vetted vendored skills.
- `../hermes/verify_factory_skills.sh` — repository and installed-state verification, including vendored digest checks.

## Vendored upstream policy

- Runtime installation performs no network acquisition (`network_install=false`).
- Upstream repositories must be explicitly allowlisted.
- Upstream identity uses an immutable full commit SHA, never a moving branch/tag such as `main` or `latest`.
- Installable upstream directories must be real non-symlink directories containing only regular `SKILL.md`.
- SHA-256 is verified before the first install write.
- For `upstream-vendored`, the installer copies only the validated `SKILL.md` bytes.
- Installed upstream `SKILL.md` symlinks, extra files, missing files, and digest drift fail closed.
- Upstream text that conflicts with higher Factory authority stays reference-only until a Factory-owned adapter exists.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree when the task already has `workspace_kind=worktree` / `workspace_path`.
- SHA-sensitive claims use exact SHAs, never branch-name assumptions.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Upstream procedures cannot override the canonical standard, Kanban contract, profile/task contract, native same-card review/rework lifecycle, or exact-SHA merge gates.
- Installer never fetches moving upstream content and never blindly `rm -rf`s an unknown installed skill.
