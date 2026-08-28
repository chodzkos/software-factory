---
name: factory-repo-map
description: "Factory-owned secure repo mapper derived from the reviewed upstream repo-map concept. Review-only in this PR: not in manifest.skills and not granted to any profile."
---

# Factory Repo Map

This is a Factory-owned implementation of the useful navigation idea from the pinned upstream `repo-map`, rewritten to satisfy Software Factory security and workspace contracts.

## Authority

`SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract -> this skill`

This skill never chooses or expands its own workspace authority.

## Activation state

**REVIEW ONLY — NOT INSTALLABLE — NO PROFILE GRANT.**

The implementation must pass independent exact-SHA review before any manifest/profile/installer activation PR. Do not invoke the pinned raw upstream `repo-map` helper for Factory runtime use.

## Required invocation contract

The caller must provide the authoritative Kanban-assigned workspace path explicitly:

```bash
python3 scripts/repo_map.py --workspace "$WORKSPACE_PATH" .
```

The target argument is workspace-relative. Absolute targets, parent traversal, hidden/generated target components, symlink components, and paths resolving outside the workspace are rejected.

## Security contract

The mapper must:

- stay inside the resolved authoritative workspace,
- reject symlink files/directories and symlink target components,
- prune hidden/generated/vendor directories during traversal,
- scan only allowlisted source-code extensions,
- skip secret-like filenames, NUL/binary-like content, and invalid UTF-8,
- enforce directory-count, per-directory-entry, file-count, per-file-byte and total-byte limits,
- enforce non-overridable hard upper ceilings on all CLI limits,
- stop or skip fail-closed when a hard limit is reached,
- open accepted files with `O_NOFOLLOW` when available,
- avoid subprocesses, shell execution, networking and repository mutation,
- print workspace-relative paths only with an `F ` row prefix,
- sanitize control characters in emitted filenames,
- remain deterministic for an unchanged tree and options.

## Default limits

- `--max-files 500`
- `--max-dirs 2000`
- `--max-dir-entries 4096`
- `--max-file-bytes 1048576` (1 MiB)
- `--max-total-bytes 8388608` (8 MiB)
- `--max-symbols 12`

Callers may choose smaller values. Larger values are accepted only up to Factory hard ceilings encoded in the helper; the later activation binder must pin policy values rather than accept arbitrary agent-provided ceilings.

## Scope

This mapper is an index, not a code graph. It reports relative file paths, exact logical line counts and regex-derived top-level symbols. It never executes repository code.
