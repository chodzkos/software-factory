---
name: factory-repo-map
description: "Factory-owned secure repository mapper candidate. Runtime activation is blocked until Hermes provides a mechanically isolated invocation surface; raw upstream repo-map remains audit-only."
---

# Factory Repo Map

Factory-owned secure repository index derived from the reviewed upstream `repo-map` concept.

## Authority

`SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract -> this skill`

This skill never chooses or expands its own workspace authority.

## Activation state

**NOT INSTALLABLE — NO PROFILE GRANT — BLOCKED ON RUNTIME ISOLATION.**

PR #17 activation review found that Hermes 0.20.4 `repository-analyst` has unrestricted local terminal/code execution. In that runtime, dispatcher environment variables are not a tamper-proof authority boundary because an autonomous worker can spawn a child with changed `HERMES_KANBAN_*` values or invoke the mapper module directly.

Therefore this candidate stays out of every profile and is skipped by `--all` until a separate reviewed runtime mechanism can expose only a narrow repo-map operation without arbitrary shell/Python bypass.

The pinned raw upstream `skills/upstream/repo-map/` also remains non-installable audit material and must never be invoked for Factory runtime use.

## Candidate binder contract

`run_repo_map.py` is retained and tested as activation infrastructure. It:

- requires `HERMES_KANBAN_TASK`,
- requires absolute `HERMES_KANBAN_WORKSPACE`,
- requires `HERMES_PROFILE=repository-analyst`,
- rejects option-shaped targets such as `--workspace=/`,
- accepts at most one workspace-relative target,
- inserts `--` before the target when invoking the mapper,
- supplies fixed Factory limits,
- disables bytecode writes before importing the mapper.

These checks are defense-in-depth only until the caller itself is mechanically isolated from arbitrary terminal/code execution.

## Mapper security contract

The mapper:

- stays inside the supplied workspace,
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

## Candidate fixed limits

- `--max-files 500`
- `--max-dirs 2000`
- `--max-dir-entries 4096`
- `--max-file-bytes 1048576`
- `--max-total-bytes 8388608`
- `--max-symbols 12`

## Scope

This is an index, not a code graph and not a secret-redaction tool. It reports workspace-relative file paths, logical line counts and regex-derived top-level symbols. It never executes repository code.
