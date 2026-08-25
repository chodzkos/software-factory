#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
PRIMARY_PROFILE="${PRIMARY_PROFILE:-primary-gpt}"
PROFILE="runtime-controller"
SOUL_SRC="${ROOT_DIR}/hermes/profiles/${PROFILE}/SOUL.md"
WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"

if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: hermes not found in PATH" >&2
  exit 1
fi

test -f "${SOUL_SRC}" || { echo "ERROR: missing ${SOUL_SRC}" >&2; exit 1; }
test -f "${WRAPPER}" || { echo "ERROR: missing ${WRAPPER}" >&2; exit 1; }

primary_provider="$(hermes -p "${PRIMARY_PROFILE}" config get model.provider 2>/dev/null | tail -n 1 | tr -d '\r')"
primary_model="$(hermes -p "${PRIMARY_PROFILE}" config get model.default 2>/dev/null | tail -n 1 | tr -d '\r')"

if [[ -z "${primary_provider}" || -z "${primary_model}" ]]; then
  echo "ERROR: PRIMARY_PROFILE=${PRIMARY_PROFILE} has no usable model configuration" >&2
  exit 1
fi

mkdir -p "${PROFILE_ROOT}"
if [[ ! -d "${PROFILE_ROOT}/${PROFILE}" ]]; then
  hermes profile create "${PROFILE}" \
    --clone-from "${PRIMARY_PROFILE}" \
    --description "Executes the scoped Software Factory Kanban runtime-control surface."
fi

install -m 0644 "${SOUL_SRC}" "${PROFILE_ROOT}/${PROFILE}/SOUL.md"
chmod 0755 "${WRAPPER}"

hermes -p "${PROFILE}" config set model.provider "${primary_provider}"
hermes -p "${PROFILE}" config set model.default "${primary_model}"
hermes -p "${PROFILE}" config set fallback_providers '[]'
hermes -p "${PROFILE}" config set tool_loop_guardrails.hard_stop_enabled true
hermes -p "${PROFILE}" config set agent.tool_use_enforcement auto
hermes -p "${PROFILE}" config set toolsets '["hermes-cli","kanban","terminal"]'
hermes -p "${PROFILE}" config set agent.disabled_toolsets '["file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'
hermes -p "${PROFILE}" config set worktree false
hermes -p "${PROFILE}" config set worktree_sync false

get_config() {
  hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'
}

expect() {
  local key="$1" expected="$2" actual
  actual="$(get_config "${key}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "ERROR: ${PROFILE}:${key} expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

expect model.provider "${primary_provider}"
expect model.default "${primary_model}"
expect fallback_providers '[]'
expect toolsets '["hermes-cli", "kanban", "terminal"]'
expect worktree 'false'
expect worktree_sync 'false'

echo "OK: ${PROFILE} bootstrapped with scoped runtime-control policy"
