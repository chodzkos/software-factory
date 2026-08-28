# Factory repo-map activation contract

Status: **FACTORY-OWNED FORK — ACTIVATION CANDIDATE**

The raw upstream `repo-map` remains byte-identical audit material under `skills/upstream/repo-map/` and remains non-installable. Independent review required a Factory-owned fork; PR #16 closed the raw-helper activation blockers and was merged after exact-SHA review.

## Runtime architecture

`factory-repo-map` is granted only to `repository-analyst` and must be invoked through `scripts/run_repo_map.py`.

The binder does not accept a workspace argument from the model. It obtains authority from Hermes dispatcher environment:

- `HERMES_KANBAN_TASK`,
- `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The dispatcher-provided workspace is passed to the reviewed mapper together with fixed Factory safety limits. Missing or inconsistent binding fails closed.

## Multi-file install contract

The manifest declares this skill as `custom-multifile` with an exact allowlist and content pins for every installed file. Installer/verifier must:

- reject source or target symlinks,
- reject missing or extra files/directories outside the declared tree,
- verify each declared file pin before writes,
- copy only the declared files into a temp directory,
- preserve nested relative paths,
- refuse overwrite of a differing installed skill,
- verify installed file pins and exact installed tree,
- complete preflight for the full selection before the first install write.

## Least privilege

Only `repository-analyst` receives `factory-repo-map`, as optional. No other profile receives it in this activation change.

## Fixed mapper limits

The binder fixes:

- 500 filenames,
- 2000 directories,
- 4096 entries per directory,
- 1 MiB per source file,
- 8 MiB total accepted bytes,
- 12 symbols per file.

The autonomous caller cannot raise these limits through binder arguments.

## Security invariants retained from PR #16

- bounded `os.scandir` traversal,
- target-component hidden/generated and symlink refusal,
- workspace containment,
- source extension allowlist,
- secret/non-code/binary/invalid-UTF8 filtering,
- hard ceilings,
- `O_NOFOLLOW` leaf open when available + regular-file `fstat`,
- relative prefixed output and control sanitization,
- no discovered-code execution, shell, network, or repository mutation.

Activation must not weaken these invariants.
