#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="${SCRIPT_DIR}/kanban_runtime_contract.py"
MODEL_ROUTING_VALIDATOR="${SCRIPT_DIR}/model_routing_policy.py"
REVIEW_DISPATCHER="${SCRIPT_DIR}/kanban_review_dispatch.py"

usage() {
  cat >&2 <<'EOF'
Usage:
  kanban_runtime_cli.sh create --board <slug> <hermes-kanban-create-args...>
  kanban_runtime_cli.sh show --board <slug> <task-id> [--json]
  kanban_runtime_cli.sh block --board <slug> <task-id> <reason...>
  kanban_runtime_cli.sh complete --board <slug> <task-id> <summary...>
  kanban_runtime_cli.sh validate-runtime --board <slug> --task-id <task-id> <validator-runtime-expectations...>
  kanban_runtime_cli.sh validate-routed-handoff --board <slug> --task-id <task-id>
  kanban_runtime_cli.sh validate-routing-body --task-body <task-body>
  kanban_runtime_cli.sh validate-routing-live --board <slug> --task-id <task-id>
  kanban_runtime_cli.sh dispatch-review --board <slug> --task-id <task-id>
  kanban_runtime_cli.sh verify-approval --board <slug> --task-id <task-id>

Live validators fetch authoritative Kanban JSON themselves. The caller never
supplies live snapshot bytes. Review dispatch is deliberately task-id-targeted
and may run only after the provenance-bound routed-handoff gate. This wrapper
intentionally exposes only the Software Factory runtime-control operations and
never interprets input as shell source.
EOF
  exit 2
}

probe_hermes_python() {
  local candidate="$1"
  [[ "${candidate}" == /* && -x "${candidate}" ]] || return 1
  if PYTHONDONTWRITEBYTECODE=1 "${candidate}" -I -c 'import hermes_cli' >/dev/null 2>&1; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  return 1
}

resolve_python_from_bash_launcher() {
  local launcher="$1"
  local line candidate inner inner_shebang resolved
  local matches=0
  local exec_re='^exec[[:space:]]+"(/[^"[:space:]]+)"[[:space:]]+"(/[^"[:space:]]+)"[[:space:]]+"\$@"[[:space:]]*$'
  candidate=""
  inner=""

  # Hermes Agent 0.20.4 installs a PATH shim whose shebang is bash and whose
  # final command is a literal, quoted exec of the venv Python + inner Hermes
  # entrypoint. Parse only that exact literal-exec shape. Any shell expansion,
  # extra argv, command substitution, relative path, or multiple matching exec
  # lines is rejected rather than interpreted.
  while IFS= read -r line; do
    if [[ "${line}" =~ ${exec_re} ]]; then
      matches=$((matches + 1))
      candidate="${BASH_REMATCH[1]}"
      inner="${BASH_REMATCH[2]}"
    fi
  done <"${launcher}"

  if [[ ${matches} -eq 1 && -r "${inner}" ]]; then
    inner_shebang="$(head -n 1 "${inner}" 2>/dev/null || true)"
    if [[ "${inner_shebang}" == '#!'*python* ]]; then
      if resolved="$(probe_hermes_python "${candidate}")"; then
        printf '%s\n' "${resolved}"
        return 0
      fi
    fi
  fi

  # Controlled compatibility fallback for the standard Hermes managed install
  # layout. It is considered only for a launcher under the user's Hermes/local
  # installation roots, and every candidate must prove it can import hermes_cli
  # in isolated Python mode before it is accepted.
  case "${launcher}" in
    "${HOME}/.local/bin/hermes"|"${HOME}/.hermes/"*) ;;
    *) return 1 ;;
  esac
  for candidate in \
    "${HOME}/.hermes/hermes-agent/.venv/bin/python" \
    "${HOME}/.hermes/hermes-agent/venv/bin/python"
  do
    if resolved="$(probe_hermes_python "${candidate}")"; then
      printf '%s\n' "${resolved}"
      return 0
    fi
  done
  return 1
}

resolve_hermes_python() {
  local hermes_bin hermes_real shebang payload candidate resolved base
  hermes_bin="$(command -v hermes)" || return 1
  hermes_real="$(readlink -f "${hermes_bin}" 2>/dev/null || printf '%s' "${hermes_bin}")"
  [[ "${hermes_real}" == /* && -r "${hermes_real}" ]] || return 1
  shebang="$(head -n 1 "${hermes_real}" 2>/dev/null || true)"

  case "${shebang}" in
    '#!/usr/bin/env '*)
      payload="${shebang#\#!/usr/bin/env }"
      [[ -n "${payload}" && "${payload}" != -* && "${payload}" != *[[:space:]]* ]] || return 1
      case "${payload}" in
        bash)
          resolve_python_from_bash_launcher "${hermes_real}"
          return $?
          ;;
        python*)
          candidate="$(command -v "${payload}" 2>/dev/null || true)"
          [[ -n "${candidate}" ]] || return 1
          resolved="$(probe_hermes_python "${candidate}")" || return 1
          printf '%s\n' "${resolved}"
          return 0
          ;;
        *) return 1 ;;
      esac
      ;;
    '#!'*)
      candidate="${shebang#\#!}"
      [[ "${candidate}" == /* && "${candidate}" != *[[:space:]]* && -x "${candidate}" ]] || return 1
      base="$(basename "${candidate}")"
      case "${base}" in
        bash)
          resolve_python_from_bash_launcher "${hermes_real}"
          return $?
          ;;
        python*)
          resolved="$(probe_hermes_python "${candidate}")" || return 1
          printf '%s\n' "${resolved}"
          return 0
          ;;
        *) return 1 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

run_review_dispatcher() {
  [[ -f "${REVIEW_DISPATCHER}" ]] || { echo "ERROR: missing ${REVIEW_DISPATCHER}" >&2; exit 2; }
  local hermes_python
  if ! hermes_python="$(resolve_hermes_python)"; then
    echo "ERROR: unable to resolve Hermes Python runtime capable of importing hermes_cli" >&2
    exit 2
  fi
  # Match the isolation assumptions used by the interpreter probe while
  # retaining the script directory needed for the reviewed sibling modules.
  unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT
  export PYTHONDONTWRITEBYTECODE=1
  exec "${hermes_python}" -E -s "${REVIEW_DISPATCHER}" "$@"
}

[[ $# -ge 1 ]] || usage
op="$1"
shift

case "${op}" in
  create)
    [[ $# -ge 3 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || usage
    export HERMES_KANBAN_BOARD="$2"; shift 2
    exec hermes kanban create "$@"
    ;;
  show)
    [[ $# -ge 3 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || usage
    export HERMES_KANBAN_BOARD="$2"; shift 2
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
    [[ $# -ge 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || usage
    export HERMES_KANBAN_BOARD="$2"; shift 2
    task_id="$1"
    shift
    for reason_part in "$@"; do
      [[ "${reason_part}" != -* ]] || { echo "ERROR: block reason must not contain flag-shaped operands" >&2; exit 2; }
    done
    reason="$*"
    exec hermes kanban block --kind needs_input "${task_id}" "${reason}"
    ;;
  complete)
    [[ $# -ge 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || usage
    export HERMES_KANBAN_BOARD="$2"; shift 2
    task_id="$1"
    shift
    summary="$*"
    exec hermes kanban complete "${task_id}" --result "${summary}" --summary "${summary}"
    ;;
  validate-runtime)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -ge 6 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && "$3" == "--task-id" ]] || usage
    exec python3 "${VALIDATOR}" runtime "$@"
    ;;
  validate-routed-handoff)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && "$3" == "--task-id" && -n "$4" && "$4" != -* ]] || usage
    exec python3 "${VALIDATOR}" routed-handoff "$@"
    ;;
  validate-routing-body)
    [[ -f "${MODEL_ROUTING_VALIDATOR}" ]] || { echo "ERROR: missing ${MODEL_ROUTING_VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 2 && "$1" == "--task-body" && -n "$2" ]] || usage
    exec python3 "${MODEL_ROUTING_VALIDATOR}" "$@"
    ;;
  validate-routing-live)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && "$3" == "--task-id" && -n "$4" && "$4" != -* ]] || usage
    exec python3 "${VALIDATOR}" routing-live "$@"
    ;;
  dispatch-review)
    [[ $# -eq 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && "$3" == "--task-id" && -n "$4" && "$4" != -* ]] || usage
    run_review_dispatcher "$@"
    ;;
  verify-approval)
    [[ -f "${VALIDATOR}" ]] || { echo "ERROR: missing ${VALIDATOR}" >&2; exit 2; }
    [[ $# -eq 4 && "$1" == "--board" && "$2" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && "$3" == "--task-id" && -n "$4" && "$4" != -* ]] || usage
    exec python3 "${VALIDATOR}" approval "$@"
    ;;
  *)
    usage
    ;;
esac
