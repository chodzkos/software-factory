#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="repository-analyst"
PLUGIN="factory-repository-readonly"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
DEST_ROOT="${PROFILE_HOME}/plugins"
TARGET="${DEST_ROOT}/${PLUGIN}"
EXPECTED_TOOLSETS='["factory-repository-readonly"]'
EXPECTED_CLI_TOOLSETS='["factory-repository-readonly","no_mcp"]'
EXPECTED_DISABLED='["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa"]'

command -v hermes >/dev/null 2>&1 || { echo "ERROR: hermes not found in PATH" >&2; exit 1; }
test -f "${INSTALLER}" || { echo "ERROR: missing plugin installer: ${INSTALLER}" >&2; exit 1; }
test -d "${PROFILE_HOME}" || { echo "ERROR: profile ${PROFILE} does not exist; run bootstrap_profiles.sh first" >&2; exit 1; }

# Named Hermes profiles are separate HERMES_HOME roots. Publish the reviewed
# plugin into the repository-analyst profile itself so dispatcher-spawned
# `-p repository-analyst` workers can discover it.
HERMES_PLUGINS_DIR="${DEST_ROOT}" bash "${INSTALLER}" --plugin "${PLUGIN}"

test -d "${TARGET}" && ! test -L "${TARGET}" || { echo "ERROR: installed plugin target missing or symlinked" >&2; exit 1; }

# Runtime imports may create __pycache__. Treat only Python bytecode cache as
# ignorable runtime noise; every reviewed regular file must remain byte-identical
# and no other file/directory/symlink is permitted in the installed tree.
python3 - "${SOURCE}" "${TARGET}" <<'PY'
from pathlib import Path
import hashlib, sys
src, dst = map(Path, sys.argv[1:])
expected = {"plugin.yaml", "__init__.py", "repo_map.py", "repository_tools.py", "kanban_guard.py"}
for name in expected:
    s, d = src / name, dst / name
    if not s.is_file() or s.is_symlink() or not d.is_file() or d.is_symlink():
        raise SystemExit(f"ERROR: reviewed plugin file missing/symlinked: {name}")
    if hashlib.sha256(s.read_bytes()).digest() != hashlib.sha256(d.read_bytes()).digest():
        raise SystemExit(f"ERROR: installed plugin file differs: {name}")
for p in dst.rglob("*"):
    rel = p.relative_to(dst)
    if len(rel.parts) == 1 and rel.name in expected:
        continue
    if rel.parts[0] == "__pycache__":
        if p.is_symlink():
            raise SystemExit(f"ERROR: symlink in runtime cache: {rel}")
        if p.is_dir():
            if len(rel.parts) != 1:
                raise SystemExit(f"ERROR: nested runtime cache directory: {rel}")
            continue
        if p.is_file() and len(rel.parts) == 2 and p.suffix == ".pyc":
            continue
    raise SystemExit(f"ERROR: unexpected installed plugin entry: {rel}")
print("OK: installed reviewed files exact; only __pycache__/*.pyc ignored")
PY

# User plugins are opt-in per profile. Use the explicit non-override flag rather
# than relying on EOF/prompt behaviour for this privileged capability.
hermes -p "${PROFILE}" plugins enable "${PLUGIN}" --no-allow-tool-override
PYTHONDONTWRITEBYTECODE=1 hermes -p "${PROFILE}" plugins doctor "${TARGET}" --ci

# Worker-authoritative capability cutover. Hermes dispatcher resolves CLI worker
# tools from platform_toolsets.cli and adds enabled MCP servers. Pin the CLI list
# to the reviewed plugin plus no_mcp and clear profile MCP definitions. The broad
# deny-list remains defense-in-depth, not the primary isolation mechanism.
hermes -p "${PROFILE}" config set platform_toolsets.cli "${EXPECTED_CLI_TOOLSETS}"
hermes -p "${PROFILE}" config set --force mcp_servers '{}'
hermes -p "${PROFILE}" config set toolsets "${EXPECTED_TOOLSETS}"
hermes -p "${PROFILE}" config set agent.disabled_toolsets "${EXPECTED_DISABLED}"
# The profile has only three small plugin tools. Disable progressive-disclosure
# bridges so the model sees the reviewed tools directly and no generic tool_call
# broker is present in the repository-analysis capability surface.
hermes -p "${PROFILE}" config set tools.tool_search.enabled off
hermes -p "${PROFILE}" config set fallback_providers '[]'
hermes -p "${PROFILE}" config set worktree false
hermes -p "${PROFILE}" config set worktree_sync false

get_config_scalar() {
  hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'
}

expect_scalar() {
  local key="$1" expected="$2" actual
  actual="$(get_config_scalar "${key}")"
  [[ "${actual}" == "${expected}" ]] || {
    echo "ERROR: ${PROFILE}:${key} expected '${expected}', got '${actual}'" >&2
    exit 1
  }
}

expect_list_exact() {
  local key="$1"; shift
  local -a expected=("$@") actual=()
  mapfile -t actual < <(
    hermes -p "${PROFILE}" config get "${key}" 2>/dev/null \
      | tr -d '\r' \
      | sed -n 's/^- //p'
  )
  if [[ ${#actual[@]} -ne ${#expected[@]} ]]; then
    echo "ERROR: ${PROFILE}:${key} expected ${#expected[@]} list items, got ${#actual[@]}" >&2
    exit 1
  fi
  local i
  for i in "${!expected[@]}"; do
    [[ "${actual[$i]}" == "${expected[$i]}" ]] || {
      echo "ERROR: ${PROFILE}:${key} item $i expected '${expected[$i]}', got '${actual[$i]}'" >&2
      exit 1
    }
  done
}

expect_profile_list_contains() {
  local key="$1" expected="$2"
  hermes -p "${PROFILE}" config get "${key}" 2>/dev/null \
    | tr -d '\r' \
    | sed -n 's/^- //p' \
    | grep -Fxq -- "${expected}" || {
      echo "ERROR: ${PROFILE}:${key} does not contain '${expected}'" >&2
      exit 1
    }
}

expect_profile_list_contains plugins.enabled "${PLUGIN}"
expect_list_exact platform_toolsets.cli factory-repository-readonly no_mcp
expect_scalar mcp_servers '{}'
expect_list_exact toolsets factory-repository-readonly
expect_list_exact agent.disabled_toolsets terminal file code_execution web browser image_gen delegation computer_use cronjob skills vision todo memory session_search clarify messaging tts moa
expect_scalar tools.tool_search.enabled 'off'
expect_scalar fallback_providers '[]'
expect_scalar worktree 'false'
expect_scalar worktree_sync 'false'
expect_scalar "plugins.entries.${PLUGIN}.allow_tool_override" 'false'

echo "OK: plugin built-in tool override grant is false"
echo "REPOSITORY_ANALYST_ISOLATION_BOOTSTRAP_OK"
