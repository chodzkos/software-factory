#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}"

printf '[check] installer/verifier syntax\n'
bash -n "$ROOT_DIR/hermes/install_factory_skills.sh"
bash -n "$ROOT_DIR/hermes/verify_factory_skills.sh"

printf '[check] manifest/profile/routing tests\n'
(cd "$ROOT_DIR/skills/tests" && python3 -m unittest -v test_factory_skills.py)

printf '[check] dry-run all custom skills\n'
tmp_dest="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dest"' EXIT
HERMES_SKILLS_DIR="$tmp_dest" bash "$ROOT_DIR/hermes/install_factory_skills.sh" --all --dry-run

if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && "$1" == "--profile" ]] || { echo "usage: bash hermes/verify_factory_skills.sh [--profile NAME]" >&2; exit 2; }
  profile="$2"
  selection="$(python3 - "$ROOT_DIR/skills/profiles.yaml" "$profile" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))["profiles"]
name=sys.argv[2]
if name not in p:
    raise SystemExit(f"ERROR: unknown profile: {name}")
for skill in p[name]["required"]:
    print(skill)
PY
)"
  required=()
  if [[ -n "$selection" ]]; then
    mapfile -t required <<<"$selection"
  fi
  printf '[check] installed required skills for %s\n' "$profile"
  for skill in "${required[@]}"; do
    src="$ROOT_DIR/skills/custom/$skill"
    target="$DEST/$skill"
    [[ -f "$target/SKILL.md" ]] || { echo "ERROR: missing installed skill: $skill" >&2; exit 1; }
    diff -qr "$src" "$target" >/dev/null || { echo "ERROR: installed skill drift: $skill" >&2; exit 1; }
    echo "OK: $skill"
  done
fi

echo 'FACTORY_SKILLS_VERIFY_OK'
