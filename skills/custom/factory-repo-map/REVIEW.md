# Factory repo-map review gate

Status: **FACTORY-OWNED FORK — REVIEW ONLY — NOT INSTALLABLE — NO PROFILE GRANT**

This implementation exists to close the activation findings found in the pinned upstream `repo-map` review. It is intentionally absent from `skills/manifest.yaml` and all profile grants in this PR.

## Findings this fork must close

The raw upstream helper remains under `skills/upstream/repo-map/` as byte-identical audit material. Independent review classified activation as `FACTORY_FORK_REQUIRED` because the raw helper:

1. defeats directory pruning via `sorted(os.walk(...))`,
2. accepts arbitrary readable paths outside the Kanban workspace,
3. follows file symlinks outside the mapped tree,
4. does not bound individual or total bytes and materializes an unbounded walk,
5. reads non-dot secret/binary-like files,
6. prints absolute paths and unsanitized control characters.

## Fork contract

`factory-repo-map/scripts/repo_map.py` must:

- require `--workspace` and treat it as the authoritative Kanban-assigned workspace,
- reject a symlink workspace argument,
- accept only workspace-relative targets,
- reject target symlinks and targets resolving outside the workspace,
- refuse hidden/generated target roots,
- prune hidden/generated/vendor directories during live `os.walk`,
- reject file and directory symlink traversal,
- use a source-code extension allowlist,
- skip secret-like names and binary-like content,
- enforce maximum directories visited, filenames examined, bytes per file and total bytes,
- stop traversal when hard limits are reached,
- emit workspace-relative paths only,
- sanitize control characters in emitted filenames,
- remain stdlib-only, read-only, deterministic and non-executing.

## Default ceilings

- 500 filenames examined
- 2000 directories visited
- 1 MiB per file
- 8 MiB total accepted source bytes
- 12 symbols per supported file

## Review/activation separation

This PR does not modify:

- `skills/manifest.yaml`,
- `skills/profiles.yaml`,
- `hermes/install_factory_skills.sh`,
- runtime-controller/Kanban routing.

`factory-repo-map` therefore cannot be selected by `--all` or any profile. A later activation PR must separately design multi-file custom installation, per-file integrity pins, installed-state verification and a least-privilege profile grant.

## Security tests

`skills/tests/test_factory_repo_map.py` pins:

- no manifest/profile exposure,
- generated/hidden directory pruning at all depths,
- refusal of generated/hidden roots,
- absolute/parent path escape rejection,
- workspace/target/file/directory symlink rejection,
- file and directory traversal limits,
- per-file and total-byte limits,
- secret/non-code/binary filtering,
- relative sanitized output,
- deterministic behavior and absence of execution/network primitives.

Do not mark this implementation installable until an independent exact-SHA adversarial review approves this contract.
