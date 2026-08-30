# Software Factory Hermes plugins

This directory contains Factory-owned Hermes plugins that are reviewed as runtime code, separately from skills.

## factory-repository-readonly

Status: **REVIEWED-RUNTIME — ACTIVATION REQUIRES THE ISOLATION BOOTSTRAP AND LIVE PROBE**.

Purpose: provide `repository-analyst` with a read-only repository surface without exposing generic Hermes execution, file, network/cloud, delegation, or skill-management capabilities.

The plugin registers exactly one toolset, `factory-repository-readonly`, with three tools:

- `factory_repo_map` — bounded source map, using a byte-identical copy of the independently reviewed Factory repo-map helper;
- `factory_repo_read` — bounded UTF-8 read of one workspace-relative regular file;
- `factory_repo_search` — bounded literal text search over allowlisted text/source files.

It also registers one `pre_tool_call` hook used only for `repository-analyst` Kanban workers. The hook fails closed for malformed security decisions, permits only the task-local Kanban allowlist (`kanban_show`, `kanban_comment`, `kanban_block`, `kanban_heartbeat`, `kanban_complete`), and refuses every other current or future `kanban_*` operation.

Before `kanban_complete` executes, the hook refuses completion when:

- a declared artifact is missing, symlinked, non-regular, or outside the assigned workspace;
- any local path present anywhere in completion arguments resolves outside the assigned workspace;
- the authoritative Kanban task/workspace binding is missing or invalid.

This closes the artifact-delivery side channel identified during the PR #19 security review when the hook is loaded on the isolated analyst.

## Authority boundary

Handlers and the completion guard require the current worker process to contain:

- `HERMES_KANBAN_TASK`,
- absolute `HERMES_KANBAN_WORKSPACE`,
- `HERMES_PROFILE=repository-analyst`.

The production isolation step is `hermes/bootstrap_repository_analyst_isolation.sh`. It installs the exact pinned plugin into the named profile home and enables it with explicit `--no-allow-tool-override`.

Hermes dispatcher CLI workers resolve their authoritative tool surface from `platform_toolsets.cli`, not from the top-level `toolsets` key alone. The isolation bootstrap therefore pins:

- `platform_toolsets.cli = [factory-repository-readonly, no_mcp]`;
- `mcp_servers = {}` as an independent fail-closed MCP control;
- top-level `toolsets = [factory-repository-readonly]` for profile consistency;
- generic execution/file/network/delegation/skills toolsets in `agent.disabled_toolsets` as defense-in-depth;
- `tools.tool_search.enabled = off` so no generic discovery/call bridge is assembled.

Dispatcher-owned workers still receive native Kanban lifecycle names separately; the plugin hook is the mechanical authority gate for those calls.

The plugin is not a general OS sandbox. Hermes native plugins execute in-process with the user account's permissions; security comes from the worker-authoritative tool pin, no-MCP controls, deny-list defense-in-depth, and bounded plugin handlers.

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

- other profiles are unchanged;
- task-local Kanban lifecycle operations remain available;
- task creation/linking, review handoff, attachment operations and future unknown `kanban_*` calls are blocked;
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
4. confirm the live worker-authoritative CLI list is exactly `factory-repository-readonly` + `no_mcp`, profile MCP definitions are empty, tool override grant is false, and generic tool-search is off;
5. perform a live dispatcher worker probe and confirm the three reviewed repository tools work while generic/MCP capabilities are absent;
6. perform negative workspace/artifact/Kanban-authority probes before calling F2 closed.

## Runtime identity

The five reviewed regular plugin files must remain byte-identical to the repository source/pins. Runtime imports may create `__pycache__/*.pyc`; those bytecode-cache files are ignored by live identity verification, while every other unexpected file, directory, or symlink is refused.

## Supply chain

`hermes/plugins/manifest.json` pins every installed plugin file by Git blob content id. `hermes/install_factory_plugins.sh` validates exact source shape and pins before writes, copies only declared files to a temporary directory, serializes publication, re-hashes the temporary tree and refuses a differing or symlinked installed target.
