"""Known-child observation/reaping for the NEW trusted native-v3 parent.

Linux pidfds, not waitpid(-1) or PID-based signals. A native run/claim and its
recorded worker PID must be bound before registration. This does not dispatch,
renew a lease, retry/requeue, declare native success, or replace cgroup cleanup.
Only the process that directly owns the child may use the handle. A competing
reaper (including an abandoned Popen handle) is a terminal loss of evidence,
not a clean exit. Full parent integration must serialize that ownership.

No model-facing tools. Trusted same-UID Python is not a security sandbox.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
import threading
from pathlib import Path
import repository_task_contract as c
import repository_execution_io as e
import repository_native_adapter_v3 as n
import repository_native_control_v3 as nc
import repository_execution_admission_v3 as a
import repository_execution_launcher_v3 as l
import finalization_core as f
from repository_parent_result_v1 import FLAGS, run_id

VERSION = '1.0.0'
SCHEMA = 'sf-parent-known-worker/v1'

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    start_ticks: str
    boot_id: str
    uid: int

def process_identity(pid: int) -> ProcessIdentity:
    e.need(type(pid) is int and pid > 1, 'worker_pid_type')
    root = Path('/proc')/str(pid)
    raw = (root/'stat').read_text()
    # comm may itself contain ')' and whitespace. The suffix starts at field3.
    end = raw.rfind(')')
    fields = raw[end+2:].split()
    e.need(end > 0 and raw[:raw.find('(')].strip()==str(pid) and len(fields)>=20,
           'worker_proc_stat')
    parent = int(fields[1]);start = fields[19]
    e.need(start.isdecimal(), 'worker_proc_start')
    return ProcessIdentity(pid,parent,start,Path('/proc/sys/kernel/random/boot_id').read_text().strip(),root.stat().st_uid)

@dataclass(frozen=True)
class WorkerExit:
    task_id: str
    run_id: int
    pid: int
    si_code: int
    status: int
    raw_wait_status: int
    receipt_sha256: str
    child_reaped: bool = True
    native_approval: bool = False
    cleanup_verified: bool = False
    automatic_retry: bool = False

class ParentWorkerHandle:
    """One registered direct child. Registration consumes one run-specific slot.

    Nonblocking observe() uses WNOWAIT; reap() consumes exactly this pidfd once.
    The accepted native exit cache is informed, but no crash recovery/dispatcher
    API is invoked. A failed journal write after wait keeps the run consumed.
    No adoption/reopen/replay of a slot from disk exists.
    """
    def __init__(self, control: nc.NativeRepositoryControl, rid: int, pid: int, *, expected_process: ProcessIdentity):
        e.need(type(control) is nc.NativeRepositoryControl, 'worker_control_type')
        e.need(type(expected_process) is ProcessIdentity and type(pid) is int and pid>1 and
               expected_process.pid==pid, 'worker_spawn_identity_required')
        for key in ('pidfd_open','P_PIDFD','waitid','WNOWAIT','WEXITED','WNOHANG'):
            e.need(hasattr(os,key), 'worker_pidfd_required')
        self.control=control;self.rid=run_id(rid);self.pid=os.getpid()
        self.ctx=control.verify();self.identity=control.identity(self.rid)
        self._lock=threading.Lock();self._fd=None;self._closed=False
        self._owned=False;self._failed=False;self._consumed=False;self._exit=None
        self.notice=None
        self.path=self.ctx.staged/'control'/('parent-worker-'+str(self.rid))
        self.binding=dict(task_id=self.identity.task_id,run_id=self.rid,
            request_sha256=e.sha(e.read(control.job/'task_request.json')),
            binding_sha256=e.sha(c.canonical(self.ctx.binding)),
            claim_lock_sha256=a._claim_hash(self.identity))
        try:
            parent=control.launcher.parent_service(self.ctx)
            with parent.fence(),control.connection() as conn:
                with control.adapter.locked(conn) as ctx:
                    role=control.adapter.active(conn,ctx,self.identity)
                    e.need(role in (n.CODER,n.REVIEWER), 'worker_role')
                    self.role=role
                    self._native_row(conn,ctx,pid,active=True)
                    first=process_identity(pid)
                    e.need(first==expected_process, 'worker_spawn_identity_changed')
                    e.need(first.parent_pid==self.pid and first.uid==os.geteuid(), 'worker_not_direct_owned_child')
                    self._fd=os.pidfd_open(pid,0)
                    e.need(not os.get_inheritable(self._fd), 'worker_pidfd_inheritable')
                    e.need(process_identity(pid)==first, 'worker_identity_race')
                    self.process=first
                    # WNOWAIT also establishes direct-child waitability. Never
                    # consume an unknown process during constructor validation.
                    self._wait(peek=True)
                    with c.directory_fd(self.path.parent) as fd:
                        try:os.mkdir(self.path.name,0o700,dir_fd=fd)
                        except FileExistsError:raise e.ExecutionRefused('worker_already_registered') from None
                        self._owned=True
                        os.fsync(fd)
                    self.registration=dict(schema=SCHEMA,binding=self.binding,role=role,
                        process=dict(pid=first.pid,parent_pid=first.parent_pid,start_ticks=first.start_ticks,
                                     boot_id=first.boot_id,uid=first.uid),**FLAGS)
                    e.new_json(self.path/'REGISTERED.json',self.registration)
        except BaseException as exc:
            if self._owned:self._fault(exc)
            self.close()
            raise

    def _native_row(self,conn,ctx,pid,*,active=False):
        self.control.adapter.check_task(ctx,f.fj_snapshot(conn,ctx.binding)[0]['task'])
        task=conn.execute('SELECT worker_pid,current_run_id FROM tasks WHERE id=?',(self.identity.task_id,)).fetchone()
        row=conn.execute('SELECT task_id,profile,claim_lock,worker_pid,ended_at FROM task_runs WHERE id=?',(self.rid,)).fetchone()
        e.need(row is not None and row['task_id']==self.identity.task_id and row['profile']==self.role,
               'worker_native_run_binding')
        if row['ended_at'] is None:
            e.need(row['claim_lock']==self.identity.claim_lock and type(row['worker_pid']) is int and row['worker_pid']==pid,
                   'worker_native_run_binding')
        else:
            # Native _end_run clears claim and worker_pid. Only an already-open
            # pidfd, registered while active, can be observed/reaped afterwards.
            # This is evidence collection, NOT reactivation or native success.
            e.need(not active and type(row['ended_at']) is int and row['ended_at']>0 and
                   row['claim_lock'] is None and row['worker_pid'] is None,'worker_native_ended_binding')
        if active:
            e.need(task is not None and task['current_run_id']==self.rid and type(task['worker_pid']) is int and
                   task['worker_pid']==pid and row['ended_at'] is None,'worker_native_active_binding')
        return row

    def _guard(self):
        e.need(os.getpid()==self.pid, 'worker_handle_wrong_process')
        e.need(not self._closed and not self._failed, 'worker_handle_closed_or_failed')
        ctx=self.control.verify()
        e.need(self.control.identity(self.rid)==self.identity and
               e.sha(c.canonical(ctx.binding))==self.binding['binding_sha256'] and
               e.sha(e.read(self.control.job/'task_request.json'))==self.binding['request_sha256'], 'worker_binding_changed')
        e.need(e.read(self.path/'REGISTERED.json')==c.canonical(self.registration), 'worker_registration_changed')
        with self.control.connection() as conn:
            with self.control.adapter.locked(conn) as fresh:
                row=self._native_row(conn,fresh,self.process.pid)
                if row['ended_at'] is None:
                    e.need(self.control.adapter.active(conn,fresh,self.identity)==self.role,'worker_active_role_changed')
        # pidfd remains the authority for wait: no reuse of a numeric PID.
        return ctx

    def _wait(self,*,peek):
        flags=os.WEXITED|os.WNOHANG|(os.WNOWAIT if peek else 0)
        return os.waitid(os.P_PIDFD,self._fd,flags)

    def _event(self,event):
        if event is None:return None
        e.need(event.si_pid==self.process.pid and event.si_uid==self.process.uid and
               event.si_code in (os.CLD_EXITED,os.CLD_KILLED,os.CLD_DUMPED), 'worker_wait_event')
        status=event.si_status
        e.need(type(status) is int and status>=0, 'worker_wait_status')
        if event.si_code==os.CLD_EXITED:
            e.need(status<=255,'worker_wait_exit_range');raw=status<<8
        else:
            e.need(0<status<128,'worker_wait_signal_range');raw=status|(0x80 if event.si_code==os.CLD_DUMPED else 0)
        return dict(si_code=event.si_code,status=status,raw_wait_status=raw)

    def _fault(self,primary):
        self._failed=True;secondary=[];requested=False
        try:
            self.control.launcher.finalizer.fj_ensure_intent(self.ctx.spec,'postflight_failed',l.utc());requested=True
        except BaseException as exc:secondary.append(type(exc).__name__)
        self.notice=dict(schema='sf-parent-worker-fault/v1',binding=self.binding,
            primary_error_type=type(primary).__name__,finalization_requested=requested,
            secondary_error_types=secondary,external_finalizer_required=True,cleanup_verified=False,**FLAGS)
        try:e.new_json(self.path/'FAULT.json',self.notice)
        except BaseException as exc:secondary.append(type(exc).__name__)

    def _usable(self):
        e.need(os.getpid()==self.pid,'worker_handle_wrong_process')
        e.need(not self._closed and not self._failed,'worker_handle_closed_or_failed')

    def observe(self) -> dict:
        """A repeatable, nonconsuming observation of this child only.

        Exited is NOT native approval, handoff success or all-children quiescence.
        After this handle reaped successfully, its exact receipt is returned.
        """
        e.need(os.getpid()==self.pid, 'worker_handle_wrong_process')
        with self._lock:
            self._usable()
            try:
                self._guard()
                if self._exit is not None:
                    raw=e.read(self.path/'EXIT.json')
                    e.need(e.sha(raw)==self._exit.receipt_sha256, 'worker_exit_receipt_changed')
                    return dict(state='reaped',exit=self._exit,**FLAGS)
                event=self._event(self._wait(peek=True))
                return dict(state='exited' if event else 'running',event=event,**FLAGS)
            except BaseException as exc:
                self._fault(exc);raise

    def reap(self) -> WorkerExit:
        """One nonblocking wait on the retained pidfd, then durable exit receipt.

        An alive child is not consumed and can be observed later. After exit is
        observed, REAP_STARTED precedes the consuming wait. Any failure thereafter
        is terminal; a new handle cannot adopt this slot. No unbounded wait.
        """
        e.need(os.getpid()==self.pid, 'worker_handle_wrong_process')
        with self._lock:
            self._usable()
            e.need(not self._consumed, 'worker_reap_already_consumed')
            try:
                self._guard()
                observed=self._event(self._wait(peek=True))
            except BaseException as exc:self._fault(exc);raise
            e.need(observed is not None, 'worker_still_running')
            self._consumed=True
            try:
                e.new_json(self.path/'REAP_STARTED.json',dict(schema=SCHEMA,binding=self.binding,
                    process=self.registration['process'],observed=observed,**FLAGS))
                actual=self._event(self._wait(peek=False))
                e.need(actual==observed, 'worker_exit_lost_or_changed')
                # Original native cache only, never detect_crashed_workers or
                # retry-enabling dispatch/reclaim helpers.
                self.control.kb._record_worker_exit(self.process.pid,actual['raw_wait_status'])
                value=dict(schema=SCHEMA,binding=self.binding,process=self.registration['process'],
                    registration_sha256=e.sha(c.canonical(self.registration)),event=actual,
                    child_reaped=True,cleanup_verified=False,**FLAGS)
                e.new_json(self.path/'EXIT.json',value)
                self._exit=WorkerExit(self.identity.task_id,self.rid,self.process.pid,
                    actual['si_code'],actual['status'],actual['raw_wait_status'],e.sha(c.canonical(value)))
                return self._exit
            except BaseException as exc:self._fault(exc);raise

    def close(self):
        """Close only this kernel handle. This NEVER claims the child is gone."""
        if os.getpid()!=self.pid:return
        with self._lock:
            if self._fd is not None:
                os.close(self._fd);self._fd=None
            self._closed=True
