#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  kanban_runtime_cli.sh create <hermes-kanban-create-args...>
  kanban_runtime_cli.sh show <task-id> [--json]
  kanban_runtime_cli.sh block <task-id> <reason...>
  kanban_runtime_cli.sh complete <task-id> <summary...>

This wrapper intentionally exposes only the Kanban operations required by the
Software Factory runtime-controller. It never evals input and never executes an
arbitrary shell command.
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
    exec hermes kanban block --kind needs_input "${task_id}" "$@"
    ;;
  complete)
    [[ $# -ge 2 ]] || usage
    task_id="$1"
    shift
    exec hermes kanban complete "${task_id}" "$@"
    ;;
  *)
    usage
    ;;
esac
