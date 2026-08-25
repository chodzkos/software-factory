#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="${ROOT_DIR}/hermes/configure_kanban.sh"
CONTRACT="${ROOT_DIR}/workflows/KANBAN_CONTRACT.md"
PARSER="${ROOT_DIR}/hermes/review_decision.py"
PARSER_TESTS="${ROOT_DIR}/hermes/test_review_decision.py"
RUNTIME_VALIDATOR="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
RUNTIME_TESTS="${ROOT_DIR}/hermes/test_kanban_runtime_contract.py"
RUNTIME_WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
RUNTIME_BOOTSTRAP="${ROOT_DIR}/hermes/bootstrap_runtime_controller.sh"
RUNTIME_SOUL="${ROOT_DIR}/hermes/profiles/runtime-controller/SOUL.md"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"
BOOTSTRAP_VERIFY="${ROOT_DIR}/hermes/verify_bootstrap.sh"

printf '[check] bash syntax\n'
bash -n "${CONFIGURE}"
bash -n "${RUNTIME_WRAPPER}"
bash -n "${RUNTIME_BOOTSTRAP}"

printf '[check] manual decomposer policy\n'
grep -Fq 'config set kanban.auto_decompose false' "${CONFIGURE}"
grep -Fq 'config set kanban.auto_subscribe_on_create true' "${CONFIGURE}"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${CONFIGURE}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${CONFIGURE}"

printf '[check] task contract states and workspace\n'
grep -Fq '`triage`' "${CONTRACT}"
grep -Fq '`review`' "${CONTRACT}"
grep -Fq '`done`' "${CONTRACT}"
grep -Fq 'worktree:<absolute-repo-path>' "${CONTRACT}"
grep -Fq 'workspace_kind=worktree' "${CONTRACT}"
grep -Fq 'IMPLEMENTED != VERIFIED' "${CONTRACT}"
grep -Fq 'nie oznacza automatycznie VERIFIED całej zmiany' "${CONTRACT}"

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
grep -Fq 'workspace_kind=dir' "${CONTRACT}"
grep -Fq 'implementation.workspace_path' "${CONTRACT}"
grep -Fq '/.worktrees/t_X' "${CONTRACT}"
grep -Fq 'normalize_snapshot' "${RUNTIME_VALIDATOR}"
grep -Fq 'resolved_implementation_worktree' "${RUNTIME_VALIDATOR}"
grep -Fq 'build_cli_parser' "${RUNTIME_VALIDATOR}"
grep -Fq 'runtime-controller' "${ORCHESTRATOR_SOUL}"
grep -Fq 'Nie masz terminala' "${ORCHESTRATOR_SOUL}"
grep -Fq 'workspace=dir:<exact-post-claim-workspace_path>' "${ORCHESTRATOR_SOUL}"
grep -Fq '~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh' "${RUNTIME_SOUL}"
grep -Fq 'validate-runtime' "${RUNTIME_SOUL}"
grep -Fq 'validate-handoff' "${RUNTIME_SOUL}"

printf '[check] scoped runtime wrapper\n'
grep -Fq 'case "${op}" in' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban create "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban show "${task_id}" --json' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban block --kind needs_input' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban complete "${task_id}" --result' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${VALIDATOR}" runtime "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${VALIDATOR}" handoff "$@"' "${RUNTIME_WRAPPER}"
if grep -Fq 'eval ' "${RUNTIME_WRAPPER}"; then echo 'ERROR: runtime wrapper must not use eval' >&2; exit 1; fi

printf '[check] runtime controller bootstrap policy\n'
grep -Fq 'PROFILE="runtime-controller"' "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set toolsets '[\"hermes-cli\",\"kanban\",\"terminal\"]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq "config set fallback_providers '[]'" "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0755 "${WRAPPER_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'install -m 0644 "${VALIDATOR_SRC}"' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set worktree false' "${RUNTIME_BOOTSTRAP}"
grep -Fq 'config set worktree_sync false' "${RUNTIME_BOOTSTRAP}"

printf '[check] review decisions\n'
grep -Fq 'DECISION: APPROVE' "${CONTRACT}"
grep -Fq 'DECISION: CHANGES_REQUIRED' "${CONTRACT}"
grep -Fq 'DECISION: SKIPPED_OX_UNAVAILABLE' "${CONTRACT}"
grep -Fq 'REVIEW_PENDING' "${CONTRACT}"
grep -Fq '`severity`: HIGH' "${CONTRACT}"
grep -Fq 'dodatkowy nieobsługiwany marker `DECISION:`' "${CONTRACT}"

printf '[check] mandatory deployment step\n'
grep -Fq 'PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh' "${CONTRACT}"
grep -Fq 'DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh' "${CONTRACT}"
grep -Fq 'Software Factory nie jest gotowy do uruchamiania tasków wymagających runtime gate' "${CONTRACT}"

printf '[check] python syntax\n'
python3 -m py_compile "${PARSER}" "${PARSER_TESTS}" "${RUNTIME_VALIDATOR}" "${RUNTIME_TESTS}"

printf '[check] parser tests\n'
(cd "${ROOT_DIR}/hermes" && python3 -m unittest -q test_review_decision.py)

printf '[check] runtime contract tests\n'
(cd "${ROOT_DIR}/hermes" && python3 -m unittest -q test_kanban_runtime_contract.py)

printf '[check] bootstrap compatibility\n'
bash "${BOOTSTRAP_VERIFY}"

printf 'OK: weryfikacja kontraktu Kanban zakończona\n'
