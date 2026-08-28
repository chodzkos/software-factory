#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT_DIR/hermes/plugins/factory-repository-readonly"

echo '[check] shell/python syntax'
bash -n "$ROOT_DIR/hermes/install_factory_plugins.sh"
bash -n "$ROOT_DIR/hermes/test_factory_plugin_installer.sh"
python3 -m py_compile \
  "$PLUGIN/__init__.py" \
  "$PLUGIN/repo_map.py" \
  "$PLUGIN/repository_tools.py" \
  "$ROOT_DIR/hermes/test_factory_repository_plugin.py"

echo '[check] plugin boundary unit tests'
python3 "$ROOT_DIR/hermes/test_factory_repository_plugin.py"

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
  hermes plugins doctor "$PLUGIN" --ci
else
  echo '[info] hermes not in PATH; plugin doctor skipped'
fi

echo 'FACTORY_REPOSITORY_PLUGIN_VERIFY_OK'
