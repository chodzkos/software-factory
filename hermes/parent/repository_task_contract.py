"""Repository-task source admission and patch preparation, version 1 (stdlib).

DATA-ONLY INCREMENT. Does not run/import repository code, start native workers,
invoke shell/Git, admit models or claim an OS sandbox. Execution is unconditionally
blocked. A trusted caller pins the approval, envelope AND snapshot digests; none
of those pins may be taken from task text or an agent's response.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Mapping
import zipfile

VERSION = '1.0.0'
SCHEMA = 'software-factory-repository-task/v1'
APPROVAL_SCHEMA = 'software-factory-repository-approval/v1'
SNAPSHOT_SCHEMA = 'software-factory-repository-snapshot/v1'
POLICY = 'offline-no-secrets-no-execution/v1'
# Repository-resident form (the only change from the reviewed bytes): a file inside
# the candidate cannot contain the digest of a manifest that covers itself. The
# accepted candidate manifest digest is bound once per process by the trusted
# factory bootstrap; until then every read of CANDIDATE refuses (fail closed).
_CANDIDATE = None
MAX_JSON = 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 25000
MAX_FILES = 512
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_ARCHIVE = 10 * 1024 * 1024
MAX_PATCH = 4 * 1024 * 1024
MAX_OBJECTS = 512
MAX_PATH = 180
# Deliberately a portable ASCII subset, not a promise to support every Git path.
COMPONENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\Z')
ID = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SHA1 = re.compile(r'[0-9a-f]{40}\Z')
BLOCKED_PARTS = frozenset({'.git','.hg','.svn','.factory-local','.hermes','.ssh','.aws','.config',
    'node_modules','__pycache__','venv','.venv','.env','credentials','credentials.json','auth.json',
    'token.json','tokens.json','secrets','secrets.json','secrets.yaml','id_rsa','id_ed25519'})

class RepositoryTaskRefused(ValueError):
    """A bounded machine-readable refusal; no repository content in the error."""

def need(ok: object, code: str) -> None:
    if not ok:
        raise RepositoryTaskRefused(code)

def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()

def _pairs(items: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in items:
        need(key not in out, 'duplicate_json_key')
        out[key] = value
    return out

def _constant(value: str) -> None:
    raise RepositoryTaskRefused('nonfinite_json')

def _json_int(value: str) -> int:
    need(len(value.lstrip('-')) <= 20, 'json_integer_size')
    return int(value)

def parse(raw: bytes) -> dict:
    need(type(raw) is bytes and len(raw) <= MAX_JSON, 'json_size_or_type')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant, parse_int=_json_int)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RepositoryTaskRefused('invalid_json') from exc
    need(type(value) is dict, 'json_root')
    pending = [(value, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        need(depth <= MAX_JSON_DEPTH and count <= MAX_JSON_NODES, 'json_complexity')
        need(type(node) is not float or math.isfinite(node), 'nonfinite_json')
        children = node.values() if type(node) is dict else node if type(node) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value

def keys(value: object, expected: tuple[str, ...], code: str) -> None:
    need(type(value) is dict and set(value) == set(expected), code)

def identifier(value: object) -> str:
    need(type(value) is str and len(value) <= 64 and ID.fullmatch(value), 'identifier')
    return value

def hex_value(value: object, size: int = 64) -> str:
    need(type(value) is str and (SHA256 if size == 64 else SHA1).fullmatch(value), 'digest_format')
    return value

def text(value: object, maximum: int, multiline: bool = True) -> str:
    need(type(value) is str and 0 < len(value.strip()) and len(value) <= maximum and
         all(ord(c) >= 32 or (multiline and c in '\n\t') for c in value) and
         not any(0x7f <= ord(c) <= 0x9f or 0xd800 <= ord(c) <= 0xdfff or
                 0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069 for c in value), 'text')
    return value

def path_name(value: object) -> str:
    need(type(value) is str and 0 < len(value) <= MAX_PATH, 'path_type_or_size')
    parts = value.split('/')
    need(len(parts) <= 12 and all(COMPONENT.fullmatch(p) for p in parts), 'noncanonical_path')
    for part in parts:
        name = part.lower()
        need(name not in BLOCKED_PARTS and not name.startswith('.env') and
             not name.endswith(('.pem','.key','.p12','.pfx')) and not name.endswith('.'), 'protected_path')
        need(name.split('.')[0] not in {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}, 'reserved_path')
    return value

def paths(value: object, *, empty: bool = False) -> tuple[str, ...]:
    need(type(value) is list and (empty or value) and len(value) <= MAX_FILES, 'path_list')
    result = tuple(path_name(v) for v in value)
    need(list(result) == sorted(set(result)), 'path_order_or_duplicate')
    no_aliases(result)
    return result

def no_aliases(names: tuple[str, ...] | list[str]) -> None:
    lower = [n.lower() for n in names]
    need(len(lower) == len(set(lower)), 'case_alias')
    used = set(lower)
    for name in lower:
        parts = name.split('/')
        need(all('/'.join(parts[:i]) not in used for i in range(1,len(parts))), 'file_directory_conflict')
    # Case-sensitive files sharing case-ambiguous directory names are rejected too.
    spelling = {}
    for name in names:
        parts = name.split('/')
        for i in range(1,len(parts)+1):
            prefix='/'.join(parts[:i]); old=spelling.setdefault(prefix.lower(),prefix)
            need(old == prefix, 'directory_case_alias')

def source_text(raw: bytes) -> None:
    need(type(raw) is bytes and len(raw) <= MAX_FILE and b'\0' not in raw and b'\r' not in raw, 'source_size_or_binary_or_crlf')
    try:
        raw.decode('utf-8')
    except UnicodeError as exc:
        raise RepositoryTaskRefused('source_not_utf8') from exc

def git_oid(kind: str, raw: bytes) -> str:
    return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()

def tree_entries(raw: bytes) -> dict[bytes, tuple[str, str]]:
    """Parse authenticated Git SHA-1 trees; inspect names as bytes, never paths."""
    need(len(raw) <= MAX_FILE, 'tree_size')
    result = {}; order = []; offset = 0
    while offset < len(raw):
        end = raw.find(b'\0',offset)
        need(end >= 0 and end+21 <= len(raw), 'tree_truncated')
        header=raw[offset:end]; fields=header.split(b' ',1)
        need(len(fields)==2, 'tree_header')
        mode,name=fields
        need(mode in (b'100644',b'100755',b'40000',b'120000',b'160000') and
             0<len(name)<=255 and b'/' not in name and name not in (b'.',b'..') and name not in result, 'tree_entry')
        result[name]=(mode.decode(),raw[end+1:end+21].hex())
        order.append(name+(b'/' if mode==b'40000' else b''));offset=end+21
    need(order==sorted(order) and len(result)<=20000, 'tree_order_or_size')
    return result

def commit_tree(raw: bytes, commit: str, tree: str) -> None:
    need(len(raw)<=MAX_FILE and git_oid('commit',raw)==commit and b'\0' not in raw, 'commit_identity')
    headers,separator,_=raw.partition(b'\n\n')
    need(bool(separator), 'commit_headers')
    tree_rows=[line for line in headers.split(b'\n') if line.startswith(b'tree ')]
    need(tree_rows==[b'tree '+tree.encode()] and headers.split(b'\n',1)[0]==tree_rows[0], 'commit_tree_binding')

@dataclass(frozen=True)
class Admission:
    job_id: str
    repository_id: str
    commit: str
    tree: str
    approval_sha256: str
    envelope_sha256: str
    snapshot_sha256: str
    manifest_sha256: str
    read: tuple[str, ...]
    modify: tuple[str, ...]
    add: tuple[str, ...]
    delete: tuple[str, ...]
    immutable_tests: tuple[str, ...]
    tests: bytes
    task: bytes
    files: Mapping[str, bytes]
    modes: Mapping[str, str]

    @property
    def execution_permitted(self) -> bool:
        return False

    def receipt(self) -> dict:
        return {'schema':'software-factory-repository-admission/v1','job_id':self.job_id,
            'repository_id':self.repository_id,'commit':self.commit,'tree':self.tree,
            'approval_sha256':self.approval_sha256,'envelope_sha256':self.envelope_sha256,
            'snapshot_sha256':self.snapshot_sha256,'manifest_sha256':self.manifest_sha256,
            'read':list(self.read),'modify':list(self.modify),'add':list(self.add),'delete':list(self.delete),
            'immutable_tests':list(self.immutable_tests),'tests':parse(self.tests),'policy':POLICY,
            'execution_permitted':False,'native_task_created':False,'merge_authorized':False,'deploy_authorized':False}

def admit(approval_raw: bytes, envelope_raw: bytes, snapshot_raw: bytes, *,
          expected_approval_sha256: str, expected_envelope_sha256: str,
          expected_snapshot_sha256: str) -> Admission:
    """Pins are owner/controller input, never envelope-supplied authorization.

    The three raw inputs are inert bytes. Snapshot membership is authenticated
    to an exact Git commit/tree, with SHA-256 pins in addition to Git SHA-1 IDs.
    No current refs are fetched or inferred; omitted files are not readable.
    """
    for raw,expected in ((approval_raw,expected_approval_sha256),(envelope_raw,expected_envelope_sha256),(snapshot_raw,expected_snapshot_sha256)):
        hex_value(expected)
        need(type(raw) is bytes and len(raw)<=MAX_ARCHIVE and sha(raw)==expected,'input_digest_mismatch')
    approval=parse(approval_raw);envelope=parse(envelope_raw)
    keys(approval,('schema','repository_id','commit','tree','snapshot_sha256','manifest_sha256','access','tests','policy'),'approval_keys')
    need(approval['schema']==APPROVAL_SCHEMA and approval['policy']==POLICY,'approval_schema_or_policy')
    repo=identifier(approval['repository_id']);commit=hex_value(approval['commit'],40);tree=hex_value(approval['tree'],40)
    need(approval['snapshot_sha256']==expected_snapshot_sha256,'approval_snapshot_binding')
    hex_value(approval['manifest_sha256'])
    access=approval['access'];keys(access,('read','modify','add','delete'),'access_keys')
    read=paths(access['read']);modify=paths(access['modify'],empty=True);add=paths(access['add'],empty=True);delete=paths(access['delete'],empty=True)
    need(set(modify)<=set(read) and set(delete)<=set(read) and not(set(modify)&set(delete)) and
         not(set(add)&set(read)) and bool(modify or add or delete),'access_relationship')
    no_aliases(list(read)+list(add))
    tests=approval['tests'];keys(tests,('recipe','paths','expected_count','timeout_seconds'),'tests_keys')
    need(tests['recipe']=='python-unittest-v1','unapproved_test_recipe')
    immutable=paths(tests['paths'])
    need(set(immutable)<=set(read) and not(set(immutable)&(set(modify)|set(delete))) and
         all(p.endswith('.py') and p.split('/')[-1].startswith('test_') for p in immutable),'immutable_tests_scope')
    need(type(tests['expected_count']) is int and 1<=tests['expected_count']<=10000 and
         type(tests['timeout_seconds']) is int and 1<=tests['timeout_seconds']<=600,'test_limits')
    keys(envelope,('schema','job_id','candidate_manifest','approval_sha256','repository_id','task'),'envelope_keys')
    need(envelope['schema']==SCHEMA and envelope['candidate_manifest']==bound_candidate() and
         envelope['approval_sha256']==expected_approval_sha256 and envelope['repository_id']==repo,'envelope_binding')
    job=identifier(envelope['job_id']);task=envelope['task']
    keys(task,('title','requirements'),'task_keys');text(task['title'],160,False);text(task['requirements'],12000)
    files,modes,manifest_sha=read_snapshot(snapshot_raw,approval,read,add)
    return Admission(job,repo,commit,tree,expected_approval_sha256,expected_envelope_sha256,expected_snapshot_sha256,
                     manifest_sha,read,modify,add,delete,immutable,canonical(tests),canonical(task),
                     MappingProxyType(files),MappingProxyType(modes))

def read_snapshot(raw: bytes, approval: dict, read: tuple[str,...], add: tuple[str,...]) -> tuple[dict,dict,str]:
    need(len(raw)<=MAX_ARCHIVE,'snapshot_size')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info=z.infolist();names=[i.filename for i in info]
            need(len(names)==len(set(names)) and 3<=len(names)<=MAX_FILES+MAX_OBJECTS+2,'snapshot_inventory_count')
            need(sum(i.file_size for i in info)<=MAX_TOTAL,'snapshot_expansion_limit')
            for item in info:
                need(not item.is_dir() and not(item.flag_bits&1) and item.file_size<=MAX_FILE and
                     stat.S_IFMT(item.external_attr>>16) in (0,stat.S_IFREG),'snapshot_member_type_or_size')
            need('manifest.json' in names and 'git/commit' in names,'snapshot_control_files')
            manifest_raw=z.read('manifest.json');manifest=parse(manifest_raw)
            need(sha(manifest_raw)==approval['manifest_sha256'],'manifest_digest')
            keys(manifest,('schema','repository_id','commit','tree','files','trees'),'manifest_keys')
            need(manifest['schema']==SNAPSHOT_SCHEMA and all(manifest[k]==approval[k] for k in ('repository_id','commit','tree')),'manifest_binding')
            rows=manifest['files'];tree_rows=manifest['trees']
            need(type(rows) is list and len(rows)==len(read) and type(tree_rows) is dict and 1<=len(tree_rows)<=MAX_OBJECTS,'manifest_rows')
            need([row.get('path') for row in rows if type(row) is dict]==list(read),'manifest_read_inventory')
            expected={'manifest.json','git/commit'}|{'files/'+p for p in read}|{'git/trees/'+hex_value(k,40) for k in tree_rows}
            need(set(names)==expected,'snapshot_exact_inventory')
            commit_tree(z.read('git/commit'),manifest['commit'],manifest['tree'])
            trees={}
            for oid,meta in tree_rows.items():
                keys(meta,('sha256','bytes'),'tree_metadata');hex_value(meta['sha256'])
                data=z.read('git/trees/'+oid)
                need(type(meta['bytes']) is int and len(data)==meta['bytes'] and sha(data)==meta['sha256'] and git_oid('tree',data)==oid,'tree_object_identity')
                trees[oid]=tree_entries(data)
            needed=set()
            def lookup(path: str, absent: bool=False):
                oid=manifest['tree'];parts=path.split('/')
                for index,part in enumerate(parts):
                    need(oid in trees,'missing_ancestry_tree');needed.add(oid)
                    need(all(name.lower()!=part.lower().encode() or name==part.encode() for name in trees[oid]),'git_case_alias')
                    entry=trees[oid].get(part.encode())
                    if entry is None:
                        need(absent,'file_absent_in_git_tree');return None
                    mode,target=entry
                    if index==len(parts)-1:
                        need(not absent,'addition_already_exists_in_git_tree');return mode,target
                    need(mode=='40000','ancestor_not_tree');oid=target
                raise RepositoryTaskRefused('empty_path')
            files={};modes={}
            for row in rows:
                keys(row,('path','mode','blob','sha256','bytes'),'file_metadata')
                p=path_name(row['path']);hex_value(row['blob'],40);hex_value(row['sha256'])
                need(row['mode'] in ('100644','100755') and type(row['bytes']) is int and 0<=row['bytes']<=MAX_FILE,'file_mode_or_size')
                need(lookup(p)==(row['mode'],row['blob']),'git_file_binding')
                data=z.read('files/'+p);source_text(data)
                need(len(data)==row['bytes'] and sha(data)==row['sha256'] and git_oid('blob',data)==row['blob'],'blob_identity')
                files[p]=data;modes[p]=row['mode']
            for p in add:lookup(p,absent=True)
            need(needed==set(trees),'unneeded_tree_objects')
            return files,modes,sha(manifest_raw)
    except (zipfile.BadZipFile,KeyError,RuntimeError,NotImplementedError,EOFError) as exc:
        raise RepositoryTaskRefused('invalid_snapshot') from exc

def test_plan(admission: Admission) -> dict:
    """A declaration for a future reviewed adapter, NOT an executable argv."""
    return {'schema':'software-factory-test-plan/v1','admission_sha256':sha(canonical(admission.receipt())),
            'tests':parse(admission.tests),'test_digests':{p:sha(admission.files[p]) for p in admission.immutable_tests},
            'shell_allowed':False,'network_allowed':False,'secrets_allowed':False,'execution_permitted':False,
            'required_future_gates':['reviewed-native-repository-adapter','verified-os-isolation','explicit-job-admission']}

def execution_request(*args: object, **kwargs: object) -> None:
    """No supplied flag, approval JSON, receipt or task text can open this gate."""
    raise RepositoryTaskRefused('repository_execution_not_implemented')

@contextmanager
def directory_fd(path: Path, *, private: bool=True):
    """Walk absolute path components with no-follow dirfds, not check-then-open."""
    p=Path(path)
    need(p.is_absolute() and str(p)==str(path) and '..' not in p.parts and not str(p).startswith('//'),'absolute_directory')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for part in p.parts[1:]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=new
        st=os.fstat(fd)
        need(st.st_uid==os.geteuid() and (not private or stat.S_IMODE(st.st_mode)==0o700),'private_directory_owner_or_mode')
        yield fd
    except OSError as exc:
        raise RepositoryTaskRefused('directory_unavailable_or_link') from exc
    finally:
        os.close(fd)

def _mkdirs(fd: int, names: list[str]) -> int:
    current=os.dup(fd)
    try:
        for name in names:
            try:os.mkdir(name,0o700,dir_fd=current)
            except FileExistsError:pass
            new=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=current)
            os.close(current);current=new
            st=os.fstat(current)
            need(st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700,'staged_directory_mode')
        return current
    except BaseException:
        os.close(current);raise

def _write_new(root: int, name: str, raw: bytes, mode: int=0o600) -> None:
    parts=name.split('/');parent=_mkdirs(root,parts[:-1]);fd=None
    try:
        fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode,dir_fd=parent)
        os.fchmod(fd,mode)
        with os.fdopen(os.dup(fd),'wb') as file:
            file.write(raw);file.flush();os.fsync(file.fileno())
        os.fsync(parent)
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)

def stage(admission: Admission, private_parent: Path) -> Path:
    """Create a new source-only snapshot. Existing directories are never reused.

    Caller owns the configured private_parent; no host repository is accessed.
    On interruption the partial directory remains consumed, never reset/retried.
    This is not a Git worktree and is not registered with the native kanban.
    """
    with directory_fd(private_parent) as parent:
        name=identifier(admission.job_id)
        try:os.mkdir(name,0o700,dir_fd=parent)
        except FileExistsError as exc:raise RepositoryTaskRefused('stage_already_consumed') from exc
        root=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            _write_new(root,'control/admission.json',canonical(admission.receipt()))
            _write_new(root,'control/task.json',admission.task)
            for path,data in admission.files.items():
                _write_new(root,'work/'+path,data,0o700 if admission.modes[path]=='100755' else 0o600)
            _write_new(root,'control/STAGED.json',canonical({'complete':True,'execution_permitted':False,
                       'admission_sha256':sha(canonical(admission.receipt()))}))
            os.fsync(root);os.fsync(parent)
        except OSError as exc:
            raise RepositoryTaskRefused('stage_write_failed_preserve_partial') from exc
        finally:
            os.close(root)
    return Path(private_parent)/name

def _stamp(st: os.stat_result) -> tuple:
    return (st.st_dev,st.st_ino,st.st_mode,st.st_nlink,st.st_uid,st.st_size,st.st_mtime_ns,st.st_ctime_ns)

def read_worktree(work: Path) -> tuple[dict[str,bytes],dict[str,str]]:
    """Bounded stable capture of a private source tree; not process quiescence.

    The future controller must separately prove all actors stopped before use.
    Same-UID malicious actors are outside the guarantees of this data-only API.
    """
    files={};modes={};total=0;entry_count=0
    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal total,entry_count
        need(depth<=12,'workspace_depth')
        before=os.fstat(fd);names=sorted(os.listdir(fd))
        entry_count+=len(names);need(entry_count<=MAX_FILES*13,'workspace_entry_limit')
        for name in names:
            relative=prefix+name;path_name(relative)
            st=os.stat(name,dir_fd=fd,follow_symlinks=False)
            need(st.st_uid==os.geteuid(),'workspace_owner')
            if stat.S_ISDIR(st.st_mode):
                need(stat.S_IMODE(st.st_mode)==0o700,'workspace_directory_mode')
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                try:
                    need(_stamp(os.fstat(child))==_stamp(st),'workspace_directory_replaced')
                    walk(child,relative+'/',depth+1)
                finally:os.close(child)
            else:
                need(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and stat.S_IMODE(st.st_mode) in (0o600,0o700) and st.st_size<=MAX_FILE,'workspace_file_type_or_mode')
                child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=fd)
                try:
                    need(_stamp(os.fstat(child))==_stamp(st),'workspace_file_replaced')
                    with os.fdopen(os.dup(child),'rb') as f:data=f.read(MAX_FILE+1)
                    need(_stamp(os.fstat(child))==_stamp(st) and len(data)==st.st_size,'workspace_file_changed')
                finally:os.close(child)
                source_text(data);files[relative]=data;modes[relative]='100755' if stat.S_IMODE(st.st_mode)==0o700 else '100644'
                total+=len(data);need(total<=MAX_TOTAL and len(files)<=MAX_FILES,'workspace_total_limit')
        need(_stamp(before)==_stamp(os.fstat(fd)) and names==sorted(os.listdir(fd)),'workspace_directory_changed')
    try:
        with directory_fd(work) as root:walk(root,'',0)
    except OSError as exc:
        raise RepositoryTaskRefused('workspace_unavailable_or_changed') from exc
    no_aliases(list(files))
    return files,modes

def patch(admission: Admission, current: Mapping[str,bytes], modes: Mapping[str,str]) -> tuple[bytes,dict]:
    """Produce a text Git patch. Never apply, commit, publish or execute it."""
    need(type(current) is dict and type(modes) is dict and set(current)==set(modes),'patch_inventory')
    need(len(current)<=MAX_FILES and sum(len(v) for v in current.values() if type(v) is bytes)<=MAX_TOTAL,'patch_source_limit')
    for name,data in current.items():path_name(name);source_text(data)
    no_aliases(list(current))
    baseline=set(admission.files);actual=set(current);added=actual-baseline;deleted=baseline-actual
    need(added<=set(admission.add) and deleted<=set(admission.delete),'patch_out_of_scope_inventory')
    modified={p for p in actual&baseline if current[p]!=admission.files[p]}
    need(modified<=set(admission.modify),'patch_out_of_scope_content')
    for name in actual:
        need(modes[name]==admission.modes.get(name,'100644'),'patch_mode_change')
    changed=sorted(added|deleted|modified);need(bool(changed),'empty_patch')
    chunks=[];size=0;metadata=[]
    for name in changed:
        old=admission.files.get(name);new=current.get(name)
        oldoid=git_oid('blob',old) if old is not None else '0'*40
        newoid=git_oid('blob',new) if new is not None else '0'*40
        mode=admission.modes.get(name,'100644')
        lines=[f'diff --git a/{name} b/{name}\n']
        if old is None:lines.append('new file mode '+mode+'\n')
        if new is None:lines.append('deleted file mode '+mode+'\n')
        lines.append(f'index {oldoid}..{newoid}'+(f' {mode}' if old is not None and new is not None else '')+'\n')
        # Linear full-file hunks avoid adversarial quadratic diff computation.
        # Git lines are LF-separated bytes, not Python Unicode splitlines().
        def lf_lines(data):
            pieces=(data or b'').split(b'\n')
            return [p+b'\n' for p in pieces[:-1]]+([pieces[-1]] if pieces[-1] else [])
        oldlines=lf_lines(old);newlines=lf_lines(new)
        raw=''.join(lines).encode()
        if oldlines or newlines:
            raw+=('--- '+('/dev/null' if old is None else 'a/'+name)+'\n'+
                  '+++ '+('/dev/null' if new is None else 'b/'+name)+'\n'+
                  f'@@ -{1 if oldlines else 0},{len(oldlines)} +{1 if newlines else 0},{len(newlines)} @@\n').encode()
            body=[]
            for prefix,content in ((b'-',oldlines),(b'+',newlines)):
                for line in content:
                    body.append(prefix+line+(b'' if line.endswith(b'\n') else b'\n\\ No newline at end of file\n'))
            raw+=b''.join(body)
        chunks.append(raw);size+=len(raw);need(size<=MAX_PATCH,'patch_size_limit')
        metadata.append({'path':name,'change':'add' if old is None else 'delete' if new is None else 'modify',
                         'before_sha256':None if old is None else sha(old),'after_sha256':None if new is None else sha(new)})
    result=b''.join(chunks)
    return result,{'schema':'software-factory-repository-patch/v1','job_id':admission.job_id,'commit':admission.commit,
        'tree':admission.tree,'admission_sha256':sha(canonical(admission.receipt())),'patch_sha256':sha(result),
        'changes':metadata,'tests_executed':False,'model_approved':False,'native_done':False,
        'execution_permitted':False,'applied':False,'merge_authorized':False,'deploy_authorized':False}


def bind_candidate(manifest_sha256: object) -> str:
    """Bind the accepted candidate manifest digest once per process.

    Only the trusted factory bootstrap calls this, with the digest of the accepted
    manifest it verified. Rebinding to the same digest is a no-op; a different
    digest is refused, so a running process can never change its candidate.
    """
    global _CANDIDATE
    value = hex_value(manifest_sha256)
    need(_CANDIDATE is None or _CANDIDATE == value, 'candidate_already_bound')
    _CANDIDATE = value
    return value


def bound_candidate() -> str:
    need(_CANDIDATE is not None, 'candidate_unbound')
    return _CANDIDATE


def __getattr__(name: str) -> str:
    # PEP 562: `c.CANDIDATE` in the reviewed callers resolves here at use time.
    if name == 'CANDIDATE':
        return bound_candidate()
    raise AttributeError(name)

