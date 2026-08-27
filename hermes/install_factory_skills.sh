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

selection="$(python3 - "$ROOT_DIR" "$MANIFEST" "$PROFILES" "$profile" "$all" <<'PY'
import hashlib, json, pathlib, re, sys
root=pathlib.Path(sys.argv[1])
manifest=json.load(open(sys.argv[2]))
profiles=json.load(open(sys.argv[3]))
profile=sys.argv[4]
install_all=sys.argv[5] == "1"
policy=manifest["upstream_policy"]
if install_all:
    names=sorted(manifest["skills"])
else:
    if profile not in profiles["profiles"]:
        raise SystemExit(f"ERROR: unknown profile: {profile}")
    p=profiles["profiles"][profile]
    names=sorted(set(p["required"] + p["optional"]))
for name in names:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise SystemExit(f"ERROR: invalid skill name: {name!r}")
    spec=manifest["skills"][name]
    source=spec.get("source")
    if source == "custom":
        expected=f"skills/custom/{name}"
        if spec.get("path") != expected:
            raise SystemExit(f"ERROR: custom skill path mismatch for {name}: {spec.get('path')!r}")
    elif source == "upstream-vendored":
        if not policy.get("enabled") or policy.get("mode") != "vendored-only" or policy.get("network_install") is not False:
            raise SystemExit("ERROR: upstream policy is not vendored-only fail-closed")
        expected=f"skills/upstream/{name}"
        if spec.get("path") != expected:
            raise SystemExit(f"ERROR: upstream local path mismatch for {name}: {spec.get('path')!r}")
        upstream=spec.get("upstream")
        if not isinstance(upstream, dict):
            raise SystemExit(f"ERROR: missing upstream provenance for {name}")
        missing=[field for field in policy["required_fields_when_enabled"] if field not in upstream]
        if missing:
            raise SystemExit(f"ERROR: incomplete upstream provenance for {name}: {missing}")
        if not isinstance(upstream["repository"], str) or not upstream["repository"]:
            raise SystemExit(f"ERROR: invalid upstream repository for {name}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(upstream["commit"])):
            raise SystemExit(f"ERROR: upstream commit must be exact 40-char SHA for {name}")
        if upstream["path"] != f"skills/{name}/SKILL.md":
            raise SystemExit(f"ERROR: unexpected upstream path for {name}: {upstream['path']!r}")
        if upstream["vetted"] is not True:
            raise SystemExit(f"ERROR: upstream skill is not vetted: {name}")
        expected_sha=str(upstream["sha256"])
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise SystemExit(f"ERROR: invalid SHA-256 for {name}")
        skill_file=root/spec["path"]/"SKILL.md"
        if not skill_file.is_file():
            raise SystemExit(f"ERROR: missing vendored upstream skill: {skill_file}")
        actual=hashlib.sha256(skill_file.read_bytes()).hexdigest()
        if actual != expected_sha:
            raise SystemExit(f"ERROR: vendored SHA-256 mismatch for {name}: {actual} != {expected_sha}")
    else:
        raise SystemExit(f"ERROR: unsupported skill source for {name}: {source!r}")
    print(f"{name}\t{spec['path']}")
PY
)"
entries=()
if [[ -n "$selection" ]]; then
  mapfile -t entries <<<"$selection"
fi
skills=()
relpaths=()
for entry in "${entries[@]}"; do
  skills+=("${entry%%$'\t'*}")
  relpaths+=("${entry#*$'\t'}")
done

echo "Target: $DEST"
echo "Skills: ${skills[*]:-(none)}"

# Preflight the complete selection before the first write.
for i in "${!skills[@]}"; do
  skill="${skills[$i]}"
  src="$ROOT_DIR/${relpaths[$i]}"
  target="$DEST/$skill"
  [[ -f "$src/SKILL.md" ]] || { echo "ERROR: missing source skill: $src/SKILL.md" >&2; exit 1; }

  if [[ -L "$target" ]]; then
    echo "ERROR: refusing symlink installed target: $target" >&2
    exit 1
  fi
  if [[ -e "$target" ]]; then
    if [[ -d "$target" ]] && diff -qr "$src" "$target" >/dev/null; then
      continue
    fi
    echo "ERROR: existing installed skill differs: $target" >&2
    echo "Refusing to overwrite. Inspect/remove or migrate it explicitly." >&2
    exit 1
  fi
done

if [[ $dry -eq 1 ]]; then
  for skill in "${skills[@]}"; do
    target="$DEST/$skill"
    if [[ -d "$target" ]]; then
      echo "OK unchanged: $skill"
    else
      echo "WOULD_INSTALL: $skill"
    fi
  done
  echo "FACTORY_SKILLS_INSTALL_OK"
  exit 0
fi

mkdir -p "$DEST"

for i in "${!skills[@]}"; do
  skill="${skills[$i]}"
  src="$ROOT_DIR/${relpaths[$i]}"
  target="$DEST/$skill"

  if [[ -d "$target" ]]; then
    echo "OK unchanged: $skill"
    continue
  fi

  tmp="$(mktemp -d "$DEST/.factory-skill.tmp.XXXXXX")"
  trap 'rm -rf -- "$tmp"' EXIT
  cp -a "$src"/. "$tmp"/
  mv -- "$tmp" "$target"
  trap - EXIT
  echo "INSTALLED: $skill"
done

echo "FACTORY_SKILLS_INSTALL_OK"
