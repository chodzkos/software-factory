# repo-map multi-file review gate

Status: **REFERENCE ONLY — NOT VETTED — NOT INSTALLABLE**

Upstream repository: `mohitagw15856/pm-claude-skills`

Pinned commit: `aa71bee8d20b7febdfd49f3aa96f26f316344628`

Upstream path: `skills/repo-map`

Local path: `skills/upstream/repo-map`

## Exact file set

Only these two byte-identical upstream files are vendored in this review stage:

| Relative path | SHA-256 |
|---|---|
| `SKILL.md` | `9d6923145b22099e604b2ea3888e6f59d215cf98c8c87e00f41c98f5db01c7e0` |
| `scripts/repo_map.py` | `bf4ccffe145eb9361f60c32aa74c13294a58890e5a2918572dacb3f9f153962d` |

The manifest declares the same allowlist and digest for each file. Any extra file, missing file, symlink, or digest mismatch must fail repository tests.

## Current exposure

- `repo-map` is absent from `manifest.skills`.
- `installable=false`.
- `vetted=false`.
- `review_status=pending-helper-review`.
- no profile references `repo-map`.
- `install_factory_skills.sh --all` cannot select it.
- no runtime network acquisition is introduced.

The helper is executed only by `skills/tests/test_repo_map_reference.py` against temporary test directories to establish deterministic baseline behavior.

## Preliminary code observations

The upstream helper imports only Python stdlib modules `argparse`, `os`, `re`, and `sys`. It uses `os.walk` and text-file reads; it does not call a shell, spawn subprocesses, write/delete files, or use networking APIs.

The initial behavioral tests verify symbol extraction, generated-directory skipping, and the `--max-files` truncation guard.

## Open security questions for independent review

These are intentionally **not** treated as resolved by this PR:

1. **Workspace boundary.** The raw helper accepts any readable path supplied on its CLI. Before profile activation, determine whether a Factory wrapper must require the path to remain inside the current Kanban-assigned `workspace_path`.
2. **Symlinked files inside a mapped tree.** Python `open()` follows file symlinks. A repository file symlink could therefore expose readable content outside the mapped root. Before activation, determine whether the helper needs an adapter/wrapper that rejects symlink files or verifies resolved paths remain under the assigned workspace.
3. **Resource bounds.** `--max-files` bounds file count but not individual file size; one very large text/binary-like file can still consume memory/context. Review whether a per-file byte limit is required.
4. **Binary/secret-like content.** The helper reads files as UTF-8 with replacement and reports only symbols/line counts, but still reads each included file into memory. Review whether sensitive/generated paths need a stronger denylist before autonomous use.

## Activation gate

A later PR may make `repo-map` installable only after:

- independent exact-SHA code/security review of both vendored files,
- adjudication of the four open questions above,
- any required Factory-owned wrapper/adapter,
- explicit multi-file install-copy and installed-state digest verification,
- adversarial tests for file allowlist, symlink/path escape, extra files, digest drift, and workspace boundary,
- explicit least-privilege profile grant.

Until all of those gates pass, this reference must remain non-installable.
