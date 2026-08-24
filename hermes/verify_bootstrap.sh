#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_profiles.sh"

echo "[check] bash syntax"
bash -n "${BOOTSTRAP}"

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
