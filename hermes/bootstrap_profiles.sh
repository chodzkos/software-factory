#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
STANDARD_SRC="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"
PRIMARY_PROFILE="${PRIMARY_PROFILE:-default}"
DISPATCHER_PROFILE="${DISPATCHER_PROFILE:-default}"
GROK_PROVIDER="${GROK_PROVIDER:-xai-oauth}"
GROK_MODEL="${GROK_MODEL:-grok-4.6}"
GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash-lite}"
CLAUDE_SKILL="${CLAUDE_SKILL:-claude-code}"
CLAUDE_NORMAL_MODEL="${CLAUDE_NORMAL_MODEL:-sonnet}"
CLAUDE_DEEP_MODEL="${CLAUDE_DEEP_MODEL:-opus}"
ALLOW_NON_GPT_PRIMARY="${ALLOW_NON_GPT_PRIMARY:-0}"

profiles=(
  orchestrator
  architect
  architect-claude-opus
  repository-analyst
  task-decomposer
  coder
  coder-claude
  quick-reviewer
  reviewer-gpt
  reviewer-claude
  critic
  auditor-gpt
  auditor-grok
  docs
  release-manager
  routing-sink
)

declare -A descriptions=(
  [orchestrator]="Coordinates Kanban routing, ownership and gates; delegates decomposition and implementation to specialist profiles."
  [architect]="Produces requirements, architecture and project plans; resolves boundaries and dependencies."
  [architect-claude-opus]="Optional Claude Code Opus escalation for hard architecture/reasoning; never a security reviewer."
  [repository-analyst]="Analyzes repository structure, contracts, dependencies, tests and risks before planning changes."
  [task-decomposer]="Turns accepted plans into small Kanban-ready tasks with explicit ownership and acceptance criteria."
  [coder]="Native OpenAI/GPT implementer for one logical change in an isolated workspace."
  [coder-claude]="Implementation coordinator that delegates the actual coding task to Claude Code CLI through the bundled claude-code skill."
  [quick-reviewer]="Performs cheap first-pass review, CI triage and obvious defect detection."
  [reviewer-gpt]="Independent native OpenAI reviewer and the only deep security-review profile."
  [reviewer-claude]="Independent reviewer that delegates read-only review to Claude Code CLI; forbidden for security-sensitive review."
  [critic]="Independent deep reviewer using Grok; challenges design, tests and verification evidence."
  [auditor-gpt]="Independent final auditor using the primary GPT model."
  [auditor-grok]="Independent final auditor using Grok; searches for missed blockers and security findings."
  [docs]="Produces project and user documentation from accepted, verified changes."
  [release-manager]="Evaluates release gate and refuses publication when evidence or required controls are missing."
  [routing-sink]="Fail-closed sink for unroutable Kanban tasks; blocks the task and requests explicit reassignment."
)

if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: hermes not found in PATH" >&2
  exit 1
fi

if [[ ! -f "${STANDARD_SRC}" ]]; then
  echo "ERROR: brak kanonicznego standardu ${STANDARD_SRC}" >&2
  exit 1
fi

profile_exists() {
  local name="$1"
  [[ -d "${PROFILE_ROOT}/${name}" ]]
}

get_config() {
  local profile="$1"
  local key="$2"
  hermes -p "${profile}" config get "${key}" 2>/dev/null | tail -n 1 | tr -d '\r'
}

expect_config() {
  local profile="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(get_config "${profile}" "${key}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "ERROR: ${profile}:${key} expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

install_profile_soul() {
  local profile="$1"
  local soul_src="${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md"

  [[ -f "${soul_src}" ]] || return 0

  if [[ "${profile}" != "orchestrator" ]]; then
    install -m 0644 "${soul_src}" "${PROFILE_ROOT}/${profile}/SOUL.md"
    return 0
  fi

  local tmp
  tmp="$(mktemp)"
  {
    cat "${soul_src}"
    printf '\n\n---\n\n# Software Development Standard — wstrzyknięty kontekst runtime\n\n'
    printf 'Kanoniczne źródło: `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`. Poniższa treść jest generowana przez bootstrap.\n\n'
    cat "${STANDARD_SRC}"
  } >"${tmp}"
  install -m 0644 "${tmp}" "${PROFILE_ROOT}/${profile}/SOUL.md"
  rm -f "${tmp}"
}

mkdir -p "${PROFILE_ROOT}"

primary_provider="$(get_config "${PRIMARY_PROFILE}" model.provider)"
primary_model="$(get_config "${PRIMARY_PROFILE}" model.default)"

if [[ -z "${primary_model}" ]]; then
  echo "ERROR: PRIMARY_PROFILE='${PRIMARY_PROFILE}' nie ma ustawionego model.default" >&2
  exit 1
fi

if [[ "${ALLOW_NON_GPT_PRIMARY}" != "1" && ! "${primary_model}" =~ [Gg][Pp][Tt] ]]; then
  echo "ERROR: PRIMARY_PROFILE='${PRIMARY_PROFILE}' używa modelu '${primary_model}', który nie wygląda na GPT." >&2
  echo "       Najpierw skonfiguruj profil z głównym modelem GPT albo ustaw PRIMARY_PROFILE=<profil-gpt>." >&2
  echo "       Tylko świadomy wyjątek: ALLOW_NON_GPT_PRIMARY=1." >&2
  exit 1
fi

echo "Primary profile: ${PRIMARY_PROFILE} -> ${primary_provider}/${primary_model}"
echo "Dispatcher profile: ${DISPATCHER_PROFILE}"
echo "Gemini policy: ${GEMINI_PROVIDER}/${GEMINI_MODEL}"
echo "Claude Code policy: skill=${CLAUDE_SKILL}, normal=${CLAUDE_NORMAL_MODEL}, deep=${CLAUDE_DEEP_MODEL}"
echo "Ox policy: disabled and removed from active Software Factory routing"
echo

for profile in "${profiles[@]}"; do
  if profile_exists "${profile}"; then
    echo "[exists] ${profile}"
  else
    echo "[create] ${profile}"
    hermes profile create "${profile}" \
      --clone-from "${PRIMARY_PROFILE}" \
      --description "${descriptions[$profile]}"
  fi

  install_profile_soul "${profile}"
  hermes -p "${profile}" config set tool_loop_guardrails.hard_stop_enabled true
  hermes -p "${profile}" config set agent.tool_use_enforcement auto
done

# Natywne role GPT są deterministycznie synchronizowane z PRIMARY_PROFILE.
for profile in orchestrator architect repository-analyst coder reviewer-gpt auditor-gpt release-manager routing-sink; do
  hermes -p "${profile}" config set model.provider "${primary_provider}"
  hermes -p "${profile}" config set model.default "${primary_model}"
done

# Profile Claude są koordynatorami Hermesa na głównym GPT, ale właściwa praca
# musi być wykonana przez bundlowany skill claude-code / Claude Code CLI.
for profile in coder-claude reviewer-claude architect-claude-opus; do
  hermes -p "${profile}" config set model.provider "${primary_provider}"
  hermes -p "${profile}" config set model.default "${primary_model}"
  hermes -p "${profile}" config set factory.execution_backend "${CLAUDE_SKILL}"
done
hermes -p coder-claude config set factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"
hermes -p reviewer-claude config set factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"
hermes -p architect-claude-opus config set factory.claude_model_class "${CLAUDE_DEEP_MODEL}"

# Natywne profile OpenAI mają jawny backend dla runtime routing evidence.
hermes -p coder config set factory.execution_backend native-openai
hermes -p reviewer-gpt config set factory.execution_backend native-openai

# Legacy auditor-ox może istnieć lokalnie po starszej konfiguracji. Nie kasujemy
# automatycznie katalogu użytkownika, ale neutralizujemy profil fail-closed.
if profile_exists auditor-ox; then
  echo "[legacy] quarantining auditor-ox"
  hermes -p auditor-ox config set factory.execution_backend disabled-legacy
  hermes -p auditor-ox config set fallback_providers '[]'
  hermes -p auditor-ox config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob","skills","vision","todo","memory","session_search","clarify","messaging","tts","moa"]'
fi

# Role Groka są jawnie przypięte do providera i modelu.
for profile in critic auditor-grok; do
  hermes -p "${profile}" config set model.provider "${GROK_PROVIDER}"
  hermes -p "${profile}" config set model.default "${GROK_MODEL}"
done

# Częste, tańsze role pozostają na Gemini.
for profile in task-decomposer quick-reviewer docs; do
  hermes -p "${profile}" config set model.provider "${GEMINI_PROVIDER}"
  hermes -p "${profile}" config set model.default "${GEMINI_MODEL}"
done

# Profile factory nie dziedziczą ukrytych fallbacków modelu z PRIMARY_PROFILE.
for profile in "${profiles[@]}"; do
  hermes -p "${profile}" config set fallback_providers '[]'
done

# Izolacją tasków kodujących zarządza Kanban przez workspace=worktree:<repo>.
hermes -p coder config set worktree false
hermes -p coder config set worktree_sync false
hermes -p coder-claude config set worktree false
hermes -p coder-claude config set worktree_sync false

# Orchestrator koordynuje wyłącznie przez Kanban.
hermes -p orchestrator config set toolsets '["hermes-cli","kanban"]'
hermes -p orchestrator config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'

# Routing-sink ma być kontrolowanym końcem dla kart, których decomposer nie umie przypisać.
hermes -p routing-sink config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'

# Routing Kanban zapisujemy w profilu gateway/dispatcher.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.orchestrator_profile orchestrator
hermes -p "${DISPATCHER_PROFILE}" config set kanban.default_assignee routing-sink

# Walidacja krytycznych ustawień po bootstrapie.
expect_config auditor-gpt model.provider "${primary_provider}"
expect_config auditor-gpt model.default "${primary_model}"
expect_config reviewer-gpt model.provider "${primary_provider}"
expect_config reviewer-gpt model.default "${primary_model}"
expect_config reviewer-gpt factory.execution_backend native-openai
expect_config coder factory.execution_backend native-openai
expect_config coder-claude factory.execution_backend "${CLAUDE_SKILL}"
expect_config coder-claude factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"
expect_config reviewer-claude factory.execution_backend "${CLAUDE_SKILL}"
expect_config reviewer-claude factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"
expect_config architect-claude-opus factory.execution_backend "${CLAUDE_SKILL}"
expect_config architect-claude-opus factory.claude_model_class "${CLAUDE_DEEP_MODEL}"
if profile_exists auditor-ox; then
  expect_config auditor-ox factory.execution_backend disabled-legacy
  expect_config auditor-ox fallback_providers '[]'
fi
expect_config critic model.provider "${GROK_PROVIDER}"
expect_config critic model.default "${GROK_MODEL}"
expect_config auditor-grok model.provider "${GROK_PROVIDER}"
expect_config auditor-grok model.default "${GROK_MODEL}"
expect_config task-decomposer model.provider "${GEMINI_PROVIDER}"
expect_config task-decomposer model.default "${GEMINI_MODEL}"
expect_config quick-reviewer model.provider "${GEMINI_PROVIDER}"
expect_config quick-reviewer model.default "${GEMINI_MODEL}"
expect_config docs model.provider "${GEMINI_PROVIDER}"
expect_config docs model.default "${GEMINI_MODEL}"
expect_config repository-analyst model.provider "${primary_provider}"
expect_config repository-analyst model.default "${primary_model}"
expect_config orchestrator tool_loop_guardrails.hard_stop_enabled "true"
expect_config routing-sink tool_loop_guardrails.hard_stop_enabled "true"
expect_config coder worktree "false"
expect_config coder worktree_sync "false"
expect_config coder-claude worktree "false"
expect_config coder-claude worktree_sync "false"
expect_config "${DISPATCHER_PROFILE}" kanban.orchestrator_profile "orchestrator"
expect_config "${DISPATCHER_PROFILE}" kanban.default_assignee routing-sink

if ! grep -Fq '# Software Development Standard — wstrzyknięty kontekst runtime' "${PROFILE_ROOT}/orchestrator/SOUL.md"; then
  echo "ERROR: orchestrator nie otrzymał wstrzykniętego Standardu" >&2
  exit 1
fi

if ! grep -Fq '# Software Development Standard' "${PROFILE_ROOT}/orchestrator/SOUL.md"; then
  echo "ERROR: w runtime SOUL orchestratora brakuje treści Standardu" >&2
  exit 1
fi

echo
hermes profile list

echo
echo "Bootstrap profili zakończony."
echo "Uruchom 'hermes doctor' i sprawdź modele/backend przez 'hermes -p <name> config get model', 'factory.execution_backend' i 'factory.claude_model_class'."
echo "Następnie zainicjalizuj/zweryfikuj Kanban przez 'hermes kanban init' i uruchom dokładnie jeden gateway/dispatcher."
