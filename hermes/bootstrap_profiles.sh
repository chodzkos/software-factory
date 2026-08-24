#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
GROK_PROVIDER="${GROK_PROVIDER:-xai-oauth}"
GROK_MODEL="${GROK_MODEL:-grok-4.6}"
GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"
GEMINI_MODEL="${GEMINI_MODEL:-}"
DISPATCHER_PROFILE="${DISPATCHER_PROFILE:-default}"

profiles=(
  orchestrator
  architect
  coder
  quick-reviewer
  critic
  auditor-gpt
  auditor-grok
  release-manager
)

declare -A descriptions=(
  [orchestrator]="Decomposes goals into Kanban tasks, routes work, enforces gates; does not implement code."
  [architect]="Produces requirements, architecture and project plans; resolves boundaries and dependencies."
  [coder]="Implements one logical change in an isolated workspace and verifies it."
  [quick-reviewer]="Performs cheap first-pass review, CI triage and obvious defect detection."
  [critic]="Independent deep reviewer; challenges design, security, tests and verification evidence."
  [auditor-gpt]="Independent final auditor using the primary GPT model."
  [auditor-grok]="Independent final auditor using Grok; searches for missed blockers and security findings."
  [release-manager]="Evaluates release gate and refuses publication when evidence or required controls are missing."
)

if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: hermes not found in PATH" >&2
  exit 1
fi

profile_exists() {
  local name="$1"
  [[ -d "${PROFILE_ROOT}/${name}" ]]
}

expect_config() {
  local profile="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(hermes -p "${profile}" config get "${key}" 2>/dev/null | tail -n 1 | tr -d '\r')"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "ERROR: ${profile}:${key} expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

mkdir -p "${PROFILE_ROOT}"

echo "Model profilu źródłowego '${DISPATCHER_PROFILE}':"
hermes -p "${DISPATCHER_PROFILE}" config get model || true

echo
for profile in "${profiles[@]}"; do
  if profile_exists "${profile}"; then
    echo "[exists] ${profile}"
  else
    echo "[create] ${profile}"
    hermes profile create "${profile}" \
      --clone-from "${DISPATCHER_PROFILE}" \
      --description "${descriptions[$profile]}"
  fi

  soul_src="${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md"
  if [[ -f "${soul_src}" ]]; then
    install -m 0644 "${soul_src}" "${PROFILE_ROOT}/${profile}/SOUL.md"
  fi

  # Worker bez nadzoru ma zatrzymać się po powtarzających się pętlach bez postępu.
  hermes -p "${profile}" config set tool_loop_guardrails.hard_stop_enabled true
  hermes -p "${profile}" config set agent.tool_use_enforcement auto
done

# Role Groka są jawnie przypięte do providera i modelu.
for profile in critic auditor-grok; do
  hermes -p "${profile}" config set model.provider "${GROK_PROVIDER}"
  hermes -p "${profile}" config set model.default "${GROK_MODEL}"
done

# Izolacją tasków kodujących zarządza Kanban przez workspace=worktree:<repo>.
# Nie ustawiamy worktree=true w profilu codera, żeby nie tworzyć zagnieżdżonych worktree.

# Orchestrator koordynuje zadania i nie ma narzędzi implementacyjnych.
# Kanban pozostaje dostępny zarówno po dispatchu, jak i w sesji interaktywnej.
hermes -p orchestrator tools enable kanban
hermes -p orchestrator config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen"]'

# Routing Kanban zapisujemy w profilu, z którego uruchamiany jest gateway/dispatcher.
# Nie ustawiamy default_assignee=orchestrator: nieprzypisane taski mają pozostać widoczne
# do jawnego routingu zamiast tworzyć samonapędzającą się pętlę koordynacyjną.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.orchestrator_profile orchestrator

if [[ -n "${GEMINI_MODEL}" ]]; then
  hermes -p quick-reviewer config set model.provider "${GEMINI_PROVIDER}"
  hermes -p quick-reviewer config set model.default "${GEMINI_MODEL}"
  echo "[configured] quick-reviewer -> ${GEMINI_PROVIDER}/${GEMINI_MODEL}"
else
  echo "[warning] GEMINI_MODEL jest pusty; quick-reviewer nadal dziedziczy model profilu źródłowego."
  echo "          Skonfiguruj go później przez:"
  echo "          hermes -p quick-reviewer config set model.provider ${GEMINI_PROVIDER}"
  echo "          hermes -p quick-reviewer config set model.default <MODEL_ID>"
fi

# Walidacja krytycznych ustawień po bootstrapie.
expect_config critic model.provider "${GROK_PROVIDER}"
expect_config critic model.default "${GROK_MODEL}"
expect_config auditor-grok model.provider "${GROK_PROVIDER}"
expect_config auditor-grok model.default "${GROK_MODEL}"
expect_config orchestrator tool_loop_guardrails.hard_stop_enabled "true"
expect_config "${DISPATCHER_PROFILE}" kanban.orchestrator_profile "orchestrator"

echo
hermes profile list

echo
echo "Bootstrap profili zakończony."
echo "Uruchom 'hermes doctor' i sprawdź modele przez 'hermes -p <name> config get model'."
echo "Następnie zainicjalizuj/zweryfikuj Kanban przez 'hermes kanban init' i uruchom dokładnie jeden gateway/dispatcher."
