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
  (cd "$ROOT_DIR/skills/tests" && python3 -m unittest -v test_factory_skills.py test_repo_map_reference.py test_factory_repo_map.py)

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
manifest=json.load(open(sys.argv[1])); profiles=json.load(open(sys.argv[2]))["profiles"]; name=sys.argv[3]
if name not in profiles: raise SystemExit(f"ERROR: unknown profile: {name}")
for skill in profiles[name]["required"] + profiles[name]["optional"]:
    if skill not in manifest["skills"]: raise SystemExit(f"ERROR: undeclared skill in profile {name}: {skill}")
    spec=manifest["skills"][skill]
    digest=spec.get("upstream", {}).get("sha256", "-")
    print(f"{skill}\t{spec['path']}\t{spec['source']}\t{digest}")
PY
)"
  entries=(); [[ -z "$selection" ]] || mapfile -t entries <<<"$selection"
  printf '[check] installed profile skills for %s\n' "$profile"
  for entry in "${entries[@]}"; do
    IFS=$'\t' read -r skill relpath source expected_sha <<<"$entry"
    src="$ROOT_DIR/$relpath"; target="$DEST/$skill"
    [[ -d "$target" && ! -L "$target" ]] || { echo "ERROR: missing/invalid installed skill: $skill" >&2; exit 1; }
    if find "$target" -type l -print -quit | grep -q .; then echo "ERROR: installed skill contains symlink: $skill" >&2; exit 1; fi
    [[ -f "$target/SKILL.md" && ! -L "$target/SKILL.md" ]] || { echo "ERROR: missing installed SKILL.md: $skill" >&2; exit 1; }
    if [[ "$source" == "upstream-vendored" ]]; then
      mapfile -t target_entries < <(find "$target" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
      [[ ${#target_entries[@]} -eq 1 && "${target_entries[0]}" == "SKILL.md" ]] || { echo "ERROR: installed upstream skill contains unexpected files: $skill" >&2; exit 1; }
      actual_sha="$(python3 - "$target/SKILL.md" <<'PY'
import hashlib,pathlib,sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
      [[ "$actual_sha" == "$expected_sha" ]] || { echo "ERROR: installed upstream skill digest drift: $skill" >&2; exit 1; }
    elif [[ "$source" == "custom-multifile" ]]; then
      python3 - "$ROOT_DIR/skills/manifest.yaml" "$target" "$skill" <<'PY'
import hashlib,json,pathlib,sys
manifest=json.load(open(sys.argv[1])); root=pathlib.Path(sys.argv[2]); name=sys.argv[3]; spec=manifest["skills"][name]
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
actual=[]
for path in root.rglob("*"):
    rel=path.relative_to(root).as_posix()
    if path.is_symlink(): raise SystemExit(f"ERROR: installed multifile symlink: {name}/{rel}")
    if path.is_file(): actual.append(rel)
    elif not path.is_dir(): raise SystemExit(f"ERROR: installed multifile non-regular: {name}/{rel}")
declared=sorted(spec["files"])
if sorted(actual) != declared: raise SystemExit(f"ERROR: installed multifile tree drift: {name}: actual={sorted(actual)} declared={declared}")
for rel in declared:
    data=(root/rel).read_bytes(); expected=spec["files"][rel]["git_blob_sha1"]; got=blob_sha(data)
    if got != expected: raise SystemExit(f"ERROR: installed multifile blob drift: {name}/{rel}: {got} != {expected}")
PY
    else
      diff -qr "$src" "$target" >/dev/null || { echo "ERROR: installed skill drift: $skill" >&2; exit 1; }
    fi
    echo "OK: $skill"
  done
fi

if [[ "$INSTALLED_ONLY" == "1" ]]; then echo 'FACTORY_SKILLS_INSTALLED_OK'; else echo 'FACTORY_SKILLS_VERIFY_OK'; fi
