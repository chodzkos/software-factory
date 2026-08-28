# Factory repo-map activation infrastructure

Status: **FACTORY-OWNED FORK — RUNTIME ACTIVATION BLOCKED**

The raw upstream `repo-map` remains byte-identical audit material under `skills/upstream/repo-map/` and remains non-installable. PR #16 produced a reviewed Factory-owned fork. PR #17 originally attempted activation, but exact-SHA activation review found two HIGH workspace-authority bypasses in the autonomous runtime model.

## Why activation remains blocked

Hermes 0.20.4 gives `repository-analyst` unrestricted local terminal/code execution. In that environment:

- a worker can launch a child with modified `HERMES_KANBAN_WORKSPACE`,
- a worker can invoke `scripts/repo_map.py --workspace ...` directly,
- dispatcher environment therefore cannot be treated as a tamper-proof security boundary.

Hermes' current ordinary-session `command_allowlist` is approval-oriented rather than deny-by-default. Until Factory has a mechanically isolated repo-map tool surface (for example a future deny-by-default terminal mode, dedicated tool/plugin, or OS-level sandbox that exposes only the assigned workspace), no profile receives this skill.

Production manifest state must remain:

- `installable=false`,
- `activation_status=blocked-on-runtime-isolation`,
- `profiles=[]`,
- absent from all profile required/optional lists,
- skipped by installer `--all`.

## Candidate binder hardening

`run_repo_map.py` is retained as reviewed activation infrastructure, not as an authority boundary. It:

- requires `HERMES_KANBAN_TASK`,
- requires absolute `HERMES_KANBAN_WORKSPACE`,
- requires `HERMES_PROFILE=repository-analyst`,
- rejects empty and option-shaped targets,
- accepts at most one workspace-relative target,
- inserts `--` before forwarding the target to argparse,
- fixes Factory limits,
- disables bytecode writes before importing the mapper.

This closes the activation review's F1 target-option injection but does not by itself close F2 unrestricted-terminal bypass.

## Multi-file infrastructure contract

The manifest retains this candidate as `custom-multifile` with exact per-file Git-blob pins. Generic installer/verifier support is tested in an isolated fixture where a temporary manifest copy enables the candidate; production policy never enables it.

Installer/verifier infrastructure must:

- reject source or target symlinks,
- reject missing or extra declared files,
- verify each declared file pin before writes,
- copy only declared files into a temp directory,
- preserve nested relative paths,
- refuse overwrite of a differing installed skill,
- verify installed file pins,
- complete preflight before the first install write.

Extra empty-directory exact-tree detection is LOW hardening and remains visible for follow-up; no undeclared file content is copied.

## Mapper invariants retained from PR #16

- bounded `os.scandir` traversal,
- target-component hidden/generated and symlink refusal,
- workspace containment relative to the supplied root,
- source extension allowlist,
- secret/non-code/binary/invalid-UTF8 filtering,
- hard ceilings,
- `O_NOFOLLOW` leaf open when available + regular-file `fstat`,
- relative prefixed output and control sanitization,
- no discovered-code execution, shell, network, or repository mutation.

Do not grant or install this skill for autonomous runtime until a separate exact-SHA review proves the new runtime isolation boundary mechanically prevents workspace/env/direct-helper bypass.
