"""WIP: owner-run bootstrap of LaunchPins provenance. Measure only; never repair, install or write profiles.

The bootstrap MEASURES a dedicated runtime native (candidate overlay applied and verified elsewhere) and the two
protected profiles with original code (no-follow reads, the native toolset resolver, the original reviewer
capability verifier), then writes ONE immutable record. The parent obtains LaunchPins only through load_pins(),
which checks the record by SHA-256 and re-measures every field. Any drift is a refusal; a new bootstrap is a new
record. This is not a workload authorization and not a cryptographic signature (0600 owner file + receipt SHA).
"""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path
import repository_task_contract as c
import repository_execution_io as e
from repository_parent_native_launch_v1 import LaunchPins, regular_hash

SCHEMA='sf-parent-launch-pins/v1'
PROFILES=('coder-claude','reviewer-gpt')
REVIEWER_TOOLS=('factory_repo_map','factory_repo_read','factory_repo_search','factory_review_approve','kanban_request_changes','kanban_show')
FLAGS=dict(live_execution_authorized=False,installed=False,model_executed=False,workload_authorized=False)
LIMIT=1024*1024

def _hex(v):return type(v) is str and len(v)==64 and all(x in '0123456789abcdef' for x in v)

def file_sha(path,*,limit=32*1024*1024):
    """sha256 of a stable, canonical, owner/root-owned regular file that is not group/other writable."""
    path=Path(path);e.need(path.is_absolute() and path==path.resolve(strict=True),'bootstrap_noncanonical_path')
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        first=os.fstat(fd)
        e.need(stat.S_ISREG(first.st_mode) and first.st_uid in (0,os.geteuid()) and not first.st_mode&0o7022 and
               first.st_nlink==1 and first.st_size<=limit,'bootstrap_file_metadata')
        digest=hashlib.sha256();size=0
        while True:
            chunk=os.read(fd,65536)
            if not chunk:break
            size+=len(chunk);e.need(size<=limit,'bootstrap_file_limit');digest.update(chunk)
        last=os.fstat(fd)
        key=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        e.need(key(first)==key(last)==key(path.lstat()) and size==first.st_size,'bootstrap_file_drift')
        return digest.hexdigest()
    finally:os.close(fd)

def tree_digest(root):
    """Digest of every regular file under root (no symlinks, no special files, private-safe modes)."""
    root=Path(root);e.need(root.is_absolute() and root==root.resolve(strict=True) and root.is_dir(),'bootstrap_tree_root')
    out={}
    for p in sorted(root.rglob('*')):
        st=p.lstat();rel=str(p.relative_to(root))
        e.need(not stat.S_ISLNK(st.st_mode),'bootstrap_tree_symlink:'+rel)
        if stat.S_ISDIR(st.st_mode):e.need(st.st_uid==os.geteuid() and not st.st_mode&0o022,'bootstrap_tree_directory:'+rel);continue
        e.need(stat.S_ISREG(st.st_mode),'bootstrap_tree_special:'+rel);out[rel]=file_sha(p)
    return out

def runtime_identity(runtime,overlay):
    """Dedicated runtime native: detached at the accepted base, the 17 overlay files exactly as the accepted manifest."""
    runtime=Path(runtime);e.need(runtime.is_absolute() and runtime==runtime.resolve(strict=True) and runtime.is_dir(),'bootstrap_runtime_root')
    e.need(type(overlay) is dict and overlay.get('source_only') is True and _hex(overlay.get('patch_sha256')) and type(overlay.get('files')) is dict and len(overlay['files'])==17,'bootstrap_overlay_manifest')
    head=(runtime/'.git'/'HEAD');e.need(not head.is_symlink() and head.is_file(),'bootstrap_runtime_head')
    e.need(head.read_text().strip()==overlay['base_head'],'bootstrap_runtime_head_drift')
    files={}
    for name,row in sorted(overlay['files'].items()):
        e.need(type(row) is dict and _hex(row.get('sha256')),'bootstrap_overlay_row:'+name)
        got=file_sha(runtime/name);e.need(got==row['sha256'],'bootstrap_runtime_overlay_drift:'+name);files[name]=got
    # Own venv (not a link to another install): its editable finder must map every overlay module to THIS runtime,
    # and the console entry point must run THIS venv's interpreter (absolute shebang, no PATH dependence).
    env=runtime/'.venv';e.need(not env.is_symlink() and env.is_dir() and env==env.resolve(strict=True),'bootstrap_runtime_venv')
    venv=env/'bin'/'python';target=Path(os.path.realpath(venv))
    e.need(target.is_absolute() and target.is_file() and target.name.startswith('python3.11'),'bootstrap_runtime_interpreter')
    finders=sorted((env/'lib'/'python3.11'/'site-packages').glob('__editable___hermes_agent_*_finder.py'))
    e.need(len(finders)==1,'bootstrap_runtime_editable_finder');finder=finders[0]
    mapping=_finder_mapping(finder.read_text())
    top={n[:-3] for n in overlay['files'] if '/' not in n and n.endswith('.py')}|{n.split('/')[0] for n in overlay['files'] if '/' in n}
    for name in sorted(top):
        e.need(mapping.get(name)==str(runtime/name),'bootstrap_runtime_editable_mapping:'+name)
    e.need(all(v==str(runtime/k) for k,v in mapping.items()),'bootstrap_runtime_editable_foreign')
    exe=env/'bin'/'hermes'
    with open(exe,'rb') as fh:first=fh.readline(4096)
    e.need(first==b'#!'+str(venv).encode()+b'\n','bootstrap_runtime_entry_shebang')
    return dict(root=str(runtime),git_head=overlay['base_head'],overlay_patch_sha256=overlay['patch_sha256'],overlay_files=files,
                interpreter=dict(link=str(venv),target=str(target),sha256=file_sha(target,limit=256*1024*1024)),
                editable_finder=dict(path=str(finder),sha256=file_sha(finder,limit=LIMIT),modules=len(mapping)),
                executable=dict(path=str(exe),sha256=file_sha(exe,limit=LIMIT)))

def _finder_mapping(text):
    """Read MAPPING from a setuptools editable finder without executing it."""
    import ast
    tree=ast.parse(text)
    for node in tree.body:
        target=node.target if isinstance(node,ast.AnnAssign) else (node.targets[0] if isinstance(node,ast.Assign) and len(node.targets)==1 else None)
        if isinstance(target,ast.Name) and target.id=='MAPPING':
            value=ast.literal_eval(node.value);e.need(type(value) is dict and all(type(k) is str and type(v) is str for k,v in value.items()),'bootstrap_runtime_finder_shape')
            return value
    e.need(False,'bootstrap_runtime_finder_mapping_missing')

def profile_identity(profile,*,kb):
    """Protected profile under $HOME/.hermes/profiles; toolsets from the ORIGINAL native resolver."""
    e.need(profile in PROFILES,'bootstrap_profile_name')
    home=Path.home()/'.hermes'/'profiles'/profile
    e.need(home.is_absolute() and home==home.resolve(strict=True) and home.is_dir(),'bootstrap_profile_home')
    config=file_sha(home/'config.yaml',limit=LIMIT)
    plugins=tree_digest(home/'plugins') if (home/'plugins').exists() else {}
    toolsets=kb._resolve_worker_cli_toolsets(str(home))
    e.need(type(toolsets) is list and toolsets and len(set(toolsets))==len(toolsets) and all(type(x) is str and x for x in toolsets),'bootstrap_toolsets')
    e.need(file_sha(home/'config.yaml',limit=LIMIT)==config,'bootstrap_profile_drift_during_resolve')
    return dict(home=str(home),config_sha256=config,plugins_sha256=e.sha(c.canonical(plugins)),plugin_files=len(plugins),toolsets=list(toolsets))

def measure(runtime,overlay,*,kb):
    return dict(runtime=runtime_identity(runtime,overlay),profiles={p:profile_identity(p,kb=kb) for p in PROFILES})

def check_reviewer_capability(result):
    e.need(type(result) is dict and result.get('profile')=='reviewer-gpt' and result.get('tools')==list(REVIEWER_TOOLS) and
           result.get('forbidden')==[],'bootstrap_reviewer_capability')
    return dict(profile='reviewer-gpt',tools=list(REVIEWER_TOOLS),toolsets=result.get('toolsets'),forbidden=[])

def bootstrap(runtime,overlay,*,kb,verify_reviewer,helper_sha256,created_utc):
    """measure -> original reviewer verifier -> measure again (must be identical) -> record dict."""
    e.need(_hex(helper_sha256) and type(created_utc) is str and created_utc,'bootstrap_identity')
    before=measure(runtime,overlay,kb=kb)
    capability=check_reviewer_capability(verify_reviewer(Path(before['profiles']['reviewer-gpt']['home'])))
    after=measure(runtime,overlay,kb=kb);e.need(before==after,'bootstrap_drift_during_verification')
    return dict(schema=SCHEMA,candidate_manifest=c.CANDIDATE,owner_uid=os.geteuid(),bootstrap_helper_sha256=helper_sha256,
                created_utc=created_utc,reviewer_capability=capability,**after,**FLAGS)

def write_record(path,record):
    """One-shot: exclusive create, 0600, then return the record SHA-256. Never overwrites."""
    e.need(type(record) is dict and record.get('schema')==SCHEMA,'bootstrap_record_schema')
    raw=c.canonical(record);path=Path(path);e.need(path.parent.is_dir() and not os.path.lexists(path),'bootstrap_record_exists')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        e.need(os.write(fd,raw)==len(raw),'bootstrap_record_short_write');os.fsync(fd)
    finally:os.close(fd)
    return e.sha(raw)

def read_record(path,expected_sha256):
    e.need(_hex(expected_sha256),'bootstrap_record_pin');path=Path(path)
    st=path.lstat();e.need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and st.st_size<=LIMIT,'bootstrap_record_metadata')
    raw=e.read(path);e.need(e.sha(raw)==expected_sha256,'bootstrap_record_sha')
    record=json.loads(raw,object_pairs_hook=_pairs)
    e.need(c.canonical(record)==raw,'bootstrap_record_not_canonical')
    return record

def _pairs(rows):
    d={}
    for k,v in rows:e.need(k not in d,'bootstrap_record_duplicate_key');d[k]=v
    return d

def load_pins(path,expected_sha256,*,kb,overlay):
    """Return {profile: LaunchPins} only if the record is the pinned one AND the host still measures identically."""
    record=read_record(path,expected_sha256)
    e.need(set(record)=={'schema','candidate_manifest','owner_uid','bootstrap_helper_sha256','created_utc','reviewer_capability','runtime','profiles',*FLAGS},'bootstrap_record_fields')
    e.need(record['schema']==SCHEMA and record['candidate_manifest']==c.CANDIDATE and record['owner_uid']==os.geteuid(),'bootstrap_record_identity')
    e.need(all(record[k] is False for k in FLAGS),'bootstrap_record_authority')
    check_reviewer_capability(dict(record['reviewer_capability']))
    now=measure(record['runtime']['root'],overlay,kb=kb)
    e.need(now['runtime']==record['runtime'],'bootstrap_runtime_drift')
    for p in PROFILES:e.need(now['profiles'][p]==record['profiles'][p],'bootstrap_profile_drift:'+p)
    exe=record['runtime']['executable']
    return {p:LaunchPins((exe['path'],),exe['sha256'],record['profiles'][p]['home'],record['profiles'][p]['config_sha256'],
                         tuple(record['profiles'][p]['toolsets']),provenance_sha256=expected_sha256) for p in PROFILES}
