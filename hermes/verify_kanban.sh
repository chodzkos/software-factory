#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="${ROOT_DIR}/hermes/configure_kanban.sh"
CONTRACT="${ROOT_DIR}/workflows/KANBAN_CONTRACT.md"
PARSER="${ROOT_DIR}/hermes/review_decision.py"
PARSER_TESTS="${ROOT_DIR}/hermes/test_review_decision.py"
RUNTIME_VALIDATOR="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
RUNTIME_TESTS="${ROOT_DIR}/hermes/test_kanban_runtime_contract.py"
ORCHESTRATOR_SOUL="${ROOT_DIR}/hermes/profiles/orchestrator/SOUL.md"
BOOTSTRAP_VERIFY="${ROOT_DIR}/hermes/verify_bootstrap.sh"

printf '[check] bash syntax\n'
bash -n "${CONFIGURE}"

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

printf '[check] runtime task gate\n'
grep -Fq 'initial_status=blocked' "${CONTRACT}"
grep -Fq 'RUNTIME_CONTRACT_DRIFT' "${CONTRACT}"
grep -Fq '`max_retries`' "${CONTRACT}"
grep -Fq '`branch_name`' "${CONTRACT}"
grep -Fq 'workspace_kind=dir' "${CONTRACT}"
grep -Fq 'resolved worktree implementera' "${CONTRACT}"
grep -Fq 'fail-closed runtime gate' "${ORCHESTRATOR_SOUL}"
grep -Fq 'workspace=dir:<exact-resolved-implementer-worktree>' "${ORCHESTRATOR_SOUL}"
grep -Fq 'Nie twórz z wyprzedzeniem independent-review taska' "${ORCHESTRATOR_SOUL}"

printf '[check] review decisions\n'
grep -Fq 'DECISION: APPROVE' "${CONTRACT}"
grep -Fq 'DECISION: CHANGES_REQUIRED' "${CONTRACT}"
grep -Fq 'DECISION: SKIPPED_OX_UNAVAILABLE' "${CONTRACT}"
grep -Fq 'REVIEW_PENDING' "${CONTRACT}"
grep -Fq '`severity`: HIGH' "${CONTRACT}"
grep -Fq 'dodatkowy nieobsługiwany marker `DECISION:`' "${CONTRACT}"

printf '[check] mandatory deployment step\n'
grep -Fq 'DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh' "${CONTRACT}"
grep -Fq 'Software Factory nie jest gotowy do uruchamiania tasków Kanban' "${CONTRACT}"

printf '[check] python syntax\n'
python3 -m py_compile \
  "${PARSER}" \
  "${PARSER_TESTS}" \
  "${RUNTIME_VALIDATOR}" \
  "${RUNTIME_TESTS}"

printf '[check] parser tests\n'
(
  cd "${ROOT_DIR}/hermes"
  python3 -m unittest -q test_review_decision.py
)

printf '[check] runtime contract tests\n'
(
  cd "${ROOT_DIR}/hermes"
  python3 -m unittest -q test_kanban_runtime_contract.py
)

printf '[check] bootstrap compatibility\n'
bash "${BOOTSTRAP_VERIFY}"

printf 'OK: weryfikacja kontraktu Kanban zakończona\n'
