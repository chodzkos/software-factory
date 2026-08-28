#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/hermes/install_factory_skills.sh"
VERIFIER="$ROOT_DIR/hermes/verify_factory_skills.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

expect_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "ERROR: expected failure: $label" >&2
    exit 1
  fi
  echo "OK fail-closed: $label"
}

make_root() {
  local dst="$1"
  mkdir -p "$dst/hermes" "$dst/skills"
  cp "$INSTALLER" "$dst/hermes/install_factory_skills.sh"
  cp "$ROOT_DIR/skills/manifest.yaml" "$dst/skills/manifest.yaml"
  cp "$ROOT_DIR/skills/profiles.yaml" "$dst/skills/profiles.yaml"
  cp -a "$ROOT_DIR/skills/custom" "$dst/skills/"
  mkdir -p "$dst/skills/upstream"
  cp -a "$ROOT_DIR/skills/upstream/bug-diagnosis" "$dst/skills/upstream/"
}

printf '[multifile-test] normal repository-analyst install\n'
dest="$TMP/normal"
HERMES_SKILLS_DIR="$dest" bash "$INSTALLER" --profile repository-analyst >/dev/null
[[ -f "$dest/factory-repo-map/SKILL.md" ]]
[[ -f "$dest/factory-repo-map/REVIEW.md" ]]
[[ -f "$dest/factory-repo-map/scripts/repo_map.py" ]]
[[ -f "$dest/factory-repo-map/scripts/run_repo_map.py" ]]
FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$dest" bash "$VERIFIER" --profile repository-analyst >/dev/null

printf '[multifile-test] runtime import does not mutate installed tree\n'
workspace="$TMP/workspace"
mkdir -p "$workspace"
printf 'def safe(): pass\n' > "$workspace/safe.py"
env \
  HERMES_KANBAN_TASK=t_multifile_test \
  HERMES_KANBAN_WORKSPACE="$workspace" \
  HERMES_PROFILE=repository-analyst \
  python3 "$dest/factory-repo-map/scripts/run_repo_map.py" . >/dev/null
[[ ! -e "$dest/factory-repo-map/scripts/__pycache__" ]] || { echo 'ERROR: binder created __pycache__' >&2; exit 1; }
FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$dest" bash "$VERIFIER" --profile repository-analyst >/dev/null

echo '[multifile-test] source tamper'
root="$TMP/tamper-root"; make_root "$root"
printf '\n# tamper\n' >> "$root/skills/custom/factory-repo-map/scripts/repo_map.py"
expect_fail "multifile source blob mismatch" env HERMES_SKILLS_DIR="$TMP/tamper-dest" bash "$root/hermes/install_factory_skills.sh" --profile repository-analyst --dry-run

echo '[multifile-test] extra source file'
root="$TMP/extra-root"; make_root "$root"
printf 'unexpected\n' > "$root/skills/custom/factory-repo-map/scripts/extra.py"
expect_fail "multifile extra source file" env HERMES_SKILLS_DIR="$TMP/extra-dest" bash "$root/hermes/install_factory_skills.sh" --profile repository-analyst --dry-run

echo '[multifile-test] missing source file'
root="$TMP/missing-root"; make_root "$root"
rm "$root/skills/custom/factory-repo-map/scripts/run_repo_map.py"
expect_fail "multifile missing source file" env HERMES_SKILLS_DIR="$TMP/missing-dest" bash "$root/hermes/install_factory_skills.sh" --profile repository-analyst --dry-run

echo '[multifile-test] nested source symlink'
root="$TMP/symlink-root"; make_root "$root"
outside="$TMP/outside.py"; cp "$root/skills/custom/factory-repo-map/scripts/repo_map.py" "$outside"
rm "$root/skills/custom/factory-repo-map/scripts/repo_map.py"
ln -s "$outside" "$root/skills/custom/factory-repo-map/scripts/repo_map.py"
expect_fail "multifile nested source symlink" env HERMES_SKILLS_DIR="$TMP/symlink-dest" bash "$root/hermes/install_factory_skills.sh" --profile repository-analyst --dry-run

echo '[multifile-test] installed tamper'
dest="$TMP/installed-tamper"
HERMES_SKILLS_DIR="$dest" bash "$INSTALLER" --profile repository-analyst >/dev/null
printf '\n# installed drift\n' >> "$dest/factory-repo-map/scripts/repo_map.py"
expect_fail "installed multifile blob drift" env FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$dest" bash "$VERIFIER" --profile repository-analyst

echo '[multifile-test] installed extra file'
dest="$TMP/installed-extra"
HERMES_SKILLS_DIR="$dest" bash "$INSTALLER" --profile repository-analyst >/dev/null
printf 'extra\n' > "$dest/factory-repo-map/extra.txt"
expect_fail "installed multifile extra file" env FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$dest" bash "$VERIFIER" --profile repository-analyst

echo '[multifile-test] installed nested symlink'
dest="$TMP/installed-symlink"
HERMES_SKILLS_DIR="$dest" bash "$INSTALLER" --profile repository-analyst >/dev/null
outside="$TMP/installed-outside.py"; cp "$dest/factory-repo-map/scripts/repo_map.py" "$outside"
rm "$dest/factory-repo-map/scripts/repo_map.py"
ln -s "$outside" "$dest/factory-repo-map/scripts/repo_map.py"
expect_fail "installed multifile nested symlink" env FACTORY_SKILLS_INSTALLED_ONLY=1 HERMES_SKILLS_DIR="$dest" bash "$VERIFIER" --profile repository-analyst

echo 'OK: factory multifile installer adversarial checks'
