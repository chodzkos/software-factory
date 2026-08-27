#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/hermes/install_factory_skills.sh"
VERIFIER="$ROOT_DIR/hermes/verify_factory_skills.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

expect_fail() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "ERROR: expected failure: $label" >&2
    exit 1
  fi
  echo "OK fail-closed: $label"
}

printf '[installer-test] argument validation\n'
expect_fail "no selector" bash "$INSTALLER"
expect_fail "conflicting selectors" bash "$INSTALLER" --all --profile coder
expect_fail "unknown profile" env HERMES_SKILLS_DIR="$TMP_ROOT/unknown" bash "$INSTALLER" --profile definitely-not-a-profile --dry-run
expect_fail "unexpected argument" bash "$INSTALLER" --wat

printf '[installer-test] dry-run is no-write\n'
dry_dest="$TMP_ROOT/dry target"
HERMES_SKILLS_DIR="$dry_dest" bash "$INSTALLER" --profile coder --dry-run >/dev/null
[[ ! -e "$dry_dest" ]] || { echo "ERROR: dry-run created destination" >&2; exit 1; }

printf '[installer-test] identical target accepted\n'
identical_dest="$TMP_ROOT/identical"
mkdir -p "$identical_dest/sha-integrity-check"
cp -a "$ROOT_DIR/skills/custom/sha-integrity-check/." "$identical_dest/sha-integrity-check/"
HERMES_SKILLS_DIR="$identical_dest" bash "$INSTALLER" --profile repository-analyst >/dev/null

printf '[installer-test] differing target refused before writes\n'
diff_dest="$TMP_ROOT/different"
mkdir -p "$diff_dest/ci-failure-recovery"
printf 'drift\n' > "$diff_dest/ci-failure-recovery/SKILL.md"
expect_fail "differing installed target" env HERMES_SKILLS_DIR="$diff_dest" bash "$INSTALLER" --profile coder
[[ ! -e "$diff_dest/evidence-ledger" ]] || { echo "ERROR: preflight allowed partial install" >&2; exit 1; }

printf '[installer-test] symlink target refused\n'
outside="$TMP_ROOT/outside"
mkdir -p "$outside"
cp -a "$ROOT_DIR/skills/custom/sha-integrity-check/." "$outside/"
symlink_dest="$TMP_ROOT/symlink"
mkdir -p "$symlink_dest"
ln -s "$outside" "$symlink_dest/sha-integrity-check"
expect_fail "symlink installed target" env HERMES_SKILLS_DIR="$symlink_dest" bash "$INSTALLER" --profile repository-analyst

printf '[installer-test] verifier checks optional installed skills\n'
optional_missing_dest="$TMP_ROOT/optional-missing"
HERMES_SKILLS_DIR="$optional_missing_dest" bash "$INSTALLER" --profile coder >/dev/null
FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$optional_missing_dest" bash "$VERIFIER" --profile coder >/dev/null
rm -rf -- "$optional_missing_dest/ci-failure-recovery"
expect_fail "missing optional installed skill" env FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$optional_missing_dest" bash "$VERIFIER" --profile coder

optional_drift_dest="$TMP_ROOT/optional-drift"
HERMES_SKILLS_DIR="$optional_drift_dest" bash "$INSTALLER" --profile coder >/dev/null
printf '\nregression-drift\n' >> "$optional_drift_dest/ci-failure-recovery/SKILL.md"
expect_fail "drifted optional installed skill" env FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$optional_drift_dest" bash "$VERIFIER" --profile coder

echo 'OK: factory skill installer adversarial checks'
