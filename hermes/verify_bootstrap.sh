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

echo "[check] no persistent coder worktree"
if grep -Eq 'coder.*config set worktree|config set worktree true' "${BOOTSTRAP}"; then
  echo "ERROR: coder nie może mieć globalnego worktree=true przy Kanban worktree" >&2
  exit 1
fi

echo "[check] directory-based profile detection"
grep -Fq '[[ -d "${PROFILE_ROOT}/${name}" ]]' "${BOOTSTRAP}"

echo "[check] orchestrator receives canonical standard at runtime"
grep -Fq 'STANDARD_SRC="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"' "${BOOTSTRAP}"
grep -Fq 'cat "${STANDARD_SRC}"' "${BOOTSTRAP}"
grep -Fq '# Software Development Standard — wstrzyknięty kontekst runtime' "${BOOTSTRAP}"

echo "[check] orchestrator Kanban runtime gate"
grep -Fq 'config set toolsets '\''["hermes-cli","kanban"]'\''' "${BOOTSTRAP}"
if grep -Fq 'tools enable kanban' "${BOOTSTRAP}"; then
  echo "ERROR: bootstrap nie może polegać na platformowym tools enable kanban" >&2
  exit 1
fi

echo "[check] orchestrator has no implementation/delegation toolsets"
grep -Fq 'orchestrator config set agent.disabled_toolsets '\''["terminal","file","code_execution","web","browser","image_gen","delegation"]'\''' "${BOOTSTRAP}"

echo "[check] dispatcher-scoped kanban routing"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${BOOTSTRAP}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${BOOTSTRAP}"

echo "[check] routing sink exists"
test -f "${ROOT_DIR}/hermes/profiles/routing-sink/SOUL.md"

echo "[check] required profile SOUL files"
for profile in orchestrator architect coder quick-reviewer critic auditor-gpt auditor-grok release-manager routing-sink; do
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
