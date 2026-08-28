#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT_DIR/hermes/plugins/factory-repository-readonly"

echo '[check] shell/python syntax without bytecode writes'
bash -n "$ROOT_DIR/hermes/install_factory_plugins.sh"
bash -n "$ROOT_DIR/hermes/test_factory_plugin_installer.sh"
python3 - "$PLUGIN" "$ROOT_DIR/hermes/test_factory_repository_plugin.py" <<'PY'
from pathlib import Path
import sys
plugin = Path(sys.argv[1])
test = Path(sys.argv[2])
for path in (plugin / "__init__.py", plugin / "repo_map.py", plugin / "repository_tools.py", test):
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
print("OK: Python syntax compiled in-memory")
PY

if find "$PLUGIN" -type d -name '__pycache__' -print -quit | grep -q . || \
   find "$PLUGIN" -type f -name '*.pyc' -print -quit | grep -q .; then
  echo 'ERROR: verification source tree contains generated Python bytecode' >&2
  exit 1
fi

echo '[check] plugin boundary unit tests'
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT_DIR/hermes/test_factory_repository_plugin.py"

echo '[check] plugin installer adversarial tests'
bash "$ROOT_DIR/hermes/test_factory_plugin_installer.sh"

echo '[check] reviewed mapper bytes retained'
cmp -s \
  "$PLUGIN/repo_map.py" \
  "$ROOT_DIR/skills/custom/factory-repo-map/scripts/repo_map.py"
echo 'OK: plugin mapper byte-identical to reviewed helper'

echo '[check] production plugin remains disabled'
python3 - "$ROOT_DIR/hermes/plugins/manifest.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); s=m["plugins"]["factory-repository-readonly"]
assert s["installable"] is False
assert s["activation_status"] == "pending-independent-review"
print("OK: readonly plugin is review-only")
PY

if command -v hermes >/dev/null 2>&1; then
  echo '[check] Hermes plugin doctor'
  PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor "$PLUGIN" --ci
else
  echo '[info] hermes not in PATH; plugin doctor skipped'
fi

if find "$PLUGIN" -type d -name '__pycache__' -print -quit | grep -q . || \
   find "$PLUGIN" -type f -name '*.pyc' -print -quit | grep -q .; then
  echo 'ERROR: plugin verification mutated pinned source tree' >&2
  exit 1
fi

echo 'FACTORY_REPOSITORY_PLUGIN_VERIFY_OK'
