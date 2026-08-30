#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_profiles.sh"
STANDARD="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"
RUNTIME_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_runtime_controller.sh"
RUNTIME_SOUL="${ROOT_DIR}/hermes/profiles/runtime-controller/SOUL.md"
MODEL_POLICY="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"

echo "[check] bash syntax"
bash -n "${BOOTSTRAP}"
bash -n "${RUNTIME_BOOTSTRAP}"

echo "[check] canonical standard exists"
test -f "${STANDARD}"
test -f "${MODEL_POLICY}"
test -f "${MODEL_ROUTING}"

echo "[check] correct hard-stop key"
grep -Fq 'tool_loop_guardrails.hard_stop_enabled true' "${BOOTSTRAP}"
if grep -Fq 'agent.hard_stop_enabled' "${BOOTSTRAP}"; then
  echo "ERROR: znaleziono nieobsługiwany klucz agent.hard_stop_enabled" >&2
  exit 1
fi

echo "[check] coder worktree forced off"
for profile in coder coder-claude; do
  grep -Fq "hermes -p ${profile} config set worktree false" "${BOOTSTRAP}"
  grep -Fq "hermes -p ${profile} config set worktree_sync false" "${BOOTSTRAP}"
done
if grep -Eq '^[[:space:]]*hermes[[:space:]].*-p[[:space:]]+(coder|coder-claude)[[:space:]].*config[[:space:]]+set[[:space:]]+worktree(_sync)?[[:space:]]+true([[:space:]]|$)' "${BOOTSTRAP}"; then
  echo "ERROR: coder profiles nie mogą mieć worktree/worktree_sync=true przy Kanban worktree" >&2
  exit 1
fi

echo "[check] directory-based profile detection"
grep -Fq '[[ -d "${PROFILE_ROOT}/${name}" ]]' "${BOOTSTRAP}"

echo "[check] model policy defaults"
grep -Fq 'GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"' "${BOOTSTRAP}"
grep -Fq 'GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash-lite}"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_SKILL="${CLAUDE_SKILL:-claude-code}"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_NORMAL_MODEL="${CLAUDE_NORMAL_MODEL:-sonnet}"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_DEEP_MODEL="${CLAUDE_DEEP_MODEL:-opus}"' "${BOOTSTRAP}"
if grep -Eqi 'OX_PROVIDER|OX_MODEL|stealth/ox-alpha' "${BOOTSTRAP}"; then
  echo "ERROR: bootstrap nadal zawiera aktywny routing Ox" >&2
  exit 1
fi

echo "[check] specialized role routing"
grep -Fq 'architect-claude-opus' "${BOOTSTRAP}"
grep -Fq 'coder-claude' "${BOOTSTRAP}"
grep -Fq 'reviewer-gpt' "${BOOTSTRAP}"
grep -Fq 'reviewer-claude' "${BOOTSTRAP}"
grep -Fq 'for profile in orchestrator architect repository-analyst coder reviewer-gpt auditor-gpt release-manager routing-sink; do' "${BOOTSTRAP}"
grep -Fq 'for profile in coder-claude reviewer-claude architect-claude-opus; do' "${BOOTSTRAP}"
grep -Fq 'config set factory.execution_backend "${CLAUDE_SKILL}"' "${BOOTSTRAP}"
grep -Fq 'coder-claude config set factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'reviewer-claude config set factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'architect-claude-opus config set factory.claude_model_class "${CLAUDE_DEEP_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'coder config set factory.execution_backend native-openai' "${BOOTSTRAP}"
grep -Fq 'reviewer-gpt config set factory.execution_backend native-openai' "${BOOTSTRAP}"

echo "[check] legacy Ox profile is quarantined if present"
grep -Fq 'if profile_exists auditor-ox; then' "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set factory.execution_backend disabled-legacy' "${BOOTSTRAP}"
grep -Fq "auditor-ox config set fallback_providers '[]'" "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set agent.disabled_toolsets' "${BOOTSTRAP}"
grep -Fq 'expect_config auditor-ox factory.execution_backend disabled-legacy' "${BOOTSTRAP}"

echo "[check] hidden model fallbacks disabled"
grep -Fq 'for profile in "${profiles[@]}"; do' "${BOOTSTRAP}"
grep -Fq "hermes -p \"\${profile}\" config set fallback_providers '[]'" "${BOOTSTRAP}"

echo "[check] orchestrator routes to model-policy specialists"
grep -Fq '`repository-analyst`' "${ORCHESTRATOR_SOUL}"
grep -Fq '`task-decomposer`' "${ORCHESTRATOR_SOUL}"
grep -Fq '`coder-claude`' "${ORCHESTRATOR_SOUL}"
grep -Fq '`reviewer-gpt`' "${ORCHESTRATOR_SOUL}"
grep -Fq '`reviewer-claude`' "${ORCHESTRATOR_SOUL}"
grep -Fq '`architect-claude-opus`' "${ORCHESTRATOR_SOUL}"
grep -Fq 'SECURITY_SENSITIVE: yes' "${ORCHESTRATOR_SOUL}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${ORCHESTRATOR_SOUL}"
if grep -Eqi 'auditor-ox|SKIPPED_OX_UNAVAILABLE|stealth/ox-alpha' "${ORCHESTRATOR_SOUL}"; then
  echo "ERROR: orchestrator nadal zawiera Ox routing" >&2
  exit 1
fi

echo "[check] orchestrator receives canonical standard at runtime"
grep -Fq 'STANDARD_SRC="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"' "${BOOTSTRAP}"
grep -Fq 'cat "${STANDARD_SRC}"' "${BOOTSTRAP}"
grep -Fq '# Software Development Standard — wstrzyknięty kontekst runtime' "${BOOTSTRAP}"

echo "[check] orchestrator Kanban runtime gate"
grep -Fq "config set toolsets '[\"hermes-cli\",\"kanban\"]'" "${BOOTSTRAP}"
if grep -Eq '^[[:space:]]*hermes[[:space:]].*tools[[:space:]]+enable[[:space:]]+kanban([[:space:]]|$)' "${BOOTSTRAP}"; then
  echo "ERROR: bootstrap nie może polegać na platformowym tools enable kanban" >&2
  exit 1
fi

echo "[check] coordination-only capability denylist"
expected_disabled='["terminal","file","code_execution","web","browser","image_gen","delegation","computer_use","cronjob"]'
grep -Fq "orchestrator config set agent.disabled_toolsets '${expected_disabled}'" "${BOOTSTRAP}"
grep -Fq "routing-sink config set agent.disabled_toolsets '${expected_disabled}'" "${BOOTSTRAP}"

echo "[check] runtime controller is separate, scoped and owns routing gate"
test -f "${RUNTIME_SOUL}"
grep -Fq 'PROFILE="runtime-controller"' "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set toolsets '[\"hermes-cli\",\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set agent.disabled_toolsets '[\"kanban\",\"file\",\"code_execution\",\"web\",\"browser\",\"image_gen\",\"delegation\",\"computer_use\",\"cronjob\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set fallback_providers '[]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'MODEL_ROUTING_SRC=' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0644 "${MODEL_ROUTING_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'get_config_full() {' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'toolsets_actual="$(get_config_full toolsets)"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'for required_toolset in hermes-cli terminal; do' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'toolsets must not expose direct kanban tools' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'profil nie powinien wystawiać toolsetu `kanban`' "${RUNTIME_SOUL}"
grep -Fq 'validate-routing' "${RUNTIME_SOUL}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${RUNTIME_SOUL}"
grep -Fq 'runtime-controller' "${ORCHESTRATOR_SOUL}"
grep -Fq 'Nie masz terminala' "${ORCHESTRATOR_SOUL}"

echo "[check] post-bootstrap model assertions"
grep -Fq 'expect_config reviewer-gpt factory.execution_backend native-openai' "${BOOTSTRAP}"
grep -Fq 'expect_config coder factory.execution_backend native-openai' "${BOOTSTRAP}"
grep -Fq 'expect_config coder-claude factory.execution_backend "${CLAUDE_SKILL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config reviewer-claude factory.execution_backend "${CLAUDE_SKILL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config architect-claude-opus factory.execution_backend "${CLAUDE_SKILL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config coder-claude factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config reviewer-claude factory.claude_model_class "${CLAUDE_NORMAL_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config architect-claude-opus factory.claude_model_class "${CLAUDE_DEEP_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config task-decomposer model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config quick-reviewer model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config docs model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config repository-analyst model.default "${primary_model}"' "${BOOTSTRAP}"

echo "[check] post-bootstrap worktree assertions"
grep -Fq 'expect_config coder worktree "false"' "${BOOTSTRAP}"
grep -Fq 'expect_config coder worktree_sync "false"' "${BOOTSTRAP}"
grep -Fq 'expect_config coder-claude worktree "false"' "${BOOTSTRAP}"
grep -Fq 'expect_config coder-claude worktree_sync "false"' "${BOOTSTRAP}"

echo "[check] dispatcher-scoped kanban routing"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${BOOTSTRAP}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${BOOTSTRAP}"

echo "[check] routing sink exists"
test -f "${ROOT_DIR}/hermes/profiles/routing-sink/SOUL.md"

echo "[check] required profile SOUL files"
for profile in orchestrator architect architect-claude-opus repository-analyst task-decomposer coder coder-claude quick-reviewer reviewer-gpt reviewer-claude critic auditor-gpt auditor-grok docs release-manager routing-sink; do
  test -f "${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md" || { echo "ERROR: brak hermes/profiles/${profile}/SOUL.md" >&2; exit 1; }
done

if command -v shellcheck >/dev/null 2>&1; then
  echo "[check] shellcheck"
  shellcheck "${BOOTSTRAP}" "${RUNTIME_BOOTSTRAP}" "$0"
else
  echo "[info] shellcheck nie jest zainstalowany; pomijam"
fi

echo "OK: statyczna weryfikacja bootstrapu i model routing zakończona"
