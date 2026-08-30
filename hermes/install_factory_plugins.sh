#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT_DIR/hermes/plugins/manifest.json"
DEST="${HERMES_PLUGINS_DIR:-$HOME/.hermes/plugins}"

usage() { echo "usage: bash hermes/install_factory_plugins.sh --plugin NAME [--dry-run] [--replace-reviewed]" >&2; exit 2; }
plugin=""; dry=0; replace_reviewed=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin) [[ $# -ge 2 ]] || usage; plugin="$2"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --replace-reviewed) replace_reviewed=1; shift ;;
    *) usage ;;
  esac
done
[[ -n "$plugin" ]] || usage

snapshot_dir="$(mktemp -d)"
trap 'rm -rf -- "$snapshot_dir"' EXIT
snapshot="$snapshot_dir/pins.json"

# Validate the mutable repository manifest/source exactly once and freeze the
# reviewed source path plus complete pin set into an immutable transaction file.
PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT_DIR" "$MANIFEST" "$plugin" "$snapshot" <<'PY'
import hashlib,json,pathlib,re,sys
root=pathlib.Path(sys.argv[1]); manifest_path=pathlib.Path(sys.argv[2]); name=sys.argv[3]; out=pathlib.Path(sys.argv[4])
manifest=json.loads(manifest_path.read_text())
if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name): raise SystemExit("ERROR: invalid plugin name")
spec=manifest.get("plugins",{}).get(name)
if not isinstance(spec,dict): raise SystemExit(f"ERROR: unknown plugin: {name}")
if spec.get("installable") is not True: raise SystemExit(f"ERROR: plugin is not installable: {name}")
if spec.get("activation_status") != "reviewed-ready": raise SystemExit(f"ERROR: plugin activation status is not reviewed-ready: {name}")
expected=f"hermes/plugins/{name}"
if spec.get("source") != expected: raise SystemExit(f"ERROR: plugin source path mismatch: {name}")
src=root/expected
if src.is_symlink() or not src.is_dir(): raise SystemExit(f"ERROR: invalid plugin source root: {name}")
pins=spec.get("files")
if not isinstance(pins,dict) or not pins: raise SystemExit(f"ERROR: invalid plugin pin set: {name}")
for rel,sha in pins.items():
    p=pathlib.PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or "." in p.parts or not p.parts: raise SystemExit(f"ERROR: invalid plugin relative path: {rel}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(sha)): raise SystemExit(f"ERROR: invalid plugin blob pin: {rel}")
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
if sorted(actual_files) != sorted(pins) or sorted(actual_dirs) != sorted(expected_dirs): raise SystemExit(f"ERROR: plugin source tree mismatch: {name}")
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
for rel,expected_sha in sorted(pins.items()):
    got=blob_sha((src/rel).read_bytes())
    if got != expected_sha: raise SystemExit(f"ERROR: plugin blob mismatch: {rel}: {got} != {expected_sha}")
out.write_text(json.dumps({"plugin":name,"source":str(src),"pins":pins}, sort_keys=True)+"\n")
PY

src="$(PYTHONDONTWRITEBYTECODE=1 python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"])' "$snapshot")"
target="$DEST/$plugin"

verify_tree() {
  local root="$1"
  PYTHONDONTWRITEBYTECODE=1 python3 - "$snapshot" "$root" <<'PY'
import hashlib,json,pathlib,sys
snap=json.load(open(sys.argv[1])); root=pathlib.Path(sys.argv[2]); pins=snap["pins"]
if root.is_symlink() or not root.is_dir(): raise SystemExit("ERROR: plugin tree missing or symlinked")
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
actual_files=[]; actual_dirs=[]
for path in root.rglob("*"):
    rel=path.relative_to(root).as_posix()
    if path.is_symlink(): raise SystemExit(f"ERROR: plugin tree contains symlink: {rel}")
    if path.is_file(): actual_files.append(rel)
    elif path.is_dir(): actual_dirs.append(rel)
    else: raise SystemExit(f"ERROR: plugin tree contains non-regular entry: {rel}")
expected_dirs=set()
for rel in pins:
    for parent in pathlib.PurePosixPath(rel).parents:
        if str(parent) != ".": expected_dirs.add(parent.as_posix())
if sorted(actual_files) != sorted(pins) or sorted(actual_dirs) != sorted(expected_dirs): raise SystemExit("ERROR: plugin tree mismatch")
for rel,expected in sorted(pins.items()):
    got=blob_sha((root/rel).read_bytes())
    if got != expected: raise SystemExit(f"ERROR: plugin blob mismatch: {rel}: {got} != {expected}")
PY
}

if [[ $dry -eq 1 ]]; then
  if [[ -e "$target" ]]; then
    if verify_tree "$target" >/dev/null 2>&1; then
      echo "OK unchanged: $plugin"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
    fi
    [[ $replace_reviewed -eq 1 ]] || { echo "ERROR: existing installed plugin differs: $target" >&2; exit 1; }
    echo "WOULD_REPLACE_REVIEWED: $plugin -> $target"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
  fi
  echo "WOULD_INSTALL: $plugin -> $target"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
fi

mkdir -p "$DEST"
command -v flock >/dev/null 2>&1 || { echo "ERROR: flock is required for serialized plugin installation" >&2; exit 1; }
lock_file="$DEST/.factory-plugin.lock.$plugin"; exec 9>"$lock_file"; flock 9

if [[ -e "$target" ]] && verify_tree "$target" >/dev/null 2>&1; then
  echo "OK unchanged: $plugin"; echo "FACTORY_PLUGIN_INSTALL_OK"; exit 0
fi
if [[ -e "$target" && $replace_reviewed -ne 1 ]]; then
  echo "ERROR: existing installed plugin differs: $target" >&2; exit 1
fi

stage="$(mktemp -d "$DEST/.factory-plugin.stage.XXXXXX")"
backup_root=""
backup=""
published=0
rollback() {
  local rc=$?
  trap - EXIT
  rm -rf -- "$stage"
  if [[ $published -eq 1 && -e "$target" ]]; then
    rm -rf -- "$target"
  fi
  if [[ -n "$backup" && -e "$backup" ]]; then
    mv -- "$backup" "$target"
  fi
  if [[ -n "$backup_root" && -d "$backup_root" ]]; then
    rmdir "$backup_root" 2>/dev/null || { echo "ERROR: rollback backup directory not empty: $backup_root" >&2; exit 1; }
  fi
  exit "$rc"
}
trap rollback EXIT

# Copy only files named in the frozen snapshot. Re-hash the staged tree against
# that same snapshot, never against a reopened manifest.
mapfile -t rels < <(PYTHONDONTWRITEBYTECODE=1 python3 -c 'import json,sys; print(*sorted(json.load(open(sys.argv[1]))["pins"]), sep="\n")' "$snapshot")
for rel in "${rels[@]}"; do
  mkdir -p "$stage/$(dirname "$rel")"
  cp -- "$src/$rel" "$stage/$rel"
done
verify_tree "$stage"

# Arm rollback before moving the old target. The backup and target live under
# DEST so renames are same-filesystem and serialized by the held lock.
if [[ -e "$target" ]]; then
  backup_root="$(mktemp -d "$DEST/.factory-plugin.backup.XXXXXX")"
  backup="$backup_root/$plugin"
  mv -- "$target" "$backup"
fi
mv -- "$stage" "$target"
published=1
verify_tree "$target"

if [[ -n "$backup" ]]; then
  rm -rf -- "$backup"
  rmdir "$backup_root"
  backup=""; backup_root=""
  echo "REPLACED_REVIEWED: $plugin"
else
  echo "INSTALLED: $plugin"
fi
published=0
trap - EXIT
rm -rf -- "$snapshot_dir"
echo "FACTORY_PLUGIN_INSTALL_OK"
