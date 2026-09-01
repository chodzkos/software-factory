#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="${ROOT_DIR}/hermes/kanban_runtime_cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

cat >"${TMP_DIR}/hermes" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "kanban show t_live --json" && -n "${HERMES_FAKE_SHOW_JSON:-}" ]]; then
  printf '%s\n' "${HERMES_FAKE_SHOW_JSON}"
  exit 0
fi
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
  [[ "${argv[$i]}" == "${expected[$i]}" ]] || { echo "ERROR: argv[$i] mismatch" >&2; exit 1; }
done

rm -f "${HERMES_FAKE_LOG}"
set +e
bash "${WRAPPER}" block t_gate --kind transient SMUGGLE >/dev/null 2>&1
rc=$?
set -e
[[ "${rc}" -eq 2 ]]
[[ ! -e "${HERMES_FAKE_LOG}" ]]

# Legacy and caller-supplied live snapshot operations must be absent.
for bad in \
  "validate-handoff --actual-json {} --implementer-profile coder --reviewer-profile reviewer-gpt" \
  "validate-routed-handoff --actual-json {}" \
  "validate-routing-live --actual-json {}"
do
  rm -f "${HERMES_FAKE_LOG}"
  set +e
  bash "${WRAPPER}" ${bad} >/dev/null 2>&1
  rc=$?
  set -e
  [[ "${rc}" -eq 2 ]] || { echo "ERROR: unsafe operation unexpectedly exposed: ${bad}" >&2; exit 1; }
  [[ ! -e "${HERMES_FAKE_LOG}" ]] || { echo "ERROR: unsafe operation invoked hermes: ${bad}" >&2; exit 1; }
done

# Targeted reviewer dispatch accepts exactly: dispatch-review --task-id <id>.
# These malformed forms must fail before the wrapper attempts its pinned-Python helper.
for bad in \
  "dispatch-review" \
  "dispatch-review --task-id" \
  "dispatch-review --task-id --evil" \
  "dispatch-review --task-id t_live extra" \
  "dispatch-review --actual-json {}" \
  "dispatch-review t_live"
do
  rm -f "${HERMES_FAKE_LOG}"
  set +e
  bash "${WRAPPER}" ${bad} >/dev/null 2>&1
  rc=$?
  set -e
  [[ "${rc}" -eq 2 ]] || { echo "ERROR: malformed targeted dispatch unexpectedly accepted: ${bad}" >&2; exit 1; }
  [[ ! -e "${HERMES_FAKE_LOG}" ]] || { echo "ERROR: malformed targeted dispatch invoked hermes: ${bad}" >&2; exit 1; }
done

NORMAL_BODY=$'## Task Contract\nTYPE: feature\nRISK: medium\nSECURITY_SENSITIVE: no\nASSIGNEE: coder\nREPOSITORY: owner/repo\nWORKSPACE: worktree:/repo\nIMPLEMENTER: coder\nREQUIRED_REVIEWERS: reviewer-claude\nOPTIONAL_REVIEWERS: none\nREQUIRED_EVIDENCE: tests\nACCEPTANCE_CRITERIA:\n- works\n'
routing_body_ok="$(bash "${WRAPPER}" validate-routing-body --task-body "${NORMAL_BODY}")"
[[ "${routing_body_ok}" == "MODEL_ROUTING_OK" ]] || { echo "ERROR: expected MODEL_ROUTING_OK, got ${routing_body_ok}" >&2; exit 1; }

repo="${TMP_DIR}/repo"
worktree="${repo}/.worktrees/t_live"
mkdir -p "${worktree}"
LIVE_BODY=$'## Task Contract\nTYPE: feature\nRISK: medium\nSECURITY_SENSITIVE: no\nASSIGNEE: coder\nREPOSITORY: owner/repo\nIMPLEMENTER: coder\nREQUIRED_REVIEWERS: reviewer-claude\nOPTIONAL_REVIEWERS: none\nREQUIRED_EVIDENCE: tests\nACCEPTANCE_CRITERIA:\n- works\n'
LIVE_BODY+="WORKSPACE: worktree:${repo}"$'\n'
export HERMES_FAKE_SHOW_JSON="$(python3 -c 'import json,sys; repo,worktree,body=sys.argv[1:]; print(json.dumps({"task":{"id":"t_live","body":body,"assignee":"reviewer-claude","status":"review","workspace_kind":"worktree","workspace_path":worktree},"events":[{"kind":"review_requested","payload":{"implementer":"coder","reviewer":"reviewer-claude"},"run_id":17}],"runs":[{"id":17,"profile":"coder","outcome":"review_requested","metadata":{"task_id":"t_live","workspace_path":worktree}}]}))' "${repo}" "${worktree}" "${LIVE_BODY}")"

routing_live_ok="$(bash "${WRAPPER}" validate-routing-live --task-id t_live)"
[[ "${routing_live_ok}" == "MODEL_ROUTING_OK" ]] || { echo "ERROR: live routing failed: ${routing_live_ok}" >&2; exit 1; }
handoff_ok="$(bash "${WRAPPER}" validate-routed-handoff --task-id t_live)"
[[ "${handoff_ok}" == "RUNTIME_CONTRACT_OK" ]] || { echo "ERROR: live handoff failed: ${handoff_ok}" >&2; exit 1; }

SECURITY_BAD_BODY=$'## Task Contract\nTYPE: review\nRISK: high\nSECURITY_SENSITIVE: yes\nASSIGNEE: reviewer-claude\nREPOSITORY: owner/repo\nWORKSPACE: worktree:/repo\nIMPLEMENTER: coder-claude\nREQUIRED_REVIEWERS: reviewer-gpt,reviewer-claude\nOPTIONAL_REVIEWERS: none\nREQUIRED_EVIDENCE: security review\nACCEPTANCE_CRITERIA:\n- reviewed\n'
set +e
routing_bad="$(bash "${WRAPPER}" validate-routing-body --task-body "${SECURITY_BAD_BODY}" 2>&1)"
rc=$?
set -e
[[ "${rc}" -eq 2 ]]
[[ "${routing_bad}" == MODEL_ROUTING_DRIFT:*anthropic_security_reviewer_forbidden* ]]

printf 'OK: scoped runtime wrapper binds live validation to internal kanban show and seals targeted review dispatch\n'
