#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT_DIR/hermes/plugins/factory-kanban-artifact-guard"
INSTALLER="$ROOT_DIR/hermes/install_factory_plugins.sh"
MANIFEST="$ROOT_DIR/hermes/plugins/manifest.json"

echo '[check] shell/python syntax without bytecode writes'
bash -n "$INSTALLER"
python3 - "$PLUGIN/__init__.py" "$ROOT_DIR/hermes/test_factory_kanban_artifact_guard.py" <<'PY'
import pathlib,sys
for raw in sys.argv[1:]:
    path=pathlib.Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("OK: Python syntax compiled in-memory")
PY

echo '[check] guard boundary unit tests'
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT_DIR/hermes/test_factory_kanban_artifact_guard.py"

echo '[check] production guard remains disabled'
python3 - "$MANIFEST" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); s=m["plugins"]["factory-kanban-artifact-guard"]
assert s["installable"] is False
assert s["activation_status"] == "pending-independent-review"
assert set(s["files"]) == {"plugin.yaml", "__init__.py"}
print("OK: kanban artifact guard is review-only")
PY

if HERMES_PLUGINS_DIR="$(mktemp -d)" bash "$INSTALLER" --plugin factory-kanban-artifact-guard --dry-run >/dev/null 2>&1; then
  echo 'ERROR: production guard unexpectedly installable' >&2
  exit 1
else
  echo 'OK fail-closed: production guard disabled'
fi

echo '[check] generalized installer accepts exact two-file fixture'
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/hermes/plugins/factory-kanban-artifact-guard"
cp "$INSTALLER" "$TMP/hermes/install_factory_plugins.sh"
cp "$MANIFEST" "$TMP/hermes/plugins/manifest.json"
cp "$PLUGIN/plugin.yaml" "$PLUGIN/__init__.py" "$TMP/hermes/plugins/factory-kanban-artifact-guard/"
python3 - "$TMP/hermes/plugins/manifest.json" <<'PY'
import json,sys
p=sys.argv[1]; m=json.load(open(p)); s=m["plugins"]["factory-kanban-artifact-guard"]
s["installable"]=True; s["activation_status"]="reviewed-ready"
open(p,"w").write(json.dumps(m,indent=2)+"\n")
PY
DEST="$TMP/install"
HERMES_PLUGINS_DIR="$DEST" bash "$TMP/hermes/install_factory_plugins.sh" --plugin factory-kanban-artifact-guard --dry-run >/dev/null
[[ ! -e "$DEST" ]] || { echo 'ERROR: fixture dry-run wrote destination' >&2; exit 1; }
HERMES_PLUGINS_DIR="$DEST" bash "$TMP/hermes/install_factory_plugins.sh" --plugin factory-kanban-artifact-guard >/dev/null
diff -qr "$PLUGIN" "$DEST/factory-kanban-artifact-guard" >/dev/null
echo 'OK: exact two-file guard fixture installed'

echo '[check] existing four-file readonly plugin verifier'
bash "$ROOT_DIR/hermes/verify_factory_repository_plugin.sh"

if command -v hermes >/dev/null 2>&1; then
  echo '[check] Hermes plugin doctor'
  hermes plugins doctor "$PLUGIN" --ci
else
  echo '[info] hermes not in PATH; plugin doctor skipped'
fi

if find "$PLUGIN" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) -print -quit | grep -q .; then
  echo 'ERROR: generated Python bytecode exists in pinned guard tree' >&2
  exit 1
fi

echo 'FACTORY_KANBAN_ARTIFACT_GUARD_VERIFY_OK'
