#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_repository_analyst_isolation.sh"
MANIFEST="${ROOT_DIR}/hermes/plugins/manifest.json"
PLUGIN="factory-repository-readonly"
PROFILE="repository-analyst"
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
for denied in terminal file code_execution web browser image_gen delegation computer_use cronjob; do
  grep -Fq "${denied}" "${BOOTSTRAP}" || { echo "ERROR: missing denied toolset ${denied}" >&2; exit 1; }
done

echo '[check] plugin install and doctor precede capability cutover'
python3 - "${BOOTSTRAP}" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text()
install=text.index('bash "${INSTALLER}" --plugin "${PLUGIN}"')
doctor=text.index('hermes plugins doctor "${TARGET}" --ci')
cutover=text.index('config set toolsets "${EXPECTED_TOOLSETS}"')
assert install < doctor < cutover
print('OK: install -> doctor -> cutover ordering')
PY

echo '[check] no generic execution toolset is enabled'
if grep -Eq 'config set toolsets .*terminal|config set toolsets .*file|config set toolsets .*code_execution|config set toolsets .*delegation' "${BOOTSTRAP}"; then
  echo 'ERROR: activation bootstrap enables a forbidden generic capability' >&2
  exit 1
fi

if [[ ${LIVE} -eq 1 ]]; then
  echo '[check] live installed plugin identity and profile config'
  TARGET="${HERMES_PLUGINS_DIR:-${HOME}/.hermes/plugins}/${PLUGIN}"
  SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
  test -d "${TARGET}" && ! test -L "${TARGET}"
  diff -qr "${SOURCE}" "${TARGET}" >/dev/null
  PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor "${TARGET}" --ci

  get_config() { hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'; }
  [[ "$(get_config toolsets)" == '["factory-repository-readonly"]' ]]
  [[ "$(get_config fallback_providers)" == '[]' ]]
  [[ "$(get_config worktree)" == 'false' ]]
  [[ "$(get_config worktree_sync)" == 'false' ]]
  disabled="$(get_config agent.disabled_toolsets)"
  for denied in terminal file code_execution web browser image_gen delegation computer_use cronjob; do
    [[ "${disabled}" == *"${denied}"* ]] || { echo "ERROR: live profile missing disabled toolset ${denied}" >&2; exit 1; }
  done
  echo 'REPOSITORY_ANALYST_ISOLATION_LIVE_OK'
fi

echo 'REPOSITORY_ANALYST_ISOLATION_VERIFY_OK'
