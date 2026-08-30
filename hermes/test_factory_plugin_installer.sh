#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/hermes/install_factory_plugins.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

expect_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ERROR: expected failure: $label" >&2; exit 1; fi
  echo "OK fail-closed: $label"
}

make_fixture() {
  local dst="$1"
  mkdir -p "$dst/hermes/plugins/factory-repository-readonly"
  cp "$INSTALLER" "$dst/hermes/install_factory_plugins.sh"
  cp "$ROOT_DIR/hermes/plugins/manifest.json" "$dst/hermes/plugins/manifest.json"
  cp "$ROOT_DIR/hermes/plugins/factory-repository-readonly/"* "$dst/hermes/plugins/factory-repository-readonly/"
}

printf '[plugin-installer] production candidate reviewed-ready dry-run no write\n'
prod="$TMP/prod"
HERMES_PLUGINS_DIR="$prod" bash "$INSTALLER" --plugin factory-repository-readonly --dry-run >/dev/null
[[ ! -e "$prod" ]] || { echo 'ERROR: production dry-run wrote destination' >&2; exit 1; }

printf '[plugin-installer] fixture dry-run no write\n'
root="$TMP/fixture"; make_fixture "$root"; dest="$TMP/install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run >/dev/null
[[ ! -e "$dest" ]] || { echo 'ERROR: dry-run wrote destination' >&2; exit 1; }

printf '[plugin-installer] fixture exact install\n'
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null

printf '[plugin-installer] serialized publication under lock\n'
root="$TMP/locked"; make_fixture "$root"; dest="$TMP/locked-install"; mkdir -p "$dest"
lock="$dest/.factory-plugin.lock.factory-repository-readonly"
exec 8>"$lock"; flock -n 8 || { echo 'ERROR: could not acquire test lock' >&2; exit 1; }
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >"$TMP/locked.out" 2>"$TMP/locked.err" &
pid=$!; sleep 0.2
kill -0 "$pid" 2>/dev/null || { echo 'ERROR: installer did not wait on publication lock' >&2; cat "$TMP/locked.err" >&2; exit 1; }
flock -u 8; exec 8>&-; wait "$pid"
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
if find "$dest/factory-repository-readonly" -mindepth 1 -type d -print -quit | grep -q .; then echo 'ERROR: serialized install nested an undeclared directory' >&2; exit 1; fi
echo 'OK: serialized plugin publication'

printf '[plugin-installer] source tamper\n'
root="$TMP/tamper"; make_fixture "$root"; printf '\n# drift\n' >> "$root/hermes/plugins/factory-repository-readonly/repository_tools.py"
expect_fail "plugin source blob mismatch" env HERMES_PLUGINS_DIR="$TMP/tamper-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] extra source file\n'
root="$TMP/extra"; make_fixture "$root"; printf 'x\n' > "$root/hermes/plugins/factory-repository-readonly/extra.py"
expect_fail "plugin extra source" env HERMES_PLUGINS_DIR="$TMP/extra-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] extra empty source directory\n'
root="$TMP/extra-dir"; make_fixture "$root"; mkdir "$root/hermes/plugins/factory-repository-readonly/empty-extra"
expect_fail "plugin extra empty source directory" env HERMES_PLUGINS_DIR="$TMP/extra-dir-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] source symlink\n'
root="$TMP/symlink"; make_fixture "$root"; outside="$TMP/outside.py"; cp "$root/hermes/plugins/factory-repository-readonly/repo_map.py" "$outside"
rm "$root/hermes/plugins/factory-repository-readonly/repo_map.py"; ln -s "$outside" "$root/hermes/plugins/factory-repository-readonly/repo_map.py"
expect_fail "plugin source symlink" env HERMES_PLUGINS_DIR="$TMP/symlink-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] differing target refused\n'
root="$TMP/differ"; make_fixture "$root"; dest="$TMP/differ-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
printf '\n# installed drift\n' >> "$dest/factory-repository-readonly/repository_tools.py"
expect_fail "differing installed plugin" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly

printf '[plugin-installer] reviewed replacement restores exact pinned target\n'
root="$TMP/replace"; make_fixture "$root"; dest="$TMP/replace-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
printf '\n# old reviewed bytes simulation\n' >> "$dest/factory-repository-readonly/repository_tools.py"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run --replace-reviewed >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
if find "$dest" -maxdepth 1 -type d -name '.factory-plugin.backup.*' -print -quit | grep -q .; then echo 'ERROR: reviewed replacement left backup directory behind' >&2; exit 1; fi
echo 'OK: reviewed plugin replacement'

printf '[plugin-installer] post-publish verification failure restores old target\n'
root="$TMP/rollback"; make_fixture "$root"; dest="$TMP/rollback-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
printf '\n# previous reviewed target marker\n' >> "$dest/factory-repository-readonly/repository_tools.py"
old="$TMP/old-target"; cp -a "$dest/factory-repository-readonly" "$old"
fakebin="$TMP/fakebin"; mkdir -p "$fakebin"
REAL_MV="$(command -v mv)"
cat >"$fakebin/mv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
"${REAL_MV:?}" "${args[@]}"
count=${#args[@]}
if [[ "${FACTORY_TEST_CORRUPT_PUBLISH:-0}" == 1 && $count -ge 2 ]]; then
  src="${args[$((count-2))]}"; dst="${args[$((count-1))]}"
  if [[ "$src" == *'.factory-plugin.stage.'* && "$dst" == "${FACTORY_TEST_TARGET:?}" ]]; then
    printf '\n# forced post-publish corruption\n' >> "$dst/repository_tools.py"
  fi
fi
EOF
chmod +x "$fakebin/mv"
expect_fail "post-publish verification rollback" env PATH="$fakebin:$PATH" REAL_MV="$REAL_MV" FACTORY_TEST_CORRUPT_PUBLISH=1 FACTORY_TEST_TARGET="$dest/factory-repository-readonly" HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed
diff -qr "$old" "$dest/factory-repository-readonly" >/dev/null || { echo 'ERROR: rollback did not restore previous target exactly' >&2; exit 1; }
if find "$dest" -maxdepth 1 -type d -name '.factory-plugin.backup.*' -print -quit | grep -q .; then echo 'ERROR: rollback left backup directory behind' >&2; exit 1; fi
echo 'OK: failed publication restored previous target exactly'

printf '[plugin-installer] target symlink refused\n'
root="$TMP/target-link"; make_fixture "$root"; mkdir -p "$TMP/target-link-dest" "$TMP/elsewhere"
ln -s "$TMP/elsewhere" "$TMP/target-link-dest/factory-repository-readonly"
expect_fail "installed plugin target symlink" env HERMES_PLUGINS_DIR="$TMP/target-link-dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed
[[ -L "$TMP/target-link-dest/factory-repository-readonly" ]] || { echo 'ERROR: installed plugin target symlink was replaced' >&2; exit 1; }
[[ "$(readlink "$TMP/target-link-dest/factory-repository-readonly")" == "$TMP/elsewhere" ]] || { echo 'ERROR: installed plugin target symlink was changed' >&2; exit 1; }

printf '[plugin-installer] target symlinks refused in every install mode\n'
for link_kind in valid dangling; do
  for mode in normal replace dry-run; do
    dest="$TMP/target-link-$link_kind-$mode"; mkdir -p "$dest"
    if [[ "$link_kind" == valid ]]; then
      link_target="$TMP/elsewhere"
    else
      link_target="$TMP/missing-target-$mode"
    fi
    ln -s "$link_target" "$dest/factory-repository-readonly"
    case "$mode" in
      normal) mode_args=() ;;
      replace) mode_args=(--replace-reviewed) ;;
      dry-run) mode_args=(--dry-run --replace-reviewed) ;;
    esac
    expect_fail "$link_kind target symlink in $mode mode" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly "${mode_args[@]}"
    [[ -L "$dest/factory-repository-readonly" ]] || { echo "ERROR: $link_kind target symlink was replaced in $mode mode" >&2; exit 1; }
    [[ "$(readlink "$dest/factory-repository-readonly")" == "$link_target" ]] || { echo "ERROR: $link_kind target symlink was changed in $mode mode" >&2; exit 1; }
    if find "$dest" -maxdepth 1 -type d -name '.factory-plugin.backup.*' -print -quit | grep -q .; then
      echo "ERROR: $link_kind target symlink was backed up in $mode mode" >&2
      exit 1
    fi
  done
done

echo 'OK: factory plugin installer adversarial checks'
