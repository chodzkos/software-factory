#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
RELEASE_PROFILE_DIR="${HOME}/.hermes/factory-profiles/release-manager"
STANDARD_SRC="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"
PLUGIN_INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
ANALYST_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_repository_analyst_isolation.sh"
ANALYST_VERIFY="${ROOT_DIR}/hermes/verify_repository_analyst_isolation.sh"
CONFIG_KEY_REMOVER="${ROOT_DIR}/hermes/remove_profile_config_keys.py"
EXECUTION_GUARD="factory-execution-guards"
REPOSITORY_READONLY="factory-repository-readonly"
REVIEWER_CAPABILITY_VERIFY="${ROOT_DIR}/hermes/verify_reviewer_capabilities.py"
RELEASE_CAPABILITY_VERIFY="${ROOT_DIR}/hermes/verify_release_manager_capabilities.py"
RELEASE_START="${ROOT_DIR}/hermes/release_manager_start.py"
PRIMARY_PROFILE="${PRIMARY_PROFILE:-default}"
DISPATCHER_PROFILE="${DISPATCHER_PROFILE:-default}"
SECURITY_REVIEW_PROVIDER="openai-codex"
SECURITY_REVIEW_MODEL="gpt-5.6-sol"
GROK_PROVIDER="${GROK_PROVIDER:-xai-oauth}"
GROK_MODEL="${GROK_MODEL:-grok-4.6}"
GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash-lite}"
CLAUDE_SKILL="claude-code"
CLAUDE_NORMAL_MODEL="sonnet"
CLAUDE_DEEP_MODEL="opus"
ALLOW_NON_GPT_PRIMARY="${ALLOW_NON_GPT_PRIMARY:-0}"

profiles=(orchestrator architect architect-claude-opus repository-analyst task-decomposer coder coder-claude quick-reviewer reviewer-gpt reviewer-claude critic auditor-gpt auditor-grok docs routing-sink)

declare -A descriptions=(
  [orchestrator]="Coordinates Kanban routing, ownership and gates; delegates decomposition and implementation to specialist profiles."
  [architect]="Produces requirements, architecture and project plans; resolves boundaries and dependencies."
  [architect-claude-opus]="Optional Claude Code Opus escalation for hard architecture/reasoning; never a security reviewer."
  [repository-analyst]="Analyzes repository structure, contracts, dependencies, tests and risks before planning changes."
  [task-decomposer]="Turns accepted plans into small Kanban-ready tasks with explicit ownership and acceptance criteria."
  [coder]="Native OpenAI/GPT implementer for one logical change in an isolated workspace."
  [coder-claude]="Implementation coordinator that delegates the actual coding task to Claude Code CLI through the bundled claude-code skill."
  [quick-reviewer]="Performs cheap first-pass review, CI triage and obvious defect detection."
  [reviewer-gpt]="Pinned native OpenAI security/cross-vendor reviewer."
  [reviewer-claude]="Independent reviewer that delegates read-only review to Claude Code CLI; forbidden for security-sensitive review."
  [critic]="Independent deep reviewer using Grok; challenges design, tests and verification evidence."
  [auditor-gpt]="Independent final auditor using the primary GPT model."
  [auditor-grok]="Independent final auditor using Grok; searches for missed blockers and security findings."
  [docs]="Produces project and user documentation from accepted, verified changes."
  [release-manager]="Evaluates release gate and refuses publication when evidence or required controls are missing."
  [routing-sink]="Fail-closed sink for unroutable Kanban tasks; blocks the task and requests explicit reassignment."
)

command -v hermes >/dev/null 2>&1 || { echo "ERROR: hermes not found in PATH" >&2; exit 1; }
for required in "${STANDARD_SRC}" "${PLUGIN_INSTALLER}" "${ANALYST_BOOTSTRAP}" "${ANALYST_VERIFY}" "${CONFIG_KEY_REMOVER}" "${REVIEWER_CAPABILITY_VERIFY}" "${RELEASE_CAPABILITY_VERIFY}" "${RELEASE_START}"; do
  [[ -f "${required}" ]] || { echo "ERROR: missing ${required}" >&2; exit 1; }
done

profile_exists() { local name="$1"; [[ -d "${PROFILE_ROOT}/${name}" ]]; }
get_config() { local profile="$1" key="$2"; hermes -p "${profile}" config get "${key}" 2>/dev/null | tail -n 1 | tr -d '\r'; }
expect_config() { local profile="$1" key="$2" expected="$3" actual; actual="$(get_config "${profile}" "${key}")"; [[ "${actual}" == "${expected}" ]] || { echo "ERROR: ${profile}:${key} expected '${expected}', got '${actual}'" >&2; exit 1; }; }
remove_profile_keys() {
  local profile="$1"; shift
  PYTHONDONTWRITEBYTECODE=1 python3 "${CONFIG_KEY_REMOVER}" "${PROFILE_ROOT}/${profile}/config.yaml" "$@"
}
expect_profile_keys_absent() {
  local profile="$1"; shift
  PYTHONDONTWRITEBYTECODE=1 python3 - "${PROFILE_ROOT}/${profile}/config.yaml" "$@" <<'PY'
import pathlib,sys,yaml
path=pathlib.Path(sys.argv[1]); data=yaml.safe_load(path.read_text()) or {}
for dotted in sys.argv[2:]:
    cur=data; found=True
    for part in dotted.split('.'):
        if not isinstance(cur,dict) or part not in cur: found=False; break
        cur=cur[part]
    if found: raise SystemExit(f"ERROR: inherited config key remains: {dotted}")
PY
}
install_profile_soul() {
  local profile="$1"
  local soul_src="${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md"
  [[ -f "${soul_src}" ]] || return 0
  if [[ "${profile}" != "orchestrator" ]]; then install -m 0644 "${soul_src}" "${PROFILE_ROOT}/${profile}/SOUL.md"; return 0; fi
  local tmp; tmp="$(mktemp)"
  { cat "${soul_src}"; printf '\n\n---\n\n# Software Development Standard — wstrzyknięty kontekst runtime\n\n'; printf 'Kanoniczne źródło: `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`. Poniższa treść jest generowana przez bootstrap.\n\n'; cat "${STANDARD_SRC}"; } >"${tmp}"
  install -m 0644 "${tmp}" "${PROFILE_ROOT}/${profile}/SOUL.md"; rm -f "${tmp}"
}
install_execution_guard() {
  local profile="$1"
  local dest="${PROFILE_ROOT}/${profile}/plugins"
  local override_flag="--no-allow-tool-override"
  if [[ "${profile}" == "reviewer-gpt" || "${profile}" == "release-manager" ]]; then override_flag="--allow-tool-override"; fi
  HERMES_PLUGINS_DIR="${dest}" PYTHONDONTWRITEBYTECODE=1 bash "${PLUGIN_INSTALLER}" --plugin "${EXECUTION_GUARD}" --replace-reviewed
  hermes -p "${profile}" plugins enable "${EXECUTION_GUARD}" "${override_flag}"
  hermes -p "${profile}" plugins doctor "${EXECUTION_GUARD}" >/dev/null
  hermes -p "${profile}" config set tools.tool_search.enabled off
}
install_reviewer_readonly() {
  local profile="$1"
  local dest="${PROFILE_ROOT}/${profile}/plugins"
  HERMES_PLUGINS_DIR="${dest}" PYTHONDONTWRITEBYTECODE=1 bash "${PLUGIN_INSTALLER}" --plugin "${REPOSITORY_READONLY}" --replace-reviewed
  hermes -p "${profile}" plugins enable "${REPOSITORY_READONLY}" --no-allow-tool-override
  hermes -p "${profile}" plugins doctor "${REPOSITORY_READONLY}" >/dev/null
}
install_isolated_release_plugin() {
  local plugin="$1" override_flag="$2"
  HERMES_PLUGINS_DIR="${RELEASE_PROFILE_DIR}/plugins" PYTHONDONTWRITEBYTECODE=1 bash "${PLUGIN_INSTALLER}" --plugin "${plugin}" --replace-reviewed
  HERMES_HOME="${RELEASE_PROFILE_DIR}" HERMES_PROFILE=release-manager hermes plugins enable "${plugin}" "${override_flag}"
  HERMES_HOME="${RELEASE_PROFILE_DIR}" HERMES_PROFILE=release-manager hermes plugins doctor "${plugin}" >/dev/null
}

mkdir -p "${PROFILE_ROOT}"
if profile_exists release-manager; then
  echo "ERROR: legacy assignable ${PROFILE_ROOT}/release-manager must be removed by the owner before installing isolated release-manager" >&2
  exit 1
fi
primary_provider="$(get_config "${PRIMARY_PROFILE}" model.provider)"; primary_model="$(get_config "${PRIMARY_PROFILE}" model.default)"
[[ -n "${primary_provider}" && -n "${primary_model}" ]] || { echo "ERROR: PRIMARY_PROFILE='${PRIMARY_PROFILE}' has no usable model config" >&2; exit 1; }
if [[ "${ALLOW_NON_GPT_PRIMARY}" != "1" && ! "${primary_model}" =~ [Gg][Pp][Tt] ]]; then echo "ERROR: PRIMARY_PROFILE='${PRIMARY_PROFILE}' uses non-GPT model '${primary_model}'" >&2; exit 1; fi

echo "Security reviewer pin: ${SECURITY_REVIEW_PROVIDER}/${SECURITY_REVIEW_MODEL}"
echo "Primary profile: ${PRIMARY_PROFILE} -> ${primary_provider}/${primary_model}"
echo "Dispatcher profile: ${DISPATCHER_PROFILE}"
echo "Gemini policy: ${GEMINI_PROVIDER}/${GEMINI_MODEL}"
echo "Claude Code policy: skill=${CLAUDE_SKILL}, normal=${CLAUDE_NORMAL_MODEL}, deep=${CLAUDE_DEEP_MODEL}"
echo "Ox policy: disabled and removed from active Software Factory routing"; echo

for profile in "${profiles[@]}"; do
  if profile_exists "${profile}"; then echo "[exists] ${profile}"; else echo "[create] ${profile}"; hermes profile create "${profile}" --clone-from "${PRIMARY_PROFILE}" --description "${descriptions[$profile]}"; fi
  install_profile_soul "${profile}"
  # Hermes 0.20.4 merges the legacy fallback_model key into the effective
  # fallback chain even when fallback_providers is empty. Remove it physically.
  remove_profile_keys "${profile}" fallback_model model.fallback_model
  hermes -p "${profile}" config set tool_loop_guardrails.hard_stop_enabled true
  hermes -p "${profile}" config set agent.tool_use_enforcement auto
done

for profile in orchestrator architect repository-analyst coder auditor-gpt routing-sink; do
  hermes -p "${profile}" config set model.provider "${primary_provider}"
  hermes -p "${profile}" config set model.default "${primary_model}"
done
hermes -p reviewer-gpt config set model.provider "${SECURITY_REVIEW_PROVIDER}"
hermes -p reviewer-gpt config set model.default "${SECURITY_REVIEW_MODEL}"
remove_profile_keys reviewer-gpt plugins.enabled plugins.disabled plugins.entries
install_execution_guard reviewer-gpt
install_reviewer_readonly reviewer-gpt
hermes -p reviewer-gpt config set toolsets '["factory-repository-readonly","factory-execution-guards"]'
hermes -p reviewer-gpt config set platform_toolsets.cli '["factory-repository-readonly","factory-execution-guards","kanban","no_mcp"]'
hermes -p reviewer-gpt config set mcp_servers '{}'
hermes -p reviewer-gpt config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa","bfl","x_search","mcp"]'

# Release-manager jest izolowany poza ~/.hermes/profiles, więc generic Kanban
# dispatcher nie może uznać go za assignable profile ani ominąć launch verifiera.
mkdir -p "${RELEASE_PROFILE_DIR}"
install -m 0644 "${ROOT_DIR}/hermes/profiles/release-manager/SOUL.md" "${RELEASE_PROFILE_DIR}/SOUL.md"
install_isolated_release_plugin "${EXECUTION_GUARD}" --allow-tool-override
install_isolated_release_plugin "${REPOSITORY_READONLY}" --no-allow-tool-override
release_config() { HERMES_HOME="${RELEASE_PROFILE_DIR}" HERMES_PROFILE=release-manager hermes config "$@"; }
release_config set model.provider "${primary_provider}"
release_config set model.default "${primary_model}"
release_config set fallback_providers '[]'
release_config set toolsets '["factory-repository-readonly","factory-execution-guards"]'
release_config set platform_toolsets.cli '["factory-repository-readonly","factory-execution-guards","no_mcp"]'
release_config set mcp_servers '{}'
release_config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa","bfl","x_search","mcp"]'
release_config set tools.tool_search.enabled off
release_config set --force factory.execution_backend read-only-release-decision

for profile in coder-claude reviewer-claude architect-claude-opus; do
  hermes -p "${profile}" config set model.provider "${primary_provider}"
  hermes -p "${profile}" config set model.default "${primary_model}"
  hermes -p "${profile}" config set --force factory.execution_backend claude-code
  hermes -p "${profile}" config set fallback_providers '[]'
  install_execution_guard "${profile}"
done
hermes -p coder-claude config set --force factory.claude_model_class sonnet
hermes -p reviewer-claude config set --force factory.claude_model_class sonnet
hermes -p architect-claude-opus config set --force factory.claude_model_class opus
hermes -p coder config set --force factory.execution_backend native-openai
hermes -p reviewer-gpt config set --force factory.execution_backend native-openai

if profile_exists auditor-ox; then
  echo "[legacy] disabling auditor-ox inference"
  # Remove inherited dormant capability/fallback state rather than merely hiding it
  # behind disabled toolsets/provider values.
  remove_profile_keys auditor-ox fallback_model model.fallback_model mcp_servers API_SERVER_ENABLED API_SERVER_KEY api_server_enabled api_server_key
  hermes -p auditor-ox config set model.provider disabled-legacy
  hermes -p auditor-ox config set model.default disabled-legacy
  hermes -p auditor-ox config set --force factory.execution_backend disabled-legacy
  hermes -p auditor-ox config set fallback_providers '[]'
  hermes -p auditor-ox config set toolsets '[]'
  hermes -p auditor-ox config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa","kanban"]'
  hermes -p auditor-ox config set tools.tool_search.enabled off
fi

for profile in critic auditor-grok; do hermes -p "${profile}" config set model.provider "${GROK_PROVIDER}"; hermes -p "${profile}" config set model.default "${GROK_MODEL}"; done
for profile in task-decomposer quick-reviewer docs; do hermes -p "${profile}" config set model.provider "${GEMINI_PROVIDER}"; hermes -p "${profile}" config set model.default "${GEMINI_MODEL}"; done
for profile in "${profiles[@]}"; do hermes -p "${profile}" config set fallback_providers '[]'; done

hermes -p coder config set worktree false; hermes -p coder config set worktree_sync false
hermes -p coder-claude config set worktree false; hermes -p coder-claude config set worktree_sync false
hermes -p orchestrator config set toolsets '["hermes-cli","kanban"]'
hermes -p orchestrator config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'
hermes -p routing-sink config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'
hermes -p "${DISPATCHER_PROFILE}" config set kanban.orchestrator_profile orchestrator
hermes -p "${DISPATCHER_PROFILE}" config set kanban.default_assignee routing-sink
# Hermes 0.20.4 auto-claims review rows by default. Disable that global lane so
# the provenance-bound runtime-controller handoff gate always runs before the
# exact task is dispatched by dispatch-review.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.review_dispatch false

expect_config reviewer-gpt model.provider "${SECURITY_REVIEW_PROVIDER}"
expect_config reviewer-gpt model.default "${SECURITY_REVIEW_MODEL}"
expect_config reviewer-gpt fallback_providers '[]'
expect_profile_keys_absent reviewer-gpt fallback_model model.fallback_model
expect_config reviewer-gpt factory.execution_backend native-openai
release_backend="$(HERMES_HOME="${RELEASE_PROFILE_DIR}" HERMES_PROFILE=release-manager hermes config get factory.execution_backend 2>/dev/null | tail -n 1 | tr -d '\r')"
[[ "${release_backend}" == read-only-release-decision ]] || { echo "ERROR: isolated release-manager backend mismatch: ${release_backend}" >&2; exit 1; }
expect_config coder factory.execution_backend native-openai
expect_config coder-claude factory.execution_backend claude-code; expect_config coder-claude factory.claude_model_class sonnet
expect_config reviewer-claude factory.execution_backend claude-code; expect_config reviewer-claude factory.claude_model_class sonnet
expect_config architect-claude-opus factory.execution_backend claude-code; expect_config architect-claude-opus factory.claude_model_class opus
expect_config critic model.provider "${GROK_PROVIDER}"; expect_config critic model.default "${GROK_MODEL}"
expect_config task-decomposer model.default "${GEMINI_MODEL}"; expect_config quick-reviewer model.default "${GEMINI_MODEL}"; expect_config docs model.default "${GEMINI_MODEL}"; expect_config repository-analyst model.default "${primary_model}"
expect_config coder worktree "false"; expect_config coder worktree_sync "false"; expect_config coder-claude worktree "false"; expect_config coder-claude worktree_sync "false"
expect_config "${DISPATCHER_PROFILE}" kanban.orchestrator_profile orchestrator; expect_config "${DISPATCHER_PROFILE}" kanban.default_assignee routing-sink
expect_config "${DISPATCHER_PROFILE}" kanban.review_dispatch false
PYTHONDONTWRITEBYTECODE=1 python3 "${REVIEWER_CAPABILITY_VERIFY}" "${PROFILE_ROOT}/reviewer-gpt" --workspace "${ROOT_DIR}" --task-id t_bootstrap_capability --board isolated --run-id 1
PYTHONDONTWRITEBYTECODE=1 python3 "${RELEASE_CAPABILITY_VERIFY}" "${RELEASE_PROFILE_DIR}" --workspace "${ROOT_DIR}" --task-id t_bootstrap_release --board isolated --run-id 1
if profile_exists auditor-ox; then
  expect_config auditor-ox model.provider disabled-legacy
  expect_config auditor-ox model.default disabled-legacy
  expect_config auditor-ox factory.execution_backend disabled-legacy
  expect_config auditor-ox fallback_providers '[]'
  expect_profile_keys_absent auditor-ox fallback_model model.fallback_model mcp_servers API_SERVER_ENABLED API_SERVER_KEY api_server_enabled api_server_key
fi

[[ -f "${PROFILE_ROOT}/orchestrator/SOUL.md" ]] && grep -Fq '# Software Development Standard — wstrzyknięty kontekst runtime' "${PROFILE_ROOT}/orchestrator/SOUL.md" || { echo "ERROR: orchestrator did not receive injected Standard" >&2; exit 1; }

PYTHONDONTWRITEBYTECODE=1 bash "${ANALYST_BOOTSTRAP}"
PYTHONDONTWRITEBYTECODE=1 bash "${ANALYST_VERIFY}" --live

echo; hermes profile list; echo; echo "Bootstrap profili zakończony."
