"""Durable evidence and bounded process observations for launcher v1.

No retry, shell, or interpretation of a test observation as native approval.
Publication is create-exclusive: a partial file stays a barrier after a crash.
"""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import secrets
import stat
import subprocess
import time

import repository_task_contract as c
import repository_isolation_plan as iso

MAX_OUTPUT = 1024 * 1024
MAX_SECONDS = 600
OBS_SCHEMA = 'software-factory-sandbox-unittest-observation/v1'


class ExecutionRefused(RuntimeError):
    """Machine code only, never a traceback/source/claim in public results."""


def need(ok: object, code: str) -> None:
    if not ok:
        raise ExecutionRefused(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, limit: int = c.MAX_JSON) -> bytes:
    return iso.read_regular(Path(path), limit)


def read_json(path: Path, limit: int = c.MAX_JSON) -> dict:
    value = c.parse(read(path, limit))
    need(type(value) is dict, 'evidence_not_object')
    return value


def new_file(path: Path, data: bytes) -> None:
    """O_EXCL, full writes, file fsync then directory fsync. Never truncate/retry."""
    need(type(data) is bytes and len(data) <= 8 * MAX_OUTPUT, 'evidence_size')
    path = Path(path)
    with c.directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                need(n > 0, 'evidence_short_write')
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)


def publish(path: Path, data: bytes) -> None:
    """Atomic, serialized export. Not a consumption primitive.

    Never replace an existing result with different bytes. A bounded exporter
    lock protects the absent-name check and rename; same-UID hostile writers
    remain outside this trusted-controller boundary. Crash before rename leaves
    an orphan temporary file, not a partial final result.
    """
    path = Path(path)
    need(type(data) is bytes and len(data) <= 8 * MAX_OUTPUT, 'export_size')
    with c.directory_fd(path.parent) as parent:
        lock = os.open('.' + path.name + '.export.lock', os.O_RDWR | os.O_CREAT |
                       os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        temporary = None
        try:
            st = os.fstat(lock)
            need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
                 st.st_nlink == 1 and st.st_size == 0 and st.st_mode & 0o777 == 0o600, 'export_lock_metadata')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if present(path):
                need(read(path, 8 * MAX_OUTPUT) == data, 'existing_evidence_conflict')
                os.fsync(parent)
                return
            temporary = '.' + path.name + '.export-' + secrets.token_hex(12)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            try:
                view = memoryview(data)
                while view:
                    count = os.write(fd, view)
                    need(count > 0, 'export_short_write')
                    view = view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
            need(not present(path), 'export_destination_race')
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            temporary = None
            os.fsync(parent)
        finally:
            if temporary is not None:
                os.unlink(temporary, dir_fd=parent)
            os.close(lock)


def new_json(path: Path, value: dict) -> None:
    new_file(path, c.canonical(value))


def publish_json(path: Path, value: dict) -> None:
    publish(path, c.canonical(value))


def mkdir(path: Path) -> None:
    path = Path(path)
    with c.directory_fd(path.parent) as fd:
        os.mkdir(path.name, 0o700, dir_fd=fd)
        os.fsync(fd)


def present(path: Path) -> bool:
    """A broken symlink is a barrier too."""
    return os.path.lexists(path)


def observation(stdout: bytes, stderr: bytes, process: dict, input_sha: str,
                expected_count: int) -> dict:
    """Validate output from the tested process, not an independent attestation."""
    c.hex_value(input_sha)
    need(type(expected_count) is int and expected_count > 0, 'expected_count')
    need(type(stdout) is bytes and type(stderr) is bytes and
         len(stdout) + len(stderr) <= MAX_OUTPUT, 'output_budget')
    need(type(process) is dict and type(process.get('exit')) is int and
         process.get('child_reaped') is True and process.get('timed_out') is False and
         process.get('output_limited') is False, 'process_unsuccessful')
    try:
        d = c.parse(stdout)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ExecutionRefused('malformed_test_json') from exc
    required = ('schema', 'input_sha256', 'tests_run', 'errors', 'failures', 'skipped',
                'expected_failures', 'unexpected_successes', 'isolation_attested',
                'native_approval', 'ok')
    need(type(d) is dict and set(d) == set(required), 'test_observation_fields')
    need(d['schema'] == OBS_SCHEMA and d['input_sha256'] == input_sha, 'test_observation_binding')
    for name in ('tests_run', 'errors', 'failures', 'skipped', 'expected_failures', 'unexpected_successes'):
        need(type(d[name]) is int and 0 <= d[name] <= 1000000, 'test_counter:' + name)
    need(d['tests_run'] == expected_count, 'test_count_mismatch')
    need(d['isolation_attested'] is False and d['native_approval'] is False and
         type(d['ok']) is bool, 'test_observation_authority')
    clean = all(d[n] == 0 for n in ('errors', 'failures', 'skipped', 'expected_failures', 'unexpected_successes'))
    need(d['ok'] is clean and process['exit'] == (0 if clean else 1), 'exit_report_disagreement')
    return dict(d)


def run_bounded(argv: list[str], pass_fds: tuple[int, ...], *, timeout: int,
                before_spawn, after_spawn) -> dict:
    """One fixed bwrap Popen. Outer managed cgroup owns survivors if broker dies.

    before_spawn is a trusted context manager keeping the parent admission fence
    and a short native read transaction through Popen only, never through wait.
    after_spawn durably binds the one process; failure kills/reaps, never retries.
    """
    need(type(timeout) is int and 1 <= timeout <= MAX_SECONDS, 'execution_deadline')
    need(type(argv) is list and argv and argv[0] == '/usr/bin/bwrap' and
         argv[-6:] == ['--', '/runtime/bin/python3.11', '-I', '-B', '-S', '/control/unittest_entry.py'],
         'execution_program')
    need(type(pass_fds) is tuple and len(set(pass_fds)) == len(pass_fds) and
         all(type(fd) is int and fd > 2 for fd in pass_fds), 'execution_fds')
    result = {'exit': None, 'timed_out': False, 'output_limited': False,
              'child_reaped': False, 'stdout': b'', 'stderr': b''}
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    child = None
    begin = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            with before_spawn():
                child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, close_fds=True, pass_fds=pass_fds, shell=False,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
                after_spawn(child)
            for name, pipe in (('stdout', child.stdout), ('stderr', child.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                left = timeout - (time.monotonic() - begin)
                if left <= 0:
                    result['timed_out'] = True
                    break
                for event, _ in selector.select(min(.1, left)):
                    used = sum(map(len, buffers.values()))
                    chunk = os.read(event.fd, min(65536, MAX_OUTPUT + 1 - used))
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    room = MAX_OUTPUT - used
                    buffers[event.data].extend(chunk[:room])
                    if len(chunk) > room:
                        result['output_limited'] = True
                        break
                if result['output_limited']:
                    break
            if not result['timed_out'] and not result['output_limited']:
                try:
                    child.wait(timeout=max(.001, timeout - (time.monotonic() - begin)))
                except subprocess.TimeoutExpired:
                    result['timed_out'] = True
            if child.poll() is None:
                child.kill()
            result['exit'] = child.wait(timeout=3)
            result['child_reaped'] = True
            result.update({n: bytes(v) for n, v in buffers.items()})
            return result
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
            finally:
                for pipe in (child.stdout, child.stderr):
                    if pipe is not None:
                        pipe.close()
