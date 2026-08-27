# Software Factory Skills v0.6

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review. They are procedures used inside an already assigned task/profile.

## v0.6 scope

This first integrated slice contains only custom factory skills plus manifest/profile policy and deterministic validation. Upstream skills from `mohitagw15856/pm-claude-skills` are intentionally not installed yet; they will be added only through pinned commit + path + digest records after separate vetting.

## Layout

- `manifest.yaml` — machine-readable skill inventory. JSON-compatible YAML so validation needs only Python stdlib.
- `profiles.yaml` — minimum profile→skill policy.
- `custom/` — factory-owned skills.
- `tests/` — manifest/profile/routing regression tests.
- `../hermes/install_factory_skills.sh` — fail-closed installer for manifest-declared custom skills.
- `../hermes/verify_factory_skills.sh` — repository and installed-state verification.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree when the task already has `workspace_kind=worktree` / `workspace_path`.
- SHA-sensitive claims use exact SHAs, never branch-name assumptions.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Installer never fetches moving upstream `main` and never blindly `rm -rf`s an unknown installed skill.
