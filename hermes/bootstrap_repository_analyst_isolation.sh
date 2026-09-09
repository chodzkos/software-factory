#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="repository-analyst"
PLUGIN="factory-repository-readonly"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
CONFIG_KEY_REMOVER="${ROOT_DIR}/hermes/remove_profile_config_keys.py"
SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
DEST_ROOT="${PROFILE_HOME}/plugins"
TARGET="${DEST_ROOT}/${PLUGIN}"
EXPECTED_TOOLSETS='["factory-repository-readonly"]'
EXPECTED_CLI_TOOLSETS='["factory-repository-readonly","no_mcp"]'
EXPECTED_DISABLED='["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa"]'

command -v hermes >/dev/null 2>&1 || { echo "ERROR: hermes not found in PATH" >&2; exit 1; }
test -f "${INSTALLER}" || { echo "ERROR: missing plugin installer: ${INSTALLER}" >&2; exit 1; }
test -f "${CONFIG_KEY_REMOVER}" || { echo "ERROR: missing config-key remover: ${CONFIG_KEY_REMOVER}" >&2; exit 1; }
test -d "${PROFILE_HOME}" || { echo "ERROR: profile ${PROFILE} does not exist; run bootstrap_profiles.sh first" >&2; exit 1; }

# Profile plugins are reviewed/pinned. Re-running bootstrap may encounter only
# runtime Python cache or older reviewed bytes; controlled replacement restores
# the exact manifest-pinned tree without manual deletion.
HERMES_PLUGINS_DIR="${DEST_ROOT}" PYTHONDONTWRITEBYTECODE=1 bash "${INSTALLER}" --plugin "${PLUGIN}" --replace-reviewed

test -d "${TARGET}" && ! test -L "${TARGET}" || { echo "ERROR: installed plugin target missing or symlinked" >&2; exit 1; }

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
        if p.is_symlink(): raise SystemExit(f"ERROR: symlink in runtime cache: {rel}")
        if p.is_dir():
            if len(rel.parts) != 1: raise SystemExit(f"ERROR: nested runtime cache directory: {rel}")
            continue
        if p.is_file() and len(rel.parts) == 2 and p.suffix == ".pyc": continue
    raise SystemExit(f"ERROR: unexpected installed plugin entry: {rel}")
print("OK: installed reviewed files exact; only __pycache__/*.pyc ignored")
PY

# A cloned or stale profile may carry arbitrary enabled plugin code even when
# its visible toolsets are narrow.  Reset all plugin-selection state before
# enabling the one reviewed analyst plugin, then verify the physical YAML.
PYTHONDONTWRITEBYTECODE=1 python3 "${CONFIG_KEY_REMOVER}" \
  "${PROFILE_HOME}/config.yaml" plugins.enabled plugins.disabled plugins.entries
hermes -p "${PROFILE}" config set --force plugins.enabled '[]'
hermes -p "${PROFILE}" config set --force plugins.disabled '[]'
hermes -p "${PROFILE}" config set --force plugins.entries '{}'
hermes -p "${PROFILE}" plugins enable "${PLUGIN}" --no-allow-tool-override
PYTHONDONTWRITEBYTECODE=1 hermes -p "${PROFILE}" plugins doctor "${TARGET}" --ci

hermes -p "${PROFILE}" config set platform_toolsets.cli "${EXPECTED_CLI_TOOLSETS}"
hermes -p "${PROFILE}" config set --force mcp_servers '{}'
hermes -p "${PROFILE}" config set toolsets "${EXPECTED_TOOLSETS}"
hermes -p "${PROFILE}" config set agent.disabled_toolsets "${EXPECTED_DISABLED}"
hermes -p "${PROFILE}" config set tools.tool_search.enabled off
hermes -p "${PROFILE}" config set fallback_providers '[]'
hermes -p "${PROFILE}" config set worktree false
hermes -p "${PROFILE}" config set worktree_sync false

get_config_scalar() { hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'; }
expect_scalar() {
  local key="$1" expected="$2" actual
  actual="$(get_config_scalar "${key}")"
  [[ "${actual}" == "${expected}" ]] || { echo "ERROR: ${PROFILE}:${key} expected '${expected}', got '${actual}'" >&2; exit 1; }
}
expect_list_exact() {
  local key="$1"; shift
  local -a expected=("$@") actual=()
  mapfile -t actual < <(hermes -p "${PROFILE}" config get "${key}" 2>/dev/null | tr -d '\r' | sed -n 's/^- //p')
  [[ ${#actual[@]} -eq ${#expected[@]} ]] || { echo "ERROR: ${PROFILE}:${key} list length mismatch" >&2; exit 1; }
  local i
  for i in "${!expected[@]}"; do [[ "${actual[$i]}" == "${expected[$i]}" ]] || { echo "ERROR: ${PROFILE}:${key} item $i mismatch" >&2; exit 1; }; done
}

expect_list_exact plugins.enabled "${PLUGIN}"
expect_list_exact plugins.disabled
expect_list_exact platform_toolsets.cli factory-repository-readonly no_mcp
expect_scalar mcp_servers '{}'
expect_list_exact toolsets factory-repository-readonly
expect_list_exact agent.disabled_toolsets terminal file code_execution web browser image_gen delegation computer_use cronjob skills vision todo memory session_search clarify messaging tts moa
expect_scalar tools.tool_search.enabled 'off'
expect_scalar fallback_providers '[]'
expect_scalar worktree 'false'
expect_scalar worktree_sync 'false'
expect_scalar "plugins.entries.${PLUGIN}.allow_tool_override" 'false'

python3 - "${PROFILE_HOME}/config.yaml" "${PLUGIN}" <<'PY'
from pathlib import Path
import sys, yaml
path, plugin = Path(sys.argv[1]), sys.argv[2]
if path.is_symlink() or not path.is_file():
    raise SystemExit(f"ERROR: profile config missing/symlinked: {path}")
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
plugins = data.get("plugins")
if not isinstance(plugins, dict):
    raise SystemExit("ERROR: physical plugins config is not a mapping")
if plugins.get("enabled") != [plugin]:
    raise SystemExit(f"ERROR: physical enabled plugin set is not exact: {plugins.get('enabled')!r}")
if plugins.get("disabled") != []:
    raise SystemExit(f"ERROR: physical disabled plugin set is not empty: {plugins.get('disabled')!r}")
entries = plugins.get("entries")
if not isinstance(entries, dict) or set(entries) != {plugin}:
    raise SystemExit(f"ERROR: physical plugin entry set is not exact: {entries!r}")
entry = entries.get(plugin)
if not isinstance(entry, dict) or entry.get("allow_tool_override") is not False:
    raise SystemExit(f"ERROR: reviewed plugin entry is not fail-closed: {entry!r}")
print("OK: repository-analyst physical plugin allowlist is exact")
PY

echo "OK: plugin built-in tool override grant is false"
echo "REPOSITORY_ANALYST_ISOLATION_BOOTSTRAP_OK"
