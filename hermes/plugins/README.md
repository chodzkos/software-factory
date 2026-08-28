# Software Factory Hermes plugins

This directory contains Factory-owned Hermes plugins that are reviewed as runtime code, separately from skills.

## factory-repository-readonly

Status: **REVIEW CANDIDATE — NOT INSTALLABLE — NO PROFILE CUTOVER**.

Purpose: provide `repository-analyst` with a future read-only repository surface without exposing the generic Hermes `terminal`, `file`, or `code_execution` toolsets.

The plugin registers exactly one toolset, `factory-repository-readonly`, with three tools:

- `factory_repo_map` — bounded source map, using a byte-identical copy of the independently reviewed Factory repo-map helper;
- `factory_repo_read` — bounded UTF-8 read of one workspace-relative regular file;
- `factory_repo_search` — bounded literal text search over allowlisted text/source files.

It also registers one `pre_tool_call` hook used only for `repository-analyst` Kanban workers. Before `kanban_complete` executes, the hook refuses completion when:

- a declared artifact is missing, symlinked, non-regular, or outside the assigned workspace;
- any absolute local path present anywhere in completion arguments resolves outside the assigned workspace;
- the authoritative Kanban task/workspace binding is missing or invalid.

This closes the artifact-delivery side channel identified during the PR #19 security review: gateway completion delivery must never receive a worker-controlled host path outside `HERMES_KANBAN_WORKSPACE` from the isolated analyst.

## Authority boundary

Handlers and the completion guard require the current worker process to contain:

- `HERMES_KANBAN_TASK`,
- absolute `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The intended future profile cutover removes generic shell/file/code-execution capabilities. In that restricted profile, the model cannot spawn a child process with altered environment variables and cannot invoke the low-level helper directly. This PR does **not** perform that cutover; it only hardens and reviews the plugin boundary.

The plugin is not a general OS sandbox. Hermes native plugins execute in-process with the user account's permissions; security comes from exposing only bounded handlers to a profile whose generic execution/write toolsets are disabled.

## Read-only constraints

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

## Kanban completion confinement

The hook is intentionally narrow:

- other tool calls are unchanged;
- other profiles are unchanged;
- `repository-analyst` `kanban_complete` fails closed if its worker binding is missing;
- relative artifacts resolve from the bound workspace;
- absolute artifacts are allowed only when they resolve to regular files inside that workspace;
- symlink artifacts/components are refused;
- absolute local paths embedded in nested completion strings are scanned so future schema-field changes cannot silently reopen the gateway-delivery channel.

The guard runs before the Kanban tool executes, so a refused external path never becomes task completion data for the gateway watcher to deliver.

## Hermes integration assumptions

Design references the Hermes native plugin/toolset and plugin-hook contracts. Hermes documents that `pre_tool_call` hooks may return `{action: block, message: ...}` and that plugin tools/hooks execute in CLI and gateway agent paths.

A later activation PR must still perform a live Kanban worker schema/toolset probe against the installed Hermes version and confirm that `terminal`, `file`, and `code_execution` are absent while this plugin and Kanban lifecycle tools remain available.

## Supply chain

`hermes/plugins/manifest.json` pins every installed plugin file by Git blob content id. `hermes/install_factory_plugins.sh` validates exact source shape and pins before writes, copies only declared files to a temporary directory, and refuses a differing or symlinked installed target.

Production state remains:

- `installable=false`
- `activation_status=pending-independent-review`

A later activation requires `activation_status=reviewed-ready` plus a separately reviewed profile cutover.
