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

The implementation must pass independent exact-SHA review before any manifest/profile/installer activation PR.

## Required invocation contract

The caller must provide the authoritative Kanban-assigned workspace path explicitly:

```bash
python3 scripts/repo_map.py --workspace "$WORKSPACE_PATH" .
```

The target argument is workspace-relative. Absolute targets and paths resolving outside the workspace are rejected.

## Security contract

The mapper must:

- stay inside the resolved authoritative workspace,
- reject symlink files and symlink directories,
- prune hidden/generated/vendor directories during traversal,
- refuse a generated/hidden directory as the requested target,
- scan only allowlisted source/text extensions,
- skip secret-like filenames and binary-like content,
- enforce per-file, total-byte and file-count limits before reading,
- stop traversal once a hard limit is reached,
- avoid subprocesses, shell execution, networking and repository mutation,
- print workspace-relative paths only,
- sanitize control characters in emitted filenames,
- remain deterministic for an unchanged tree and options.

## Default limits

- `--max-files 500`
- `--max-file-bytes 1048576` (1 MiB)
- `--max-total-bytes 8388608` (8 MiB)
- `--max-symbols 12`

These are safety ceilings, not goals. A caller may choose smaller values; larger values remain bounded by explicit CLI options and future profile policy.

## Scope

This mapper is an index, not a code graph. It reports relative file paths, line counts and regex-derived top-level symbols. It never executes repository code.
