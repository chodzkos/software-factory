# Software Factory Hermes plugins

This directory contains Factory-owned Hermes plugins that are reviewed as runtime code, separately from skills.

## factory-repository-readonly

Status: **REVIEW CANDIDATE — NOT INSTALLABLE — NO PROFILE CUTOVER**.

Purpose: provide `repository-analyst` with a future read-only repository surface without exposing the generic Hermes `terminal`, `file`, or `code_execution` toolsets.

The plugin registers exactly one toolset, `factory-repository-readonly`, with three tools:

- `factory_repo_map` — bounded source map, using a byte-identical copy of the independently reviewed Factory repo-map helper;
- `factory_repo_read` — bounded UTF-8 read of one workspace-relative regular file;
- `factory_repo_search` — bounded literal text search over allowlisted text/source files.

### Authority boundary

Handlers never accept a workspace parameter. They require the current worker process to contain:

- `HERMES_KANBAN_TASK`,
- absolute `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The intended future profile cutover removes generic shell/file/code-execution capabilities. In that restricted profile, the model cannot spawn a child process with altered environment variables and cannot invoke the low-level helper directly. The plugin candidate itself does not perform that cutover.

The plugin is not a general OS sandbox. Hermes native plugins execute in-process with the user account's permissions; security comes from exposing only bounded handlers to a profile whose generic execution/write toolsets are disabled.

### Read-only constraints

The plugin code must not expose subprocess/shell execution, network access, file writes, delete/rename operations, arbitrary regex execution, or caller-controlled workspace paths.

Reads/searches reject:

- absolute or parent-traversal paths,
- symlink workspace/path components,
- hidden files,
- secret-like filenames and key/database suffixes,
- generated/vendor/VCS directories,
- binary/NUL content,
- invalid UTF-8,
- oversized files and traversal budgets.

`.github/` is the only hidden directory intentionally traversable so repository analysis can inspect CI configuration; hidden files inside it remain subject to the hidden/secret-file refusal.

## factory-kanban-artifact-guard

Status: **REVIEW CANDIDATE — NOT INSTALLABLE — NO PROFILE CUTOVER**.

Purpose: close the Kanban filesystem-delivery side channel identified during the independent review of `factory-repository-readonly` before `repository-analyst` is called isolated.

The plugin registers exactly one `pre_tool_call` hook and no tools. It is scoped to `HERMES_PROFILE=repository-analyst` and only intercepts `kanban_complete`.

For the assigned worker it requires:

- a non-empty `HERMES_KANBAN_TASK`,
- an existing absolute, non-symlink `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The guard blocks completion when:

- an explicit `artifacts` entry is not a regular file resolving inside the assigned workspace;
- an artifact path is absolute outside the workspace, uses `..`, or crosses a symlink;
- completion text contains an absolute local path outside the assigned workspace that Hermes' Kanban notifier could later interpret as a deliverable path;
- `kanban_complete` arguments or the artifact list are malformed while the profile is repository-analyst.

Absolute paths inside the assigned workspace and normal workspace-relative artifact paths are allowed. Other profiles keep native Hermes Kanban semantics unchanged.

This guard is intentionally enforced before tool dispatch. Hermes plugin `pre_tool_call` supports returning `{"action":"block","message":"..."}`; in that case `kanban_complete` is not executed and no completion event containing the refused path is created.

The guard does **not** make environment variables authoritative while the profile still has generic command execution. Its security value becomes complete only when a separately reviewed activation removes `terminal`, `file`, and `code_execution` from `repository-analyst` and a live worker probe confirms the final tool surface.

## Hermes integration assumptions

The design relies on the Hermes native plugin/toolset and plugin-hook contracts, plus Kanban worker toolset pinning. A future activation must verify these behaviors against the actually installed Hermes version, including a live Kanban worker schema/toolset probe.

## Supply chain

`hermes/plugins/manifest.json` pins every installed plugin file by Git blob content id. `hermes/install_factory_plugins.sh` validates the exact source tree and pins before writes, copies only manifest-declared files to a temporary directory, re-hashes the temporary tree, serializes publication under a per-plugin lock, and refuses a differing or symlinked installed target.

Each review candidate remains:

- `installable=false`
- `activation_status=pending-independent-review`

A later activation requires `installable=true`, `activation_status=reviewed-ready`, independent exact-SHA activation review, and the profile/runtime isolation gates appropriate to that plugin.
