"""WORK IN PROGRESS: retain real Popen objects while accepted pidfd code reaps.

This is a trusted-caller adapter, not a replacement native spawn implementation.
The caller must retain this registry for its whole lifetime and transfer a real
Popen immediately after spawn, with independently captured ProcessIdentity. The
native run/PID must already be bound. No arbitrary PID adoption, Popen polling,
waiting, signal delivery, model execution, requeue or global reaping occurs here.

Strong references prevent Popen.__del__ from stealing the exit while ownership
is retained. They DO NOT prevent another holder from calling wait/poll, or a
native/global reaper from running. Those callers must be serialized by the full
parent. Evidence lost to another reaper is a terminal failure, never success.
The existing default native spawn returns only an integer: it cannot directly
supply this API. Integration of the native spawn path remains OPEN.

Failure keeps the handle/Popen retained; caller/external supervisor lifetime and
finalization remain mandatory. This library is not a same-UID security sandbox.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
import subprocess
import threading
import repository_execution_io as e
import repository_native_control_v3 as nc
import repository_parent_workers_v1 as workers

VERSION = '0.1.0-WIP'

@dataclass(frozen=True)
class OwnedWorker:
    """In-process capability returned by THIS registry; not deserializable trust."""
    task_id: str
    run_id: int
    pid: int

class _Entry:
    def __init__(self, token, process, handle):
        self.token = token
        self.process = process
        self.handle = handle
        self.lock = threading.Lock()
        self.failed = False
        self.exit = None
        self.closed = False
        self.secondary_fault_errors = []

class ParentProcessOwner:
    """One trusted parent's in-memory strong ownership of direct worker children.

    Registry is intentionally NOT recovered from journals or newly observed PIDs.
    Exact tokens, not equivalent dataclasses, are required on later operations.
    A terminal entry remains retained. close_reaped only closes the pidfd after a
    validated exit; it does not kill a live worker or claim cgroup quiescence.
    """
    def __init__(self):
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._entries = {}

    def _parent(self):
        e.need(os.getpid() == self._pid, 'owner_wrong_process')

    def register(self, control: nc.NativeRepositoryControl, run_id: int,
                 process: subprocess.Popen, *,
                 expected_process: workers.ProcessIdentity) -> OwnedWorker:
        """Transfer a retained real child, without poll(), wait(), or spawning.

        On a pre-admission failure responsibility remains with the original
        caller. Caller must retain process even when this method raises.
        """
        self._parent()
        e.need(type(control) is nc.NativeRepositoryControl, 'owner_control_type')
        e.need(type(process) is subprocess.Popen, 'owner_popen_type')
        e.need(type(run_id) is int and run_id > 0, 'owner_run_id')
        e.need(type(expected_process) is workers.ProcessIdentity and
               type(process.pid) is int and process.pid > 1 and
               expected_process.pid == process.pid, 'owner_spawn_identity')
        e.need(process.returncode is None, 'owner_process_already_waited')
        # Default native redirection creates no Popen PIPE file objects.
        # Blocking/draining stream ownership is a different unimplemented API.
        e.need(process.stdin is None and process.stdout is None and process.stderr is None,
               'owner_pipes_not_supported')
        identity = control.identity(run_id)
        key = (identity.task_id, run_id)
        with self._lock:
            e.need(key not in self._entries, 'owner_run_already_registered')
            e.need(all(x.process is not process and x.token.pid != process.pid
                       for x in self._entries.values()), 'owner_process_already_registered')
            # Accepted constructor performs real native locks, PID-generation
            # checks, pidfd registration and exclusive durable reservation.
            handle = workers.ParentWorkerHandle(control, run_id, process.pid,
                                                 expected_process=expected_process)
            token = OwnedWorker(identity.task_id, run_id, process.pid)
            entry = _Entry(token, process, handle)
            self._entries[key] = entry
            try:
                e.need(process.returncode is None, 'owner_concurrent_waiter')
            except BaseException as primary:
                self._fail(entry, primary)
                raise
            return token

    def _entry(self, token):
        self._parent()
        e.need(type(token) is OwnedWorker, 'owner_token_type')
        with self._lock:
            entry = self._entries.get((token.task_id, token.run_id))
            e.need(entry is not None and entry.token is token, 'owner_unknown_token')
            return entry

    @staticmethod
    def _fail(entry, primary):
        entry.failed = True
        # Accepted notification records failure; it is NOT verified stop.
        # The original exception must survive a notification failure as well.
        try:
            entry.handle._fault(primary)
        except BaseException as secondary:
            entry.secondary_fault_errors.append(type(secondary).__name__)

    @staticmethod
    def _usable(entry):
        if entry.exit is None:
            e.need(entry.process.returncode is None, 'owner_concurrent_waiter')
        else:
            e.need(entry.process.returncode == os.waitstatus_to_exitcode(entry.exit.raw_wait_status),
                   'owner_returncode_drift')

    def observe(self, token: OwnedWorker) -> dict:
        """Nonconsuming accepted pidfd observation, not Popen.poll()."""
        entry = self._entry(token)
        with entry.lock:
            e.need(not entry.failed and not entry.closed, 'owner_entry_failed_or_closed')
            try:
                self._usable(entry)
                return entry.handle.observe()
            except BaseException as primary:
                self._fail(entry, primary)
                raise

    def reap(self, token: OwnedWorker) -> workers.WorkerExit:
        """Reap via accepted handle once, then synchronize retained Popen status.

        Only an accepted real exit receipt permits assigning returncode. No
        Popen.wait/poll/_internal_poll/__del__ is invoked to infer an exit.
        A live child's observation is not an error/stop or consumption.
        """
        entry = self._entry(token)
        with entry.lock:
            e.need(entry.exit is None, 'owner_reap_already_consumed')
            e.need(not entry.failed and not entry.closed, 'owner_entry_failed_or_closed')
            try:
                self._usable(entry)
                observation = entry.handle.observe()
            except BaseException as primary:
                self._fail(entry, primary)
                raise
            e.need(observation['state'] == 'exited', 'owner_worker_still_running')
            try:
                receipt = entry.handle.reap()
                e.need(type(receipt) is workers.WorkerExit and
                       (receipt.task_id, receipt.run_id, receipt.pid) ==
                       (token.task_id, token.run_id, token.pid), 'owner_exit_identity')
                e.need(entry.process.returncode is None, 'owner_concurrent_waiter')
                entry.process.returncode = os.waitstatus_to_exitcode(receipt.raw_wait_status)
                entry.exit = receipt
                return receipt
            except BaseException as primary:
                self._fail(entry, primary)
                raise

    def close_reaped(self, token: OwnedWorker) -> None:
        """Close only a successfully reaped pidfd; retain token and Popen status."""
        entry = self._entry(token)
        with entry.lock:
            e.need(not entry.closed, 'owner_already_closed')
            e.need(not entry.failed, 'owner_entry_failed_or_closed')
            self._usable(entry)
            e.need(entry.exit is not None, 'owner_cannot_abandon_unreaped_child')
            # Recheck the accepted durable receipt before closing the descriptor.
            try:
                obs = entry.handle.observe()
                e.need(obs.get('state') == 'reaped' and obs.get('exit') == entry.exit,
                       'owner_exit_receipt_changed')
                entry.handle.close()
                entry.closed = True
            except BaseException as primary:
                self._fail(entry, primary)
                raise
