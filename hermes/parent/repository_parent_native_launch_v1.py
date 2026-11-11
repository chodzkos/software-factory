"""WIP: explicit native launch-plan translation, not a patched kanban module.

Preserves the pinned _default_spawn argv, environment, profile selection, quiet
CLI, board and task binding and log redirection. Does NOT run a dispatcher tick,
retag historical sessions, reclaim, rotate old logs or select another profile.
The new single-attempt owner opens the native task log securely. No Popen here.
Full bootstrap must authenticate LaunchPins; this module cannot self-authorize.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import hashlib
import stat
import repository_execution_io as e

VERSION = '0.2.0-WIP'
NATIVE_KANBAN_SHA = '1f825422dd7d6cb36bca65676d2225ac55abc6dbb832b9ba0f4aa25b00d5f022'

@dataclass(frozen=True)
class NativeLaunchPlan:
    task_id: str
    run_id: int
    claim_lock: str
    profile: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    workspace: str
    log_path: str

    def env(self):
        return dict(self.environment)

@dataclass(frozen=True)
class LaunchPins:
    """Explicit pins supplied by the trusted parent, never by a model tool.

    This is NOT a workload authorization or independently authenticated proof.
    provenance_sha256 is set only by repository_parent_launch_bootstrap_v1.load_pins
    (the SHA-256 of the owner bootstrap record); None means test/explicit pins.
    """
    argv_prefix: tuple[str, ...]
    executable_sha256: str
    profile_home: str
    config_sha256: str
    toolsets: tuple[str, ...]
    provenance_sha256: str | None = None


def regular_hash(path, expected, *, limit=32*1024*1024):
    """Stable no-follow read; do not edit code/configuration or follow symlinks."""
    e.need(type(expected) is str and len(expected)==64 and all(x in '0123456789abcdef' for x in expected), 'spawn_bad_pin')
    path=Path(path)
    e.need(path.is_absolute() and path==path.resolve(strict=True), 'spawn_noncanonical_pin_path')
    flags=os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK
    fd=os.open(path,flags)
    try:
        first=os.fstat(fd)
        e.need(stat.S_ISREG(first.st_mode) and first.st_uid in (0,os.geteuid()) and
               not first.st_mode & 0o7022 and first.st_nlink==1 and 0<=first.st_size<=limit,'spawn_pin_metadata')
        digest=hashlib.sha256();size=0
        while True:
            chunk=os.read(fd,min(65536,limit+1-size))
            if not chunk:break
            size+=len(chunk);e.need(size<=limit,'spawn_pin_limit');digest.update(chunk)
        last=os.fstat(fd)
        key=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        e.need(key(first)==key(last)==key(path.lstat()) and size==first.st_size,'spawn_pin_drift')
        e.need(digest.hexdigest()==expected,'spawn_pin_hash')
    finally:os.close(fd)


def build_native_plan(kb, task, workspace, *, board):
    """Translate the original native command/env builder, with no execution.

    Original conditional branches retained, though the routed-v3 adapter rejects
    model/provider/goal/timeout/skills overrides on persisted cards. The original
    targeted review dispatcher adds transient sdlc-review to its claim copy.
    """
    e.need(type(task) is kb.Task and task.assignee, 'spawn_native_task_required')
    e.need(type(workspace) is str and Path(workspace).is_absolute() and
           Path(workspace).is_dir() and str(Path(workspace).resolve(strict=True))==workspace,'spawn_workspace')
    e.need(type(board) is str and bool(board),'spawn_explicit_board')
    from hermes_cli.profiles import normalize_profile_name, resolve_profile_env
    from gateway.session_context import _VAR_MAP
    profile_arg=normalize_profile_name(task.assignee)
    prompt=f'work kanban task {task.id}'
    env=dict(os.environ)
    for key in _VAR_MAP:env.pop(key,None)
    # Unlike native's test-only missing-profile fallback, a missing real profile
    # is an explicit refusal here. Never fall back to an ambient profile.
    env['HERMES_HOME']=resolve_profile_env(profile_arg)
    if task.tenant:env['HERMES_TENANT']=task.tenant
    env['HERMES_KANBAN_TASK']=task.id
    env['HERMES_KANBAN_WORKSPACE']=workspace
    env['HERMES_SESSION_SOURCE']='kanban'
    env['TERMINAL_CWD']=workspace
    if task.branch_name:env['HERMES_KANBAN_BRANCH']=task.branch_name
    if task.current_run_id is not None:env['HERMES_KANBAN_RUN_ID']=str(task.current_run_id)
    if task.claim_lock:env['HERMES_KANBAN_CLAIM_LOCK']=task.claim_lock
    if task.goal_mode:
        env['HERMES_KANBAN_GOAL_MODE']='1'
        if task.goal_max_turns is not None:env['HERMES_KANBAN_GOAL_MAX_TURNS']=str(int(task.goal_max_turns))
    timeout=kb._worker_terminal_timeout_env(task.max_runtime_seconds,env.get('TERMINAL_TIMEOUT'))
    if timeout is not None:env['TERMINAL_TIMEOUT']=timeout
    timeout=kb._worker_terminal_timeout_env(task.max_runtime_seconds,env.get('TERMINAL_MAX_FOREGROUND_TIMEOUT'))
    if timeout is not None:env['TERMINAL_MAX_FOREGROUND_TIMEOUT']=timeout
    env['HERMES_KANBAN_DB']=str(kb.kanban_db_path(board=board))
    env['HERMES_KANBAN_WORKSPACES_ROOT']=str(kb.workspaces_root(board=board))
    env['HERMES_KANBAN_BOARD']=kb._normalize_board_slug(board) or kb.get_current_board()
    env['HERMES_PROFILE']=profile_arg
    env.pop('HERMES_TUI',None)
    cmd=[*kb._resolve_hermes_argv(),'-p',profile_arg,'--cli','--accept-hooks']
    if task.skills:
        for sk in task.skills:
            if sk:cmd.extend(['--skills',sk])
    if task.model_override:
        cmd.extend(['-m',task.model_override])
        if task.provider_override:cmd.extend(['--provider',task.provider_override])
    if task.reasoning_effort:cmd.extend(['--reasoning',task.reasoning_effort])
    toolsets=kb._resolve_worker_cli_toolsets(env.get('HERMES_HOME'))
    if toolsets:cmd.extend(['--toolsets',','.join(toolsets)])
    cmd.extend(['chat','-q',prompt])
    if task.goal_mode:cmd.append('-Q')
    e.need(type(task.current_run_id) is int and task.current_run_id>0 and
           type(task.claim_lock) is str and bool(task.claim_lock),'spawn_run_claim_required')
    return NativeLaunchPlan(task.id,task.current_run_id,task.claim_lock,profile_arg,tuple(cmd),
        tuple(sorted(env.items())),workspace,str(kb.worker_logs_dir(board=board)/(task.id+'.log')))


def check_plan(plan, pins, *, kb, board, database, workspace, profile):
    e.need(type(plan) is NativeLaunchPlan and type(pins) is LaunchPins,'spawn_plan_or_pins_type')
    prefix=pins.argv_prefix
    e.need(type(prefix) is tuple and bool(prefix) and all(type(x) is str and x for x in prefix),'spawn_argv_pin')
    # Only the console-script form. The native fallback `python -m hermes_cli.main`
    # puts cwd (the task worktree) first on sys.path, so implementer-written
    # hermes_cli/ would run instead of Hermes.
    e.need(len(prefix)==1,'spawn_script_launcher_required')
    e.need(plan.argv[:len(prefix)]==prefix and plan.argv[len(prefix):len(prefix)+4]==('-p',profile,'--cli','--accept-hooks'), 'spawn_argv_drift')
    e.need(profile in ('coder-claude','reviewer-gpt') and plan.profile==profile and plan.workspace==workspace,'spawn_profile_or_cwd_drift')
    env=plan.env();home=Path(pins.profile_home)
    e.need(home.is_absolute() and str(home.resolve(strict=True))==str(home) and home.is_dir() and
           home.name==profile and home.parent.name=='profiles','spawn_profile_home')
    # The pinned review dispatcher verifies Path.home()/.hermes/profiles/reviewer-gpt
    # in this process; the accepted worker start gates refuse any HERMES_HOME other
    # than $HOME/.hermes/profiles/<profile>. Refuse before Popen, not after the
    # claim and attempt are consumed.
    e.need(env.get('HOME')==str(Path.home()) and str(home)==str(Path.home()/'.hermes'/'profiles'/profile),
           'spawn_profile_home_identity')
    expected={'HERMES_HOME':str(home),'HERMES_PROFILE':profile,'HERMES_KANBAN_TASK':plan.task_id,
        'HERMES_KANBAN_RUN_ID':str(plan.run_id),'HERMES_KANBAN_CLAIM_LOCK':plan.claim_lock,
        'HERMES_KANBAN_DB':str(database),'HERMES_KANBAN_BOARD':board,
        'HERMES_KANBAN_WORKSPACE':workspace,'TERMINAL_CWD':workspace,'HERMES_SESSION_SOURCE':'kanban'}
    e.need(all(env.get(k)==v for k,v in expected.items()) and 'HERMES_TUI' not in env,'spawn_environment_drift')
    # None/missing optional native resolver is never sufficient for protected
    # worker start. The exact effective toolsets must equal an external pin.
    e.need(type(pins.toolsets) is tuple and pins.toolsets and len(set(pins.toolsets))==len(pins.toolsets) and
           all(type(x) is str and x for x in pins.toolsets),'spawn_toolsets_pin')
    i=plan.argv.index('--toolsets') if '--toolsets' in plan.argv else -1
    e.need(i>=0 and i+1<len(plan.argv) and plan.argv[i+1]==','.join(pins.toolsets),'spawn_toolsets_drift')
    # Reject inherited goal controls in non-goal v3 workers (native builds them
    # only conditionally and would otherwise preserve the parent's values).
    e.need(not any(k in env for k in ('HERMES_KANBAN_GOAL_MODE','HERMES_KANBAN_GOAL_MAX_TURNS')),'spawn_inherited_goal')
    regular_hash(prefix[0],pins.executable_sha256)
    regular_hash(home/'config.yaml',pins.config_sha256,limit=1024*1024)
    regular_hash(Path(kb.__file__),NATIVE_KANBAN_SHA)
