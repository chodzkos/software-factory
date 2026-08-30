#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT_DIR/hermes/plugins/manifest.json"
DEST="${HERMES_PLUGINS_DIR:-$HOME/.hermes/plugins}"

usage() { echo "usage: bash hermes/install_factory_plugins.sh --plugin NAME [--dry-run]" >&2; exit 2; }
plugin=""; dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin) [[ $# -ge 2 ]] || usage; plugin="$2"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) usage ;;
  esac
done
[[ -n "$plugin" ]] || usage

selection="$(PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT_DIR" "$MANIFEST" "$plugin" <<'PY'
import hashlib,json,pathlib,re,sys
root=pathlib.Path(sys.argv[1]); manifest=json.load(open(sys.argv[2])); name=sys.argv[3]
if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name): raise SystemExit("ERROR: invalid plugin name")
spec=manifest.get("plugins",{}).get(name)
if not isinstance(spec,dict): raise SystemExit(f"ERROR: unknown plugin: {name}")
if spec.get("installable") is not True: raise SystemExit(f"ERROR: plugin is not installable: {name}")
if spec.get("activation_status") != "reviewed-ready": raise SystemExit(f"ERROR: plugin activation status is not reviewed-ready: {name}")
expected=f"hermes/plugins/{name}"
if spec.get("source") != expected: raise SystemExit(f"ERROR: plugin source path mismatch: {name}")
src=root/spec["source"]
if src.is_symlink() or not src.is_dir(): raise SystemExit(f"ERROR: invalid plugin source root: {name}")
pins=spec.get("files")
if not isinstance(pins,dict) or not pins: raise SystemExit(f"ERROR: invalid plugin pin set: {name}")
for rel,sha in pins.items():
    p=pathlib.PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or "." in p.parts or not p.parts:
        raise SystemExit(f"ERROR: invalid plugin relative path: {rel}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(sha)):
        raise SystemExit(f"ERROR: invalid plugin blob pin: {rel}")
actual_files=[]; actual_dirs=[]
for path in src.rglob("*"):
    rel=path.relative_to(src).as_posix()
    if path.is_symlink(): raise SystemExit(f"ERROR: symlink plugin source refused: {rel}")
    if path.is_file(): actual_files.append(rel)
    elif path.is_dir(): actual_dirs.append(rel)
    else: raise SystemExit(f"ERROR: non-regular plugin source refused: {rel}")
expected_dirs=set()
for rel in pins:
    for parent in pathlib.PurePosixPath(rel).parents:
        if str(parent) != ".": expected_dirs.add(parent.as_posix())
if sorted(actual_files) != sorted(pins) or sorted(actual_dirs) != sorted(expected_dirs):
    raise SystemExit(f"ERROR: plugin source tree mismatch: {name}")
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
for rel,expected_sha in sorted(pins.items()):
    got=blob_sha((src/rel).read_bytes())
    if got != expected_sha: raise SystemExit(f"ERROR: plugin blob mismatch: {rel}: {got} != {expected_sha}")
print(spec["source"])
PY
)"

src="$ROOT_DIR/$selection"; target="$DEST/$plugin"
check_existing_target() {
  if [[ ! -e "$target" ]]; then return 2; fi
  [[ -d "$target" && ! -L "$target" ]] || { echo "ERROR: invalid installed plugin target: $target" >&2; exit 1; }
  if find "$target" -type l -print -quit | grep -q .; then echo "ERROR: installed plugin contains symlink: $target" >&2; exit 1; fi
  if diff -qr "$src" "$target" >/dev/null; then
    echo "OK unchanged: $plugin"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
  fi
  echo "ERROR: existing installed plugin differs: $target" >&2; exit 1
}
if [[ $dry -eq 1 ]]; then
  if [[ -e "$target" ]]; then check_existing_target; fi
  echo "WOULD_INSTALL: $plugin -> $target"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
fi

mkdir -p "$DEST"
command -v flock >/dev/null 2>&1 || { echo "ERROR: flock is required for serialized plugin installation" >&2; exit 1; }
lock_file="$DEST/.factory-plugin.lock.$plugin"; exec 9>"$lock_file"; flock 9
if [[ -e "$target" ]]; then check_existing_target; fi

tmp="$(mktemp -d "$DEST/.factory-plugin.tmp.XXXXXX")"; trap 'rm -rf -- "$tmp"' EXIT
mapfile -t rels < <(PYTHONDONTWRITEBYTECODE=1 python3 - "$MANIFEST" "$plugin" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); print(*sorted(m["plugins"][sys.argv[2]]["files"]), sep="\n")
PY
)
for rel in "${rels[@]}"; do
  mkdir -p "$tmp/$(dirname "$rel")"
  cp -- "$src/$rel" "$tmp/$rel"
done

PYTHONDONTWRITEBYTECODE=1 python3 - "$MANIFEST" "$plugin" "$tmp" <<'PY'
import hashlib,json,pathlib,sys
manifest=json.load(open(sys.argv[1])); name=sys.argv[2]; root=pathlib.Path(sys.argv[3]); pins=manifest["plugins"][name]["files"]
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
actual_files=[]; actual_dirs=[]
for path in root.rglob("*"):
    rel=path.relative_to(root).as_posix()
    if path.is_symlink(): raise SystemExit(f"ERROR: copied plugin contains symlink: {rel}")
    if path.is_file(): actual_files.append(rel)
    elif path.is_dir(): actual_dirs.append(rel)
expected_dirs=set()
for rel in pins:
    for parent in pathlib.PurePosixPath(rel).parents:
        if str(parent) != ".": expected_dirs.add(parent.as_posix())
if sorted(actual_files) != sorted(pins) or sorted(actual_dirs) != sorted(expected_dirs):
    raise SystemExit("ERROR: copied plugin tree mismatch")
for rel,expected in sorted(pins.items()):
    got=blob_sha((root/rel).read_bytes())
    if got != expected: raise SystemExit(f"ERROR: copied plugin blob mismatch: {rel}: {got} != {expected}")
PY

mv -- "$tmp" "$target"; trap - EXIT
diff -qr "$src" "$target" >/dev/null || { echo "ERROR: published plugin differs from reviewed source" >&2; exit 1; }
echo "INSTALLED: $plugin"; echo "FACTORY_PLUGIN_INSTALL_OK"
