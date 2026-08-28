# Software Factory Hermes plugins

This directory contains Factory-owned Hermes plugins that are reviewed as runtime code, separately from skills.

## factory-repository-readonly

Status: **REVIEW CANDIDATE — NOT INSTALLABLE — NO PROFILE CUTOVER**.

Purpose: provide `repository-analyst` with a future read-only repository surface without exposing the generic Hermes `terminal`, `file`, or `code_execution` toolsets.

The plugin registers exactly one toolset, `factory-repository-readonly`, with three tools:

- `factory_repo_map` — bounded source map, using a byte-identical copy of the independently reviewed Factory repo-map helper;
- `factory_repo_read` — bounded UTF-8 read of one workspace-relative regular file;
- `factory_repo_search` — bounded literal text search over allowlisted text/source files.

## Authority boundary

Handlers never accept a workspace parameter. They require the current worker process to contain:

- `HERMES_KANBAN_TASK`,
- absolute `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The intended future profile cutover removes generic shell/file/code-execution capabilities. In that restricted profile, the model cannot spawn a child process with altered environment variables and cannot invoke the low-level helper directly. This PR does **not** perform that cutover; it only prepares and reviews the plugin boundary.

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

## Hermes integration assumptions

Design references the Hermes native plugin/toolset contract and Kanban worker toolset pinning. Hermes documents that plugin tools can register their own toolset and that dispatcher workers are spawned with the assigned profile's CLI toolsets pinned; Kanban lifecycle tools are added separately for Kanban workers.

Before profile cutover, PR #20 must verify these behaviors against the actually installed Hermes v0.20.4 on the target host, including a live Kanban worker schema/toolset probe.

## Supply chain

`hermes/plugins/manifest.json` pins every installed plugin file by Git blob content id. `hermes/install_factory_plugins.sh` validates exact source shape and pins before writes, copies only declared files to a temporary directory, and refuses a differing or symlinked installed target.

Production state remains:

- `installable=false`
- `activation_status=pending-independent-review`

A later activation requires `activation_status=reviewed-ready` plus a separately reviewed profile cutover.
