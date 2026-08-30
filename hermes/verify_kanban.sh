#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="${ROOT_DIR}/hermes/configure_kanban.sh"
CONTRACT="${ROOT_DIR}/workflows/KANBAN_CONTRACT.md"
MODEL_POLICY_DOC="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
PARSER="${ROOT_DIR}/hermes/review_decision.py"
PARSER_TESTS="${ROOT_DIR}/hermes/test_review_decision.py"
RUNTIME_VALIDATOR="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
RUNTIME_TESTS="${ROOT_DIR}/hermes/test_kanban_runtime_contract.py"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"
MODEL_ROUTING_TESTS="${ROOT_DIR}/hermes/test_model_routing_policy.py"
RUNTIME_WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
RUNTIME_WRAPPER_TEST="${ROOT_DIR}/hermes/test_kanban_runtime_wrapper.sh"
RUNTIME_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_runtime_controller.sh"
RUNTIME_SOUL="${ROOT_DIR}/hermes/profiles/runtime-controller/SOUL.md"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"
DECOMPOSER_SOUL="${ROOT_DIR}/hermes/profiles/task-decomposer/SOUL.md"
CRITIC_SOUL="${ROOT_DIR}/hermes/profiles/critic/SOUL.md"
QUICK_REVIEWER_SOUL="${ROOT_DIR}/hermes/profiles/quick-reviewer/SOUL.md"
REVIEWER_GPT_SOUL="${ROOT_DIR}/hermes/profiles/reviewer-gpt/SOUL.md"
REVIEWER_CLAUDE_SOUL="${ROOT_DIR}/hermes/profiles/reviewer-claude/SOUL.md"
BOOTSTRAP_VERIFY="${ROOT_DIR}/hermes/verify_bootstrap.sh"

printf '[check] bash syntax\n'
bash -n "${CONFIGURE}"
bash -n "${RUNTIME_WRAPPER}"
bash -n "${RUNTIME_WRAPPER_TEST}"
bash -n "${RUNTIME_BOOTSTRAP}"

printf '[check] manual decomposer policy\n'
grep -Fq 'config set kanban.auto_decompose false' "${CONFIGURE}"
grep -Fq 'config set kanban.auto_subscribe_on_create true' "${CONFIGURE}"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${CONFIGURE}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${CONFIGURE}"

printf '[check] task contract states, workspace and security routing field\n'
grep -Fq '`triage`' "${CONTRACT}"
grep -Fq '`review`' "${CONTRACT}"
grep -Fq '`done`' "${CONTRACT}"
grep -Fq 'worktree:<absolute-repo-path>' "${CONTRACT}"
grep -Fq 'workspace_kind=worktree' "${CONTRACT}"
grep -Fq 'IMPLEMENTED != VERIFIED' "${CONTRACT}"
grep -Fq 'nie oznacza automatycznie VERIFIED całej zmiany' "${CONTRACT}"
grep -Fq 'SECURITY_SENSITIVE: yes|no' "${CONTRACT}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${CONTRACT}"
test -f "${MODEL_POLICY_DOC}"
grep -Fq 'Mechanical routing matrix' "${MODEL_POLICY_DOC}"

printf '[check] runtime controller and gate\n'
grep -Fq 'runtime-controller' "${CONTRACT}"
grep -Fq 'bootstrap_runtime_controller.sh' "${CONTRACT}"
grep -Fq 'kanban_runtime_cli.sh' "${CONTRACT}"
grep -Fq 'nie jest sticky quarantine' "${CONTRACT}"
grep -Fq 'RUNTIME_CONTRACT_PENDING' "${CONTRACT}"
grep -Fq 'sticky `kanban block --kind needs_input`' "${CONTRACT}"
grep -Fq 'parentem wskazującym tę kartę kontrolną' "${CONTRACT}"
grep -Fq 'RUNTIME_CONTRACT_DRIFT' "${CONTRACT}"
grep -Fq -- '--branch ... --max-retries ... --json' "${CONTRACT}"
grep -Fq 'nie twierdzi' "${CONTRACT}"
grep -Fq 'max_runtime' "${CONTRACT}"
grep -Fq 'same-card review flow' "${CONTRACT}"
grep -Fq 'outcome=review_requested' "${CONTRACT}"
grep -Fq 'status=review' "${CONTRACT}"
grep -Fq '/.worktrees/<task-id>' "${CONTRACT}"
grep -Fq 'normalize_snapshot' "${RUNTIME_VALIDATOR}"
grep -Fq 'resolved_implementation_worktree' "${RUNTIME_VALIDATOR}"
grep -Fq '_latest_review_requested_event' "${RUNTIME_VALIDATOR}"
grep -Fq '_latest_run' "${RUNTIME_VALIDATOR}"
grep -Fq 'review_requested_event_run_mismatch' "${RUNTIME_VALIDATOR}"
grep -Fq 'current_implementer_review_run_missing_or_mismatched' "${RUNTIME_VALIDATOR}"
grep -Fq 'implementer_review_run_workspace_mismatched' "${RUNTIME_VALIDATOR}"
grep -Fq 'build_cli_parser' "${RUNTIME_VALIDATOR}"
grep -Fq 'runtime-controller' "${ORCHESTRATOR_SOUL}"
grep -Fq 'Nie masz terminala' "${ORCHESTRATOR_SOUL}"
grep -Fq 'natywnego same-card review Hermesa' "${ORCHESTRATOR_SOUL}"
grep -Fq 'natywne same-card `kanban_request_changes`' "${ORCHESTRATOR_SOUL}"
grep -Fq '~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh' "${RUNTIME_SOUL}"
grep -Fq 'profil nie powinien wystawiać toolsetu `kanban`' "${RUNTIME_SOUL}"
grep -Fq 'validate-runtime' "${RUNTIME_SOUL}"
grep -Fq 'validate-handoff --actual-json' "${RUNTIME_SOUL}"
grep -Fq 'validate-routing' "${RUNTIME_SOUL}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${RUNTIME_SOUL}"
grep -Fq 'metadata.workspace_path' "${RUNTIME_SOUL}"
grep -Fq 'Nie twórz osobnej karty review' "${RUNTIME_SOUL}"

printf '[check] scoped runtime wrapper\n'
grep -Fq 'case "${op}" in' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban create "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban show "${task_id}" --json' "${RUNTIME_WRAPPER}"
grep -Fq 'block reason must not contain flag-shaped operands' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban block --kind needs_input "${task_id}" "${reason}"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban complete "${task_id}" --result' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${VALIDATOR}" runtime "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${VALIDATOR}" handoff "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${MODEL_ROUTING_VALIDATOR}" "$@"' "${RUNTIME_WRAPPER}"
if grep -Fq 'eval ' "${RUNTIME_WRAPPER}"; then echo 'ERROR: runtime wrapper must not use eval' >&2; exit 1; fi
bash "${RUNTIME_WRAPPER_TEST}"

printf '[check] runtime controller bootstrap policy\n'
grep -Fq 'PROFILE="runtime-controller"' "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set toolsets '[\"hermes-cli\",\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set agent.disabled_toolsets '[\"kanban\",\"file\",\"code_execution\",\"web\",\"browser\",\"image_gen\",\"delegation\",\"computer_use\",\"cronjob\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'for required_toolset in hermes-cli terminal; do' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'toolsets must not expose direct kanban tools' "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set fallback_providers '[]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0755 "${WRAPPER_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0644 "${VALIDATOR_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0644 "${MODEL_ROUTING_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set worktree false' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set worktree_sync false' "${RUNTIME_BOOTSTRAP}"

printf '[check] cross-vendor and security-sensitive model routing\n'
grep -Fq 'OPENAI_IMPLEMENTER = "coder"' "${MODEL_ROUTING}"
grep -Fq 'CLAUDE_IMPLEMENTER = "coder-claude"' "${MODEL_ROUTING}"
grep -Fq 'OPENAI_REVIEWER = "reviewer-gpt"' "${MODEL_ROUTING}"
grep -Fq 'CLAUDE_REVIEWER = "reviewer-claude"' "${MODEL_ROUTING}"
grep -Fq 'anthropic_security_reviewer_forbidden' "${MODEL_ROUTING}"
grep -Fq 'normal_review_must_be_cross_vendor' "${MODEL_ROUTING}"
grep -Fq 'SECURITY_SENSITIVE: yes|no' "${DECOMPOSER_SOUL}"
grep -Fq '`coder` wymaga `reviewer-claude`' "${DECOMPOSER_SOUL}"
grep -Fq '`coder-claude` wymaga `reviewer-gpt`' "${DECOMPOSER_SOUL}"
grep -Fq 'review zawsze wykonuje `reviewer-gpt`' "${ORCHESTRATOR_SOUL}"
grep -Fq 'security-sensitive' "${REVIEWER_GPT_SOUL}"
grep -Fq 'SECURITY_SENSITIVE: yes' "${REVIEWER_CLAUDE_SOUL}"
if grep -Eqi 'stealth/ox-alpha|auditor-ox|SKIPPED_OX_UNAVAILABLE' "${CONTRACT}" "${MODEL_POLICY_DOC}" "${ORCHESTRATOR_SOUL}" "${DECOMPOSER_SOUL}"; then
  echo 'ERROR: active workflow still references Ox routing' >&2
  exit 1
fi

printf '[check] review decisions\n'
grep -Fq 'DECISION: APPROVE' "${CONTRACT}"
grep -Fq 'DECISION: CHANGES_REQUIRED' "${CONTRACT}"
grep -Fq 'REVIEW_PENDING' "${CONTRACT}"
grep -Fq '`severity`: HIGH' "${CONTRACT}"
grep -Fq 'dodatkowy nieobsługiwany marker `DECISION:`' "${CONTRACT}"
grep -Fq 'Przy `DECISION: CHANGES_REQUIRED` podczas aktywnego same-card review runu wywołaj natywne `kanban_request_changes` przed zakończeniem review; nie kończ review wyłącznie tekstową decyzją i nie twórz nowej karty dla zwykłego reworku.' "${CRITIC_SOUL}"
grep -Fq 'Przy `DECISION: CHANGES_REQUIRED` podczas aktywnego same-card review runu wywołaj natywne `kanban_request_changes` przed zakończeniem review; nie kończ review wyłącznie tekstową decyzją i nie twórz nowej karty dla zwykłego reworku.' "${QUICK_REVIEWER_SOUL}"

printf '[check] mandatory deployment step\n'
grep -Fq 'PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh' "${CONTRACT}"
grep -Fq 'DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh' "${CONTRACT}"
grep -Fq 'Software Factory nie jest gotowy do uruchamiania tasków wymagających runtime gate' "${CONTRACT}"

printf '[check] python syntax\n'
python3 -m py_compile "${PARSER}" "${PARSER_TESTS}" "${RUNTIME_VALIDATOR}" "${RUNTIME_TESTS}" "${MODEL_ROUTING}" "${MODEL_ROUTING_TESTS}"

printf '[check] parser tests\n'
(cd "${ROOT_DIR}/hermes" && python3 -m unittest -q test_review_decision.py)

printf '[check] runtime contract tests\n'
(cd "${ROOT_DIR}/hermes" && python3 -m unittest -q test_kanban_runtime_contract.py)

printf '[check] model routing tests\n'
(cd "${ROOT_DIR}/hermes" && python3 -m unittest -q test_model_routing_policy.py)

printf '[check] bootstrap compatibility\n'
bash "${BOOTSTRAP_VERIFY}"

printf 'OK: weryfikacja kontraktu Kanban i model routing zakończona\n'
