# Software Factory Skills v0.8

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review. They are subordinate procedures used inside an already assigned task/profile.

## v0.8 scope

v0.8 keeps the v0.7 pinned/vendored supply-chain model and adds the first **multi-file upstream reference** for security review: `repo-map`.

`repo-map` is intentionally **reference-only** in this stage. It is absent from `manifest.skills`, has `installable=false`, `vetted=false`, and `review_status=pending-helper-review`; therefore `--all` and every profile remain unable to install it.

The vendored reference is bound to upstream repository `mohitagw15856/pm-claude-skills` at exact commit `aa71bee8d20b7febdfd49f3aa96f26f316344628`. The manifest allowlists exactly two files and pins each one independently by SHA-256:

- `SKILL.md`
- `scripts/repo_map.py`

The helper is executed only by repository tests against temporary test directories during this PR. It is not activated for Hermes profiles until a separate independent helper/security review approves it and a later PR adds an explicit multi-file installation contract.

The v0.7 batch remains unchanged: `bug-diagnosis` is directly installable for coder, while raw `tdd-workflow` and `ai-code-review` remain byte-identical non-installable references exposed through Factory-owned adapters `factory-tdd-workflow` and `factory-ai-code-review`.

## Layout

- `manifest.yaml` — machine-readable inventory, profile grants, vendored-upstream provenance, exact commit/digest pins, and non-installable upstream references. JSON-compatible YAML keeps validation Python-stdlib-only.
- `profiles.yaml` — minimum profile→skill policy.
- `custom/` — factory-owned skills and Factory-safe adapters.
- `upstream/` — pinned upstream source material. Reference-only material may include an explicitly allowlisted multi-file tree when every file has a pinned digest.
- `upstream/VETTING.md` — accepted/deferred upstream decisions.
- `tests/` — manifest/profile/routing, supply-chain, and helper-behavior regression tests.
- `../hermes/install_factory_skills.sh` — fail-closed profile-aware installer for manifest-declared installable custom/vendored skills only.
- `../hermes/verify_factory_skills.sh` — repository and installed-state verification; v0.8 also runs reference/helper tests.

## Vendored upstream policy

- Runtime installation performs no network acquisition (`network_install=false`).
- Upstream repositories must be explicitly allowlisted.
- Upstream identity uses an immutable full commit SHA, never a moving branch/tag such as `main` or `latest`.
- Installable single-file upstream directories must be real non-symlink directories containing only regular `SKILL.md`.
- SHA-256 is verified before the first install write.
- For current `upstream-vendored` installable skills, the installer copies only validated `SKILL.md` bytes.
- Installed upstream `SKILL.md` symlinks, extra files, missing files, and digest drift fail closed.
- A multi-file upstream reference must list the exact relative file allowlist and a SHA-256 for every file; reference status does not grant installation authority.
- Upstream text/code that conflicts with higher Factory authority or has not completed review stays reference-only.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree when the task already has `workspace_kind=worktree` / `workspace_path`.
- SHA-sensitive claims use exact SHAs, never branch-name assumptions.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Upstream procedures cannot override the canonical standard, Kanban contract, profile/task contract, native same-card review/rework lifecycle, or exact-SHA merge gates.
- Executable upstream helpers require separate code/security review before profile activation.
- Installer never fetches moving upstream content and never blindly `rm -rf`s an unknown installed skill.
