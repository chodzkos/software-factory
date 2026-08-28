---
name: factory-repo-map
description: "Factory-owned secure repository mapper for repository-analyst. Runtime invocation is bound to the current Hermes Kanban worker workspace; raw upstream repo-map remains audit-only."
---

# Factory Repo Map

Factory-owned secure repository index derived from the reviewed upstream `repo-map` concept.

## Authority

`SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract -> this skill`

This skill never chooses or expands its own workspace authority.

## Runtime grant

**INSTALLABLE — `repository-analyst` OPTIONAL ONLY.**

Do not grant this skill to coder, reviewers, auditors, orchestrator, runtime-controller, task-decomposer, release-manager, docs, or routing-sink without a separate reviewed policy change.

The pinned raw upstream `skills/upstream/repo-map/` remains non-installable audit material and must never be invoked for Factory runtime use.

## Required invocation

Use only the installed binder:

```bash
python3 scripts/run_repo_map.py .
python3 scripts/run_repo_map.py src
```

Do **not** invoke `scripts/repo_map.py --workspace ...` directly during autonomous Factory work.

`run_repo_map.py` obtains authority only from Hermes dispatcher environment:

- `HERMES_KANBAN_TASK` must exist,
- `HERMES_KANBAN_WORKSPACE` must exist and be absolute,
- `HERMES_PROFILE` must equal `repository-analyst`.

The binder supplies fixed Factory limits; the model cannot raise them through CLI arguments.

## Mapper security contract

The mapper:

- stays inside the dispatcher-bound workspace,
- rejects absolute/parent target traversal and hidden/generated target components,
- rejects symlink files/directories and symlink target components,
- prunes hidden/generated/vendor directories with bounded `os.scandir`,
- scans only allowlisted source-code extensions,
- skips secret-like filenames, NUL/binary-like content, and invalid UTF-8,
- enforces directory-count, per-directory-entry, file-count, per-file-byte and total-byte limits,
- enforces hard upper ceilings internally,
- opens accepted files with `O_NOFOLLOW` when available and reads via held fd,
- avoids shell execution, networking and repository mutation,
- prints workspace-relative paths only with `F ` row prefix,
- sanitizes control characters in emitted filenames,
- remains deterministic for unchanged tree and options.

## Bound runtime limits

The binder always supplies:

- `--max-files 500`
- `--max-dirs 2000`
- `--max-dir-entries 4096`
- `--max-file-bytes 1048576`
- `--max-total-bytes 8388608`
- `--max-symbols 12`

## Scope

This is an index, not a code graph and not a secret-redaction tool. It reports workspace-relative file paths, logical line counts and regex-derived top-level symbols. It never executes repository code.
