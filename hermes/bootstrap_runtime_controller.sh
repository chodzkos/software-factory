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
PLUGIN_INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
EXECUTION_GUARD="factory-execution-guards"

command -v hermes >/dev/null 2>&1 || { echo "ERROR: hermes not found in PATH" >&2; exit 1; }
for path in "${SOUL_SRC}" "${WRAPPER_SRC}" "${VALIDATOR_SRC}" "${MODEL_ROUTING_SRC}" "${PLUGIN_INSTALLER}"; do test -f "${path}" || { echo "ERROR: missing ${path}" >&2; exit 1; }; done

primary_provider="$(hermes -p "${PRIMARY_PROFILE}" config get model.provider 2>/dev/null | tail -n 1 | tr -d '\r')"
primary_model="$(hermes -p "${PRIMARY_PROFILE}" config get model.default 2>/dev/null | tail -n 1 | tr -d '\r')"
[[ -n "${primary_provider}" && -n "${primary_model}" ]] || { echo "ERROR: PRIMARY_PROFILE=${PRIMARY_PROFILE} has no usable model configuration" >&2; exit 1; }

mkdir -p "${PROFILE_ROOT}"
if [[ ! -d "${PROFILE_DIR}" ]]; then hermes profile create "${PROFILE}" --clone-from "${PRIMARY_PROFILE}" --description "Executes the mechanically guarded Software Factory Kanban runtime-control surface."; fi

install -m 0644 "${SOUL_SRC}" "${PROFILE_DIR}/SOUL.md"
install -m 0755 "${WRAPPER_SRC}" "${PROFILE_DIR}/kanban_runtime_cli.sh"
install -m 0644 "${VALIDATOR_SRC}" "${PROFILE_DIR}/kanban_runtime_contract.py"
install -m 0644 "${MODEL_ROUTING_SRC}" "${PROFILE_DIR}/model_routing_policy.py"
HERMES_PLUGINS_DIR="${PROFILE_DIR}/plugins" PYTHONDONTWRITEBYTECODE=1 bash "${PLUGIN_INSTALLER}" --plugin "${EXECUTION_GUARD}"
hermes -p "${PROFILE}" plugins enable "${EXECUTION_GUARD}" --no-allow-tool-override
hermes -p "${PROFILE}" plugins doctor "${EXECUTION_GUARD}" >/dev/null

hermes -p "${PROFILE}" config set model.provider "${primary_provider}"
hermes -p "${PROFILE}" config set model.default "${primary_model}"
hermes -p "${PROFILE}" config set fallback_providers '[]'
hermes -p "${PROFILE}" config set tool_loop_guardrails.hard_stop_enabled true
hermes -p "${PROFILE}" config set agent.tool_use_enforcement auto
hermes -p "${PROFILE}" config set toolsets '["terminal"]'
hermes -p "${PROFILE}" config set agent.disabled_toolsets '["kanban","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa"]'
hermes -p "${PROFILE}" config set tools.tool_search.enabled off
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
expect tools.tool_search.enabled 'off'

toolsets_actual="$(get_config_full toolsets)"
[[ "${toolsets_actual}" == *"terminal"* ]] || { echo "ERROR: ${PROFILE}:toolsets missing terminal, got '${toolsets_actual}'" >&2; exit 1; }
for forbidden in kanban hermes-cli file code_execution; do
  [[ "${toolsets_actual}" != *"${forbidden}"* ]] || { echo "ERROR: ${PROFILE}:toolsets unexpectedly exposes '${forbidden}', got '${toolsets_actual}'" >&2; exit 1; }
done

test -x "${PROFILE_DIR}/kanban_runtime_cli.sh"
test -f "${PROFILE_DIR}/kanban_runtime_contract.py"
test -f "${PROFILE_DIR}/model_routing_policy.py"
test -f "${PROFILE_DIR}/plugins/${EXECUTION_GUARD}/guard.py"
echo "OK: ${PROFILE} bootstrapped with mechanically guarded runtime-control policy"
