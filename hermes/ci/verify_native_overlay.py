#!/usr/bin/env python3
"""Odczytowa kontrola nakładki CI przed/po git apply; nie instalator ani akceptacja.

Manifest i patch to wejście recenzji, nie podpis kryptograficzny. Kontrola wiąże
ich deklarację z bajtami, bazą Git i trybami plików; nie zatwierdza bezpieczeństwa.
Sprawdza wyłącznie zamknięty zestaw plików nakładki, nie cały checkout zależności.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any


class OverlayError(ValueError):
    """Niezgodność kandydata; kod błędu blokuje następny krok CI."""


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise OverlayError('duplicate manifest key')
        result[key] = value
    return result


def _read(path: Path, limit: int = 16 * 1024 * 1024) -> tuple[bytes, int]:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise OverlayError('noncanonical or symlink source path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise OverlayError('source is not a bounded regular file')
        chunks = []
        total = 0
        while block := os.read(fd, min(65536, limit + 1 - total)):
            chunks.append(block)
            total += len(block)
            if total > limit:
                raise OverlayError('source size limit exceeded')
        after = os.fstat(fd)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if identity(before) != identity(after) or total != after.st_size:
            raise OverlayError('source changed during read')
        return b''.join(chunks), stat.S_IMODE(after.st_mode)
    finally:
        os.close(fd)


def _git(native: Path, *args: str) -> bytes:
    env = {'PATH': os.defpath, 'HOME': '/nonexistent', 'LANG': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    return subprocess.run(['/usr/bin/git', '-c', 'core.fsmonitor=false',
                           '-c', 'core.hooksPath=/dev/null', '-C', str(native), *args],
                          env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=30).stdout


def _path(value: str) -> None:
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_./-]+', value)
            or PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value
            or any(part in {'.', '..', '.git'} for part in PurePosixPath(value).parts)):
        raise OverlayError('unsafe overlay path')


def _manifest(path: Path) -> dict:
    raw, _ = _read(path, 1024 * 1024)
    data = json.loads(raw, object_pairs_hook=_strict_pairs)
    if (type(data) is not dict or set(data) != {'base_head', 'base_tree', 'source_only',
            'installable', 'patch_sha256', 'files'} or data['source_only'] is not True
            or data['installable'] is not False):
        raise OverlayError('invalid or install-enabled source manifest')
    for key, length in [('base_head', 40), ('base_tree', 40), ('patch_sha256', 64)]:
        if type(data[key]) is not str or not re.fullmatch('[0-9a-f]{'+str(length)+'}', data[key]):
            raise OverlayError('invalid manifest hash')
    if type(data['files']) is not dict or not 1 <= len(data['files']) <= 64:
        raise OverlayError('invalid overlay inventory')
    for name, spec in data['files'].items():
        _path(name)
        if (type(spec) is not dict or set(spec) != {'sha256', 'mode'}
                or type(spec['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', spec['sha256'])
                or spec['mode'] not in {'0o644', '0o755'}):
            raise OverlayError('invalid file hash or mode')
    return data


def verify(native: Path, manifest: Path, patch: Path, phase: str) -> dict:
    if phase not in {'before', 'after'}:
        raise OverlayError('unknown verification phase')
    if not native.is_absolute() or native.resolve(strict=True) != native or not native.is_dir():
        raise OverlayError('noncanonical native root')
    data = _manifest(manifest)
    patch_bytes, _ = _read(patch)
    if hashlib.sha256(patch_bytes).hexdigest() != data['patch_sha256']:
        raise OverlayError('patch SHA-256 mismatch')
    blocks = []
    for line in patch_bytes.decode('utf-8').splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([A-Za-z0-9_./-]+) b/([A-Za-z0-9_./-]+)', line)
            if match is None or match[1] != match[2]:
                raise OverlayError('rename or ambiguous patch path refused')
            _path(match[1]); blocks.append(match[1])
        if line.startswith(('deleted file mode ', 'rename ', 'copy ')):
            raise OverlayError('deletion/rename/copy is outside overlay contract')
        if line.startswith(('old mode ', 'new mode ', 'new file mode ')) and line.split()[-1] not in {'100644', '100755'}:
            raise OverlayError('nonregular patch mode')
    if len(blocks) != len(set(blocks)) or set(blocks) != set(data['files']):
        raise OverlayError('patch file inventory differs from manifest')
    if (_git(native, 'rev-parse', 'HEAD').decode().strip() != data['base_head']
            or _git(native, 'rev-parse', 'HEAD^{tree}').decode().strip() != data['base_tree']):
        raise OverlayError('native Git base differs from manifest')
    for name, spec in data['files'].items():
        target = native / name
        if phase == 'before':
            entry = _git(native, 'ls-tree', '-z', 'HEAD', '--', name)
            if not entry:
                if os.path.lexists(target):
                    raise OverlayError('new overlay target already exists: ' + name)
                continue
            mode, kind, rest = entry.rstrip(b'\0').split(b' ', 2)
            oid, returned = rest.split(b'\t', 1)
            if kind != b'blob' or returned.decode() != name or mode not in {b'100644', b'100755'}:
                raise OverlayError('nonregular Git baseline entry')
            raw, actual_mode = _read(target)
            actual_oid = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            if actual_oid != oid.decode() or actual_mode != (int(mode, 8) & 0o777):
                raise OverlayError('overlay target differs from Git baseline: ' + name)
        else:
            raw, actual_mode = _read(target)
            if hashlib.sha256(raw).hexdigest() != spec['sha256'] or actual_mode != int(spec['mode'], 8):
                raise OverlayError('applied source hash or mode mismatch: ' + name)
    return {'ok': True, 'phase': phase, 'files': len(data['files']),
            'patch_sha256': data['patch_sha256'], 'base_head': data['base_head'],
            'installable': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    args = parser.parse_args()
    try:
        result = verify(args.native_root.absolute(), args.manifest.absolute(), args.patch.absolute(), args.phase)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('NATIVE_OVERLAY_REFUSED: '+str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
