"""Frozen repository test inputs and a fail-closed Linux sandbox plan, v1.0.0.

This module never spawns a process. A plan is NOT execution permission, runtime
approval, a service-quiescence receipt, or a claim that isolation was observed.
The existing DATA contract and native adapter execute() remain unchanged.

Trusted controller obligations: supply externally pinned admission/runtime data,
freeze under the native run/claim lock, and keep staged roots private from
concurrent writers. Same-UID hostile host processes and kernel exploits are not
addressed here. The future launcher must recheck all pins and live native state,
attach the existing managed service, and validate its resource limits/exit.
"""
from __future__ import annotations
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
from types import MappingProxyType
from typing import Mapping
import repository_task_contract as c

VERSION = '1.0.0'
POLICY = 'linux-x86_64-cpython311-stdlib-offline-plan/v1'
RUNTIME_SCHEMA = 'software-factory-approved-test-runtime/v1'
MAX_RUNTIME_FILES = 4096
MAX_RUNTIME_FILE = 64 * 1024 * 1024
MAX_RUNTIME_TOTAL = 256 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
BLOCKED_RUNTIME = frozenset({'site-packages','dist-packages','__pycache__','ensurepip'})
REQUIRED_RUNTIME = frozenset({'runtime/bin/python3.11','runtime/lib/python3.11/encodings/__init__.py',
    'runtime/lib/python3.11/unittest/__init__.py','lib64/ld-linux-x86-64.so.2'})
OPEN_GATES = ('exact-isolation-source-review','approved-runtime-capsule',
    'observed-linux-isolation-and-resource-limits','native-run-bound-launch-and-collection',
    'explicit-release-and-job-admission')

# Linux x86_64 syscall ABI only. No libseccomp, socket, ctypes or native import.
# Deny new sockets, namespace changes, process/thread creation and high-risk
# kernel interfaces. Default allow is NOT a complete kernel syscall allowlist.
DENIED_SYSCALLS = {
    'socket':41,'connect':42,'accept':43,'sendto':44,'recvfrom':45,'sendmsg':46,'recvmsg':47,
    'shutdown':48,'bind':49,'listen':50,'getsockname':51,'getpeername':52,'socketpair':53,
    'setsockopt':54,'getsockopt':55,'clone':56,'fork':57,'vfork':58,'ptrace':101,
    'pivot_root':155,'chroot':161,'mount':165,'umount2':166,'reboot':169,
    'init_module':175,'delete_module':176,'kexec_load':246,'add_key':248,'request_key':249,
    'keyctl':250,'unshare':272,'accept4':288,'perf_event_open':298,'recvmmsg':299,
    'name_to_handle_at':303,'open_by_handle_at':304,'sendmmsg':307,'setns':308,
    'process_vm_readv':310,'process_vm_writev':311,'finit_module':313,'kexec_file_load':320,
    'bpf':321,'userfaultfd':323,'io_uring_setup':425,'io_uring_enter':426,'io_uring_register':427,
    'open_tree':428,'move_mount':429,'fsopen':430,'fsconfig':431,'fsmount':432,'fspick':433,
    'clone3':435,'pidfd_getfd':438,'mount_setattr':442,
}

# This code is inert until a separately reviewed/validated launcher is activated.
# Output describes test observations; it is NOT an OS attestation or approval.
INNER_ENTRY = b'''"""Fixed immutable unittest entry. No shell, discovery, pip or user argv."""
import contextlib, hashlib, importlib.util, json, pathlib, sys, unittest
root = pathlib.Path('/work')
spec = json.loads(pathlib.Path('/control/TEST_INPUT.json').read_bytes())
if sys.version_info[:3] != (3, 11, 16):
    raise SystemExit(80)
suite = unittest.TestSuite()
sys.path.insert(0, str(root))
with contextlib.redirect_stdout(sys.stderr):
    for index, row in enumerate(spec['tests']):
        path = root / row['path']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise SystemExit(81)
        module_spec = importlib.util.spec_from_file_location('sf_immutable_test_' + str(index), path)
        if module_spec is None or module_spec.loader is None:
            raise SystemExit(82)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    discovered = suite.countTestCases()
    if discovered != spec['expected_count']:
        raise SystemExit(83)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
observed = dict(schema='software-factory-sandbox-unittest-observation/v1',
    input_sha256=hashlib.sha256(pathlib.Path('/control/TEST_INPUT.json').read_bytes()).hexdigest(),
    tests_run=result.testsRun, errors=len(result.errors), failures=len(result.failures),
    skipped=len(result.skipped), expected_failures=len(result.expectedFailures),
    unexpected_successes=len(result.unexpectedSuccesses),
    isolation_attested=False, native_approval=False)
ok = result.wasSuccessful() and result.testsRun == spec['expected_count'] and not (
    result.skipped or result.expectedFailures or result.unexpectedSuccesses)
observed['ok'] = bool(ok)
print(json.dumps(observed, sort_keys=True))
raise SystemExit(0 if ok else 1)
'''

class IsolationPlanRefused(ValueError):
    """Refusal code only; no repository contents or secrets in messages."""

def need(ok: object, code: str) -> None:
    if not ok:
        raise IsolationPlanRefused(code)

def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def source_state(admission: c.Admission, files: Mapping[str, bytes], modes: Mapping[str, str]) -> dict:
    """Check changes against the unchanged approved DATA contract."""
    need(type(admission) is c.Admission, 'admission_type')
    need(type(files) is dict and type(modes) is dict and set(files)==set(modes), 'workspace_maps')
    need(0 < len(files) <= c.MAX_FILES, 'workspace_file_count')
    need(all(type(p) is str for p in files), 'workspace_path_type')
    c.no_aliases(list(files))
    total=0
    for path, raw in files.items():
        c.path_name(path);c.source_text(raw)
        need(modes[path] in ('100644','100755'), 'workspace_mode')
        total+=len(raw)
    need(total <= c.MAX_TOTAL, 'workspace_total_size')
    if files!=dict(admission.files) or modes!=dict(admission.modes):
        c.patch(admission, files, modes)
    tests=c.parse(admission.tests)
    need(tests['recipe']=='python-unittest-v1', 'test_recipe')
    need(all(files.get(p)==admission.files[p] and modes.get(p)==admission.modes[p]
             for p in admission.immutable_tests), 'immutable_tests_changed')
    return {'schema':'software-factory-frozen-test-input/v1',
        'job_id':admission.job_id,'repository_id':admission.repository_id,
        'admission_sha256':sha(c.canonical(admission.receipt())),
        'commit':admission.commit,'tree':admission.tree,
        'files':{p:{'sha256':sha(files[p]),'bytes':len(files[p]),'mode':modes[p]} for p in sorted(files)},
        'tests':[{'path':p,'sha256':sha(files[p])} for p in admission.immutable_tests],
        'expected_count':tests['expected_count'],'timeout_seconds':tests['timeout_seconds'],
        'entry_sha256':sha(INNER_ENTRY),'policy':POLICY,'execution_permitted':False,
        'native_binding_verified':False}

def secure_path(path: Path) -> Path:
    p=Path(path)
    need(p.is_absolute() and re.fullmatch(r'/[A-Za-z0-9_./-]+', str(p)) is not None and
         '..' not in p.parts and str(p)!='/', 'host_path_grammar')
    # directory_fd opens each component with O_NOFOLLOW and checks ownership.
    with c.directory_fd(p):
        pass
    return p

def freeze(admission: c.Admission, files: dict, modes: dict, destination: Path) -> dict:
    """One exclusive frozen copy, never the live worktree; no cleanup/retry.

    Partially written directories stay consumed. An existing directory, even
    empty, is not reusable. c.directory_fd prevents following symlink ancestors.
    The owner must protect the destination's parent from concurrent changes.
    """
    descriptor=source_state(admission,files,modes)
    destination=Path(destination)
    need(destination.name and c.COMPONENT.fullmatch(destination.name), 'freeze_directory_name')
    parent=secure_path(destination.parent)
    with c.directory_fd(parent) as parent_fd:
        os.mkdir(destination.name,0o700,dir_fd=parent_fd)
        os.fsync(parent_fd)
    with c.directory_fd(destination) as fd:
        c._write_new(fd,'FREEZE_PENDING.json',c.canonical({'job_id':admission.job_id,'automatic_retry':False}))
        for path,raw in sorted(files.items()):
            c._write_new(fd,'work/'+path,raw,0o700 if modes[path]=='100755' else 0o600)
        c._write_new(fd,'control/TEST_INPUT.json',c.canonical(descriptor))
        c._write_new(fd,'control/unittest_entry.py',INNER_ENTRY)
        manifest={'schema':'software-factory-frozen-test-seal/v1','input_sha256':sha(c.canonical(descriptor)),
                  'entry_sha256':sha(INNER_ENTRY),'files':descriptor['files']}
        raw=c.canonical(manifest)
        c._write_new(fd,'SEALED.json',raw)
        # Persist intermediate directories too, before the completion marker.
        for base, _, _ in os.walk(destination, topdown=False, followlinks=False):
            with c.directory_fd(Path(base)) as subfd:
                os.fsync(subfd)
        os.unlink('FREEZE_PENDING.json',dir_fd=fd);os.fsync(fd)
    return {'root':str(destination),'seal_sha256':sha(raw),'input_sha256':manifest['input_sha256'],
            'job_id':admission.job_id,'execution_permitted':False,'automatic_retry':False}

def read_regular(path: Path, maximum: int) -> bytes:
    """Bounded regular-file read, no symlinks, group writes, hardlinks or FIFO."""
    with c.directory_fd(path.parent) as parent:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
             and before.st_size<=maximum and not before.st_mode&0o022, 'file_metadata')
        chunks=[];length=0
        while True:
            chunk=os.read(fd,min(65536,maximum+1-length))
            if not chunk:break
            chunks.append(chunk);length+=len(chunk)
            need(length<=maximum,'file_read_limit')
        after=os.fstat(fd)
        need(c._stamp(before)==c._stamp(after),'file_changed_during_read')
        return b''.join(chunks)
    finally:
        os.close(fd)

def inventory(root: Path, expected: set[str]) -> None:
    """Exact inventory; all directory ancestors validated without symlink following."""
    secure_path(root);actual=set();dirs=set()
    allowed_dirs={str(p) for n in expected for p in Path(n).parents if str(p)!='.'}
    for base,subdirs,names in os.walk(root,followlinks=False):
        for name in subdirs:
            path=Path(base)/name
            with c.directory_fd(path):pass
            dirs.add(path.relative_to(root).as_posix())
        for name in names:
            path=Path(base)/name
            actual.add(path.relative_to(root).as_posix())
    need(actual==expected and dirs==allowed_dirs,'exact_directory_inventory')

def verify_frozen(root: Path, seal_sha256: str, admission: c.Admission) -> dict:
    c.hex_value(seal_sha256);root=secure_path(root)
    raw=read_regular(root/'SEALED.json',MAX_MANIFEST)
    need(sha(raw)==seal_sha256,'frozen_seal_drift')
    seal=c.parse(raw)
    need(seal.get('schema')=='software-factory-frozen-test-seal/v1' and type(seal.get('files')) is dict,'frozen_seal_schema')
    need(len(seal['files'])<=c.MAX_FILES,'frozen_file_count')
    files={};modes={}
    for path,row in seal['files'].items():
        c.path_name(path);c.keys(row,('sha256','bytes','mode'),'frozen_file_schema')
        data=read_regular(root/'work'/path,c.MAX_FILE)
        need(type(row['bytes']) is int and len(data)==row['bytes'] and sha(data)==row['sha256'],'frozen_content_drift')
        mode=(root/'work'/path).lstat().st_mode & 0o777
        need(mode==(0o700 if row['mode']=='100755' else 0o600),'frozen_mode_drift')
        files[path]=data;modes[path]=row['mode']
    expected=source_state(admission,files,modes)
    input_raw=read_regular(root/'control/TEST_INPUT.json',MAX_MANIFEST)
    need(input_raw==c.canonical(expected) and sha(input_raw)==seal['input_sha256'],'frozen_input_binding')
    need(read_regular(root/'control/unittest_entry.py',len(INNER_ENTRY))==INNER_ENTRY and
         seal['entry_sha256']==sha(INNER_ENTRY),'frozen_entry_drift')
    need(seal=={'schema':'software-factory-frozen-test-seal/v1','input_sha256':sha(input_raw),
                'entry_sha256':sha(INNER_ENTRY),'files':expected['files']},'frozen_seal_extra_fields')
    inventory(root,{'SEALED.json','control/TEST_INPUT.json','control/unittest_entry.py'}|{'work/'+p for p in files})
    return expected

@dataclass(frozen=True)
class RuntimeCapsule:
    root: Path
    approval_sha256: str
    files: Mapping[str, tuple[str,int,str]]

def runtime_path(name: str) -> str:
    need(type(name) is str and 0<len(name)<=240 and not name.startswith('/') and
         all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]{0,99}',p) for p in name.split('/')),
         'runtime_path')
    need(len(name.split('/'))<=12 and not any(p.lower() in c.BLOCKED_PARTS|BLOCKED_RUNTIME for p in name.split('/'))
         and not name.lower().endswith(('.pth','.pyc','.pyo','.pem','.key','.json','.yaml','.yml')), 'runtime_forbidden_path')
    permitted=(name=='runtime/bin/python3.11' or name.startswith('runtime/lib/python3.11/') or
               re.fullmatch(r'runtime/lib/libpython3\.11\.so(?:\.[0-9]+)*',name) or
               name=='lib64/ld-linux-x86-64.so.2' or
               re.fullmatch(r'lib/x86_64-linux-gnu/(?:lib[A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_-]+)*|ld-linux-x86-64\.so\.2)',name))
    need(permitted,'runtime_mount_destination')
    return name

def admit_runtime(root: Path, approval_raw: bytes, expected_sha256: str) -> RuntimeCapsule:
    """No discovery, ldd, copying, packages or TOFU. SHA is controller input."""
    c.hex_value(expected_sha256)
    need(type(approval_raw) is bytes and len(approval_raw)<=c.MAX_JSON and sha(approval_raw)==expected_sha256,'runtime_approval_pin')
    d=c.parse(approval_raw);c.keys(d,('schema','candidate_manifest','platform','python','files'),'runtime_approval_schema')
    need(d['schema']==RUNTIME_SCHEMA and d['candidate_manifest']==c.CANDIDATE and d['platform']=='linux-x86_64'
         and d['python']==[3,11,16], 'runtime_identity')
    rows=d['files'];need(type(rows) is dict and REQUIRED_RUNTIME<=set(rows) and len(rows)<=MAX_RUNTIME_FILES,'runtime_inventory')
    root=secure_path(root);total=0;files={};c.no_aliases(list(rows))
    inventory(root,set(rows))
    for name,row in sorted(rows.items()):
        runtime_path(name);c.keys(row,('sha256','bytes','mode'),'runtime_file_row');c.hex_value(row['sha256'])
        need(type(row['bytes']) is int and 0<=row['bytes']<=MAX_RUNTIME_FILE and row['mode'] in ('100644','100755'),'runtime_file_size_mode')
        total+=row['bytes'];need(total<=MAX_RUNTIME_TOTAL,'runtime_total_size')
        raw=read_regular(root/name,MAX_RUNTIME_FILE)
        mode=(root/name).lstat().st_mode&0o777
        need(len(raw)==row['bytes'] and sha(raw)==row['sha256'] and mode==(0o700 if row['mode']=='100755' else 0o600),'runtime_file_drift')
        if name in ('runtime/bin/python3.11','lib64/ld-linux-x86-64.so.2'):
            need(row['mode']=='100755' and row['bytes']>0,'runtime_entry_not_executable')
        files[name]=(row['sha256'],row['bytes'],row['mode'])
    return RuntimeCapsule(root,expected_sha256,MappingProxyType(files))

def seccomp_program() -> bytes:
    """Classic BPF, little-endian Linux x86_64; never installed by this module."""
    instructions=[(0x20,0,0,4),(0x15,1,0,0xc000003e),(0x06,0,0,0x80000000),
                  (0x20,0,0,0),(0x35,0,1,0x40000000),(0x06,0,0,0x80000000)]
    for number in sorted(set(DENIED_SYSCALLS.values())):
        instructions.extend(((0x15,0,1,number),(0x06,0,0,0x00050000|errno.EPERM)))
    instructions.append((0x06,0,0,0x7fff0000))
    return b''.join(struct.pack('<HBBI',*i) for i in instructions)

def build_plan(admission: c.Admission, frozen_root: Path, seal_sha256: str,
               runtime_root: Path, runtime_approval: bytes, runtime_sha256: str,
               bwrap_sha256: str) -> dict:
    """Inert argv and required FD/service policy, not callable subprocess input.

    Numeric FD 3 is a requirement for a future launcher, not an inherited FD.
    No API can turn this plan into permission by changing JSON fields.
    """
    frozen_root=secure_path(frozen_root)
    frozen=verify_frozen(frozen_root,seal_sha256,admission)
    runtime=admit_runtime(runtime_root,runtime_approval,runtime_sha256)
    need(runtime.root!=frozen_root and runtime.root not in frozen_root.parents and frozen_root not in runtime.root.parents,'overlapping_capsules')
    c.hex_value(bwrap_sha256)
    argv=['/usr/bin/bwrap','--unshare-user','--unshare-pid','--unshare-net','--unshare-ipc',
          '--unshare-uts','--unshare-cgroup','--disable-userns','--assert-userns-disabled',
          '--cap-drop','ALL','--new-session','--die-with-parent','--clearenv',
          '--setenv','HOME','/tmp/home','--setenv','TMPDIR','/tmp','--setenv','PATH','/runtime/bin',
          '--setenv','LANG','C.UTF-8','--setenv','LC_ALL','C.UTF-8']
    for name in sorted(runtime.files):argv+=['--ro-bind',str(runtime.root/name),'/'+name]
    argv+=['--ro-bind',str(frozen_root/'work'),'/work',
           '--ro-bind',str(frozen_root/'control'),'/control','--proc','/proc','--remount-ro','/proc',
           '--dev','/dev','--size','67108864','--tmpfs','/tmp','--dir','/tmp/home',
           '--chdir','/work','--remount-ro','/','--seccomp','3','--',
           '/runtime/bin/python3.11','-I','-B','-S','/control/unittest_entry.py']
    properties={'Restart':'no','KillMode':'control-group','SendSIGKILL':'yes','NoNewPrivileges':'yes',
        'Delegate':'no','UMask':'0077','MemoryMax':'512M','MemorySwapMax':'0','TasksMax':'16',
        'CPUQuota':'100%','RuntimeMaxSec':str(frozen['timeout_seconds']+10)+'s','TimeoutStopSec':'3s'}
    return {'schema':'software-factory-isolation-plan/v1','policy':POLICY,
        'input_sha256':sha(c.canonical(frozen)),'seal_sha256':seal_sha256,
        'runtime_approval_sha256':runtime.approval_sha256,'bwrap_sha256':bwrap_sha256,
        'argv':argv,'host_environment':{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'},
        'close_fds':True,'stdin':'devnull','stdout':'bounded-pipe','stderr':'bounded-pipe',
        'max_combined_output_bytes':1024*1024,
        'fd3':{'kind':'sealed-readonly-seccomp-bpf','sha256':sha(seccomp_program()),'bytes':len(seccomp_program())},
        'service_required_properties':properties,'required_open_gates':list(OPEN_GATES),
        'shell_allowed':False,'network_allowed':False,'host_secrets_allowed':False,
        'host_writable_mounts':[],'process_creation_allowed':False,'threads_allowed':False,
        'execution_permitted':False,'os_isolation_verified':False,'runtime_boot_verified':False,
        'native_binding_verified':False,'production_launch_ready':False}

def launch(*args: object, **kwargs: object) -> None:
    raise IsolationPlanRefused('execution_not_implemented_or_admitted_in_plan_increment')
