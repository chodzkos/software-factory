"""Private immutable-Git projection for admitted DATA, version 2.0.0.

A linked worktree is built from ONLY the approved source slice. Its deterministic
projection HEAD is NOT the upstream commit. Both identities remain explicit.
There are no remotes, hooks, alternates, clone/fetch, index mutation after sealing,
model argv, profile changes, repository imports, automatic repair or retry.
The one trusted Git operation is read-tree (no checkout) to build a new index.
All later DATA operations use no Git process and verify exact metadata first.
Not a sandbox against hostile same-UID processes or a live execution authority.
"""
from __future__ import annotations
import hashlib
import errno
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
import zlib
import repository_task_contract as c
import repository_execution_io as e

VERSION = '2.0.0'
POLICY = 'immutable-git-projection-for-admitted-patch/v2'
PROJECTION = 'WORKTREE_PROJECTION_V2.json'
BOUND = 'WORKTREE_BOUND_V2.json'
CONSUMED = 'WORKTREE_CARD_CONSUMED_V2.json'
MAX_META_FILES, MAX_META_BYTES = 4096, 20 * 1024 * 1024
GIT_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'XDG_CONFIG_HOME': '/nonexistent',
    'LANG': 'C', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_ATTR_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
    'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_CONFIG_SYSTEM': '/dev/null'}
CONFIG = b'''[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tlogallrefupdates = false\n\thooksPath = /dev/null\n\tfsmonitor = false\n\tattributesFile = /dev/null\n\tuntrackedCache = false\n[gc]\n\tauto = 0\n[protocol]\n\tallow = never\n[diff]\n\texternal =\n'''

class WorktreeRefused(ValueError):
    """Bounded machine code without source contents or private claims."""

def need(ok, code):
    if not ok:
        raise WorktreeRefused(code)

def task_id(value):
    need(type(value) is str and re.fullmatch(r't_[0-9a-f]{8}', value), 'worktree_task_id')
    return value

def repository(ctx):
    return ctx.staged / 'git-project'

def control(ctx):
    return ctx.staged / 'control'

def stamp(st):
    return list(c._stamp(st))

def new_dir(path):
    """Only finalize the new descriptor. Never chmod an existing path."""
    with c.directory_fd(Path(path).parent) as parent:
        os.mkdir(Path(path).name, 0o700, dir_fd=parent)
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            need(os.fstat(fd).st_uid == os.geteuid(), 'new_directory_owner')
            os.fchmod(fd, 0o700); os.fsync(fd); os.fsync(parent)
        finally:
            os.close(fd)

def git_authority(ctx):
    d = ctx.spec.get('repository_worktree')
    c.keys(d, ('schema', 'policy', 'git_path', 'git_sha256', 'admission_sha256'), 'worktree_authority_fields')
    need(d['schema'] == 'sf-repository-worktree-authority/v2' and d['policy'] == POLICY and
         d['admission_sha256'] == c.sha(c.canonical(ctx.admission.receipt())), 'worktree_authority_binding')
    need(d['git_path'] == '/usr/bin/git', 'worktree_git_path'); c.hex_value(d['git_sha256'])
    return dict(d)

def verify_git(d):
    """Pinned existing root-owned executable. No PATH or caller-selected binary."""
    p = Path(d['git_path']); cur = Path('/')
    for part in p.parts[1:]:
        cur /= part
        st = cur.lstat()
        need(not stat.S_ISLNK(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'git_binary_ancestor')
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and
             before.st_mode & 0o111 and not before.st_mode & 0o7022 and
             0 < before.st_size <= 32 * 1024 * 1024, 'git_binary_metadata')
        for attribute in ('security.capability', 'system.posix_acl_access'):
            try: value = os.getxattr(fd, attribute)
            except OSError as exc: need(exc.errno in (errno.ENODATA, errno.ENOTSUP), 'git_binary_xattr_unknown')
            else: need(not value, 'git_binary_extra_privilege')
        digest = hashlib.sha256(); length = 0
        while True:
            raw = os.read(fd, 65536)
            if not raw: break
            length += len(raw); need(length <= 32 * 1024 * 1024, 'git_binary_size')
            digest.update(raw)
        need(stamp(before) == stamp(os.fstat(fd)) == stamp(p.lstat()) and
             length == before.st_size and digest.hexdigest() == d['git_sha256'], 'git_binary_pin')
        return {'sha256': d['git_sha256'], 'identity': stamp(before)}
    finally:
        os.close(fd)

def object_layout(admission):
    """Authenticated source data -> deterministic local projection; no Git import."""
    need(type(admission) is c.Admission, 'worktree_admission_type')
    objects, tree = {}, {}
    def put(kind, raw):
        oid = c.git_oid(kind, raw)
        value = zlib.compress(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw)
        old = objects.setdefault('objects/' + oid[:2] + '/' + oid[2:], value)
        need(old == value, 'projection_object_collision')
        return oid
    for name, raw in sorted(admission.files.items()):
        parts = c.path_name(name).split('/'); node = tree
        for part in parts[:-1]: node = node.setdefault(part, {})
        node[parts[-1]] = (admission.modes[name], put('blob', raw))
    def encode(node):
        rows = []
        for name, value in node.items():
            mode, oid = ('40000', encode(value)) if type(value) is dict else value
            rows.append((name.encode() + (b'/' if mode == '40000' else b''),
                         mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(oid)))
        return put('tree', b''.join(raw for _, raw in sorted(rows)))
    projection_tree = encode(tree)
    raw = (f'tree {projection_tree}\nauthor Software Factory <factory@example.invalid> 0 +0000\n'
           'committer Software Factory <factory@example.invalid> 0 +0000\n\n'
           f'Admitted source projection v2\nsource_commit={admission.commit}\nsource_tree={admission.tree}\n'
           f'admission_sha256={c.sha(c.canonical(admission.receipt()))}\n').encode()
    head = put('commit', raw)
    return head, projection_tree, dict(objects, **{'config': CONFIG,
        'HEAD': b'ref: refs/heads/sf-baseline\n', 'refs/heads/sf-baseline': (head + '\n').encode()})

def metadata(root):
    """Exact stable metadata inventory, no symlinks/hardlinks/special files."""
    files, directories = {}, {}; total = 0; entries = 0
    def visit(fd, prefix, depth):
        nonlocal total, entries
        need(depth <= 16, 'metadata_depth')
        initial = os.fstat(fd); names = sorted(os.listdir(fd)); entries += len(names)
        need(entries <= MAX_META_FILES * 2, 'metadata_entry_limit')
        directories[prefix.rstrip('/') or '.'] = stamp(initial)
        for name in names:
            need(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name) and name not in ('.', '..'), 'metadata_name')
            st = os.stat(name, dir_fd=fd, follow_symlinks=False); rel = prefix + name
            need(st.st_uid == os.geteuid(), 'metadata_owner')
            if stat.S_ISDIR(st.st_mode):
                need(stat.S_IMODE(st.st_mode) == 0o700, 'metadata_directory_mode')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    need(stamp(st) == stamp(os.fstat(child)), 'metadata_directory_replaced')
                    visit(child, rel + '/', depth + 1)
                finally: os.close(child)
            else:
                need(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and
                     stat.S_IMODE(st.st_mode) == 0o600 and st.st_size <= c.MAX_FILE + 65536,
                     'metadata_file_type_or_mode')
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
                try:
                    need(stamp(st) == stamp(os.fstat(child)), 'metadata_file_replaced')
                    data = b''
                    while len(data) <= st.st_size:
                        block = os.read(child, min(65536, st.st_size + 1 - len(data)))
                        if not block: break
                        data += block
                    need(stamp(st) == stamp(os.fstat(child)) and len(data) == st.st_size, 'metadata_changed_during_read')
                finally: os.close(child)
                total += len(data); need(total <= MAX_META_BYTES and len(files) < MAX_META_FILES, 'metadata_budget')
                files[rel] = {'sha256': c.sha(data), 'bytes': len(data), 'identity': stamp(st)}
        need(stamp(initial) == stamp(os.fstat(fd)) and names == sorted(os.listdir(fd)), 'metadata_directory_changed')
    with c.directory_fd(root) as fd: visit(fd, '', 0)
    return {'files': files, 'directories': directories}

def prepare_projection(ctx):
    """New pre-service staging only; caller has already consumed snapshot slot."""
    authority = git_authority(ctx); verify_git(authority)
    head, tree, layout = object_layout(ctx.admission)
    repo = repository(ctx); new_dir(repo); new_dir(repo / '.git'); new_dir(repo / '.worktrees')
    with c.directory_fd(repo / '.git') as fd:
        for name, raw in sorted(layout.items()): c._write_new(fd, name, raw)
    record = {'schema': 'sf-worktree-projection/v2', 'policy': POLICY, 'job_id': ctx.admission.job_id,
        'repository': str(repo), 'source_commit': ctx.admission.commit, 'source_tree': ctx.admission.tree,
        'projection_head': head, 'projection_tree': tree,
        'admission_sha256': c.sha(c.canonical(ctx.admission.receipt())),
        'authority_sha256': c.sha(c.canonical(authority)), 'initial_metadata': metadata(repo / '.git'),
        'automatic_retry': False, 'execution_permitted': False, 'projection_is_source_commit': False}
    e.new_json(control(ctx) / PROJECTION, record)
    return record

def projection(ctx):
    d = e.read_json(control(ctx) / PROJECTION)
    head, tree, layout = object_layout(ctx.admission)
    c.keys(d, ('schema','policy','job_id','repository','source_commit','source_tree','projection_head',
        'projection_tree','admission_sha256','authority_sha256','initial_metadata','automatic_retry',
        'execution_permitted','projection_is_source_commit'), 'projection_fields')
    need(d['schema'] == 'sf-worktree-projection/v2' and d['policy'] == POLICY and
         d['job_id'] == ctx.admission.job_id and d['repository'] == str(repository(ctx)) and
         d['source_commit'] == ctx.admission.commit and d['source_tree'] == ctx.admission.tree and
         d['projection_head'] == head and d['projection_tree'] == tree and
         d['admission_sha256'] == c.sha(c.canonical(ctx.admission.receipt())) and
         d['authority_sha256'] == c.sha(c.canonical(git_authority(ctx))) and
         d['automatic_retry'] is False and d['execution_permitted'] is False and
         d['projection_is_source_commit'] is False, 'projection_binding')
    # Generated, inert base files may never drift, including Git config/refs.
    for name, expected in layout.items():
        need(e.read(repository(ctx) / '.git' / name, c.MAX_FILE + 65536) == expected, 'projection_base_changed')
    return d

def _read_tree(ctx, work, head):
    """One exact trusted Git command. No hooks, checkout, shell, model argv or retry."""
    auth = git_authority(ctx); binary = verify_git(auth)
    need(work.parent == repository(ctx) / '.worktrees' and task_id(work.name), 'git_index_destination')
    c.hex_value(head, 40)
    argv = [auth['git_path'], '--no-pager', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never', '-c', 'core.attributesFile=/dev/null',
        '-C', str(work), 'read-tree', '--no-sparse-checkout', head]
    child = None; buffers = {'stdout': bytearray(), 'stderr': bytearray()}; start = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(GIT_ENV), cwd=str(work), close_fds=True, shell=False, umask=0o077)
            for name in buffers:
                pipe = getattr(child, name); os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                need(time.monotonic() - start < 20, 'git_index_deadline')
                for key, _ in selector.select(.05):
                    data = os.read(key.fd, 65536)
                    if not data: selector.unregister(key.fileobj); continue
                    buffers[key.data].extend(data)
                    need(sum(map(len, buffers.values())) <= 65536, 'git_index_output_limit')
            code = child.wait(timeout=max(.01, 20 - (time.monotonic() - start)))
            need(code == 0 and not buffers['stdout'], 'git_index_failed')
        need(verify_git(auth) == binary, 'git_binary_changed_during_index')
        result = {'schema': 'sf-worktree-index-process/v2', 'exit': code, 'reaped': True,
            'argv_sha256': c.sha(c.canonical(argv)), 'git_sha256': auth['git_sha256'],
            'stderr_sha256': c.sha(bytes(buffers['stderr'])), 'repository_code_executed': False}
        e.new_json(control(ctx) / 'WORKTREE_INDEX_RESULT_V2.json', result)
        return result
    finally:
        if child is not None:
            if child.poll() is None: child.kill()
            child.wait()
            if child.stdout: child.stdout.close()
            if child.stderr: child.stderr.close()

def create_linked(ctx, tid):
    tid = task_id(tid); p = projection(ctx); repo = repository(ctx)
    need(metadata(repo / '.git') == p['initial_metadata'], 'projection_changed_before_card')
    with c.directory_fd(repo / '.worktrees') as fd:
        need(not os.listdir(fd), 'worktree_already_present')
    branch = 'wt/' + tid; work = repo / '.worktrees' / tid; gitdir = repo / '.git' / 'worktrees' / tid
    new_dir(work)
    with c.directory_fd(repo / '.git') as fd:
        c._write_new(fd, 'refs/heads/' + branch, (p['projection_head'] + '\n').encode())
        c._write_new(fd, 'worktrees/' + tid + '/HEAD', ('ref: refs/heads/' + branch + '\n').encode())
        c._write_new(fd, 'worktrees/' + tid + '/commondir', b'../..\n')
        c._write_new(fd, 'worktrees/' + tid + '/gitdir', (str(work / '.git') + '\n').encode())
    with c.directory_fd(work) as fd:
        c._write_new(fd, '.git', ('gitdir: ' + str(gitdir) + '\n').encode())
        for name, raw in sorted(ctx.admission.files.items()):
            c._write_new(fd, name, raw, 0o700 if ctx.admission.modes[name] == '100755' else 0o600)
    _read_tree(ctx, work, p['projection_head'])
    # Git's index must be durable before its metadata seal and native commit.
    with c.directory_fd(gitdir) as parent:
        fd = os.open('index', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try: os.fsync(fd)
        finally: os.close(fd)
        os.fsync(parent)
    raw = e.read(work / '.git'); pointer = {'sha256': c.sha(raw), 'identity': stamp((work / '.git').lstat())}
    record = {'schema':'sf-native-worktree-binding/v2', 'policy':POLICY, 'job_id':ctx.admission.job_id,
        'task_id':tid, 'repository':str(repo), 'workspace':str(work), 'branch_name':branch,
        'projection_sha256':c.sha(c.canonical(p)), 'projection_head':p['projection_head'],
        'admission_sha256':c.sha(c.canonical(ctx.admission.receipt())),
        'metadata':metadata(repo / '.git'), 'pointer':pointer,
        'root_identity': [work.stat().st_dev, work.stat().st_ino],
        'automatic_retry':False, 'git_metadata_mutations_allowed':False}
    e.new_json(control(ctx) / BOUND, record)
    return record

def bound(ctx):
    d = e.read_json(control(ctx) / BOUND); tid = task_id(d.get('task_id')); repo = repository(ctx)
    c.keys(d, ('schema','policy','job_id','task_id','repository','workspace','branch_name','projection_sha256',
        'projection_head','admission_sha256','metadata','pointer','root_identity','automatic_retry',
        'git_metadata_mutations_allowed'), 'worktree_bound_fields')
    need(d['schema'] == 'sf-native-worktree-binding/v2' and d['policy'] == POLICY and
         d['job_id'] == ctx.admission.job_id and d['repository'] == str(repo) and
         d['workspace'] == str(repo / '.worktrees' / tid) and d['branch_name'] == 'wt/' + tid and
         d['admission_sha256'] == c.sha(c.canonical(ctx.admission.receipt())) and
         d['automatic_retry'] is False and d['git_metadata_mutations_allowed'] is False, 'worktree_bound_identity')
    p = e.read_json(control(ctx) / PROJECTION)
    need(d['projection_sha256'] == c.sha(c.canonical(p)) and d['projection_head'] == p['projection_head'], 'worktree_projection_pin')
    return d

def verify_bound(ctx):
    d = bound(ctx); p = projection(ctx); repo = repository(ctx); work = Path(d['workspace']); tid = d['task_id']
    with c.directory_fd(repo) as fd: need(set(os.listdir(fd)) == {'.git', '.worktrees'}, 'projection_root_inventory')
    with c.directory_fd(repo / '.worktrees') as fd: need(os.listdir(fd) == [tid], 'linked_worktree_inventory')
    with c.directory_fd(work) as fd:
        st = os.fstat(fd); need([st.st_dev, st.st_ino] == d['root_identity'], 'worktree_root_replaced')
    need(metadata(repo / '.git') == d['metadata'], 'git_metadata_changed')
    pointer = e.read(work / '.git')
    need(pointer == ('gitdir: ' + str(repo / '.git/worktrees' / tid) + '\n').encode() and
         c.sha(pointer) == d['pointer']['sha256'] and stamp((work / '.git').lstat()) == d['pointer']['identity'], 'git_pointer_changed')
    # Literal immutable metadata, not just a self-described user config.
    for name, raw in {'HEAD': ('ref: refs/heads/wt/' + tid + '\n').encode(),
         'commondir': b'../..\n', 'gitdir': (str(work / '.git') + '\n').encode()}.items():
        need(e.read(repo / '.git/worktrees' / tid / name) == raw, 'linked_git_metadata_changed')
    need(e.read(repo / '.git/refs/heads/wt' / tid) == (p['projection_head'] + '\n').encode(), 'linked_branch_changed')
    return d

def descriptor(ctx):
    d = verify_bound(ctx)
    return {'schema':'sf-immutable-worktree-pin/v2', 'policy':POLICY,
        'binding_sha256':c.sha(c.canonical(d)), 'task_id':d['task_id'], 'workspace':d['workspace'],
        'projection_head':d['projection_head'], 'metadata_sha256':c.sha(c.canonical(d['metadata'])),
        'source_commit':ctx.admission.commit, 'source_tree':ctx.admission.tree,
        'git_metadata_excluded_from_frozen_input':True}


from repository_task_contract import MAX_FILES, MAX_FILE, MAX_TOTAL, path_name, source_text, no_aliases, directory_fd, _stamp, RepositoryTaskRefused
def _read_data_with_bound_git_pointer(work: Path) -> tuple[dict[str,bytes],dict[str,str]]:
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
            if prefix == '' and name == '.git':
                continue  # Exactly the root pointer, verified before AND after capture.
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

def read_sources(ctx):
    before = descriptor(ctx)
    current, modes = _read_data_with_bound_git_pointer(Path(before['workspace']))
    if current != dict(ctx.admission.files) or modes != dict(ctx.admission.modes):
        c.patch(ctx.admission, current, modes)
    need(descriptor(ctx) == before, 'git_changed_during_data_capture')
    return current, modes
