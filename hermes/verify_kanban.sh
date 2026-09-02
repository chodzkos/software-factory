#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="${ROOT_DIR}/hermes/configure_kanban.sh"
CONTRACT="${ROOT_DIR}/workflows/KANBAN_CONTRACT.md"
MODEL_POLICY_DOC="${ROOT_DIR}/workflows/MODEL_ROUTING_POLICY.md"
PARSER="${ROOT_DIR}/hermes/review_decision.py"
RUNTIME_VALIDATOR="${ROOT_DIR}/hermes/kanban_runtime_contract.py"
MODEL_ROUTING="${ROOT_DIR}/hermes/model_routing_policy.py"
REVIEW_DISPATCHER="${ROOT_DIR}/hermes/kanban_review_dispatch.py"
RUNTIME_WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
RUNTIME_WRAPPER_TEST="${ROOT_DIR}/hermes/test_kanban_runtime_wrapper.sh"
PYTHON_BINDING_TEST="${ROOT_DIR}/hermes/test_kanban_runtime_python_binding.sh"
REVIEW_DISPATCH_TEST="${ROOT_DIR}/hermes/test_kanban_review_dispatch.py"
TARGETED_GUARD_TEST="${ROOT_DIR}/hermes/test_targeted_review_dispatch_guard.py"
BOOTSTRAP_VERIFY="${ROOT_DIR}/hermes/verify_bootstrap.sh"
GUARD_VERSION_VERIFY="${ROOT_DIR}/hermes/verify_execution_guard_version.py"

printf '[check] bash syntax\n'
bash -n "${CONFIGURE}" "${RUNTIME_WRAPPER}" "${RUNTIME_WRAPPER_TEST}" "${PYTHON_BINDING_TEST}"

printf '[check] dispatcher policy\n'
grep -Fq 'config set kanban.auto_decompose false' "${CONFIGURE}"
grep -Fq 'config set kanban.auto_subscribe_on_create true' "${CONFIGURE}"
grep -Fq 'config set kanban.orchestrator_profile orchestrator' "${CONFIGURE}"
grep -Fq 'config set kanban.default_assignee routing-sink' "${CONFIGURE}"
grep -Fq 'config set kanban.review_dispatch false' "${CONFIGURE}"

printf '[check] task contract baseline\n'
grep -Fq 'SECURITY_SENSITIVE: yes|no' "${CONTRACT}"
grep -Fq 'worktree:<absolute-base-repository>' "${CONTRACT}"
grep -Fq 'IMPLEMENTED != VERIFIED' "${CONTRACT}"
grep -Fq 'RUNTIME_CONTRACT_DRIFT' "${CONTRACT}"
grep -Fq 'MODEL_ROUTING_DRIFT' "${CONTRACT}"
grep -Fq 'validate-routed-handoff' "${CONTRACT}"
grep -Fq 'validate-routing-live' "${CONTRACT}"
grep -Fq 'dispatch-review --task-id <task-id>' "${CONTRACT}"
grep -Fq 'kanban.review_dispatch=false' "${CONTRACT}"
test -f "${MODEL_POLICY_DOC}"

printf '[check] exact model routing\n'
grep -Fq 'security_sensitive_openai_implementer_forbidden' "${MODEL_ROUTING}"
grep -Fq 'reviewer_set_mismatch:' "${MODEL_ROUTING}"
grep -Fq 'malformed_required_reviewers_csv' "${MODEL_ROUTING}"
grep -Fq 'required_reviewers_none_forbidden' "${MODEL_ROUTING}"
grep -Fq 'actual_json_duplicate_key:' "${MODEL_ROUTING}"
grep -Fq 'strict_json_loads' "${MODEL_ROUTING}"
grep -Fq '`coder` | `yes` | **forbidden**' "${MODEL_POLICY_DOC}"
grep -Fq '`coder-claude` | `yes` | `reviewer-gpt`' "${MODEL_POLICY_DOC}"
PYTHONDONTWRITEBYTECODE=1 python3 "${GUARD_VERSION_VERIFY}" --root "${ROOT_DIR}"

printf '[check] provenance-bound live handoff\n'
grep -Fq 'def _live_snapshot(task_id: str)' "${RUNTIME_VALIDATOR}"
grep -Fq '["hermes", "kanban", "show", task_id, "--json"]' "${RUNTIME_VALIDATOR}"
grep -Fq 'strict_json_loads(value)' "${RUNTIME_VALIDATOR}"
grep -Fq 'type(raw_event_run_id) is not int' "${RUNTIME_VALIDATOR}"
grep -Fq 'type(run_id) is not int' "${RUNTIME_VALIDATOR}"
grep -Fq 'if not path.exists()' "${RUNTIME_VALIDATOR}"
grep -Fq 'current.is_symlink()' "${RUNTIME_VALIDATOR}"
grep -Fq 'validate-routed-handoff --task-id <task-id>' "${RUNTIME_WRAPPER}"
grep -Fq 'validate-routing-live --task-id <task-id>' "${RUNTIME_WRAPPER}"
if grep -F -A4 'validate-routed-handoff)' "${RUNTIME_WRAPPER}" | grep -Fq -- '--actual-json'; then echo 'ERROR: routed handoff still accepts caller JSON' >&2; exit 1; fi
if grep -F -A4 'validate-routing-live)' "${RUNTIME_WRAPPER}" | grep -Fq -- '--actual-json'; then echo 'ERROR: live routing still accepts caller JSON' >&2; exit 1; fi
if grep -Fq 'sub.add_parser("handoff")' "${RUNTIME_VALIDATOR}"; then echo 'ERROR: legacy handoff CLI remains exposed' >&2; exit 1; fi

printf '[check] atomic gated targeted review dispatch\n'
grep -Fq 'dispatch-review --task-id <task-id>' "${RUNTIME_WRAPPER}"
grep -Fq 'REVIEW_DISPATCHER=' "${RUNTIME_WRAPPER}"
grep -Fq 'resolve_python_from_bash_launcher' "${RUNTIME_WRAPPER}"
grep -Fq -- "-I -c 'import hermes_cli'" "${RUNTIME_WRAPPER}"
grep -Fq 'unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT' "${RUNTIME_WRAPPER}"
grep -Fq 'exec "${hermes_python}" -E -s "${REVIEW_DISPATCHER}" "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'hermes-agent/venv/bin/python' "${RUNTIME_WRAPPER}"
grep -Fq '_EXPECTED_HERMES_VERSION = "0.20.4"' "${REVIEW_DISPATCHER}"
grep -Fq 'if kb.review_dispatch_enabled()' "${REVIEW_DISPATCHER}"
grep -Fq 'validate_routed_review_handoff(live)' "${REVIEW_DISPATCHER}"
grep -Fq 'with kb.write_txn(conn)' "${REVIEW_DISPATCHER}"
grep -Fq 'kb.claim_review_task(_SavepointConnection(conn), task_id)' "${REVIEW_DISPATCHER}"
grep -Fq 'kb._default_spawn(claimed, resolved_workspace, board=board)' "${REVIEW_DISPATCHER}"
grep -Fq 'task_body_changed_after_validation' "${REVIEW_DISPATCHER}"
grep -Fq 'claimed_review_assignee_mismatch' "${REVIEW_DISPATCHER}"
grep -Fq 'review_requested_event_changed_after_validation' "${REVIEW_DISPATCHER}"
if grep -Fq 'hermes kanban dispatch' "${RUNTIME_WRAPPER}"; then echo 'ERROR: runtime wrapper exposes board-global dispatch' >&2; exit 1; fi

printf '[check] scoped runtime wrapper\n'
grep -Fq 'case "${op}" in' "${RUNTIME_WRAPPER}"
grep -Fq 'exec hermes kanban create "$@"' "${RUNTIME_WRAPPER}"
grep -Fq 'block reason must not contain flag-shaped operands' "${RUNTIME_WRAPPER}"
contains_eval_text() {
  awk '
    /^[[:space:]]*#/ { next }
    /eval/ { found=1 }
    END { exit(found ? 0 : 1) }
  ' "$1"
}
if contains_eval_text "${RUNTIME_WRAPPER}"; then
  echo 'ERROR: runtime wrapper must not contain executable eval text' >&2
  exit 1
fi
EVAL_FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "${EVAL_FIXTURE_DIR}"' EXIT
printf '%s\n' '# eval${IFS} is only a full-line comment' >"${EVAL_FIXTURE_DIR}/comment.sh"
printf '%s\n' 'eval${IFS}'"'"'printf EXECUTED'"'"'' >"${EVAL_FIXTURE_DIR}/command.sh"
if contains_eval_text "${EVAL_FIXTURE_DIR}/comment.sh"; then
  echo 'ERROR: full-line comment was treated as executable eval text' >&2
  exit 1
fi
contains_eval_text "${EVAL_FIXTURE_DIR}/command.sh" || {
  echo 'ERROR: eval${IFS} command form escaped the verifier' >&2
  exit 1
}
PYTHONDONTWRITEBYTECODE=1 bash "${RUNTIME_WRAPPER_TEST}"
printf '[check] Hermes Python launcher binding and env-sanitization regression\n'
PYTHONDONTWRITEBYTECODE=1 bash "${PYTHON_BINDING_TEST}"

printf '[check] python syntax\n'
PYTHONDONTWRITEBYTECODE=1 python3 - "${PARSER}" "${RUNTIME_VALIDATOR}" "${MODEL_ROUTING}" "${REVIEW_DISPATCHER}" "${REVIEW_DISPATCH_TEST}" "${TARGETED_GUARD_TEST}" <<'PY'
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
printf '[check] targeted review dispatcher tests\n'
(cd "${ROOT_DIR}/hermes" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_kanban_review_dispatch.py test_targeted_review_dispatch_guard.py)
printf '[check] routed handoff adversarial regression\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_routed_handoff_policy)
printf '[check] model routing tests from repo root\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_model_routing_policy)
printf '[check] effective execution guard adversarial tests\n'
(cd "${ROOT_DIR}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q hermes.test_factory_execution_guards hermes.test_factory_execution_guard_profile_resolution hermes.test_factory_execution_guard_terminal_args hermes.test_execution_guard_version_consistency hermes.test_targeted_review_dispatch_guard)
printf '[check] bootstrap compatibility\n'
bash "${BOOTSTRAP_VERIFY}"

printf 'OK: weryfikacja Kanban, atomic provenance-bound review claim i hardened execution guards zakończona\n'
