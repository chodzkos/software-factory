#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
PRIMARY_PROFILE="${PRIMARY_PROFILE:-primary-gpt}"
PROFILE="runtime-controller"
PROFILE_DIR="${PROFILE_ROOT}/${PROFILE}"
SOUL_SRC="${ROOT_DIR}/hermes/profiles/${PROFILE}/SOUL.md"
WRAPPER_SRC="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
VALIDATOR_SRC="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
MODEL_ROUTING_SRC="${ROOT_DIR}/hermes/model_routing_policy.py"

if ! command -v hermes >/dev/null 2>&1; then echo "ERROR: hermes not found in PATH" >&2; exit 1; fi
for path in "${SOUL_SRC}" "${WRAPPER_SRC}" "${VALIDATOR_SRC}" "${MODEL_ROUTING_SRC}"; do
  test -f "${path}" || { echo "ERROR: missing ${path}" >&2; exit 1; }
done

primary_provider="$(hermes -p "${PRIMARY_PROFILE}" config get model.provider 2>/dev/null | tail -n 1 | tr -d '\r')"
primary_model="$(hermes -p "${PRIMARY_PROFILE}" config get model.default 2>/dev/null | tail -n 1 | tr -d '\r')"
if [[ -z "${primary_provider}" || -z "${primary_model}" ]]; then echo "ERROR: PRIMARY_PROFILE=${PRIMARY_PROFILE} has no usable model configuration" >&2; exit 1; fi

mkdir -p "${PROFILE_ROOT}"
if [[ ! -d "${PROFILE_DIR}" ]]; then
  hermes profile create "${PROFILE}" --clone-from "${PRIMARY_PROFILE}" --description "Executes the scoped Software Factory Kanban runtime-control surface."
fi

install -m 0644 "${SOUL_SRC}" "${PROFILE_DIR}/SOUL.md"
install -m 0755 "${WRAPPER_SRC}" "${PROFILE_DIR}/kanban_runtime_cli.sh"
install -m 0644 "${VALIDATOR_SRC}" "${PROFILE_DIR}/kanban_runtime_contract.py"
install -m 0644 "${MODEL_ROUTING_SRC}" "${PROFILE_DIR}/model_routing_policy.py"

hermes -p "${PROFILE}" config set model.provider "${primary_provider}"
hermes -p "${PROFILE}" config set model.default "${primary_model}"
hermes -p "${PROFILE}" config set fallback_providers '[]'
hermes -p "${PROFILE}" config set tool_loop_guardrails.hard_stop_enabled true
hermes -p "${PROFILE}" config set agent.tool_use_enforcement auto
hermes -p "${PROFILE}" config set toolsets '["hermes-cli","terminal"]'
hermes -p "${PROFILE}" config set agent.disabled_toolsets '["kanban","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'
hermes -p "${PROFILE}" config set worktree false
hermes -p "${PROFILE}" config set worktree_sync false

get_config() { hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tail -n 1 | tr -d '\r'; }
get_config_full() { hermes -p "${PROFILE}" config get "$1" 2>/dev/null | tr -d '\r'; }
expect() { local key="$1" expected="$2" actual; actual="$(get_config "${key}")"; [[ "${actual}" == "${expected}" ]] || { echo "ERROR: ${PROFILE}:${key} expected '${expected}', got '${actual}'" >&2; exit 1; }; }

expect model.provider "${primary_provider}"
expect model.default "${primary_model}"
expect fallback_providers '[]'
expect worktree 'false'
expect worktree_sync 'false'

toolsets_actual="$(get_config_full toolsets)"
for required_toolset in hermes-cli terminal; do
  [[ "${toolsets_actual}" == *"${required_toolset}"* ]] || { echo "ERROR: ${PROFILE}:toolsets missing '${required_toolset}', got '${toolsets_actual}'" >&2; exit 1; }
done
if [[ "${toolsets_actual}" == *"kanban"* ]]; then
  echo "ERROR: ${PROFILE}:toolsets must not expose direct kanban tools, got '${toolsets_actual}'" >&2
  exit 1
fi

test -x "${PROFILE_DIR}/kanban_runtime_cli.sh"
test -f "${PROFILE_DIR}/kanban_runtime_contract.py"
test -f "${PROFILE_DIR}/model_routing_policy.py"
echo "OK: ${PROFILE} bootstrapped with scoped runtime-control policy"
