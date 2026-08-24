#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_profiles.sh"
STANDARD="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"

echo "[check] bash syntax"
bash -n "${BOOTSTRAP}"

echo "[check] canonical standard exists"
test -f "${STANDARD}"

echo "[check] correct hard-stop key"
grep -Fq 'tool_loop_guardrails.hard_stop_enabled true' "${BOOTSTRAP}"
if grep -Fq 'agent.hard_stop_enabled' "${BOOTSTRAP}"; then
  echo "ERROR: znaleziono nieobsługiwany klucz agent.hard_stop_enabled" >&2
  exit 1
fi

echo "[check] coder worktree forced off"
grep -Fq 'hermes -p coder config set worktree false' "${BOOTSTRAP}"
grep -Fq 'hermes -p coder config set worktree_sync false' "${BOOTSTRAP}"
if grep -Eq '^[[:space:]]*hermes[[:space:]].*-p[[:space:]]+coder[[:space:]].*config[[:space:]]+set[[:space:]]+worktree(_sync)?[[:space:]]+true([[:space:]]|$)' "${BOOTSTRAP}"; then
  echo "ERROR: coder nie może mieć worktree/worktree_sync=true przy Kanban worktree" >&2
  exit 1
fi

echo "[check] directory-based profile detection"
grep -Fq '[[ -d "${PROFILE_ROOT}/${name}" ]]' "${BOOTSTRAP}"

echo "[check] model policy defaults"
grep -Fq 'GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"' "${BOOTSTRAP}"
grep -Fq 'GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash-lite}"' "${BOOTSTRAP}"
grep -Fq 'OX_PROVIDER="${OX_PROVIDER:-openrouter}"' "${BOOTSTRAP}"
grep -Fq 'OX_MODEL="${OX_MODEL-stealth/ox-alpha}"' "${BOOTSTRAP}"

echo "[check] specialized role routing"
grep -Fq 'for profile in task-decomposer quick-reviewer docs; do' "${BOOTSTRAP}"
grep -Fq 'for profile in repository-analyst auditor-ox; do' "${BOOTSTRAP}"
grep -Fq 'hermes -p repository-analyst config set model.provider "${primary_provider}"' "${BOOTSTRAP}"
grep -Fq 'profiles+=(auditor-ox)' "${BOOTSTRAP}"

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

echo "[check] post-bootstrap model assertions"
grep -Fq 'expect_config task-decomposer model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config quick-reviewer model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config docs model.default "${GEMINI_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config repository-analyst model.default "${OX_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config auditor-ox model.default "${OX_MODEL}"' "${BOOTSTRAP}"

echo "[check] post-bootstrap worktree assertions"
grep -Fq 'expect_config coder worktree "false"' "${BOOTSTRAP}"
grep -Fq 'expect_config coder worktree_sync "false"' "${BOOTSTRAP}"

echo "[check] dispatcher-scoped kanban routing"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${BOOTSTRAP}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${BOOTSTRAP}"

echo "[check] routing sink exists"
test -f "${ROOT_DIR}/hermes/profiles/routing-sink/SOUL.md"

echo "[check] required profile SOUL files"
for profile in orchestrator architect repository-analyst task-decomposer coder quick-reviewer critic auditor-gpt auditor-grok auditor-ox docs release-manager routing-sink; do
  test -f "${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md" || {
    echo "ERROR: brak hermes/profiles/${profile}/SOUL.md" >&2
    exit 1
  }
done

if command -v shellcheck >/dev/null 2>&1; then
  echo "[check] shellcheck"
  shellcheck "${BOOTSTRAP}" "$0"
else
  echo "[info] shellcheck nie jest zainstalowany; pomijam"
fi

echo "OK: statyczna weryfikacja bootstrapu zakończona"
