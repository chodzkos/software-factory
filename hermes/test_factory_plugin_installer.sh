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

  # Exercise installer mechanics only in a disposable reviewed-ready fixture.
  # The real source candidate is reviewed-ready with factory-home-only scope and is tested separately below.
  python3 - "$dst/hermes/plugins/manifest.json" <<'FIXTURE'
import json, sys
path = sys.argv[1]
with open(path) as handle: manifest = json.load(handle)
spec = manifest["plugins"]["factory-execution-guards"]
assert spec["installable"] is True and spec["activation_status"] == "reviewed-ready" and spec["install_scope"] == "factory-home-only"
spec["activation_status"] = "reviewed-ready"
del spec["install_scope"]
with open(path, "w") as handle: json.dump(manifest, handle, indent=2); handle.write("\n")
FIXTURE
}

# Retained predecessor bytes, independent of local checkout/history availability.
# Every read verifies the original Git blob id; these files are never imported.
read_historical_blob() {
  python3 - "$1" "$ROOT_DIR/hermes/tests_support/historical_plugin_blobs/$1" <<'HISTORICAL'
import hashlib, pathlib, re, sys
sha, raw_path = sys.argv[1:]
path = pathlib.Path(raw_path)
if not re.fullmatch(r"[0-9a-f]{40}", sha) or path.is_symlink() or not path.is_file():
    raise SystemExit("historical plugin blob unavailable")
content = path.read_bytes()
actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
if actual != sha:
    raise SystemExit("historical plugin blob integrity mismatch")
sys.stdout.buffer.write(content)
HISTORICAL
}

make_known_execution_guard_predecessor() {
  local target="$1"
  mkdir -p "$target"
  # Historical reviewed v0.11.0 fixture; not the immediate v0.12.0 predecessor.
  read_historical_blob 74bb36d6bb9b63a6453d2c42544586b84a8e165d >"$target/plugin.yaml"
  read_historical_blob afdeed78fdb539c0a11498a4a3f8844d55d2a980 >"$target/__init__.py"
  read_historical_blob 46c5c0082a41ac11c9648efc8a6a12c3de8157a2 >"$target/guard.py"
  read_historical_blob be5a17896c5c08f0a6f7b890cd11f69a15276272 >"$target/handoff.py"
  read_historical_blob b0da39ae49d9aca0ed9a816a2a7d15ca9ad8c934 >"$target/supervisor.py"
}

make_known_repository_analyst_d756376_predecessor() {
  local target="$1"
  mkdir -p "$target"
  # Zrecenzowane drzewo poprzednika wdrożone z commita d756376.
  read_historical_blob e26285a24ab1048c1c61e4dd7bfecd22e7178554 >"$target/plugin.yaml"
  read_historical_blob d1607626706e425b0057f10154a1b923da8f55e7 >"$target/__init__.py"
  read_historical_blob ce7718e0879600631e84a4e44f9c7f97842af9cc >"$target/repo_map.py"
  read_historical_blob cc028cb35f54e21660356662c727cabe7f789eca >"$target/repository_tools.py"
  read_historical_blob 881dacf7715f8fd0c088df54c9e8d273b15a21da >"$target/kanban_guard.py"
}

make_known_repository_analyst_base_predecessor() {
  local target="$1"
  mkdir -p "$target"
  # Zrecenzowane drzewo v0.2.0 wdrożone z bazowego commita d901cb2.
  read_historical_blob e26285a24ab1048c1c61e4dd7bfecd22e7178554 >"$target/plugin.yaml"
  read_historical_blob d1607626706e425b0057f10154a1b923da8f55e7 >"$target/__init__.py"
  read_historical_blob ce7718e0879600631e84a4e44f9c7f97842af9cc >"$target/repo_map.py"
  read_historical_blob 93193e0db3453ef9b5d8f175c5fe4334ff5bf58a >"$target/repository_tools.py"
  read_historical_blob 881dacf7715f8fd0c088df54c9e8d273b15a21da >"$target/kanban_guard.py"
}

printf '[plugin-installer] production candidate reviewed-ready dry-run no write\n'
prod="$TMP/prod"
HERMES_PLUGINS_DIR="$prod" bash "$INSTALLER" --plugin factory-repository-readonly --dry-run >/dev/null
[[ ! -e "$prod" ]] || { echo 'ERROR: production dry-run wrote destination' >&2; exit 1; }

printf '[plugin-installer] factory-home-only guard refuses every activation mode outside a factory HOME without writes\n'
for mode in normal replace dry-run; do
  case "$mode" in normal) flags=() ;; replace) flags=(--replace-reviewed) ;; dry-run) flags=(--dry-run --replace-reviewed) ;; esac
  dest="$TMP/blocked-guard-$mode"
  expect_fail "factory-home-only guard without FACTORY_HOME $mode" env -u FACTORY_HOME HERMES_PLUGINS_DIR="$dest" bash "$INSTALLER" --plugin factory-execution-guards "${flags[@]}"
  [[ ! -e "$dest" ]] || { echo 'ERROR: blocked guard created destination' >&2; exit 1; }
done

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

printf '[plugin-installer] reviewed d756376 repository-analyst predecessor replacement\n'
root="$TMP/repository-analyst-d756376"; make_fixture "$root"; dest="$TMP/repository-analyst-d756376-install"; mkdir -p "$dest"
make_known_repository_analyst_d756376_predecessor "$dest/factory-repository-readonly"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null

printf '[plugin-installer] reviewed d901cb2 repository-analyst predecessor replacement\n'
root="$TMP/repository-analyst-base"; make_fixture "$root"; dest="$TMP/repository-analyst-base-install"; mkdir -p "$dest"
make_known_repository_analyst_base_predecessor "$dest/factory-repository-readonly"
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --dry-run --replace-reviewed >/dev/null
HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed >/dev/null
diff -qr "$root/hermes/plugins/factory-repository-readonly" "$dest/factory-repository-readonly" >/dev/null
root="$TMP/repository-analyst-base-drift"; make_fixture "$root"; dest="$TMP/repository-analyst-base-drift-install"; mkdir -p "$dest"
make_known_repository_analyst_base_predecessor "$dest/factory-repository-readonly"
printf '\n# unreviewed base-tree drift\n' >> "$dest/factory-repository-readonly/repository_tools.py"
expect_fail "drifted d901cb2 repository-analyst predecessor" env HERMES_PLUGINS_DIR="$dest" bash "$root/hermes/install_factory_plugins.sh" --plugin factory-repository-readonly --replace-reviewed

echo 'OK: exact d901cb2 repository-analyst predecessor replaced; drift rejected'

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
