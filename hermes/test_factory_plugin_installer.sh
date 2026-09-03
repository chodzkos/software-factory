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

copy_regular_files() {
  local src="$1" dst="$2"
  while IFS= read -r -d '' file; do
    cp -- "$file" "$dst/$(basename "$file")"
  done < <(find "$src" -maxdepth 1 -type f -print0)
}

make_fixture() {
  local dst="$1"
  mkdir -p "$dst/hermes/plugins/factory-repository-readonly" "$dst/hermes/plugins/factory-execution-guards"
  cp "$INSTALLER" "$dst/hermes/install_factory_plugins.sh"
  cp "$ROOT_DIR/hermes/plugins/manifest.json" "$dst/hermes/plugins/manifest.json"
  copy_regular_files "$ROOT_DIR/hermes/plugins/factory-repository-readonly" "$dst/hermes/plugins/factory-repository-readonly"
  copy_regular_files "$ROOT_DIR/hermes/plugins/factory-execution-guards" "$dst/hermes/plugins/factory-execution-guards"
}

make_known_execution_guard_predecessor() {
  local target="$1"
  mkdir -p "$target"
  # Immediate predecessor v0.9.0 had exactly these three files; handoff.py is new in v0.10.0.
  git -C "$ROOT_DIR" cat-file blob f5136a9df630891a4c2a3504142b4cf623622733 >"$target/plugin.yaml"
  git -C "$ROOT_DIR" cat-file blob 8de9ffacc7a7ae6c61a3bc7922313a56d22d64f6 >"$target/__init__.py"
  git -C "$ROOT_DIR" cat-file blob 3c8bad02c637fb78bf7bcd18a440a966ac4179dd >"$target/guard.py"
}

printf '[plugin-installer] production candidate reviewed-ready dry-run no write\n'
prod="$TMP/prod"
HERMES_PLUGINS_DIR="$prod" bash "$INSTALLER" --plugin factory-repository-readonly --dry-run >/dev/null
[[ ! -e "$prod" ]] || { echo 'ERROR: production dry-run wrote destination' >&2; exit 1; }

printf '[plugin-installer] exact install and idempotence\n'
root="$TMP/fixture"; make_fixture "$root"; dest="$TMP/install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null

printf '[plugin-installer] serialized publication locks destination directory\n'
root="$TMP/locked"; make_fixture "$root"; dest="$TMP/locked-install"; mkdir -p "$dest"
exec 8<"$dest"; flock -n 8 || { echo 'ERROR: could not acquire destination test lock' >&2; exit 1; }
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >"$TMP/locked.out" 2>"$TMP/locked.err" &
pid=$!; sleep 0.2
kill -0 "$pid" 2>/dev/null || { echo 'ERROR: installer did not wait on destination lock' >&2; cat "$TMP/locked.err" >&2; exit 1; }
flock -u 8; exec 8>&-; wait "$pid"
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
echo 'OK: serialized plugin publication'

printf '[plugin-installer] legacy lock-path symlink cannot truncate victim\n'
root="$TMP/lock-link"; make_fixture "$root"; dest="$TMP/lock-link-install"; mkdir -p "$dest"
victim="$TMP/lock-victim"; printf 'DO_NOT_TRUNCATE\n' >"$victim"
ln -s "$victim" "$dest/.factory-plugin.lock.factory-repository-readonly"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
[[ "$(cat "$victim")" == 'DO_NOT_TRUNCATE' ]] || { echo 'ERROR: legacy lock symlink target was modified' >&2; exit 1; }

printf '[plugin-installer] source tamper / extra material / source symlink\n'
root="$TMP/tamper"; make_fixture "$root"; printf '\n# drift\n' >> "$root/hermes/plugins/factory-repository-readonly/repository_tools.py"
expect_fail "plugin source blob mismatch" env HERMES_PLUGINS_DIR="$TMP/tamper-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run
root="$TMP/extra"; make_fixture "$root"; printf 'x\n' > "$root/hermes/plugins/factory-repository-readonly/extra.py"
expect_fail "plugin extra source" env HERMES_PLUGINS_DIR="$TMP/extra-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run
root="$TMP/extra-dir"; make_fixture "$root"; mkdir "$root/hermes/plugins/factory-repository-readonly/empty-extra"
expect_fail "plugin extra empty source directory" env HERMES_PLUGINS_DIR="$TMP/extra-dir-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run
root="$TMP/source-link"; make_fixture "$root"; outside="$TMP/outside.py"; cp "$root/hermes/plugins/factory-repository-readonly/repo_map.py" "$outside"
rm "$root/hermes/plugins/factory-repository-readonly/repo_map.py"; ln -s "$outside" "$root/hermes/plugins/factory-repository-readonly/repo_map.py"
expect_fail "plugin source symlink" env HERMES_PLUGINS_DIR="$TMP/source-link-install" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run

printf '[plugin-installer] unknown/drifted target refused even with --replace-reviewed\n'
root="$TMP/differ"; make_fixture "$root"; dest="$TMP/differ-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
printf '\n# unreviewed installed drift\n' >> "$dest/factory-repository-readonly/repository_tools.py"
expect_fail "unreviewed replacement target" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed

printf '[plugin-installer] current reviewed tree plus __pycache__ may be cleaned\n'
root="$TMP/noise"; make_fixture "$root"; dest="$TMP/noise-install"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly >/dev/null
mkdir -p "$dest/factory-repository-readonly/__pycache__"; printf 'runtime' >"$dest/factory-repository-readonly/__pycache__/x.pyc"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null

printf '[plugin-installer] explicitly pinned predecessor replacement\n'
root="$TMP/predecessor"; make_fixture "$root"; dest="$TMP/predecessor-install"; mkdir -p "$dest"
make_known_execution_guard_predecessor "$dest/factory-execution-guards"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-execution-guards --dry-run --replace-reviewed >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-execution-guards --replace-reviewed >/dev/null
diff -qr "$root/hermes/plugins/factory-execution-guards" "$dest/factory-execution-guards" >/dev/null
if find "$dest" -maxdepth 1 -type d -name '.factory-plugin.backup.*' -print -quit | grep -q .; then echo 'ERROR: reviewed replacement left backup directory behind' >&2; exit 1; fi
echo 'OK: known reviewed predecessor replaced'

printf '[plugin-installer] post-publish verification failure restores exact known predecessor\n'
root="$TMP/rollback"; make_fixture "$root"; dest="$TMP/rollback-install"; mkdir -p "$dest"
make_known_execution_guard_predecessor "$dest/factory-execution-guards"
old="$TMP/old-target"; cp -a "$dest/factory-execution-guards" "$old"
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
    printf '\n# forced post-publish corruption\n' >> "$dst/__init__.py"
  fi
fi
EOF
chmod +x "$fakebin/mv"
expect_fail "post-publish verification rollback" env PATH="$fakebin:$PATH" REAL_MV="$REAL_MV" FACTORY_TEST_CORRUPT_PUBLISH=1 FACTORY_TEST_TARGET="$dest/factory-execution-guards" HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-execution-guards --replace-reviewed
diff -qr "$old" "$dest/factory-execution-guards" >/dev/null || { echo 'ERROR: rollback did not restore predecessor exactly' >&2; exit 1; }

printf '[plugin-installer] target symlinks refused in every install mode\n'
root="$TMP/target-link"; make_fixture "$root"; mkdir -p "$TMP/elsewhere"
for link_kind in valid dangling; do
  for mode in normal replace dry-run; do
    dest="$TMP/target-link-$link_kind-$mode"; mkdir -p "$dest"
    if [[ "$link_kind" == valid ]]; then link_target="$TMP/elsewhere"; else link_target="$TMP/missing-target-$mode"; fi
    ln -s "$link_target" "$dest/factory-repository-readonly"
    case "$mode" in normal) mode_args=() ;; replace) mode_args=(--replace-reviewed) ;; dry-run) mode_args=(--dry-run --replace-reviewed) ;; esac
    expect_fail "$link_kind target symlink in $mode mode" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly "${mode_args[@]}"
    [[ -L "$dest/factory-repository-readonly" ]] || { echo "ERROR: target symlink replaced in $mode mode" >&2; exit 1; }
    [[ "$(readlink "$dest/factory-repository-readonly")" == "$link_target" ]] || { echo "ERROR: target symlink changed in $mode mode" >&2; exit 1; }
  done
done

printf '[plugin-installer] destination parent symlink refused\n'
root="$TMP/parent-link"; make_fixture "$root"; real_parent="$TMP/real-parent"; mkdir -p "$real_parent"
ln -s "$real_parent" "$TMP/symlink-parent"
expect_fail "symlinked destination parent" env HERMES_PLUGINS_DIR="$TMP/symlink-parent/plugins" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly
[[ ! -e "$real_parent/plugins/factory-repository-readonly" ]] || { echo 'ERROR: installer wrote through symlinked destination parent' >&2; exit 1; }

echo 'OK: factory plugin installer adversarial checks'
