#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_profiles.sh"
RUNTIME_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_runtime_controller.sh"
ANALYST_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_repository_analyst_isolation.sh"
ANALYST_VERIFY="${ROOT_DIR}/hermes/verify_repository_analyst_isolation.sh"
PLUGIN_INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
STANDARD="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"
MODEL_POLICY="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"
GUARD="${ROOT_DIR}/hermes/plugins/factory-execution-guards/guard.py"
GUARD_TESTS="${ROOT_DIR}/hermes/test_factory_execution_guards.py"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"

printf '[check] syntax and required sources\n'
bash -n "${BOOTSTRAP}" "${RUNTIME_BOOTSTRAP}" "${PLUGIN_INSTALLER}" "${ANALYST_BOOTSTRAP}" "${ANALYST_VERIFY}"
for path in "${STANDARD}" "${MODEL_POLICY}" "${MODEL_ROUTING}" "${GUARD}" "${GUARD_TESTS}" "${PLUGIN_INSTALLER}"; do test -f "${path}"; done

printf '[check] pinned Claude policy\n'
grep -Fq 'CLAUDE_SKILL="claude-code"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_NORMAL_MODEL="sonnet"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_DEEP_MODEL="opus"' "${BOOTSTRAP}"
if grep -Eq 'CLAUDE_(SKILL|NORMAL_MODEL|DEEP_MODEL)="\$\{' "${BOOTSTRAP}"; then echo 'ERROR: Claude backend/model policy must not be environment-overridable' >&2; exit 1; fi
for profile in coder-claude reviewer-claude architect-claude-opus; do grep -Fq "install_execution_guard \"\${profile}\"" "${BOOTSTRAP}"; done
grep -Fq 'config set --force factory.execution_backend claude-code' "${BOOTSTRAP}"
grep -Fq 'coder-claude config set --force factory.claude_model_class sonnet' "${BOOTSTRAP}"
grep -Fq 'reviewer-claude config set --force factory.claude_model_class sonnet' "${BOOTSTRAP}"
grep -Fq 'architect-claude-opus config set --force factory.claude_model_class opus' "${BOOTSTRAP}"

printf '[check] pinned OpenAI security reviewer\n'
grep -Fq 'SECURITY_REVIEW_PROVIDER="openai-codex"' "${BOOTSTRAP}"
grep -Fq 'SECURITY_REVIEW_MODEL="gpt-5.6-sol"' "${BOOTSTRAP}"
grep -Fq 'reviewer-gpt config set model.provider "${SECURITY_REVIEW_PROVIDER}"' "${BOOTSTRAP}"
grep -Fq 'reviewer-gpt config set model.default "${SECURITY_REVIEW_MODEL}"' "${BOOTSTRAP}"
grep -Fq 'expect_config reviewer-gpt model.provider "${SECURITY_REVIEW_PROVIDER}"' "${BOOTSTRAP}"
grep -Fq 'expect_config reviewer-gpt model.default "${SECURITY_REVIEW_MODEL}"' "${BOOTSTRAP}"

printf '[check] transactional reviewed plugin upgrade\n'
grep -Fq -- '--replace-reviewed' "${PLUGIN_INSTALLER}"
grep -Fq 'immutable transaction file' "${PLUGIN_INSTALLER}"
grep -Fq 'trap rollback EXIT' "${PLUGIN_INSTALLER}"
grep -Fq 'rm -rf -- "$target"' "${PLUGIN_INSTALLER}"
grep -Fq 'mv -- "$backup" "$target"' "${PLUGIN_INSTALLER}"
if grep -Fq '|| true' "${PLUGIN_INSTALLER}"; then echo 'ERROR: plugin rollback must not suppress restoration failures' >&2; exit 1; fi
# After the initial snapshot is frozen, publication code must not reopen MANIFEST.
count="$(grep -c '"$MANIFEST"' "${PLUGIN_INSTALLER}")"
[[ "$count" -eq 1 ]] || { echo "ERROR: installer reopens mutable manifest after snapshot (count=$count)" >&2; exit 1; }
grep -Fq -- '--plugin "${EXECUTION_GUARD}" --replace-reviewed' "${BOOTSTRAP}"
grep -Fq -- '--plugin "${EXECUTION_GUARD}" --replace-reviewed' "${RUNTIME_BOOTSTRAP}"

printf '[check] legacy Ox inference kill switch\n'
grep -Fq 'if profile_exists auditor-ox; then' "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set model.provider disabled-legacy' "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set model.default disabled-legacy' "${BOOTSTRAP}"
grep -Fq "auditor-ox config set fallback_providers '[]'" "${BOOTSTRAP}"
grep -Fq "auditor-ox config set toolsets '[]'" "${BOOTSTRAP}"
if grep -Eqi 'stealth/ox-alpha|OX_MODEL|OX_PROVIDER' "${BOOTSTRAP}" "${MODEL_POLICY}" "${ORCHESTRATOR_SOUL}"; then echo 'ERROR: active Ox routing remains' >&2; exit 1; fi

printf '[check] runtime-controller mechanical boundary\n'
grep -Fq "config set toolsets '[\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'plugins enable "${EXECUTION_GUARD}" --no-allow-tool-override' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set tools.tool_search.enabled off' "${RUNTIME_BOOTSTRAP}"
if grep -Fq "config set toolsets '[\"hermes-cli\",\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"; then echo 'ERROR: runtime-controller must not expose hermes-cli' >&2; exit 1; fi
grep -Fq 'RUNTIME_PROFILE = "runtime-controller"' "${GUARD}"
grep -Fq 'validate-routed-handoff' "${GUARD}"
if grep -Fq '"validate-handoff"' "${GUARD}"; then echo 'ERROR: legacy handoff remains in runtime allowlist' >&2; exit 1; fi

printf '[check] sealed Claude execution/evidence boundary\n'
grep -Fq 'tokens[0] != "claude"' "${GUARD}"
grep -Fq 'CODER_CLAUDE_TOOLS' "${GUARD}"
grep -Fq 'READONLY_CLAUDE_TOOLS' "${GUARD}"
grep -Fq 'FORBIDDEN_CLAUDE_FLAGS' "${GUARD}"
grep -Fq 'claude_binary_sha256' "${GUARD}"
grep -Fq 'workspace' "${GUARD}"
grep -Fq 'HERMES_KANBAN_TASK' "${GUARD}"
grep -Fq 'requires successful canonical Claude Code evidence' "${GUARD}"

printf '[check] fresh bootstrap activates repository-analyst isolation\n'
grep -Fq 'ANALYST_BOOTSTRAP=' "${BOOTSTRAP}"
grep -Fq 'ANALYST_VERIFY=' "${BOOTSTRAP}"
grep -Fq 'bash "${ANALYST_BOOTSTRAP}"' "${BOOTSTRAP}"
grep -Fq 'bash "${ANALYST_VERIFY}" --live' "${BOOTSTRAP}"

printf '[check] routing policy shape\n'
grep -Fq 'security_sensitive_openai_implementer_forbidden' "${MODEL_ROUTING}"
grep -Fq 'reviewer_set_mismatch:' "${MODEL_ROUTING}"
grep -Fq 'strict_json_loads' "${MODEL_ROUTING}"
grep -Fq 'validate-routed-handoff' "${ORCHESTRATOR_SOUL}"

printf '[check] coder worktree forced off\n'
for profile in coder coder-claude; do
  grep -Fq "hermes -p ${profile} config set worktree false" "${BOOTSTRAP}"
  grep -Fq "hermes -p ${profile} config set worktree_sync false" "${BOOTSTRAP}"
done

printf '[check] orchestration restrictions\n'
grep -Fq "orchestrator config set toolsets '[\"hermes-cli\",\"kanban\"]'" "${BOOTSTRAP}"
grep -Fq 'Nie masz terminala' "${ORCHESTRATOR_SOUL}"
grep -Fq 'SECURITY_SENSITIVE: yes' "${ORCHESTRATOR_SOUL}"

printf '[check] guard adversarial unit tests\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_factory_execution_guards)

if command -v shellcheck >/dev/null 2>&1; then shellcheck "${BOOTSTRAP}" "${RUNTIME_BOOTSTRAP}" "${PLUGIN_INSTALLER}" "$0"; else echo '[info] shellcheck nie jest zainstalowany; pomijam'; fi
printf 'OK: statyczna weryfikacja bootstrapu i sealed execution guards zakończona\n'
