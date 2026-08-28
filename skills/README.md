# Software Factory Skills v0.9

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review.

## v0.9 scope

v0.9 activates the reviewed Factory-owned `factory-repo-map` fork for **`repository-analyst` only**. The raw upstream `repo-map` remains byte-identical, non-installable audit material under `skills/upstream/repo-map/`.

`factory-repo-map` is a `custom-multifile` skill. Its runtime tree is explicitly pinned in `manifest.yaml` and contains:

- `SKILL.md`
- `REVIEW.md`
- `scripts/repo_map.py`
- `scripts/run_repo_map.py`

The installer validates the exact tree and each Git blob content pin before writes, copies only declared files, rejects symlinks/extra/missing files, and the installed-state verifier repeats the exact-tree/content checks.

## Authoritative Kanban binding

Autonomous workers must invoke only `scripts/run_repo_map.py`. The binder does not accept a workspace argument. It uses dispatcher-injected worker environment:

- `HERMES_KANBAN_TASK`
- `HERMES_KANBAN_WORKSPACE`
- `HERMES_PROFILE`

The binder requires `HERMES_PROFILE=repository-analyst`, requires an absolute dispatcher workspace, and passes fixed Factory safety limits to the mapper. A missing dispatcher binding fails closed. The model can provide only an optional workspace-relative target such as `.` or `src`.

The raw `scripts/repo_map.py --workspace ...` interface exists as the reviewed low-level implementation and test surface; the skill contract forbids direct autonomous invocation.

## Existing runtime skills

- `bug-diagnosis` — vetted pinned upstream, coder optional.
- `factory-tdd-workflow` — Factory adapter, coder optional.
- `factory-ai-code-review` — Factory adapter, quick-reviewer/critic optional.
- `factory-repo-map` — Factory-owned secure multi-file fork, repository-analyst optional.

Raw upstream `tdd-workflow`, `ai-code-review`, and `repo-map` remain audit references and are not runtime skills.

## Layout

- `manifest.yaml` — inventory, profile grants, source types, content pins, upstream references.
- `profiles.yaml` — minimum profile→skill policy.
- `custom/` — Factory-owned skills/adapters/forks.
- `upstream/` — pinned upstream source material and vetting/review records.
- `tests/` — policy, supply-chain and helper security regression tests.
- `../hermes/install_factory_skills.sh` — fail-closed profile-aware installer.
- `../hermes/verify_factory_skills.sh` — repository and installed-state verification.

## Supply-chain rules

- Runtime installation performs no network acquisition.
- Unknown/moving upstream content is never fetched by the installer.
- Single-file custom/upstream skills retain their one-`SKILL.md` source contract.
- `custom-multifile` requires an exact declared file tree and per-file content pins.
- Source/target symlinks, extra files, missing files, pin drift and differing pre-existing installs fail closed.
- Full selection preflight happens before the first install write.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree for an already assigned task.
- SHA-sensitive claims use exact SHAs.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Upstream procedures cannot override the canonical standard, Kanban contract, profile/task contract, native same-card lifecycle, or exact-SHA gates.
