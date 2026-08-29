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

echo '[check] capability cutover is explicit and narrow'
grep -Fq 'EXPECTED_TOOLSETS='"'"'["factory-repository-readonly"]'"'"'' "${BOOTSTRAP}"
grep -Fq 'config set toolsets "${EXPECTED_TOOLSETS}"' "${BOOTSTRAP}"
grep -Fq 'config set agent.disabled_toolsets "${EXPECTED_DISABLED}"' "${BOOTSTRAP}"
for denied in terminal file code_execution web browser image_gen delegation computer_use cronjob skills; do
  grep -Fq "${denied}" "${BOOTSTRAP}" || { echo "ERROR: missing denied toolset ${denied}" >&2; exit 1; }
done

echo '[check] plugin is installed and enabled in repository-analyst profile scope'
grep -Fq 'PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"' "${BOOTSTRAP}"
grep -Fq 'DEST_ROOT="${PROFILE_HOME}/plugins"' "${BOOTSTRAP}"
grep -Fq 'HERMES_PLUGINS_DIR="${DEST_ROOT}" bash "${INSTALLER}" --plugin "${PLUGIN}"' "${BOOTSTRAP}"
grep -Fq 'hermes -p "${PROFILE}" plugins enable "${PLUGIN}" </dev/null' "${BOOTSTRAP}"

echo '[check] plugin install, profile enable and doctor precede capability cutover'
python3 - "${BOOTSTRAP}" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text()
install=text.index('HERMES_PLUGINS_DIR="${DEST_ROOT}" bash "${INSTALLER}" --plugin "${PLUGIN}"')
enable=text.index('hermes -p "${PROFILE}" plugins enable "${PLUGIN}" </dev/null')
doctor=text.index('hermes -p "${PROFILE}" plugins doctor "${TARGET}" --ci')
cutover=text.index('config set toolsets "${EXPECTED_TOOLSETS}"')
assert install < enable < doctor < cutover
print('OK: profile install -> profile enable -> doctor -> cutover ordering')
PY

echo '[check] no generic execution/tool-management toolset is enabled'
if grep -Eq 'config set toolsets .*terminal|config set toolsets .*file|config set toolsets .*code_execution|config set toolsets .*delegation|config set toolsets .*skills' "${BOOTSTRAP}"; then
  echo 'ERROR: activation bootstrap enables a forbidden generic capability' >&2
  exit 1
fi

if [[ ${LIVE} -eq 1 ]]; then
  echo '[check] live profile-scoped installed/enabled plugin identity and config'
  TARGET="${PROFILE_HOME}/plugins/${PLUGIN}"
  SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
  test -d "${TARGET}" && ! test -L "${TARGET}"
  diff -qr "${SOURCE}" "${TARGET}" >/dev/null
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

  expect_list_exact toolsets factory-repository-readonly
  expect_list_exact agent.disabled_toolsets terminal file code_execution web browser image_gen delegation computer_use cronjob skills
  [[ "$(get_scalar fallback_providers)" == '[]' ]]
  [[ "$(get_scalar worktree)" == 'false' ]]
  [[ "$(get_scalar worktree_sync)" == 'false' ]]
  echo 'REPOSITORY_ANALYST_ISOLATION_LIVE_OK'
fi

echo 'REPOSITORY_ANALYST_ISOLATION_VERIFY_OK'
