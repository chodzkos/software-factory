#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="repository-analyst"
PLUGIN="factory-repository-readonly"
INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
SOURCE="${ROOT_DIR}/hermes/plugins/${PLUGIN}"
DEST_ROOT="${HERMES_PLUGINS_DIR:-${HOME}/.hermes/plugins}"
TARGET="${DEST_ROOT}/${PLUGIN}"
EXPECTED_TOOLSETS='["factory-repository-readonly"]'
EXPECTED_DISABLED='["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'

command -v hermes >/dev/null 2>&1 || { echo "ERROR: hermes not found in PATH" >&2; exit 1; }
test -f "${INSTALLER}" || { echo "ERROR: missing plugin installer: ${INSTALLER}" >&2; exit 1; }
test -d "${HOME}/.hermes/profiles/${PROFILE}" || { echo "ERROR: profile ${PROFILE} does not exist; run bootstrap_profiles.sh first" >&2; exit 1; }

# Publication is fail-closed: manifest must be reviewed-ready/installable and all
# Git-blob pins/exact-tree checks must pass before the profile capability cutover.
bash "${INSTALLER}" --plugin "${PLUGIN}"

test -d "${TARGET}" && ! test -L "${TARGET}" || { echo "ERROR: installed plugin target missing or symlinked" >&2; exit 1; }
diff -qr "${SOURCE}" "${TARGET}" >/dev/null || { echo "ERROR: installed plugin differs from reviewed source" >&2; exit 1; }
PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor "${TARGET}" --ci

# Capability cutover. Dispatcher-owned workers receive Kanban lifecycle tools
# separately; the profile itself exposes only the reviewed repository surface.
hermes -p "${PROFILE}" config set toolsets "${EXPECTED_TOOLSETS}"
hermes -p "${PROFILE}" config set agent.disabled_toolsets "${EXPECTED_DISABLED}"
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

expect_list_exact toolsets factory-repository-readonly
expect_list_exact agent.disabled_toolsets terminal file code_execution web browser image_gen delegation computer_use cronjob
expect_scalar fallback_providers '[]'
expect_scalar worktree 'false'
expect_scalar worktree_sync 'false'

echo "REPOSITORY_ANALYST_ISOLATION_BOOTSTRAP_OK"
