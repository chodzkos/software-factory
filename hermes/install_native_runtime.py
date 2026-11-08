"""Production native-overlay runtime installer (G4/G5). Source candidate — NOT FOR INSTALL until activation is reviewed.

Builds ONE dedicated runtime directory, offline, from:
  * the pinned native Git repository (clean clone, detached at the overlay base, no hardlinks),
  * this candidate's overlay (verify_native_overlay before -> git apply -> verify after),
  * a hashed lock (native_integration/runtime.lock.txt + build.lock.txt) installed only from a local wheelhouse
    (--require-hashes, --no-index, copy link mode), then an editable install of the runtime itself (--no-deps).
Refuses an existing target, symlinks, hardlinks, foreign editable mappings and any drift. Network is never used.
Writes INSTALL_REPORT.json (0600) in the runtime. Does not touch ~/.hermes, profiles or the pinned native.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, stat, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NI = HERE / 'native_integration'


def need(ok, why):
    if not ok:
        raise SystemExit('ERROR: ' + why)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, *, cwd=None, env=None, check=True):
    base = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8'}
    cp = subprocess.run([str(a) for a in argv], cwd=cwd, env={**base, **(env or {})}, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1800)
    need(not check or cp.returncode == 0, 'command failed: %s\n%s' % (argv[0], cp.stderr[-2000:]))
    return cp


def lock_hashes(path):
    """Every requirement line pinned with ==, and every wheel hash it allows."""
    text = Path(path).read_text()
    names = re.findall(r'^([A-Za-z0-9_.\-]+)==([^ \\\n]+)', text, re.M)
    need(names, 'empty lock: %s' % path)
    return names, set(re.findall(r'--hash=sha256:([0-9a-f]{64})', text))


def check_wheelhouse(wheelhouse, allowed):
    wheelhouse = Path(wheelhouse)
    need(wheelhouse.is_absolute() and wheelhouse.is_dir() and not wheelhouse.is_symlink(), 'wheelhouse must be a real absolute directory')
    found = {}
    for p in sorted(wheelhouse.iterdir()):
        st = p.lstat()
        need(stat.S_ISREG(st.st_mode) and p.suffix == '.whl' and not st.st_mode & 0o022, 'wheelhouse entry refused: ' + p.name)
        digest = sha(p)
        need(digest in allowed, 'wheel not in lock: ' + p.name)
        found[p.name] = digest
    return found


def no_hardlinks(root):
    bad = [str(p) for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink() and p.lstat().st_nlink != 1]
    need(not bad, 'hardlinked files in runtime: %s' % bad[:3])


def editable_mapping(runtime):
    import ast
    finders = sorted((runtime / '.venv/lib/python3.11/site-packages').glob('__editable___hermes_agent_*_finder.py'))
    need(len(finders) == 1, 'editable finder missing or ambiguous')
    for node in ast.parse(finders[0].read_text()).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == 'MAPPING':
            mapping = ast.literal_eval(node.value)
            need(all(v == str(runtime / k) for k, v in mapping.items()), 'editable finder maps outside the runtime')
            return finders[0], mapping
    need(False, 'editable finder has no MAPPING')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pinned-native', type=Path, required=True)
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--python', type=Path, required=True, help='CPython 3.11.16 interpreter (not a venv)')
    ap.add_argument('--uv', type=Path, required=True)
    ap.add_argument('--wheelhouse', type=Path, required=True)
    ap.add_argument('--verify-python', type=Path, required=True, help='interpreter used to run verify_native_overlay.py')
    a = ap.parse_args()
    os.umask(0o022)
    overlay = json.loads((NI / 'native_overlay.json').read_text()); patch = NI / 'native_runtime.patch'
    need(overlay['patch_sha256'] == sha(patch) and overlay['source_only'] is True, 'overlay manifest/patch mismatch')
    runtime = a.runtime
    need(runtime.is_absolute() and not os.path.lexists(runtime), 'runtime target must be absolute and must not exist')
    need(runtime.parent.is_dir() and not runtime.parent.is_symlink() and runtime.parent.resolve() == runtime.parent, 'runtime parent must be a real directory')
    pinned = a.pinned_native.resolve(strict=True); need((pinned / '.git').is_dir(), 'pinned native has no .git')
    need(a.python.is_file() and not str(a.python).find('/.venv/') >= 0, 'interpreter must be a base CPython, not a venv')
    names, allowed = lock_hashes(NI / 'runtime.lock.txt'); bnames, ballowed = lock_hashes(NI / 'build.lock.txt')
    wheels = check_wheelhouse(a.wheelhouse, allowed | ballowed)
    git = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false']
    genv = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
    status_before = run(git + ['-C', pinned, '--no-optional-locks', 'status', '--porcelain'], env=genv).stdout
    run(git + ['clone', '-q', '--no-hardlinks', '--no-checkout', pinned / '.git', runtime], env=genv)
    run(git + ['-C', runtime, 'checkout', '-q', '--detach', overlay['base_head']], env=genv)
    verify = [a.verify_python, '-B', HERE / 'ci/verify_native_overlay.py', '--native-root', runtime, '--manifest', NI / 'native_overlay.json', '--patch', patch, '--phase']
    before = json.loads(run(verify + ['before'], env={'PYTHONDONTWRITEBYTECODE': '1'}).stdout.strip().splitlines()[-1])
    run(git + ['-C', runtime, 'apply', '--whitespace=nowarn', patch], env=genv)
    after = json.loads(run(verify + ['after'], env={'PYTHONDONTWRITEBYTECODE': '1'}).stdout.strip().splitlines()[-1])
    need(before.get('ok') is True and after.get('ok') is True and after.get('files') == 17, 'overlay verification failed')
    uenv = {'UV_OFFLINE': '1', 'UV_NO_CONFIG': '1', 'UV_PYTHON_DOWNLOADS': 'never', 'UV_LINK_MODE': 'copy', 'UV_CACHE_DIR': str(runtime / '.uv-cache'),
            'UV_NO_INDEX': '1', 'UV_FIND_LINKS': str(a.wheelhouse)}
    run([a.uv, 'venv', '-q', '--offline', '--python', a.python, '--prompt', 'hermes-agent-runtime', runtime / '.venv'], env=uenv)
    vpy = runtime / '.venv/bin/python'
    run([a.uv, 'pip', 'install', '-q', '--offline', '--no-index', '--find-links', a.wheelhouse, '--python', vpy, '--require-hashes', '--no-deps', '-r', NI / 'runtime.lock.txt'], env=uenv)
    run([a.uv, 'pip', 'install', '-q', '--offline', '--no-index', '--find-links', a.wheelhouse, '--python', vpy, '--no-deps', '-e', runtime], env=uenv)
    run(['/bin/rm', '-rf', runtime / '.uv-cache'])
    need(not (runtime / '.venv').is_symlink(), 'runtime venv is a symlink')
    no_hardlinks(runtime / '.venv')
    finder, mapping = editable_mapping(runtime)
    for name in [n[:-3] for n in overlay['files'] if '/' not in n and n.endswith('.py')]:
        need(mapping.get(name) == str(runtime / name), 'overlay module not packaged: ' + name)
    entry = runtime / '.venv/bin/hermes'
    need(entry.read_bytes().split(b'\n', 1)[0] == b'#!' + str(vpy).encode(), 'console entry shebang is not the runtime interpreter')
    norm = lambda n: re.sub(r'[-_.]+', '-', n).lower()  # PEP 503
    installed = {norm(k): v for k, v in json.loads(run([vpy, '-I', '-B', '-c', 'import importlib.metadata as m,json;print(json.dumps({d.metadata["Name"]:d.version for d in m.distributions()}))']).stdout).items()}
    for n, v in names:
        need(installed.get(norm(n)) == v, 'locked package missing or different: %s==%s' % (n, v))
    need(set(installed) - {norm(n) for n, _ in names} == {'hermes-agent'}, 'unlocked packages in runtime: %s' % sorted(set(installed) - {norm(n) for n, _ in names} - {'hermes-agent'}))
    version = run([entry, '--version'], env={'HOME': str(runtime), 'PYTHONDONTWRITEBYTECODE': '1'}).stdout.splitlines()[0]
    status_after = run(git + ['-C', pinned, '--no-optional-locks', 'status', '--porcelain'], env=genv).stdout
    need(status_before == status_after, 'pinned native changed during installation')
    report = dict(schema='sf-native-runtime-install/v1', runtime=str(runtime), base_head=overlay['base_head'], patch_sha256=overlay['patch_sha256'],
                  overlay_before=before, overlay_after=after, wheels=wheels, locked_packages=len(names), build_packages=len(bnames),
                  editable_finder=str(finder), editable_finder_sha256=sha(finder), editable_modules=len(mapping),
                  entry=str(entry), entry_sha256=sha(entry), hermes_version=version, network_used=False, hardlinks=0,
                  pinned_native_unchanged=True, installed=True, live_execution_authorized=False)
    out = runtime / 'INSTALL_REPORT.json'; out.write_text(json.dumps(report, indent=1, sort_keys=True) + '\n'); out.chmod(0o600)
    print(json.dumps({k: report[k] for k in ('runtime', 'patch_sha256', 'locked_packages', 'editable_modules', 'hermes_version')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
