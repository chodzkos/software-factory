#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
TMP_DIR="$(mktemp -d)"
ORIGINAL_PATH="${PATH}"
trap 'rm -rf "${TMP_DIR}"' EXIT

make_fake_python() {
  local path="$1"
  mkdir -p "$(dirname "${path}")"
  cat >"${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" -eq 3 && "$1" == "-I" && "$2" == "-c" && "$3" == "import hermes_cli" ]]; then
  exit 0
fi
printf '%s\n' "$@" >"${HERMES_TEST_PY_LOG:?}"
exit 37
EOF
  chmod +x "${path}"
}

expect_helper_exec() {
  local label="$1"
  local rc
  rm -f "${HERMES_TEST_PY_LOG}"
  set +e
  bash "${WRAPPER}" dispatch-review --task-id t_probe >/dev/null 2>&1
  rc=$?
  set -e
  [[ ${rc} -eq 37 ]] || { echo "ERROR: ${label}: expected fake Hermes Python rc=37, got ${rc}" >&2; exit 1; }
  mapfile -t argv <"${HERMES_TEST_PY_LOG}"
  local expected=("${ROOT_DIR}/hermes/kanban_review_dispatch.py" "--task-id" "t_probe")
  [[ ${#argv[@]} -eq ${#expected[@]} ]] || { echo "ERROR: ${label}: helper argv length mismatch" >&2; exit 1; }
  local i
  for i in "${!expected[@]}"; do
    [[ "${argv[$i]}" == "${expected[$i]}" ]] || { echo "ERROR: ${label}: argv[$i] mismatch" >&2; exit 1; }
  done
  echo "OK: ${label}"
}

expect_fail_closed() {
  local label="$1"
  local rc
  rm -f "${HERMES_TEST_PY_LOG}"
  set +e
  bash "${WRAPPER}" dispatch-review --task-id t_probe >/dev/null 2>&1
  rc=$?
  set -e
  [[ ${rc} -eq 2 ]] || { echo "ERROR: ${label}: expected rc=2, got ${rc}" >&2; exit 1; }
  [[ ! -e "${HERMES_TEST_PY_LOG}" ]] || { echo "ERROR: ${label}: invalid launcher reached helper interpreter" >&2; exit 1; }
  echo "OK fail-closed: ${label}"
}

export HERMES_TEST_PY_LOG="${TMP_DIR}/python-argv.log"
FAKE_PY="${TMP_DIR}/python-hermes-test"
INNER="${TMP_DIR}/inner-hermes"
make_fake_python "${FAKE_PY}"
printf '#!/usr/bin/env python3\n' >"${INNER}"
chmod +x "${INNER}"

# Reproduce the real Hermes Agent 0.20.4 launcher shape:
#   #!/usr/bin/env bash
#   exec "/.../venv/bin/python" "/.../hermes" "$@"
cat >"${TMP_DIR}/hermes" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "${FAKE_PY}" "${INNER}" "\$@"
EOF
chmod +x "${TMP_DIR}/hermes"
PATH="${TMP_DIR}:${ORIGINAL_PATH}"
export PATH
expect_helper_exec "Hermes 0.20.4 bash launcher resolves literal venv Python"

# Direct env-python launchers remain supported, but only after import probe.
cat >"${TMP_DIR}/hermes" <<'EOF'
#!/usr/bin/env python-hermes-test
EOF
chmod +x "${TMP_DIR}/hermes"
expect_helper_exec "simple /usr/bin/env python launcher"

# Direct absolute Python shebang remains supported.
printf '#!%s\n' "${FAKE_PY}" >"${TMP_DIR}/hermes"
chmod +x "${TMP_DIR}/hermes"
expect_helper_exec "absolute Python launcher"

# env bash without the strict literal exec shape must never be treated as Python.
cat >"${TMP_DIR}/hermes" <<'EOF'
#!/usr/bin/env bash
echo not-a-supported-hermes-launcher
EOF
chmod +x "${TMP_DIR}/hermes"
expect_fail_closed "unrecognized bash launcher"

# Argument-bearing env shebangs are deliberately unsupported.
cat >"${TMP_DIR}/hermes" <<'EOF'
#!/usr/bin/env python3 -u
EOF
chmod +x "${TMP_DIR}/hermes"
expect_fail_closed "argument-bearing env shebang"

# More than one matching exec line is ambiguous and must fail closed.
cat >"${TMP_DIR}/hermes" <<EOF
#!/usr/bin/env bash
exec "${FAKE_PY}" "${INNER}" "\$@"
exec "${FAKE_PY}" "${INNER}" "\$@"
EOF
chmod +x "${TMP_DIR}/hermes"
expect_fail_closed "ambiguous multiple literal exec lines"

# Controlled standard-layout fallback: only under ~/.local/bin/hermes and only
# when the managed Hermes venv candidate proves it imports hermes_cli.
FAKE_HOME="${TMP_DIR}/home"
FALLBACK_PY="${FAKE_HOME}/.hermes/hermes-agent/venv/bin/python"
make_fake_python "${FALLBACK_PY}"
mkdir -p "${FAKE_HOME}/.local/bin"
cat >"${FAKE_HOME}/.local/bin/hermes" <<'EOF'
#!/usr/bin/env bash
# Deliberately no parseable exec line: exercise controlled managed-install fallback.
exit 99
EOF
chmod +x "${FAKE_HOME}/.local/bin/hermes"
HOME="${FAKE_HOME}"
PATH="${FAKE_HOME}/.local/bin:${ORIGINAL_PATH}"
export HOME PATH
expect_helper_exec "managed Hermes venv fallback"

printf 'OK: targeted review helper binds to a Hermes Python runtime, including the production bash-wrapper shape\n'
