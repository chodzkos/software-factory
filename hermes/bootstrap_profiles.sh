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
GEMINI_MODEL="${GEMINI_MODEL:-}"
ALLOW_NON_GPT_PRIMARY="${ALLOW_NON_GPT_PRIMARY:-0}"

profiles=(
  orchestrator
  architect
  coder
  quick-reviewer
  critic
  auditor-gpt
  auditor-grok
  release-manager
  routing-sink
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

  # Orchestrator nie ma narzędzi plikowych, więc kanoniczny Standard jest wstrzykiwany do jego kontekstu runtime.
  # Kopia poniżej nie staje się drugim source of truth; przy każdym bootstrapie jest odtwarzana z pliku kanonicznego.
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

  # Worker bez nadzoru ma zatrzymać się po powtarzających się pętlach bez postępu.
  hermes -p "${profile}" config set tool_loop_guardrails.hard_stop_enabled true
  hermes -p "${profile}" config set agent.tool_use_enforcement auto
done

# Role korzystające z głównego GPT są deterministycznie synchronizowane z PRIMARY_PROFILE.
for profile in orchestrator architect coder auditor-gpt release-manager routing-sink; do
  hermes -p "${profile}" config set model.provider "${primary_provider}"
  hermes -p "${profile}" config set model.default "${primary_model}"
done

# Role Groka są jawnie przypięte do providera i modelu.
for profile in critic auditor-grok; do
  hermes -p "${profile}" config set model.provider "${GROK_PROVIDER}"
  hermes -p "${profile}" config set model.default "${GROK_MODEL}"
done

# Izolacją tasków kodujących zarządza Kanban przez workspace=worktree:<repo>.
# Nie ustawiamy worktree=true w profilu codera, żeby nie tworzyć zagnieżdżonych worktree.

# Orchestrator koordynuje wyłącznie przez Kanban. Runtime gate Hermesa czyta top-level `toolsets`,
# dlatego ustawiamy go jawnie zamiast polegać wyłącznie na platformowym `hermes tools enable kanban`.
hermes -p orchestrator config set toolsets '["hermes-cli","kanban"]'
hermes -p orchestrator config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation"]'

# Routing-sink ma być kontrolowanym końcem dla kart, których decomposer nie umie przypisać.
# Po dispatchu Hermes doda mu lifecycle Kanban, ale nie dostanie narzędzi implementacyjnych.
hermes -p routing-sink config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen","delegation"]'

# Routing Kanban zapisujemy w profilu, z którego uruchamiany jest gateway/dispatcher.
# Nie używamy pustego default_assignee: Hermes traktuje pustą wartość jako fallback do aktywnego profilu.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.orchestrator_profile orchestrator
hermes -p "${DISPATCHER_PROFILE}" config set kanban.default_assignee routing-sink

if [[ -n "${GEMINI_MODEL}" ]]; then
  hermes -p quick-reviewer config set model.provider "${GEMINI_PROVIDER}"
  hermes -p quick-reviewer config set model.default "${GEMINI_MODEL}"
  echo "[configured] quick-reviewer -> ${GEMINI_PROVIDER}/${GEMINI_MODEL}"
else
  hermes -p quick-reviewer config set model.provider "${primary_provider}"
  hermes -p quick-reviewer config set model.default "${primary_model}"
  echo "[warning] GEMINI_MODEL jest pusty; quick-reviewer używa tymczasowo ${primary_provider}/${primary_model}."
fi

# Walidacja krytycznych ustawień po bootstrapie.
expect_config auditor-gpt model.provider "${primary_provider}"
expect_config auditor-gpt model.default "${primary_model}"
expect_config critic model.provider "${GROK_PROVIDER}"
expect_config critic model.default "${GROK_MODEL}"
expect_config auditor-grok model.provider "${GROK_PROVIDER}"
expect_config auditor-grok model.default "${GROK_MODEL}"
expect_config orchestrator tool_loop_guardrails.hard_stop_enabled "true"
expect_config routing-sink tool_loop_guardrails.hard_stop_enabled "true"
expect_config "${DISPATCHER_PROFILE}" kanban.orchestrator_profile "orchestrator"
expect_config "${DISPATCHER_PROFILE}" kanban.default_assignee "routing-sink"

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
echo "Uruchom 'hermes doctor' i sprawdź modele przez 'hermes -p <name> config get model'."
echo "Następnie zainicjalizuj/zweryfikuj Kanban przez 'hermes kanban init' i uruchom dokładnie jeden gateway/dispatcher."
