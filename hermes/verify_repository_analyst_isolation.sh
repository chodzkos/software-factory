#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_repository_analyst_isolation.sh"
MANIFEST="${ROOT_DIR}/hermes/plugins/manifest.json"
PLUGIN="factory-repository-readonly"
PROFILE="repository-analyst"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
LIVE=0

if [[ "${1:-}" == "--live" ]]; then
  LIVE=1
elif [[ $# -ne 0 ]]; then
  echo "usage: bash hermes/verify_repository_analyst_isolation.sh [--live]" >&2
  exit 2
fi

echo '[check] activation bootstrap syntax'
bash -n "${BOOTSTRAP}"

echo '[check] reviewed-ready exact plugin state'
python3 - "${MANIFEST}" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
s=m['plugins']['factory-repository-readonly']
assert s['installable'] is True
assert s['activation_status'] == 'reviewed-ready'
assert set(s['files']) == {'plugin.yaml','__init__.py','repo_map.py','repository_tools.py','kanban_guard.py'}
print('OK: reviewed-ready plugin manifest')
PY

echo '[check] worker-authoritative capability cutover is explicit and narrow'
grep -Fq 'EXPECTED_TOOLSETS='"'"'["factory-repository-readonly"]'"'"'' "${BOOTSTRAP}"
grep -Fq 'EXPECTED_CLI_TOOLSETS='"'"'["factory-repository-readonly","no_mcp"]'"'"'' "${BOOTSTRAP}"
grep -Fq 'config set platform_toolsets.cli "${EXPECTED_CLI_TOOLSETS}"' "${BOOTSTRAP}"
grep -Fq "config set mcp_servers '{}'" "${BOOTSTRAP}"
grep -Fq 'config set toolsets "${EXPECTED_TOOLSETS}"' "${BOOTSTRAP}"
grep -Fq 'config set agent.disabled_toolsets "${EXPECTED_DISABLED}"' "${BOOTSTRAP}"
grep -Fq 'config set tools.tool_search.enabled off' "${BOOTSTRAP}"
for denied in terminal file code_execution web browser image_gen delegation computer_use cronjob skills vision todo memory session_search clarify messaging tts moa; do
  grep -Fq "${denied}" "${BOOTSTRAP}" || { echo "ERROR: missing denied toolset ${denied}" >&2; exit 1; }
done

echo '[check] plugin is installed and enabled in repository-analyst profile scope'
grep -Fq 'PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"' "${BOOTSTRAP}"
grep -Fq 'DEST_ROOT="${PROFILE_HOME}/plugins"' "${BOOTSTRAP}"
grep -Fq 'HERMES_PLUGINS_DIR="${DEST_ROOT}" bash "${INSTALLER}" --plugin "${PLUGIN}"' "${BOOTSTRAP}"
grep -Fq 'plugins enable "${PLUGIN}" --no-allow-tool-override' "${BOOTSTRAP}"

echo '[check] plugin install, profile enable and doctor precede worker cutover'
python3 - "${BOOTSTRAP}" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text()
install=text.index('HERMES_PLUGINS_DIR="${DEST_ROOT}" bash "${INSTALLER}" --plugin "${PLUGIN}"')
enable=text.index('plugins enable "${PLUGIN}" --no-allow-tool-override')
doctor=text.index('plugins doctor "${TARGET}" --ci')
cutover=text.index('config set platform_toolsets.cli "${EXPECTED_CLI_TOOLSETS}"')
assert install < enable < doctor < cutover
print('OK: profile install -> explicit non-override enable -> doctor -> worker cutover ordering')
PY

echo '[check] no generic execution/tool-management toolset is enabled by bootstrap'
if grep -Eq 'config set (toolsets|platform_toolsets\.cli) .*terminal|config set (toolsets|platform_toolsets\.cli) .*file|config set (toolsets|platform_toolsets\.cli) .*code_execution|config set (toolsets|platform_toolsets\.cli) .*delegation|config set (toolsets|platform_toolsets\.cli) .*skills' "${BOOTSTRAP}"; then
  echo 'ERROR: activation bootstrap enables a forbidden generic capability' >&2
  exit 1
fi

verify_installed_tree() {
  local source="$1" target="$2"
  python3 - "${source}" "${target}" <<'PY'
from pathlib import Path
import hashlib, sys
src, dst = map(Path, sys.argv[1:])
expected = {"plugin.yaml", "__init__.py", "repo_map.py", "repository_tools.py", "kanban_guard.py"}
if not dst.is_dir() or dst.is_symlink():
    raise SystemExit("ERROR: installed plugin target missing/symlinked")
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
print("OK: live reviewed plugin files exact; only __pycache__/*.pyc ignored")
PY
}

if [[ ${LIVE} -eq 1 ]]; then
  echo '[check] live profile-scoped plugin identity and worker-authoritative config'
  TARGET="${PROFILE_HOME}/plugins/${PLUGIN}"
  SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
  verify_installed_tree "${SOURCE}" "${TARGET}"
  PYTHONDONTWRITEBYTECODE=1 hermes -p "${PROFILE}" plugins doctor "${TARGET}" --ci

  hermes -p "${PROFILE}" config get plugins.enabled 2>/dev/null \
    | tr -d '\r' \
    | sed -n 's/^- //p' \
    | grep -Fxq -- "${PLUGIN}" || {
      echo "ERROR: live ${PROFILE}:plugins.enabled missing ${PLUGIN}" >&2
      exit 1
    }

  get_scalar() { hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'; }
  expect_list_exact() {
    local key="$1"; shift
    local -a expected=("$@") actual=()
    mapfile -t actual < <(
      hermes -p "${PROFILE}" config get "${key}" 2>/dev/null \
        | tr -d '\r' \
        | sed -n 's/^- //p'
    )
    [[ ${#actual[@]} -eq ${#expected[@]} ]] || {
      echo "ERROR: live ${PROFILE}:${key} expected ${#expected[@]} list items, got ${#actual[@]}" >&2
      exit 1
    }
    local i
    for i in "${!expected[@]}"; do
      [[ "${actual[$i]}" == "${expected[$i]}" ]] || {
        echo "ERROR: live ${PROFILE}:${key} item $i expected '${expected[$i]}', got '${actual[$i]}'" >&2
        exit 1
      }
    done
  }

  # Worker-authoritative persisted inputs.
  expect_list_exact platform_toolsets.cli factory-repository-readonly no_mcp
  [[ "$(get_scalar mcp_servers)" == '{}' ]]
  expect_list_exact toolsets factory-repository-readonly
  expect_list_exact agent.disabled_toolsets terminal file code_execution web browser image_gen delegation computer_use cronjob skills vision todo memory session_search clarify messaging tts moa
  [[ "$(get_scalar tools.tool_search.enabled)" == 'off' ]]
  [[ "$(get_scalar fallback_providers)" == '[]' ]]
  [[ "$(get_scalar worktree)" == 'false' ]]
  [[ "$(get_scalar worktree_sync)" == 'false' ]]

  override="$(hermes -p "${PROFILE}" config get "plugins.entries.${PLUGIN}.allow_tool_override" 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ "${override}" == 'false' ]] || {
    echo "ERROR: live plugin allow_tool_override expected false, got '${override}'" >&2
    exit 1
  }

  echo '[check] resolved dispatcher worker CLI toolsets'
  PYTHONDONTWRITEBYTECODE=1 python3 - "${PROFILE_HOME}" <<'PY'
import sys
from hermes_cli import kanban_db as kb
profile_home = sys.argv[1]
resolved = kb._resolve_worker_cli_toolsets(profile_home)
if resolved is None:
    raise SystemExit("ERROR: dispatcher worker toolset resolver returned None")
resolved = list(resolved)
actual = set(resolved)
allowed = {"factory-repository-readonly", "no_mcp", "kanban"}
required = {"factory-repository-readonly", "kanban"}
missing = required - actual
extra = actual - allowed
if missing:
    raise SystemExit(f"ERROR: resolved worker toolsets missing required: {sorted(missing)}; got {resolved}")
if extra:
    raise SystemExit(f"ERROR: resolved worker toolsets contain unexpected capability: {sorted(extra)}; got {resolved}")
print("OK: resolved worker CLI toolsets =", ",".join(resolved))
PY

  echo 'REPOSITORY_ANALYST_ISOLATION_LIVE_OK'
fi

echo 'REPOSITORY_ANALYST_ISOLATION_VERIFY_OK'
