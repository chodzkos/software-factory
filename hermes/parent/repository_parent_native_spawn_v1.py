"""WIP: explicit native-dispatch seam with one Popen owner and pre-exec gate.

This is a NEW trusted caller. It passes an explicit kb facade to the unchanged
review dispatcher; it does not modify/alias any native module. Task factories,
global scheduling/reaping, stale-claim retries and task-id-only error recovery
are NOT called. The original routing/seal/startup/claim checks still run.

Live bootstrap, profile/runtime provenance, protected model conversations and
external finalization remain outside this component. LaunchPins MUST come from
the trusted bootstrap, not a model argument or a self-authored acceptance.
"""
from __future__ import annotations
import contextlib
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import threading
import types
import repository_task_contract as c
import repository_execution_io as e
import repository_native_adapter_v3 as n
import repository_native_control_v3 as nc
import repository_execution_launcher_v3 as l
import linux_memfd_seals as seals
import repository_parent_workers_v1 as workers
import repository_parent_process_owner_v1 as owner
from repository_parent_native_launch_v1 import LaunchPins, build_native_plan, check_plan, regular_hash
import repository_parent_launch_bootstrap_v1 as bootstrap
import kanban_review_dispatch as dispatch

VERSION='0.2.0-WIP'
GATE_SHA256='f396093aeae3136bfeb3e94d5d4c6a3fbdbdbe53478f85dd2eb0f021ea3feb4f'
DISPATCH_SHA256='0b18ad437bd0f2018e93fe851dc57f56e2ebdc69a4ff6afeb204bfa299a5e79e'
# Accepted UAPI adapter: checked against Python exports, no local ABI literals.
F_ADD_SEALS=seals.F_ADD_SEALS
MEMFD_SEALS=seals.REQUIRED_SEALS
FLAGS=dict(automatic_retry=False,native_approval=False,cleanup_verified=False,
           live_execution_authorized=False,production_launch_ready=False)

def cache_safe_environment(env,prefix):
    """G3: the worker never reads a cached .pyc. Inherited PYTHON* settings (PYTHONPATH, PYTHONSTARTUP, a shared
    PYTHONPYCACHEPREFIX, ...) are dropped; bytecode writes are disabled and the cache prefix is a fresh, private, empty
    directory, so every module (stdlib and site-packages included) is compiled from its source file."""
    out={k:v for k,v in env.items() if not k.startswith('PYTHON')}
    out['PYTHONDONTWRITEBYTECODE']='1';out['PYTHONPYCACHEPREFIX']=str(prefix);return out

class _DispatchAbort(BaseException):
    """Prevent original task-id-only spawn-error recovery after child ownership.

    A private control-flow token carries the original exception. Public methods
    re-raise that exception, not this token. No claim is ended while a new child
    may still exist. The external child-aware finalizer owns that decision.
    """
    def __init__(self,primary):self.primary=primary

@dataclasses.dataclass(frozen=True)
class NativeSpawnedWorker:
    task_id:str
    run_id:int
    pid:int

class _Attempt:
    def __init__(self,identity,path):
        self.identity=identity;self.path=path;self.process=None;self.birth=None
        self.gatefd=None;self.token=None;self.owned=None;self.released=False
        self.failed=False;self.notice=None;self.task=None;self.plan=None;self.pins=None

class ParentNativeSpawner:
    """Retain this object for the entire parent lifetime; never adopt from disk."""
    def __init__(self,control: nc.NativeRepositoryControl,*,coder_pins:LaunchPins,reviewer_pins:LaunchPins):
        e.need(type(control) is nc.NativeRepositoryControl,'spawn_control_type')
        e.need(type(coder_pins) is LaunchPins and type(reviewer_pins) is LaunchPins,'spawn_pins_type')
        self.control=control;self.ctx=control.verify();self.pid=os.getpid()
        self.request_sha256=e.sha(e.read(control.job/'task_request.json'))
        self.kb=control.kb;self.owner=owner.ParentProcessOwner()
        self.pins={n.CODER:coder_pins,n.REVIEWER:reviewer_pins};self.bootstrap_record=None
        self.lock=threading.RLock();self.attempts={};self.tokens={};self._review_active=False
        self._review_consumed=False;self._review_attempt=None
        self.gate=Path(__file__).with_name('repository_parent_exec_gate_v1.py')
        regular_hash(self.gate,GATE_SHA256)
        regular_hash(Path(dispatch.__file__),DISPATCH_SHA256)
        e.need(dispatch._TEST_MUTATION_HOOK is None,'spawn_dispatch_test_hook_forbidden')
        self._native_functions={key:getattr(self.kb,key) for key in (
            '_set_worker_pid','_bind_factory_coder_run','_resolve_worktree_workspace',
            '_fire_worker_spawned_hook','claim_review_task','get_task','write_txn')}
        self._dispatch=dispatch.dispatch_review

    def _parent(self):
        e.need(os.getpid()==self.pid,'spawn_wrong_parent')
        ctx=self.control.verify()
        e.need(e.sha(e.read(self.control.job/'task_request.json'))==self.request_sha256,'spawn_request_drift')
        e.need(all(getattr(self.kb,k) is f for k,f in self._native_functions.items()) and
               dispatch.dispatch_review is self._dispatch and dispatch._TEST_MUTATION_HOOK is None,'spawn_native_function_drift')
        return ctx

    def _fault(self,attempt,primary):
        if attempt.failed:return
        attempt.failed=True;secondary=[];requested=False
        # Never exec a still-gated child after failure. EOF lets the inert gate
        # terminate; this is neither wait(), a signal, nor verified cleanup.
        if attempt.gatefd is not None:
            try:os.close(attempt.gatefd)
            except BaseException as x:secondary.append(type(x).__name__)
            attempt.gatefd=None
        try:
            self.control.launcher.finalizer.fj_ensure_intent(self.ctx.spec,'postflight_failed',l.utc());requested=True
        except BaseException as x:secondary.append(type(x).__name__)
        notice=dict(schema='sf-parent-native-spawn-fault/v1',task_id=attempt.identity.task_id,
            run_id=attempt.identity.run_id,pid=None if attempt.process is None else attempt.process.pid,
            primary_error_type=type(primary).__name__,secondary_error_types=secondary,
            finalization_requested=requested,external_finalizer_required=True,**FLAGS)
        attempt.notice=notice
        try:e.new_json(attempt.path/'FAULT.json',notice)
        except BaseException as x:secondary.append(type(x).__name__)

    def _row(self,conn,ctx,identity,*,pid=None):
        role=self.control.adapter.active(conn,ctx,identity)
        task=self.kb.get_task(conn,identity.task_id)
        run=conn.execute('SELECT worker_pid FROM task_runs WHERE id=? AND task_id=?',
                         (identity.run_id,identity.task_id)).fetchone()
        e.need(task is not None and run is not None and task.worker_pid==pid and run['worker_pid']==pid,'spawn_native_pid_drift')
        e.need(role in self.pins and task.assignee==role,'spawn_native_role')
        return task

    def _bound_identity(self,task):
        ident=n.RunIdentity(task.id,task.current_run_id,task.claim_lock);ident.validate()
        path=nc.binding_path(self.control.verify(),ident.run_id)
        if e.present(path):
            e.need(self.control.identity(ident.run_id)==ident,'spawn_control_binding_drift')
        else:self.control.bind_run(ident)
        return ident

    @classmethod
    def from_bootstrap(cls,control,record_path,record_sha256,*,overlay):
        """The only production construction: pins from the owner bootstrap record, re-measured now."""
        pins=bootstrap.load_pins(record_path,record_sha256,kb=control.kb,overlay=overlay)
        obj=cls(control,coder_pins=pins[n.CODER],reviewer_pins=pins[n.REVIEWER])
        obj.bootstrap_record=(Path(record_path),record_sha256);return obj
    def _check_bootstrap(self):
        if self.bootstrap_record is None:return
        path,expected=self.bootstrap_record
        # TOCTOU: the record must still be the pinned bytes when a worker is started.
        bootstrap.read_record(path,expected)
        e.need(all(p.provenance_sha256==expected for p in self.pins.values()),'spawn_pins_provenance')
    def _start_claimed(self,claimed,workspace,*,board,review=False):
        with self.lock:
            self._check_bootstrap()
            ctx=self._parent()
            e.need(type(claimed) is self.kb.Task and type(board) is str and board==ctx.binding['board'],'spawn_claim_or_board')
            e.need((claimed.assignee==n.REVIEWER and review and self._review_active) or
                   (claimed.assignee==n.CODER and not review),'spawn_role_lane')
            ident=n.RunIdentity(claimed.id,claimed.current_run_id,claimed.claim_lock);ident.validate()
            key=(ident.task_id,ident.run_id)
            path=ctx.staged/'control'/('parent-native-spawn-'+str(ident.run_id))
            e.need(key not in self.attempts and not e.present(path),'spawn_attempt_already_consumed')
            # Before the new reservation: no execution or failure notification
            # for a duplicate, wrong task, wrong profile or invalid snapshot.
            parent=self.control.launcher.parent_service(ctx)
            with parent.fence(),self.control.connection() as conn,self.control.adapter.locked(conn) as fresh:
                actual=self._row(conn,fresh,ident)
                expected=dataclasses.asdict(actual);received=dataclasses.asdict(claimed)
                if review:expected['skills']=list(dict.fromkeys([*(actual.skills or []),'sdlc-review']))
                e.need(expected==received,'spawn_claim_snapshot_drift')
                e.need(actual.workspace_path==workspace and str(fresh.work)==workspace,'spawn_resolved_worktree_drift')
                with c.directory_fd(path.parent) as fd:
                    try:os.mkdir(path.name,0o700,dir_fd=fd)
                    except FileExistsError:raise e.ExecutionRefused('spawn_attempt_already_consumed') from None
                    # Strong in-memory ownership begins before fsync/other IO.
                    attempt=_Attempt(ident,path);self.attempts[key]=attempt
                    if review:self._review_attempt=attempt
                    try:os.fsync(fd)
                    except BaseException as primary:self._fault(attempt,primary);raise
            try:
                self._bound_identity(actual)
                # No global dispatcher tick, stale claim logic or reaping.
                # The unchanged metadata binder is composed under the existing
                # native transaction via the original savepoint adapter.
                if not review:
                    with parent.fence(),self.control.connection() as conn,self.control.adapter.locked(conn) as fresh:
                        actual=self._row(conn,fresh,ident)
                        self.kb._bind_factory_coder_run(dispatch._SavepointConnection(conn),actual,workspace)
                attempt.task=claimed
                plan=build_native_plan(self.kb,claimed,workspace,board=board)
                pins=self.pins[claimed.assignee]
                check_plan(plan,pins,kb=self.kb,board=board,database=ctx.binding['db'],workspace=workspace,profile=claimed.assignee)
                attempt.plan,attempt.pins=plan,pins
                e.new_json(path/'INTENT.json',dict(schema='sf-parent-native-spawn/v1',
                    task_id=ident.task_id,run_id=ident.run_id,claim_sha256=e.sha(ident.claim_lock.encode()),
                    request_sha256=self.request_sha256,plan_sha256=e.sha(c.canonical(dataclasses.asdict(plan))),**FLAGS))
                with parent.fence(),self.control.connection() as conn,self.control.adapter.locked(conn) as fresh:
                    self._row(conn,fresh,ident)
                    self._popen_gated(attempt)
                return attempt.process.pid
            except BaseException as primary:
                self._fault(attempt,primary);raise

    def _popen_gated(self,attempt):
        plan=attempt.plan;release=secrets.token_bytes(32)
        prefix=attempt.path/'pycache';e.need(not os.path.lexists(prefix),'spawn_pycache_prefix_exists');prefix.mkdir(mode=0o700)
        env=cache_safe_environment(plan.env(),prefix)
        data=dict(schema='sf-native-worker-exec-gate/v1',argv=list(plan.argv),environment=env,
                  cwd=plan.workspace,parent_pid=self.pid,release_sha256=e.sha(release))
        raw=c.canonical(data);e.need(len(raw)<=1024*1024,'spawn_gate_payload_size')
        regular_hash(self.gate,GATE_SHA256)
        planfd=seals.create_memfd('sf-native-launch')
        rd=wr=None
        try:
            e.need(os.write(planfd,raw)==len(raw),'spawn_plan_short_write')
            seals.seal(planfd)
            rd,wr=os.pipe2(os.O_CLOEXEC)
            log=Path(plan.log_path)
            # Native log location/append/STDOUT merge are preserved. No rotation
            # or historical session retagging is performed by a single-task owner.
            log.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            logfd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            try:
                st=os.fstat(logfd)
                e.need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and not st.st_mode&0o7022,'spawn_log_metadata')
                # -I ignores PYTHON* for the gate itself, so its private cache prefix is an explicit -X option.
                attempt.process=subprocess.Popen([sys.executable,'-I','-B','-S','-X','pycache_prefix='+str(prefix),str(self.gate),str(planfd),str(rd),e.sha(raw)],
                    cwd=plan.workspace,env=env,stdin=subprocess.DEVNULL,stdout=logfd,stderr=subprocess.STDOUT,
                    close_fds=True,pass_fds=(planfd,rd),start_new_session=True)
                # Retain Popen BEFORE any further IO which may fail.
                attempt.gatefd=wr;wr=None;attempt.token=release
                attempt.birth=workers.process_identity(attempt.process.pid)
                e.need(attempt.birth.parent_pid==self.pid and attempt.birth.uid==os.geteuid(),'spawn_direct_child')
            finally:os.close(logfd)
        finally:
            os.close(planfd)
            if rd is not None:os.close(rd)
            if wr is not None:os.close(wr)

    def _bind_pid(self,conn,task_id,pid):
        with self.lock:
            choices=[a for a in self.attempts.values() if a.identity.task_id==task_id and a.process is not None and a.process.pid==pid]
            e.need(len(choices)==1,'spawn_unowned_pid_binding');a=choices[0]
            e.need(not a.failed and not a.released and a.owned is None,'spawn_binding_consumed')
            try:
                ctx=self._parent()
                e.need(not conn.in_transaction,'spawn_caller_transaction')
                parent=self.control.launcher.parent_service(ctx)
                # The dispatcher's native connection is a tracked sqlite3 subclass,
                # which the accepted adapter lock refuses; lock and write through
                # the controller's own connection, as the coder path does.
                with parent.fence(),self.control.connection() as own,self.control.adapter.locked(own) as fresh:
                    self._row(own,fresh,a.identity)
                    e.need(workers.process_identity(pid)==a.birth,'spawn_birth_drift')
                    # Exact native function, not task-id-only outside a lock.
                    self.kb._set_worker_pid(dispatch._SavepointConnection(own),task_id,pid)
                    self._row(own,fresh,a.identity,pid=pid)
                a.owned=self.owner.register(self.control,a.identity.run_id,a.process,expected_process=a.birth)
                e.new_json(a.path/'PID_BOUND.json',dict(schema='sf-parent-native-pid-bound/v1',
                    task_id=task_id,run_id=a.identity.run_id,process=dataclasses.asdict(a.birth),**FLAGS))
            except BaseException as primary:self._fault(a,primary);raise

    def _release_and_hook(self,conn,claimed,workspace,pid,*,board):
        a=self.attempts[(claimed.id,claimed.current_run_id)]
        with self.lock:
            e.need(not a.failed and not a.released and a.owned is not None,'spawn_release_not_ready')
            try:
                ctx=self._parent();parent=self.control.launcher.parent_service(ctx)
                self.kb._fire_worker_spawned_hook(conn,claimed,workspace,pid,board=board)
                e.need(not conn.in_transaction,'spawn_caller_transaction')
                with parent.fence(),self.control.connection() as own,self.control.adapter.locked(own) as fresh:
                    self._row(own,fresh,a.identity,pid=pid)
                    e.need(workers.process_identity(pid)==a.birth,'spawn_release_process_drift')
                    check_plan(a.plan,a.pins,kb=self.kb,board=board,database=ctx.binding['db'],workspace=workspace,profile=claimed.assignee)
                    e.new_json(a.path/'RELEASE_CONSUMED.json',dict(schema='sf-parent-native-release/v1',
                        task_id=claimed.id,run_id=claimed.current_run_id,pid=pid,**FLAGS))
                    e.need(a.gatefd is not None and os.write(a.gatefd,a.token)==32,'spawn_release_write')
                    os.close(a.gatefd);a.gatefd=None;a.token=None;a.released=True
                    result=NativeSpawnedWorker(claimed.id,claimed.current_run_id,pid)
                    self.tokens[(claimed.id,claimed.current_run_id)]=result
                return result
            except BaseException as primary:self._fault(a,primary);raise

    def spawn_coder(self,run_id):
        """Consume an already authentically claimed coder run, not create one."""
        ctx=self._parent();ident=self.control.identity(run_id)
        with self.control.connection() as conn,self.control.adapter.locked(conn) as fresh:
            task=self._row(conn,fresh,ident);e.need(task.assignee==n.CODER,'spawn_coder_only')
        workspace,branch=self.kb._resolve_worktree_workspace(task,board=ctx.binding['board'])
        workspace=str(Path(workspace).resolve(strict=True))
        e.need(workspace==task.workspace_path and (branch or task.branch_name)==task.branch_name,'spawn_resolver_drift')
        pid=self._start_claimed(task,workspace,board=ctx.binding['board'])
        a=self.attempts[(task.id,task.current_run_id)]
        try:
            with self.control.connection() as conn:
                self._bind_pid(conn,task.id,pid)
                return self._release_and_hook(conn,task,workspace,pid,board=ctx.binding['board'])
        except BaseException as primary:
            self._fault(a,primary);raise

    def _snapshot(self):
        ctx=self._parent()
        with self.control.connection() as conn,self.control.adapter.locked(conn) as fresh:
            snap=__import__('finalization_core').fj_snapshot(conn,fresh.binding)[0]
            self.control.adapter.check_task(fresh,snap['task'])
            tid=snap['task']['id']
            return dict(task=dataclasses.asdict(self.kb.get_task(conn,tid)),
                events=[dataclasses.asdict(x) for x in self.kb.list_events(conn,tid)],
                runs=[dataclasses.asdict(x) for x in self.kb.list_runs(conn,tid)])

    def dispatch_reviewer(self):
        """Invoke the exact pinned dispatcher, including real seal/startup checks.

        No supplied fake snapshot, no hidden patch of _default_spawn, and no
        global scheduling tick. A negative gate is not a new execution permit.
        """
        with self.lock:
            ctx=self._parent();e.need(not self._review_consumed,'spawn_review_dispatch_consumed')
            # The dispatcher's native connections need the original one-start hook
            # for exactly this job (installed by the parent bootstrap); refuse before
            # the one-shot dispatch is consumed rather than at the native claim.
            e.need(getattr(self.kb,'_sf_finalization_binding',None)==ctx.binding,'spawn_native_one_start_hook_missing')
            snap=self._snapshot();tid=snap['task']['id']
            path=ctx.staged/'control'/'PARENT_REVIEW_DISPATCH_CONSUMED.json'
            e.new_json(path,dict(schema='sf-parent-review-dispatch/v1',task_id=tid,**FLAGS))
            self._review_consumed=True;self._review_active=True
            facade=_NativeFacade(self)
            try:
                code=dispatch.dispatch_review(tid,board=ctx.binding['board'],snapshot=snap,kb=facade)
                if code!=0:return code,None
                e.need(self._review_attempt is not None and self._review_attempt.released,'spawn_dispatch_missing_owned_child')
                a=self._review_attempt;return code,self.tokens[(a.identity.task_id,a.identity.run_id)]
            except _DispatchAbort as abort:
                raise abort.primary.with_traceback(abort.primary.__traceback__) from None
            finally:self._review_active=False

    def _attempt(self,token):
        self._parent();e.need(type(token) is NativeSpawnedWorker,'spawn_token_type')
        key=(token.task_id,token.run_id)
        e.need(self.tokens.get(key) is token,'spawn_unknown_token')
        a=self.attempts[key];e.need(a.released and not a.failed,'spawn_attempt_failed')
        return a

    def observe(self,token):return self.owner.observe(self._attempt(token).owned)
    def reap(self,token):return self.owner.reap(self._attempt(token).owned)
    def close_reaped(self,token):return self.owner.close_reaped(self._attempt(token).owned)

class _NativeFacade:
    """Explicit kb argument to the unchanged dispatcher, not a module alias.

    All provenance, workspace and claim methods are the original callable.
    Only spawn/PID/post-spawn/error custody is adapted for the new parent.
    """
    FORWARD=frozenset({'review_dispatch_enabled','board_exists','connect_closing','write_txn',
        'get_task','claim_review_task','_resolve_worktree_workspace','set_workspace_path','set_branch_name'})
    def __init__(self,spawner):self.s=spawner
    def __getattr__(self,key):
        if key not in self.FORWARD:raise AttributeError(key)
        return getattr(self.s.kb,key)
    def _default_spawn(self,task,workspace,*,board):
        try:return self.s._start_claimed(task,workspace,board=board,review=True)
        except BaseException as primary:raise _DispatchAbort(primary) from None
    def _set_worker_pid(self,conn,tid,pid):
        try:return self.s._bind_pid(conn,tid,pid)
        except BaseException as primary:raise _DispatchAbort(primary) from None
    def _fire_worker_spawned_hook(self,conn,task,workspace,pid,*,board):
        try:return self.s._release_and_hook(conn,task,workspace,pid,board=board)
        except BaseException as primary:raise _DispatchAbort(primary) from None
    def _record_spawn_failure(self,conn,tid,error):
        # The original recovery ends the current task-id's run and may requeue.
        # New parent never calls it; preserve the active claim for the external
        # child-aware finalizer rather than ending a possibly changed run.
        primary=e.ExecutionRefused('native_review_dispatch_failed:'+str(error)[:400])
        a=self.s._review_attempt
        if a is not None:self.s._fault(a,primary)
        else:
            try:self.s.control.launcher.finalizer.fj_ensure_intent(self.s.ctx.spec,'postflight_failed',l.utc())
            except BaseException:pass
        raise _DispatchAbort(primary)
