#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/hermes/install_factory_plugins.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

expect_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "ERROR: expected failure: $label" >&2
    exit 1
  fi
  echo "OK fail-closed: $label"
}

make_fixture() {
  local dst="$1"
  mkdir -p "$dst/hermes/plugins/factory-repository-readonly"
  cp "$INSTALLER" "$dst/hermes/install_factory_plugins.sh"
  cp "$ROOT_DIR/hermes/plugins/manifest.json" "$dst/hermes/plugins/manifest.json"
  cp "$ROOT_DIR/hermes/plugins/factory-repository-readonly/"* "$dst/hermes/plugins/factory-repository-readonly/"
  python3 - "$dst/hermes/plugins/manifest.json" <<'PY'
import json,sys
p=sys.argv[1]; m=json.load(open(p)); s=m["plugins"]["factory-repository-readonly"]
s["installable"]=True; s["activation_status"]="reviewed-ready"
open(p,"w").write(json.dumps(m,indent=2)+"\n")
PY
}

printf '[plugin-installer] production candidate remains disabled\n'
expect_fail "production plugin disabled" env HERMES_PLUGINS_DIR="$TMP/prod" bash "$INSTALLER" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] fixture dry-run no write\n'
root="$TMP/fixture"; make_fixture "$root"; dest="$TMP/install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run >/dev/null
[[ ! -e "$dest" ]] || { echo 'ERROR: dry-run wrote destination' >&2; exit 1; }

printf '[plugin-installer] fixture exact install\n'
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null

printf '[plugin-installer] source tamper\n'
root="$TMP/tamper"; make_fixture "$root"
printf '\n# drift\n' >> "$root/hermes/plugins/factory-repository-readonly/repository_tools.py"
expect_fail "plugin source blob mismatch" env HERMES_PLUGINS_DIR="$TMP/tamper-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] extra source file\n'
root="$TMP/extra"; make_fixture "$root"
printf 'x\n' > "$root/hermes/plugins/factory-repository-readonly/extra.py"
expect_fail "plugin extra source" env HERMES_PLUGINS_DIR="$TMP/extra-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] source symlink\n'
root="$TMP/symlink"; make_fixture "$root"
outside="$TMP/outside.py"; cp "$root/hermes/plugins/factory-repository-readonly/repo_map.py" "$outside"
rm "$root/hermes/plugins/factory-repository-readonly/repo_map.py"
ln -s "$outside" "$root/hermes/plugins/factory-repository-readonly/repo_map.py"
expect_fail "plugin source symlink" env HERMES_PLUGINS_DIR="$TMP/symlink-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] differing target refused\n'
root="$TMP/differ"; make_fixture "$root"; dest="$TMP/differ-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
printf '\n# installed drift\n' >> "$dest/factory-repository-readonly/repository_tools.py"
expect_fail "differing installed plugin" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly

printf '[plugin-installer] target symlink refused\n'
root="$TMP/target-link"; make_fixture "$root"; mkdir -p "$TMP/target-link-dest" "$TMP/elsewhere"
ln -s "$TMP/elsewhere" "$TMP/target-link-dest/factory-repository-readonly"
expect_fail "installed plugin target symlink" env HERMES_PLUGINS_DIR="$TMP/target-link-dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly

echo 'OK: factory plugin installer adversarial checks'
