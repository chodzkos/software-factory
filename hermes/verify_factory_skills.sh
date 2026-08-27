#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}"
INSTALLED_ONLY="${FACTORY_SKILLS_INSTALLED_ONLY:-0}"

if [[ "$INSTALLED_ONLY" == "1" ]]; then
  [[ $# -eq 2 && "$1" == "--profile" ]] || { echo "usage: FACTORY_SKILLS_INSTALLED_ONLY=1 bash hermes/verify_factory_skills.sh --profile NAME" >&2; exit 2; }
else
  printf '[check] installer/verifier syntax\n'
  bash -n "$ROOT_DIR/hermes/install_factory_skills.sh"
  bash -n "$ROOT_DIR/hermes/verify_factory_skills.sh"
  bash -n "$ROOT_DIR/hermes/test_factory_skill_installer.sh"

  printf '[check] manifest/profile/routing tests\n'
  (cd "$ROOT_DIR/skills/tests" && python3 -m unittest -v test_factory_skills.py)

  printf '[check] installer adversarial tests\n'
  bash "$ROOT_DIR/hermes/test_factory_skill_installer.sh"

  printf '[check] dry-run all factory skills\n'
  tmp_dest="$(mktemp -d)"
  trap 'rm -rf -- "$tmp_dest"' EXIT
  HERMES_SKILLS_DIR="$tmp_dest" bash "$ROOT_DIR/hermes/install_factory_skills.sh" --all --dry-run
fi

if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && "$1" == "--profile" ]] || { echo "usage: bash hermes/verify_factory_skills.sh [--profile NAME]" >&2; exit 2; }
  profile="$2"
  selection="$(python3 - "$ROOT_DIR/skills/manifest.yaml" "$ROOT_DIR/skills/profiles.yaml" "$profile" <<'PY'
import json,sys
manifest=json.load(open(sys.argv[1]))
p=json.load(open(sys.argv[2]))["profiles"]
name=sys.argv[3]
if name not in p:
    raise SystemExit(f"ERROR: unknown profile: {name}")
for skill in p[name]["required"] + p[name]["optional"]:
    if skill not in manifest["skills"]:
        raise SystemExit(f"ERROR: undeclared skill in profile {name}: {skill}")
    spec=manifest["skills"][skill]
    digest=spec.get("upstream", {}).get("sha256", "-")
    print(f"{skill}\t{spec['path']}\t{spec['source']}\t{digest}")
PY
)"
  entries=()
  if [[ -n "$selection" ]]; then
    mapfile -t entries <<<"$selection"
  fi
  printf '[check] installed profile skills for %s\n' "$profile"
  for entry in "${entries[@]}"; do
    IFS=$'\t' read -r skill relpath source expected_sha <<<"$entry"
    src="$ROOT_DIR/$relpath"
    target="$DEST/$skill"
    [[ -L "$target" || -L "$target/SKILL.md" ]] && { echo "ERROR: installed skill is symlink: $skill" >&2; exit 1; }
    [[ -f "$target/SKILL.md" ]] || { echo "ERROR: missing installed skill: $skill" >&2; exit 1; }
    if [[ "$source" == "upstream-vendored" ]]; then
      mapfile -t target_entries < <(find "$target" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
      [[ ${#target_entries[@]} -eq 1 && "${target_entries[0]}" == "SKILL.md" ]] || { echo "ERROR: installed upstream skill contains unexpected files: $skill" >&2; exit 1; }
      actual_sha="$(python3 - "$target/SKILL.md" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
      [[ "$actual_sha" == "$expected_sha" ]] || { echo "ERROR: installed upstream skill digest drift: $skill" >&2; exit 1; }
    else
      diff -qr "$src" "$target" >/dev/null || { echo "ERROR: installed skill drift: $skill" >&2; exit 1; }
    fi
    echo "OK: $skill"
  done
fi

if [[ "$INSTALLED_ONLY" == "1" ]]; then
  echo 'FACTORY_SKILLS_INSTALLED_OK'
else
  echo 'FACTORY_SKILLS_VERIFY_OK'
fi
