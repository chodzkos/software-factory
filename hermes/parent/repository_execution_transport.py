"""Exact fixed-boot13 runtime mapping and sealed input transport for launcher v1.

Only the 593 runtime rows of the pinned archive are mapped. The three fixed
probe control/work rows are excluded by exact name, not a loose prefix rule.
The boot snapshot's metadata hash is NOT compared to the approval schema hash.
"""
from __future__ import annotations
import errno
import io
import os
from pathlib import Path
import resource
import stat
import zipfile

import linux_memfd_seals as seals
import repository_task_contract as c
import repository_isolation_plan as iso
import repository_execution_admission as a
from repository_execution_io import need, read, sha

EXECUTABLES = frozenset({'runtime/bin/python3.11', 'lib64/ld-linux-x86-64.so.2'})
PROBE_INPUTS = frozenset({'control/fixed_boot_probe.py', 'control/boot.json', 'work/read-only.txt'})
MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 593 + c.MAX_FILES + 2
TOOLS = {'/usr/bin/bwrap': 'e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71',
         '/usr/bin/systemctl': 'e0d3d0e9444da1b2b58c792c3f5028b69f049b77d5ca17b3ec0d09f89117225b',
         '/usr/bin/systemd-run': 'dbc8b988a849d5c9d7ef2de7068a6f107021bc6c11e0d7864c73f373eef726a7'}


def boot_runtime(archive: bytes) -> dict:
    need(type(archive) is bytes and len(archive) < 2 * 1024 * 1024 and
         sha(archive) == a.FIXED_BOOT13_ARCHIVE_SHA256, 'boot_archive_pin')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = z.namelist()
        need(len(names) == len(set(names)) and sum(i.file_size for i in z.infolist()) < 8 * 1024 * 1024,
             'boot_inventory')
        need(z.testzip() is None, 'boot_crc')
        files = {n: z.read(n) for n in names}
    coll = c.parse(files['COLLECTION.json'])['included']
    need(set(files) == set(coll) | {'COLLECTION.json'}, 'boot_collection_inventory')
    for name, row in coll.items():
        need(len(files[name]) == row['bytes'] and sha(files[name]) == row['sha256'], 'boot_collection')
    state = c.parse(files['state.json'])
    complete = c.parse(files['BOOT_COMPLETE.json'])
    need(state['status'] == complete['status'] == 'PASS' and state['start_consumed'] is True and
         state['runner_sha256'] == a.FIXED_BOOT13_HELPER_SHA256 and complete['fixed_probe_passed'] is True,
         'boot_not_pass')
    need(complete['postflight']['snapshot_sha256'] == a.FIXED_BOOT13_RUNTIME_SNAPSHOT_SHA256 and
         complete['postflight']['tool_hashes'] == TOOLS, 'boot_snapshot_or_tools')
    rows = c.parse(files['SEALED_INPUTS.json'])['files']
    need(len(rows) == 596 and PROBE_INPUTS <= set(rows), 'boot_sealed_inventory')
    mapped = {}
    for name, row in sorted(rows.items()):
        if name in PROBE_INPUTS:
            continue
        iso.runtime_path(name)
        need(set(row) == {'sha256', 'bytes', 'mode', 'seals'} and row['seals'] == 15 and
             type(row['bytes']) is int and row['bytes'] >= 0, 'boot_runtime_row')
        need(row['mode'] == ('0500' if name in EXECUTABLES else '0400'), 'boot_runtime_mode')
        c.hex_value(row['sha256'])
        mapped[name] = {'sha256': row['sha256'], 'bytes': row['bytes'],
                        'mode': '100755' if name in EXECUTABLES else '100644'}
    need(len(mapped) == 593, 'boot_runtime_count')
    return {'schema': iso.RUNTIME_SCHEMA, 'candidate_manifest': c.CANDIDATE,
            'platform': 'linux-x86_64', 'python': [3, 11, 16], 'files': mapped}


def runtime_binding(root: Path, approval: bytes, expected_sha: str, boot_archive: bytes):
    """Approval bytes and all live runtime files must equal the booted row mapping."""
    d = boot_runtime(boot_archive)
    need(c.parse(approval) == d, 'runtime_not_the_booted_capsule')
    return iso.admit_runtime(root, approval, expected_sha)


def host_tool(path: str) -> dict:
    need(path in TOOLS, 'unapproved_host_tool')
    p = Path(path)
    # Fixed system directories only; none of these paths may be symlinks.
    for parent in (p, *p.parents):
        st = parent.lstat()
        need(not stat.S_ISLNK(st.st_mode) and st.st_uid in (0, os.geteuid()) and
             not st.st_mode & 0o022, 'host_tool_ancestor')
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
             0 < before.st_size <= 16 * 1024 * 1024 and not before.st_mode & 0o6022 and
             before.st_mode & 0o111, 'host_tool_metadata')
        for attr in ('security.capability', 'system.posix_acl_access'):
            try:
                value = os.getxattr(fd, attr)
            except OSError as exc:
                need(exc.errno in (errno.ENODATA, errno.ENOTSUP), 'host_tool_xattr_unknown')
            else:
                need(not value, 'host_tool_extra_privilege')
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            raw = os.read(fd, min(65536, remaining))
            if not raw:
                break
            chunks.append(raw)
            remaining -= len(raw)
        raw = b''.join(chunks)
        need(c._stamp(before) == c._stamp(os.fstat(fd)) == c._stamp(p.lstat()) and
             len(raw) == before.st_size and sha(raw) == TOOLS[path], 'host_tool_drift')
        return {'path': path, 'sha256': sha(raw), 'bytes': len(raw),
                'device': before.st_dev, 'inode': before.st_ino}
    finally:
        os.close(fd)


def sealed_fd(raw: bytes) -> int:
    need(type(raw) is bytes and len(raw) <= iso.MAX_RUNTIME_FILE, 'sealed_input_size')
    fd = seals.create_memfd('sf-repository-execution-v1')
    try:
        view = memoryview(raw)
        while view:
            n = os.write(fd, view)
            need(n > 0, 'sealed_short_write')
            view = view[n:]
        os.lseek(fd, 0, os.SEEK_SET)
        seals.seal(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


class Inputs:
    def __init__(self):
        self.fds = {}
        self.rows = {}
        self.seccomp = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            all_fds = list(self.fds.values()) + ([] if self.seccomp is None else [self.seccomp])
            for fd in all_fds:
                os.close(fd)

    def add(self, path: str, raw: bytes, mode: str) -> None:
        need(not self.closed and path not in self.fds and mode in ('0400', '0500'), 'input_duplicate_or_mode')
        if path.startswith(('runtime/', 'lib/', 'lib64/')):
            iso.runtime_path(path)
        elif path.startswith('work/'):
            c.path_name(path[5:])
        else:
            need(path in ('control/TEST_INPUT.json', 'control/unittest_entry.py'), 'input_destination')
        need(len(self.fds) < MAX_FILES and sum(v['bytes'] for v in self.rows.values()) + len(raw) <= MAX_BYTES,
             'input_budget')
        self.fds[path] = sealed_fd(raw)
        self.rows[path] = {'sha256': sha(raw), 'bytes': len(raw), 'mode': mode}

    def capture(self, runtime, frozen_root: Path, frozen: dict) -> None:
        need(not self.fds and self.seccomp is None, 'input_capture_already_used')
        count = len(runtime.files) + len(frozen['files']) + 2
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        need(soft == resource.RLIM_INFINITY or soft >= count + 96, 'input_fd_limit')
        for name, (expected, size, mode) in sorted(runtime.files.items()):
            raw = read(runtime.root / name, iso.MAX_RUNTIME_FILE)
            need(sha(raw) == expected and len(raw) == size, 'runtime_copy_drift')
            self.add(name, raw, '0500' if name in EXECUTABLES else '0400')
        for name, row in sorted(frozen['files'].items()):
            raw = read(frozen_root / 'work' / name, c.MAX_FILE)
            need(sha(raw) == row['sha256'] and len(raw) == row['bytes'], 'work_copy_drift')
            self.add('work/' + name, raw, '0500' if row['mode'] == '100755' else '0400')
        raw = read(frozen_root / 'control/TEST_INPUT.json')
        need(raw == c.canonical(frozen), 'input_copy_drift')
        self.add('control/TEST_INPUT.json', raw, '0400')
        raw = read(frozen_root / 'control/unittest_entry.py')
        need(raw == iso.INNER_ENTRY, 'entry_copy_drift')
        self.add('control/unittest_entry.py', raw, '0400')
        self.seccomp = sealed_fd(iso.seccomp_program())
        self.verify()

    def verify(self) -> None:
        need(not self.closed and self.seccomp is not None, 'input_transport_state')
        for name, fd in self.fds.items():
            row = self.rows[name]
            need(seals.get_seals(fd) & 15 == 15 and os.fstat(fd).st_size == row['bytes'], 'input_seal_lost')
            need(sha(os.pread(fd, row['bytes'] + 1, 0)) == row['sha256'], 'input_bytes_changed')
            os.lseek(fd, 0, os.SEEK_SET)
        bpf = iso.seccomp_program()
        need(seals.get_seals(self.seccomp) & 15 == 15 and
             os.pread(self.seccomp, len(bpf) + 1, 0) == bpf, 'filter_seal_lost')
        os.lseek(self.seccomp, 0, os.SEEK_SET)

    def argv(self, plan: dict, runtime, frozen_root: Path) -> list[str]:
        """Exact match-and-rewrite of rebuilt plan; no arbitrary argv passthrough."""
        self.verify()
        expected = []
        for name in sorted(runtime.files):
            expected += ['--ro-bind', str(runtime.root / name), '/' + name]
        expected += ['--ro-bind', str(frozen_root / 'work'), '/work',
                     '--ro-bind', str(frozen_root / 'control'), '/control']
        prefix = ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
                  '--unshare-uts', '--unshare-cgroup', '--disable-userns', '--assert-userns-disabled',
                  '--cap-drop', 'ALL', '--new-session', '--die-with-parent', '--clearenv',
                  '--setenv', 'HOME', '/tmp/home', '--setenv', 'TMPDIR', '/tmp',
                  '--setenv', 'PATH', '/runtime/bin', '--setenv', 'LANG', 'C.UTF-8',
                  '--setenv', 'LC_ALL', 'C.UTF-8']
        suffix = ['--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--size', '67108864',
                  '--tmpfs', '/tmp', '--dir', '/tmp/home', '--chdir', '/work', '--remount-ro', '/',
                  '--seccomp', '3', '--', '/runtime/bin/python3.11', '-I', '-B', '-S', '/control/unittest_entry.py']
        need(plan['argv'] == prefix + expected + suffix, 'plan_to_launch_mismatch')
        mounts = []
        for name, fd in sorted(self.fds.items()):
            mounts += ['--perms', self.rows[name]['mode'],
                       '--file' if name in EXECUTABLES else '--ro-bind-data', str(fd), '/' + name]
        suffix[suffix.index('--seccomp') + 1] = str(self.seccomp)
        return prefix + mounts + suffix

    def pass_fds(self) -> tuple[int, ...]:
        need(self.seccomp is not None, 'filter_missing')
        return tuple(sorted([*self.fds.values(), self.seccomp]))
