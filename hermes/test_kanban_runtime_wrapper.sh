#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

cat >"${TMP_DIR}/hermes" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >"${HERMES_FAKE_LOG:?}"
EOF
chmod +x "${TMP_DIR}/hermes"

export PATH="${TMP_DIR}:${PATH}"
export HERMES_FAKE_LOG="${TMP_DIR}/argv.log"

bash "${WRAPPER}" block t_gate RUNTIME CONTRACT PENDING
mapfile -t argv <"${HERMES_FAKE_LOG}"
expected=(kanban block --kind needs_input t_gate "RUNTIME CONTRACT PENDING")
[[ "${#argv[@]}" -eq "${#expected[@]}" ]]
for i in "${!expected[@]}"; do
  [[ "${argv[$i]}" == "${expected[$i]}" ]] || {
    echo "ERROR: argv[$i] expected '${expected[$i]}', got '${argv[$i]}'" >&2
    exit 1
  }
done

rm -f "${HERMES_FAKE_LOG}"
set +e
bash "${WRAPPER}" block t_gate --kind transient SMUGGLE >/dev/null 2>&1
rc=$?
set -e
[[ "${rc}" -eq 2 ]] || { echo "ERROR: flag-shaped block reason expected exit 2, got ${rc}" >&2; exit 1; }
[[ ! -e "${HERMES_FAKE_LOG}" ]] || { echo "ERROR: rejected block reason must not invoke hermes" >&2; exit 1; }

printf 'OK: scoped runtime wrapper block hardening\n'
