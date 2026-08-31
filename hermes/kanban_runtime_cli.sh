#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="${SCRIPT_DIR}/kanban_runtime_contract.py"
MODEL_ROUTING_VALIDATOR="${SCRIPT_DIR}/model_routing_policy.py"

usage() {
  cat >&2 <<'EOF'
Usage:
  kanban_runtime_cli.sh create <hermes-kanban-create-args...>
  kanban_runtime_cli.sh show <task-id> [--json]
  kanban_runtime_cli.sh block <task-id> <reason...>
  kanban_runtime_cli.sh complete <task-id> <summary...>
  kanban_runtime_cli.sh validate-runtime --task-id <task-id> <validator-runtime-expectations...>
  kanban_runtime_cli.sh validate-routed-handoff --task-id <task-id>
  kanban_runtime_cli.sh validate-routing-body --task-body <task-body>
  kanban_runtime_cli.sh validate-routing-live --task-id <task-id>

Live validators fetch authoritative Kanban JSON themselves. The caller never
supplies live snapshot bytes. This wrapper intentionally exposes only the
Software Factory runtime-control operations and never evals input.
EOF
  exit 2
}

[[ $# -ge 1 ]] || usage
op="$1"
shift

case "${op}" in
  create)
    [[ $# -ge 1 ]] || usage
    exec hermes kanban create "$@"
    ;;
  show)
    [[ $# -ge 1 ]] || usage
    task_id="$1"
    shift
    if [[ $# -eq 0 ]]; then
      exec hermes kanban show "${task_id}"
    fi
    if [[ $# -eq 1 && "$1" == "--json" ]]; then
      exec hermes kanban show "${task_id}" --json
    fi
    usage
    ;;
  block)
    [[ $# -ge 2 ]] || usage
    task_id="$1"
    shift
    for reason_part in "$@"; do
      [[ "${reason_part}" != -* ]] || { echo "ERROR: block reason must not contain flag-shaped operands" >&2; exit 2; }
    done
    reason="$*"
    exec hermes kanban block --kind needs_input "${task_id}" "${reason}"
    ;;
  complete)
    [[ $# -ge 2 ]] || usage
    task_id="$1"
    shift
    summary="$*"
    exec hermes kanban complete "${task_id}" --result "${summary}" --summary "${summary}"
    ;;
  validate-runtime)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    exec python3 "${VALIDATOR}" runtime "$@"
    ;;
  validate-routed-handoff)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 2 && "$1" == "--task-id" ]] || usage
    exec python3 "${VALIDATOR}" routed-handoff "$@"
    ;;
  validate-routing-body)
    [[ -f "${MODEL_ROUTING_VALIDATOR}" ]] || { echo "ERROR: missing ${MODEL_ROUTING_VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 2 && "$1" == "--task-body" ]] || usage
    exec python3 "${MODEL_ROUTING_VALIDATOR}" "$@"
    ;;
  validate-routing-live)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 2 && "$1" == "--task-id" ]] || usage
    exec python3 "${VALIDATOR}" routing-live "$@"
    ;;
  *)
    usage
    ;;
esac
