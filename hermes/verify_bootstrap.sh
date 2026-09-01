#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_profiles.sh"
RUNTIME_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_runtime_controller.sh"
ANALYST_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_repository_analyst_isolation.sh"
ANALYST_VERIFY="${ROOT_DIR}/hermes/verify_repository_analyst_isolation.sh"
PLUGIN_INSTALLER="${ROOT_DIR}/hermes/install_factory_plugins.sh"
CONFIG_KEY_REMOVER="${ROOT_DIR}/hermes/remove_profile_config_keys.py"
RUNTIME_WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
REVIEW_DISPATCHER="${ROOT_DIR}/hermes/kanban_review_dispatch.py"
STANDARD="${ROOT_DIR}/standards/SOFTWARE_DEVELOPMENT_STANDARD.md"
MODEL_POLICY="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"
GUARD="${ROOT_DIR}/hermes/plugins/factory-execution-guards/guard.py"
GUARD_ENTRY="${ROOT_DIR}/hermes/plugins/factory-execution-guards/__init__.py"
GUARD_MANIFEST="${ROOT_DIR}/hermes/plugins/factory-execution-guards/plugin.yaml"
PLUGIN_MANIFEST="${ROOT_DIR}/hermes/plugins/manifest.json"
GUARD_TESTS="${ROOT_DIR}/hermes/test_factory_execution_guards.py"
GUARD_PROFILE_TESTS="${ROOT_DIR}/hermes/test_factory_execution_guard_profile_resolution.py"
TARGETED_GUARD_TEST="${ROOT_DIR}/hermes/test_targeted_review_dispatch_guard.py"
REVIEW_DISPATCH_TEST="${ROOT_DIR}/hermes/test_kanban_review_dispatch.py"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"
RUNTIME_CONTROLLER_SOUL="${ROOT_DIR}/hermes/profiles/runtime-controller/SOUL.md"
CODER_CLAUDE_SOUL="${ROOT_DIR}/hermes/profiles/coder-claude/SOUL.md"
REVIEWER_CLAUDE_SOUL="${ROOT_DIR}/hermes/profiles/reviewer-claude/SOUL.md"
ARCHITECT_CLAUDE_SOUL="${ROOT_DIR}/hermes/profiles/architect-claude-opus/SOUL.md"

printf '[check] syntax and required sources\n'
bash -n "${BOOTSTRAP}" "${RUNTIME_BOOTSTRAP}" "${PLUGIN_INSTALLER}" "${ANALYST_BOOTSTRAP}" "${ANALYST_VERIFY}" "${RUNTIME_WRAPPER}"
PYTHONDONTWRITEBYTECODE=1 python3 - "${CONFIG_KEY_REMOVER}" "${REVIEW_DISPATCHER}" "${REVIEW_DISPATCH_TEST}" "${TARGETED_GUARD_TEST}" <<'PY'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
PY
for path in "${STANDARD}" "${MODEL_POLICY}" "${MODEL_ROUTING}" "${GUARD}" "${GUARD_ENTRY}" "${GUARD_MANIFEST}" "${PLUGIN_MANIFEST}" "${GUARD_TESTS}" "${GUARD_PROFILE_TESTS}" "${TARGETED_GUARD_TEST}" "${REVIEW_DISPATCH_TEST}" "${PLUGIN_INSTALLER}" "${RUNTIME_WRAPPER}" "${REVIEW_DISPATCHER}"; do test -f "${path}"; done

printf '[check] pinned Claude policy and clean invocation mode\n'
grep -Fq 'CLAUDE_SKILL="claude-code"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_NORMAL_MODEL="sonnet"' "${BOOTSTRAP}"
grep -Fq 'CLAUDE_DEEP_MODEL="opus"' "${BOOTSTRAP}"
if grep -Eq 'CLAUDE_(SKILL|NORMAL_MODEL|DEEP_MODEL)="\$\{' "${BOOTSTRAP}"; then echo 'ERROR: Claude backend/model policy must not be environment-overridable' >&2; exit 1; fi
for profile in coder-claude reviewer-claude architect-claude-opus; do grep -Fq "install_execution_guard \"\${profile}\"" "${BOOTSTRAP}"; done
for soul in "${CODER_CLAUDE_SOUL}" "${REVIEWER_CLAUDE_SOUL}" "${ARCHITECT_CLAUDE_SOUL}"; do grep -Fq -- '--safe-mode' "${soul}"; grep -Fq 'TASK_ID:' "${soul}"; grep -Fq 'RUN_ID:' "${soul}"; grep -Fq 'WORKSPACE:' "${soul}"; done
grep -Fq -- '--permission-mode dontAsk' "${CODER_CLAUDE_SOUL}"
grep -Fq 'Edit(//<exact-resolved-worktree>/**)' "${CODER_CLAUDE_SOUL}"
for soul in "${REVIEWER_CLAUDE_SOUL}" "${ARCHITECT_CLAUDE_SOUL}"; do grep -Fq -- '--permission-mode plan' "${soul}"; grep -Fq -- "--allowedTools 'Read,Glob,Grep'" "${soul}"; done
if grep -Fq 'Bash(' "${CODER_CLAUDE_SOUL}"; then echo 'ERROR: coder-claude must not receive Bash after hardening' >&2; exit 1; fi

printf '[check] pinned OpenAI security reviewer and legacy fallback removal\n'
grep -Fq 'SECURITY_REVIEW_PROVIDER="openai-codex"' "${BOOTSTRAP}"
grep -Fq 'SECURITY_REVIEW_MODEL="gpt-5.6-sol"' "${BOOTSTRAP}"
grep -Fq 'remove_profile_keys "${profile}" fallback_model model.fallback_model' "${BOOTSTRAP}"
grep -Fq 'expect_profile_keys_absent reviewer-gpt fallback_model model.fallback_model' "${BOOTSTRAP}"
grep -Fq 'expect_config reviewer-gpt fallback_providers' "${BOOTSTRAP}"
grep -Fq 'CONFIG_KEY_REMOVER=' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'fallback_model model.fallback_model' "${RUNTIME_BOOTSTRAP}"

printf '[check] gated same-card review dispatch bootstrap\n'
grep -Fq 'config set kanban.review_dispatch false' "${BOOTSTRAP}"
grep -Fq 'expect_config "${DISPATCHER_PROFILE}" kanban.review_dispatch false' "${BOOTSTRAP}"
grep -Fq 'REVIEW_DISPATCH_SRC=' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'kanban_review_dispatch.py' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set kanban.review_dispatch false' "${RUNTIME_BOOTSTRAP}"
grep -Fq "expect kanban.review_dispatch 'false'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'dispatch-review --task-id <task-id>' "${RUNTIME_WRAPPER}"
grep -Fq 'dispatch-review --task-id <task-id>' "${RUNTIME_CONTROLLER_SOUL}"
grep -Fq 'dispatch-review --task-id <task-id>' "${ORCHESTRATOR_SOUL}"
grep -Fq '_EXPECTED_HERMES_VERSION = "0.20.4"' "${REVIEW_DISPATCHER}"
grep -Fq 'if kb.review_dispatch_enabled()' "${REVIEW_DISPATCHER}"
grep -Fq 'kb.claim_review_task(conn, task_id)' "${REVIEW_DISPATCHER}"

printf '[check] transactional reviewed plugin upgrade\n'
grep -Fq 'verify_reviewed_provenance' "${PLUGIN_INSTALLER}"
grep -Fq 'replace_from' "${PLUGIN_INSTALLER}"
grep -Fq 'assert_safe_dest_path' "${PLUGIN_INSTALLER}"
grep -Fq 'exec 9<"$DEST"' "${PLUGIN_INSTALLER}"
if grep -Fq '.factory-plugin.lock.' "${PLUGIN_INSTALLER}"; then echo 'ERROR: symlinkable lock pathname must not be used' >&2; exit 1; fi
grep -Fq 'trap rollback EXIT' "${PLUGIN_INSTALLER}"
grep -Fq 'rollback failed to restore reviewed target' "${PLUGIN_INSTALLER}"
grep -Fq 'old backup cleanup failed' "${PLUGIN_INSTALLER}"
count="$(grep -c '"$MANIFEST"' "${PLUGIN_INSTALLER}")"
[[ "$count" -eq 1 ]] || { echo "ERROR: installer reopens mutable manifest after snapshot (count=$count)" >&2; exit 1; }
grep -Fq '"replace_from"' "${PLUGIN_MANIFEST}"
PYTHONDONTWRITEBYTECODE=1 python3 - "${PLUGIN_MANIFEST}" <<'PY'
import json, pathlib, sys
path=pathlib.Path(sys.argv[1]); data=json.loads(path.read_text())
plugin=data["plugins"]["factory-execution-guards"]
expected={
    "plugin.yaml":"8887d7cf9d5ac0460cb95655a28687dcf6dc1ae7",
    "__init__.py":"b2af45c1c427d7a9684e640154eabe4d2e8aa38b",
    "guard.py":"fb2109da908740b63d62ba7951b38ef44505c905",
}
predecessor={
    "plugin.yaml":"2ec6370b04237d6c5db8b22bcbb63de48ad657ea",
    "__init__.py":"33e776e5177e0e17307f4f8b4d95a24ab4d4d5b9",
    "guard.py":"fb2109da908740b63d62ba7951b38ef44505c905",
}
if plugin.get("files") != expected: raise SystemExit("ERROR: v0.7.0 current execution-guard pins mismatch")
replace=plugin.get("replace_from") or []
if not replace or replace[0] != predecessor: raise SystemExit("ERROR: v0.6.0 is not the immediate reviewed predecessor")
PY

printf '[check] legacy Ox inference kill switch and inherited config cleanup\n'
grep -Fq 'remove_profile_keys auditor-ox fallback_model model.fallback_model mcp_servers API_SERVER_ENABLED API_SERVER_KEY' "${BOOTSTRAP}"
grep -Fq 'expect_profile_keys_absent auditor-ox fallback_model model.fallback_model mcp_servers API_SERVER_ENABLED API_SERVER_KEY' "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set model.provider disabled-legacy' "${BOOTSTRAP}"
grep -Fq 'auditor-ox config set model.default disabled-legacy' "${BOOTSTRAP}"
grep -Fq "auditor-ox config set fallback_providers '[]'" "${BOOTSTRAP}"
grep -Fq "auditor-ox config set toolsets '[]'" "${BOOTSTRAP}"
if grep -Eqi 'stealth/ox-alpha|OX_MODEL|OX_PROVIDER' "${BOOTSTRAP}" "${MODEL_POLICY}" "${ORCHESTRATOR_SOUL}"; then echo 'ERROR: active Ox routing remains' >&2; exit 1; fi

printf '[check] runtime-controller quote-aware mechanical boundary\n'
grep -Fq "config set toolsets '[\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'plugins enable "${EXECUTION_GUARD}" --no-allow-tool-override' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set tools.tool_search.enabled off' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'Software Factory execution guard refused unquoted terminal line break' "${GUARD_ENTRY}"
grep -Fq 'def _has_unquoted_linebreak' "${GUARD_ENTRY}"
grep -Fq '_guard._runtime_terminal_allowed = _runtime_terminal_allowed' "${GUARD_ENTRY}"
grep -Fq '"--actual-json" in args' "${GUARD_ENTRY}"
grep -Fq 'validate-routing-body' "${GUARD_ENTRY}"
grep -Fq 'validate-routing-live' "${GUARD_ENTRY}"
grep -Fq '"dispatch-review"' "${GUARD_ENTRY}"
if grep -Fq '"validate-handoff"' "${GUARD_ENTRY}"; then echo 'ERROR: legacy handoff remains in effective runtime allowlist' >&2; exit 1; fi

printf '[check] sealed Claude execution/evidence boundary v0.7.0\n'
grep -Fq 'version: 0.7.0' "${GUARD_MANIFEST}"
grep -Fq '_CODER_READ_TOOLS = "Read,Glob,Grep"' "${GUARD_ENTRY}"
grep -Fq 'return f"Edit(/{workspace}/**)"' "${GUARD_ENTRY}"
grep -Fq 'expected_mode = "dontAsk" if profile == "coder-claude" else "plan"' "${GUARD_ENTRY}"
grep -Fq '_READONLY_TOOLS = "Read,Glob,Grep"' "${GUARD_ENTRY}"
grep -Fq '_REQUIRED_BOOL_FLAGS = frozenset({"--safe-mode"})' "${GUARD_ENTRY}"
grep -Fq '_exact_marker(prompt, "TASK_ID", task_id)' "${GUARD_ENTRY}"
grep -Fq '_exact_marker(prompt, "RUN_ID", run_id)' "${GUARD_ENTRY}"
grep -Fq '_exact_marker(prompt, "WORKSPACE", workspace)' "${GUARD_ENTRY}"
grep -Fq '["ls-files", "-c", "-o", "-z"]' "${GUARD_ENTRY}"
if grep -Fq 'ls-files", "-c", "-o", "--exclude-standard"' "${GUARD_ENTRY}"; then echo 'ERROR: ignored untracked paths are excluded from attestation' >&2; exit 1; fi
grep -Fq '_guard._workspace_content_state = _hardened_workspace_content_state' "${GUARD_ENTRY}"
grep -Fq '_guard._parse_claude_argv = _hardened_parse_claude_argv' "${GUARD_ENTRY}"
grep -Fq '_PENDING_ATTESTATIONS' "${GUARD}"
grep -Fq '_COMPLETED_ATTESTATIONS' "${GUARD}"
grep -Fq 'data.get("schema") == 5' "${GUARD}"

printf '[check] fresh bootstrap activates repository-analyst isolation\n'
grep -Fq 'bash "${ANALYST_BOOTSTRAP}"' "${BOOTSTRAP}"
grep -Fq 'bash "${ANALYST_VERIFY}" --live' "${BOOTSTRAP}"

printf '[check] routing policy shape\n'
grep -Fq 'security_sensitive_openai_implementer_forbidden' "${MODEL_ROUTING}"
grep -Fq 'reviewer_set_mismatch:' "${MODEL_ROUTING}"
grep -Fq 'strict_json_loads' "${MODEL_ROUTING}"

printf '[check] coder worktree forced off\n'
for profile in coder coder-claude; do
  grep -Fq "hermes -p ${profile} config set worktree false" "${BOOTSTRAP}"
  grep -Fq "hermes -p ${profile} config set worktree_sync false" "${BOOTSTRAP}"
done

printf '[check] orchestration restrictions\n'
grep -Fq "orchestrator config set toolsets '[\"hermes-cli\",\"kanban\"]'" "${BOOTSTRAP}"
grep -Fq 'Nie masz terminala' "${ORCHESTRATOR_SOUL}"
grep -Fq 'SECURITY_SENSITIVE: yes' "${ORCHESTRATOR_SOUL}"

printf '[check] targeted review dispatcher unit tests\n'
(cd "${ROOT_DIR}/hermes" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_kanban_review_dispatch.py test_targeted_review_dispatch_guard.py)
printf '[check] guard adversarial unit tests\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_factory_execution_guards hermes.test_factory_execution_guard_profile_resolution hermes.test_targeted_review_dispatch_guard)

if command -v shellcheck >/dev/null 2>&1; then shellcheck --severity=warning "${BOOTSTRAP}" "${RUNTIME_BOOTSTRAP}" "${PLUGIN_INSTALLER}" "${RUNTIME_WRAPPER}" "$0"; else echo '[info] shellcheck nie jest zainstalowany; pomijam'; fi
printf 'OK: statyczna weryfikacja bootstrapu, gated targeted review dispatch i sealed execution guards v0.7.0 zakończona\n'
