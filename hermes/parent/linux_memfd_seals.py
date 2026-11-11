"""Linux x86-64 memfd UAPI adapter for builds omitting optional Python names.

No monkey-patching of fcntl/os, no filesystem fallback, no weakened seals. UAPI:
linux v6.8 include/uapi/{linux/fcntl.h,asm-generic/fcntl.h,linux/memfd.h}
and arch/x86/entry/syscalls/syscall_64.tbl. See docs/MEMFD_UAPI.md.
A present but wrong exported value is an error, not a reason to override it.
"""
from __future__ import annotations
import ctypes
import errno
import fcntl
import mmap
import os
import struct
import sys

FCNTL_UAPI = {'F_ADD_SEALS': 1033, 'F_GET_SEALS': 1034, 'F_SEAL_SEAL': 1,
              'F_SEAL_SHRINK': 2, 'F_SEAL_GROW': 4, 'F_SEAL_WRITE': 8}
MEMFD_UAPI = {'MFD_CLOEXEC': 1, 'MFD_ALLOW_SEALING': 2}
SYS_MEMFD_CREATE_X86_64 = 319
REQUIRED_SEALS = 15
_MISSING = object()


def require_platform():
    if (sys.platform != 'linux' or os.uname().machine != 'x86_64'
            or sys.byteorder != 'little' or struct.calcsize('P') != 8):
        raise RuntimeError('memfd_requires_linux_x86_64_lp64')


def checked_constants(module, expected):
    """Use the pinned UAPI value only when the Python export is absent."""
    require_platform()
    result = {}
    for name, value in expected.items():
        exported = getattr(module, name, _MISSING)
        if exported is not _MISSING and (type(exported) is not int or exported != value):
            raise RuntimeError('memfd_exported_constant_mismatch:' + name)
        result[name] = value if exported is _MISSING else exported
    return result


C = checked_constants(fcntl, FCNTL_UAPI)
M = checked_constants(os, MEMFD_UAPI)
F_ADD_SEALS = C['F_ADD_SEALS']
F_GET_SEALS = C['F_GET_SEALS']


def description():
    c = checked_constants(fcntl, FCNTL_UAPI)
    m = checked_constants(os, MEMFD_UAPI)
    api = getattr(os, 'memfd_create', _MISSING)
    if api is not _MISSING and not callable(api):
        raise RuntimeError('memfd_create_export_not_callable')
    return dict(platform=sys.platform, machine=os.uname().machine, pointer_bits=64,
                byteorder=sys.byteorder, constants=c, flags=m,
                missing_fcntl_exports=sorted(n for n in c if not hasattr(fcntl, n)),
                missing_os_flags=sorted(n for n in m if not hasattr(os, n)),
                creation_backend='linux_x86_64_syscall_319' if api is _MISSING else 'os.memfd_create',
                fcntl_module_file=getattr(fcntl, '__file__', None),
                python_modules_modified=False, filesystem_fallback=False)


def _raw_memfd(label: bytes, flags: int) -> int:
    # Exactly one specific syscall, only on the ABI checked above. No shell,
    # executable discovery, external library path or general syscall API.
    require_platform()
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_char_p, ctypes.c_uint]
    ctypes.set_errno(0)
    fd = syscall(SYS_MEMFD_CREATE_X86_64, label, flags)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err or errno.EIO, 'memfd_create syscall failed; no fallback')
    return int(fd)


def create_memfd(label: str) -> int:
    require_platform()
    if type(label) is not str or not label or len(label) > 128 or '\x00' in label:
        raise ValueError('memfd_label')
    raw = label.encode('ascii')
    flags = checked_constants(os, MEMFD_UAPI)
    api = getattr(os, 'memfd_create', _MISSING)
    if api is _MISSING:
        fd = _raw_memfd(raw, flags['MFD_CLOEXEC'] | flags['MFD_ALLOW_SEALING'])
    elif callable(api):
        # A failed present implementation is NOT retried via another backend.
        fd = api(label, flags['MFD_CLOEXEC'] | flags['MFD_ALLOW_SEALING'])
    else:
        raise RuntimeError('memfd_create_export_not_callable')
    if type(fd) is not int or fd < 0:
        raise RuntimeError('memfd_invalid_descriptor')
    try:
        if fd <= 2 or os.get_inheritable(fd):
            raise RuntimeError('memfd_descriptor_not_private_cloexec')
        return fd
    except BaseException:
        os.close(fd)
        raise


def get_seals(fd: int) -> int:
    c = checked_constants(fcntl, FCNTL_UAPI)
    return fcntl.fcntl(fd, c['F_GET_SEALS'])


def seal(fd: int):
    c = checked_constants(fcntl, FCNTL_UAPI)
    fcntl.fcntl(fd, c['F_ADD_SEALS'], REQUIRED_SEALS)
    if get_seals(fd) & REQUIRED_SEALS != REQUIRED_SEALS:
        raise RuntimeError('memfd_required_seals_missing')


def kernel_probe() -> dict:
    """Actual kernel enforcement on disposable data; never runtime/capsule bytes."""
    info = description()
    data = b'SF_SEAL_ENFORCEMENT_V1\n'
    fd = create_memfd('sf-seal-selftest')
    try:
        if os.write(fd, data) != len(data) or os.pread(fd, len(data), 0) != data:
            raise RuntimeError('memfd_probe_baseline')
        seal(fd)
        denials = {}
        def mapping():
            with mmap.mmap(fd, len(data), flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ | mmap.PROT_WRITE):
                pass
        actions = {'write': lambda: os.pwrite(fd, b'X', 0),
                   'shrink': lambda: os.ftruncate(fd, len(data)-1),
                   'grow': lambda: os.ftruncate(fd, len(data)+1),
                   'add_seal': lambda: fcntl.fcntl(fd, F_ADD_SEALS, 0),
                   'shared_write_mapping': mapping}
        for name, action in actions.items():
            try:
                action()
            except OSError as exc:
                if exc.errno != errno.EPERM:
                    raise RuntimeError('memfd_probe_wrong_errno:' + name) from exc
                denials[name] = exc.errno
            else:
                raise RuntimeError('memfd_probe_not_enforced:' + name)
        if os.fstat(fd).st_size != len(data) or os.pread(fd, len(data)+1, 0) != data:
            raise RuntimeError('memfd_probe_content_changed')
        return dict(schema='sf-memfd-kernel-probe/v1', ok=True, interface=info,
                    required_seals=REQUIRED_SEALS, observed_seals=get_seals(fd),
                    denied_operations=denials, bytes_preserved=True,
                    captured_code_executed=False, service_started=False, bwrap_executed=False)
    finally:
        os.close(fd)
