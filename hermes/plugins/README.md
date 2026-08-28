# Software Factory Hermes plugins

This directory contains Factory-owned Hermes plugins that are reviewed as runtime code, separately from skills.

## factory-repository-readonly

Status: **REVIEWED-RUNTIME — ACTIVATION REQUIRES THE ISOLATION BOOTSTRAP AND LIVE PROBE**.

Purpose: provide `repository-analyst` with a read-only repository surface without exposing the generic Hermes `terminal`, `file`, or `code_execution` toolsets.

The plugin registers exactly one toolset, `factory-repository-readonly`, with three tools:

- `factory_repo_map` — bounded source map, using a byte-identical copy of the independently reviewed Factory repo-map helper;
- `factory_repo_read` — bounded UTF-8 read of one workspace-relative regular file;
- `factory_repo_search` — bounded literal text search over allowlisted text/source files.

It also registers one `pre_tool_call` hook used only for `repository-analyst` Kanban workers. Before `kanban_complete` executes, the hook refuses completion when:

- a declared artifact is missing, symlinked, non-regular, or outside the assigned workspace;
- any local path present anywhere in completion arguments resolves outside the assigned workspace;
- the authoritative Kanban task/workspace binding is missing or invalid.

This closes the artifact-delivery side channel identified during the PR #19 security review when the hook is loaded on the isolated analyst.

## Authority boundary

Handlers and the completion guard require the current worker process to contain:

- `HERMES_KANBAN_TASK`,
- absolute `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The production isolation step is `hermes/bootstrap_repository_analyst_isolation.sh`. It installs the exact pinned plugin, requires `hermes plugins doctor --ci` to pass, then sets the profile toolset to only `factory-repository-readonly` and deny-lists generic execution/file/network/delegation surfaces. Dispatcher-owned workers receive Kanban lifecycle tools separately.

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
- local path-bearing nested completion strings are scanned so schema-field changes cannot silently reopen gateway delivery.

The guard runs before the Kanban tool executes, so a refused external path never becomes task completion data for the gateway watcher to deliver.

## Activation and verification

Manifest production state:

- `installable=true`
- `activation_status=reviewed-ready`

Required deployment sequence:

1. run the normal profile bootstrap;
2. run `bash hermes/bootstrap_repository_analyst_isolation.sh`;
3. run `bash hermes/verify_repository_analyst_isolation.sh --live`;
4. perform a live dispatcher worker schema probe and confirm only the reviewed repository tools plus Kanban lifecycle tools are present;
5. confirm generic terminal/process/file/code-execution/delegation capabilities are absent before calling F2 closed.

## Supply chain

`hermes/plugins/manifest.json` pins every installed plugin file by Git blob content id. `hermes/install_factory_plugins.sh` validates exact source shape and pins before writes, copies only declared files to a temporary directory, serializes publication, re-hashes the temporary tree and refuses a differing or symlinked installed target.
