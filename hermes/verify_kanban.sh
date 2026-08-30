#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="${ROOT_DIR}/hermes/configure_kanban.sh"
CONTRACT="${ROOT_DIR}/workflows/KANBAN_CONTRACT.md"
MODEL_POLICY_DOC="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
PARSER="${ROOT_DIR}/hermes/review_decision.py"
RUNTIME_VALIDATOR="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"
RUNTIME_WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
RUNTIME_WRAPPER_TEST="${ROOT_DIR}/hermes/test_kanban_runtime_wrapper.sh"
BOOTSTRAP_VERIFY="${ROOT_DIR}/hermes/verify_bootstrap.sh"

printf '[check] bash syntax\n'
bash -n "${CONFIGURE}" "${RUNTIME_WRAPPER}" "${RUNTIME_WRAPPER_TEST}"

printf '[check] dispatcher policy\n'
grep -Fq 'config set kanban.auto_decompose false' "${CONFIGURE}"
grep -Fq 'config set kanban.auto_subscribe_on_create true' "${CONFIGURE}"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${CONFIGURE}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${CONFIGURE}"

printf '[check] task contract baseline\n'
grep -Fq 'SECURITY_SENSITIVE: yes|no' "${CONTRACT}"
grep -Fq 'worktree:<absolute-repo-path>' "${CONTRACT}"
grep -Fq 'IMPLEMENTED != VERIFIED' "${CONTRACT}"
grep -Fq 'RUNTIME_CONTRACT_DRIFT' "${CONTRACT}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${CONTRACT}"
test -f "${MODEL_POLICY_DOC}"

printf '[check] exact model routing\n'
grep -Fq 'security_sensitive_openai_implementer_forbidden' "${MODEL_ROUTING}"
grep -Fq 'reviewer_set_mismatch:' "${MODEL_ROUTING}"
grep -Fq 'malformed_required_reviewers_csv' "${MODEL_ROUTING}"
grep -Fq 'required_reviewers_none_forbidden' "${MODEL_ROUTING}"
grep -Fq 'actual_json_duplicate_key:' "${MODEL_ROUTING}"
grep -Fq 'actual_json_task_not_object' "${MODEL_ROUTING}"
grep -Fq '`coder` | `yes` | **forbidden**' "${MODEL_POLICY_DOC}"
grep -Fq '`coder-claude` | `yes` | `reviewer-gpt`' "${MODEL_POLICY_DOC}"

printf '[check] body-bound live handoff\n'
grep -Fq 'def validate_routed_review_handoff' "${RUNTIME_VALIDATOR}"
grep -Fq 'route_from_payload(payload)' "${RUNTIME_VALIDATOR}"
grep -Fq 'same_card_review_requires_exactly_one_reviewer' "${RUNTIME_VALIDATOR}"
grep -Fq 'routed-handoff' "${RUNTIME_VALIDATOR}"
grep -Fq 'validate-routed-handoff' "${RUNTIME_WRAPPER}"
grep -Fq 'exec python3 "${VALIDATOR}" routed-handoff "$@"' "${RUNTIME_WRAPPER}"

printf '[check] scoped runtime wrapper\n'
grep -Fq 'case "${op}" in' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban create "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban show "${task_id}" --json' "${RUNTIME_WRAPPER}"
grep -Fq 'block reason must not contain flag-shaped operands' "${RUNTIME_WRAPPER}"
if grep -Fq 'eval ' "${RUNTIME_WRAPPER}"; then echo 'ERROR: runtime wrapper must not use eval' >&2; exit 1; fi
PYTHONDONTWRITEBYTECODE=1 bash "${RUNTIME_WRAPPER_TEST}"

printf '[check] python syntax\n'
PYTHONDONTWRITEBYTECODE=1 python3 - "${PARSER}" "${RUNTIME_VALIDATOR}" "${MODEL_ROUTING}" <<'PY'
from pathlib import Path
import sys
for raw in sys.argv[1:]:
    p=Path(raw); compile(p.read_text(encoding='utf-8'), str(p), 'exec')
print('OK: Python syntax compiled in-memory')
PY

printf '[check] review decision tests\n'
(cd "${ROOT_DIR}/hermes" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_review_decision.py)

printf '[check] runtime contract tests\n'
(cd "${ROOT_DIR}/hermes" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_kanban_runtime_contract.py)

printf '[check] model routing tests from repo root\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_model_routing_policy)

printf '[check] execution guard tests\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_factory_execution_guards)

printf '[check] bootstrap compatibility\n'
bash "${BOOTSTRAP_VERIFY}"

printf 'OK: weryfikacja Kanban, routed handoff i execution guards zakończona\n'
