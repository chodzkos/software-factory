#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${HOME}/.hermes/profiles"
GROK_PROVIDER="${GROK_PROVIDER:-xai-oauth}"
GROK_MODEL="${GROK_MODEL:-grok-4.6}"
GEMINI_PROVIDER="${GEMINI_PROVIDER:-gemini}"
GEMINI_MODEL="${GEMINI_MODEL:-}"

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

mkdir -p "${PROFILE_ROOT}"

echo "Primary profile model:"
hermes config get model || true

echo
for profile in "${profiles[@]}"; do
  if hermes profile list | awk '{print $1}' | grep -Fxq "${profile}"; then
    echo "[exists] ${profile}"
  else
    echo "[create] ${profile}"
    hermes profile create "${profile}" \
      --clone-from default \
      --description "${descriptions[$profile]}"
  fi

  soul_src="${ROOT_DIR}/hermes/profiles/${profile}/SOUL.md"
  if [[ -f "${soul_src}" ]]; then
    install -m 0644 "${soul_src}" "${PROFILE_ROOT}/${profile}/SOUL.md"
  fi

  # Unattended workers should stop on repeated no-progress/failure loops.
  hermes -p "${profile}" config set agent.hard_stop_enabled true >/dev/null
  hermes -p "${profile}" config set agent.tool_use_enforcement auto >/dev/null

done

# Grok roles are explicit and deterministic.
for profile in critic auditor-grok; do
  hermes -p "${profile}" config set model.provider "${GROK_PROVIDER}"
  hermes -p "${profile}" config set model.default "${GROK_MODEL}"
done

# Keep implementation workers isolated when used interactively as well.
hermes -p coder config set worktree true
hermes -p coder config set worktree_sync true

# The orchestrator is coordination-only. These toolsets are globally disabled
# inside its profile; Kanban worker tools remain injected by Hermes on dispatch.
hermes -p orchestrator config set agent.disabled_toolsets '["terminal","file","code_execution","web","browser","image_gen"]'

if [[ -n "${GEMINI_MODEL}" ]]; then
  hermes -p quick-reviewer config set model.provider "${GEMINI_PROVIDER}"
  hermes -p quick-reviewer config set model.default "${GEMINI_MODEL}"
  echo "[configured] quick-reviewer -> ${GEMINI_PROVIDER}/${GEMINI_MODEL}"
else
  echo "[warning] GEMINI_MODEL is empty; quick-reviewer still inherits default model."
  echo "          Set it later with:"
  echo "          quick-reviewer config set model.provider ${GEMINI_PROVIDER}"
  echo "          quick-reviewer config set model.default <MODEL_ID>"
fi

echo
hermes profile list

echo
echo "Run 'hermes doctor' and then inspect each profile with 'hermes -p <name> config get model'."
