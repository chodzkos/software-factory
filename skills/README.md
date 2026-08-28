# Software Factory Skills v0.9 activation infrastructure

This directory is the task-level skill layer for Software Factory.

Authority order:

1. `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`
2. `workflows/KANBAN_CONTRACT.md`
3. agent profile / task contract
4. skills in this directory

Skills do not orchestrate Kanban, create parallel review tasks, weaken runtime gates, or replace independent review.

## v0.9 scope

v0.9 adds reviewed **multi-file skill infrastructure** and preserves the Factory-owned `factory-repo-map` fork as a pinned activation candidate. Runtime activation is intentionally blocked after independent review found that Hermes 0.20.4 gives `repository-analyst` unrestricted local terminal/code execution, so dispatcher environment variables cannot serve as a tamper-proof workspace authority boundary.

Production state for `factory-repo-map`:

- `source=custom-multifile`,
- `installable=false`,
- `activation_status=blocked-on-runtime-isolation`,
- `profiles=[]`,
- absent from every profile grant,
- skipped by installer `--all`.

The raw upstream `repo-map` remains byte-identical, non-installable audit material under `skills/upstream/repo-map/`.

## Multi-file candidate tree

`factory-repo-map` retains an explicitly pinned candidate tree in `manifest.yaml`:

- `SKILL.md`
- `REVIEW.md`
- `scripts/repo_map.py`
- `scripts/run_repo_map.py`

The generic `custom-multifile` installer validates declared file paths and Git blob content pins before writes, copies only declared files, and rejects file drift/symlinks/missing/extra files. The installed-state verifier repeats the file/content checks. Adversarial tests exercise this machinery in an isolated temporary manifest fixture without enabling the production candidate.

## Why runtime activation is blocked

PR #17 activation review found two HIGH authority issues in the autonomous runtime model:

1. option-shaped target injection into argparse; this is now fixed by rejecting targets beginning with `-` and inserting `--` before the target,
2. unrestricted `repository-analyst` terminal/code execution can still override `HERMES_KANBAN_*` for a child process or invoke `repo_map.py --workspace ...` directly.

The second issue cannot be solved by skill text or an approval-oriented command allowlist. Runtime activation requires a separate mechanically isolated surface, such as a future deny-by-default command mode, dedicated Hermes tool/plugin, or OS-level sandbox that exposes only the assigned workspace.

Until that boundary exists, `run_repo_map.py` is defense-in-depth infrastructure only, not a security authority boundary.

## Existing runtime skills

- `bug-diagnosis` — vetted pinned upstream, coder optional.
- `factory-tdd-workflow` — Factory adapter, coder optional.
- `factory-ai-code-review` — Factory adapter, quick-reviewer/critic optional.

Raw upstream `tdd-workflow`, `ai-code-review`, and `repo-map` remain audit references and are not runtime skills. `factory-repo-map` is also not a runtime skill yet.

## Layout

- `manifest.yaml` — inventory, profile grants, source types, content pins, upstream references and disabled candidates.
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
- `custom-multifile` candidates require an explicit declared file set and per-file content pins.
- Source/target symlinks, missing/extra files and pin drift fail closed for selected installable skills.
- `installable=false` entries are excluded from `--all`; a profile referencing one fails closed.
- Full selection preflight happens before the first install write.

## Design rules

- Kanban owns orchestration and workspace assignment.
- A skill must never create a second worktree for an already assigned task.
- SHA-sensitive claims use exact SHAs.
- Evidence records observations, not intentions.
- Mandatory unknown evidence fails closed.
- Merge/release decisions remain release-manager policy and must match reviewed/verified PR HEAD.
- Upstream procedures cannot override the canonical standard, Kanban contract, profile/task contract, native same-card lifecycle, or exact-SHA gates.
