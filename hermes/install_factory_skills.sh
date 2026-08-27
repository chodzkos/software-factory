#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${ROOT_DIR}/skills/manifest.yaml"
PROFILES="${ROOT_DIR}/skills/profiles.yaml"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}"

usage() {
  echo "usage: bash hermes/install_factory_skills.sh (--profile NAME | --all) [--dry-run]" >&2
  exit 2
}

profile=""
all=0
dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || usage; profile="$2"; shift 2 ;;
    --all) all=1; shift ;;
    --dry-run) dry=1; shift ;;
    *) usage ;;
  esac
done
[[ $all -eq 1 || -n "$profile" ]] || usage
[[ $all -eq 0 || -z "$profile" ]] || usage

mapfile -t skills < <(python3 - "$MANIFEST" "$PROFILES" "$profile" "$all" <<'PY'
import json, sys
manifest=json.load(open(sys.argv[1]))
profiles=json.load(open(sys.argv[2]))
profile=sys.argv[3]
install_all=sys.argv[4] == "1"
if manifest["upstream_policy"]["enabled"]:
    raise SystemExit("ERROR: upstream install is not implemented in v0.6 foundation")
if install_all:
    names=sorted(manifest["skills"])
else:
    if profile not in profiles["profiles"]:
        raise SystemExit(f"ERROR: unknown profile: {profile}")
    p=profiles["profiles"][profile]
    names=sorted(set(p["required"] + p["optional"]))
for name in names:
    spec=manifest["skills"][name]
    if spec["source"] != "custom":
        raise SystemExit(f"ERROR: non-custom source not supported yet: {name}")
    print(name)
PY
)

mkdir -p "$DEST"
echo "Target: $DEST"
echo "Skills: ${skills[*]:-(none)}"

for skill in "${skills[@]}"; do
  src="$ROOT_DIR/skills/custom/$skill"
  target="$DEST/$skill"
  [[ -f "$src/SKILL.md" ]] || { echo "ERROR: missing source skill: $src/SKILL.md" >&2; exit 1; }

  if [[ -e "$target" ]]; then
    if [[ -d "$target" ]] && diff -qr "$src" "$target" >/dev/null; then
      echo "OK unchanged: $skill"
      continue
    fi
    echo "ERROR: existing installed skill differs: $target" >&2
    echo "Refusing to overwrite. Inspect/remove or migrate it explicitly." >&2
    exit 1
  fi

  if [[ $dry -eq 1 ]]; then
    echo "WOULD_INSTALL: $skill"
    continue
  fi

  tmp="$DEST/.${skill}.tmp.$$"
  trap 'rm -rf -- "$tmp"' EXIT
  mkdir "$tmp"
  cp -a "$src"/. "$tmp"/
  mv "$tmp" "$target"
  trap - EXIT
  echo "INSTALLED: $skill"
done

echo "FACTORY_SKILLS_INSTALL_OK"
