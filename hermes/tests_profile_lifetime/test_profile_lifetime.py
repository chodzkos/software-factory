"""Reviewer/release lifetime: real constructors, plugin generations and SQLite.

CLI attestation and dispatcher claims are synthetic; no models or network.
"""
from __future__ import annotations
from contextlib import contextmanager
import contextvars
import threading
import time
from concurrent.futures import CancelledError
from types import SimpleNamespace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
# Match retained isolated import setup, before importing native agent modules.
spec=importlib.util.spec_from_file_location('profile_lifetime_import',ROOT/'hermes/tests_worker_start/test_native_worker_start.py')
prior=importlib.util.module_from_spec(spec);sys.modules[spec.name]=prior;spec.loader.exec_module(prior)
from hermes.tests_support.protected_profile_fixture import NativeProtectedProfile
from hermes.tests_support.native_review_fixture import NativeReviewWorker
import factory_worker_lifetime as life


def tables(conn):
    return {n:[tuple(r) for r in conn.execute(f'SELECT * FROM {n} ORDER BY rowid')]
            for n in ('tasks','task_runs','task_events')}


@contextmanager
def approval_fixture():
    with tempfile.TemporaryDirectory(prefix='sf-approval-life-') as td:
        with NativeReviewWorker(Path(td),task_id='t_approval',run_id=1) as worker:
            worker.attest();seal=worker.publish();worker.start_reviewer(seal)
            with NativeProtectedProfile(Path(td),'reviewer-gpt',database=worker) as reviewer:
                # PID reuse simulation, unchanged seal and genuine committed receipt.
                with patch.object(reviewer.handoff,'_process_identity',return_value=(seal['implementer_pid'],str(int(seal['implementer_proc_start'])+1))):
                    with worker.kb.connect_closing(board=worker.board) as conn:
                        assert reviewer.handoff.reviewer_completion_authorized(content_state=reviewer.handoff.workspace_content_state)
                        yield worker,reviewer,conn


class ProfileLifetimeTests(unittest.TestCase):
    def _bound(self,profile):
        with tempfile.TemporaryDirectory() as td, NativeProtectedProfile(Path(td),profile) as fx:
            self.assertIsNotNone(fx.state,'successful protected profile needs persistent lifetime')
            self.assertIn('PROFILE_READ_OK',fx.read())

    def test_reviewer_constructor_binds_lifetime(self): self._bound('reviewer-gpt')
    def test_release_constructor_binds_lifetime(self): self._bound('release-manager')

    def _unload(self,profile):
        for plugin in ('factory-execution-guards','factory-repository-readonly'):
            with self.subTest(plugin=plugin),tempfile.TemporaryDirectory() as td, NativeProtectedProfile(Path(td),profile) as fx:
                fx.manager.unload(plugin)
                with patch.object(prior.registry,'_dispatch_handler',side_effect=AssertionError('UNGUARDED_HANDLER')) as handler:
                    with self.assertRaises(life.WorkerLifetimeError):
                        fx.read()
                handler.assert_not_called()

    def test_reviewer_unload_either_plugin_refuses_before_handler(self): self._unload('reviewer-gpt')
    def test_release_unload_either_plugin_refuses_before_handler(self): self._unload('release-manager')

    def _closed(self,profile):
        with tempfile.TemporaryDirectory() as td, NativeProtectedProfile(Path(td),profile) as fx:
            fx.agent.close()
            with patch.object(prior.registry,'_dispatch_handler',side_effect=AssertionError('CLOSED_HANDLER')) as handler:
                with self.assertRaises(life.WorkerLifetimeError): fx.read()
            handler.assert_not_called()

    def test_reviewer_close_refuses_further_read(self): self._closed('reviewer-gpt')
    def test_release_close_refuses_further_read(self): self._closed('release-manager')

    def test_raw_approval_without_operation_refuses_before_sql(self):
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn)
            with patch.object(worker.kb,'complete_task',wraps=worker.kb.complete_task) as sql:
                with self.assertRaises(life.WorkerLifetimeError):
                    reviewer.handoff.guarded_reviewer_complete({'summary':'RAW_FORBIDDEN'})
            sql.assert_not_called()
            self.assertEqual(tables(conn),before)

    def test_close_after_real_approval_sql_prevents_commit(self):
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn); observed=[]; native=worker.kb.complete_task
            def complete(*args,**kw):
                result=native(*args,**kw)
                self.assertIs(result,True)
                self.assertTrue(args[0].in_transaction is False) # released native savepoint
                self.assertEqual(tables(conn),before,'SQL must still be uncommitted')
                observed.append('sql')
                reviewer.agent.close()
                return result
            with patch.object(worker.kb,'complete_task',side_effect=complete):
                try: result=reviewer.approve()
                except life.WorkerLifetimeError: result='LIFETIME_REFUSED'
            self.assertEqual(observed,['sql'],'the test must reach real SQL before close')
            self.assertEqual(tables(conn),before,'closed reviewer must not commit its approval')
            self.assertNotIn('"ok": true',result)


class PhaseClock:
    def __init__(self, expired): self.expired=expired
    def monotonic(self): return 10.0 if self.expired.is_set() else 0.0
    def __getattr__(self,name): return getattr(time,name)


class ObservedLock:
    def __init__(self,lock,event): self.lock,self.event=lock,event
    def acquire(self,blocking=True,timeout=-1):
        if self.lock.acquire(False): return True
        self.event.set()
        return self.lock.acquire(True,timeout) if blocking else False
    def release(self): self.lock.release()
    def __enter__(self): self.acquire();return self
    def __exit__(self,*_): self.release()


class ConnectionProbe:
    def __init__(self,conn,before=None,after=None): self.conn,self.before,self.after=conn,before,after
    def execute(self,sql,*args,**kw):
        if sql=='COMMIT' and self.before: self.before(self.conn)
        value=self.conn.execute(sql,*args,**kw)
        if sql=='COMMIT' and self.after: self.after(self.conn)
        return value
    def __getattr__(self,name): return getattr(self.conn,name)


class ProfileOrderingTests(unittest.TestCase):
    def spawn(self,callback):
        done=threading.Event();errors=[];results=[]
        def run():
            try: results.append(callback())
            except BaseException as exc: errors.append(exc)
            finally: done.set()
        thread=threading.Thread(target=contextvars.copy_context().run,args=(run,),daemon=True)
        thread.start();return thread,done,errors,results

    def drain(self,thread,futures=()):
        thread.join(10);self.assertFalse(thread.is_alive(),'controller thread not joined')
        for f in list(futures):
            try: f.result(timeout=10)
            except (life.WorkerLifetimeError,CancelledError): pass
        self.assertTrue(all(f.done() for f in futures),'worker futures not drained')

    def test_unchanged_approval_commits_and_downstream_reader_accepts(self):
        with approval_fixture() as (worker,reviewer,conn):
            seal=reviewer.handoff.load_handoff_seal(worker.board,worker.task_id,1)
            self.assertTrue(json.loads(reviewer.approve())['ok'])
            self.assertEqual(worker.kb.get_task(conn,worker.task_id).status,'done')
            self.assertEqual(conn.execute('SELECT status FROM task_runs WHERE id=2').fetchone()[0],'done')
            self.assertFalse(reviewer.state.revoked.is_set())
            self.assertTrue(reviewer.handoff.verify_downstream_approval(worker.board,worker.task_id)['ok'])
            self.assertEqual(reviewer.handoff.load_handoff_seal(worker.board,worker.task_id,1),seal)

    def _late_close(self,reason):
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn);ready=threading.Event();resume=threading.Event()
            original=life.commit_approval
            def at_fence(*args,**kw):
                self.assertTrue(args[1].in_transaction)
                self.assertEqual(args[1].execute('SELECT status FROM task_runs WHERE id=2').fetchone()[0],'done')
                ready.set();self.assertTrue(resume.wait(10));return original(*args,**kw)
            with patch.object(life,'commit_approval',side_effect=at_fence):
                thread,done,errors,results=self.spawn(reviewer.approve)
                try:
                    self.assertTrue(ready.wait(10),'approval never reached final COMMIT gate')
                    self.assertEqual(tables(conn),before)
                    if reason=='close': reviewer.agent.close()
                    else: reviewer.agent.interrupt('approval test',hard_cancel=True)
                    self.assertTrue(reviewer.state.revoked.is_set());self.assertFalse(done.is_set())
                finally: resume.set();self.drain(thread)
            self.assertEqual(results,[]);self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],life.WorkerLifetimeError)
            self.assertEqual(tables(conn),before)
            print('APPROVAL_EVIDENCE',json.dumps(dict(case=reason+'_before_commit',tables_unchanged=True)),flush=True)

    def test_close_at_final_approval_fence_rolls_back(self): self._late_close('close')
    def test_interrupt_at_final_approval_fence_rolls_back(self): self._late_close('interrupt')

    def _commit_wins(self,reason):
        with approval_fixture() as (worker,reviewer,conn):
            ready,resume,contended=(threading.Event() for _ in range(3))
            def before_commit(c):
                self.assertTrue(c.in_transaction);ready.set();self.assertTrue(resume.wait(10))
            def connect(*args,**kw): return ConnectionProbe(worker.connect(*args,**kw),before=before_commit)
            def revoke():
                if reason=='close': reviewer.agent.close()
                else: reviewer.agent.interrupt('approval test',hard_cancel=True)
            with patch.object(worker.kb,'connect',side_effect=connect),patch.object(reviewer.state,'decision_lock',ObservedLock(reviewer.state.decision_lock,contended)):
                thread,done,errors,results=self.spawn(reviewer.approve);closer=None
                try:
                    self.assertTrue(ready.wait(10),'outer COMMIT never started')
                    closer,closed,close_errors,_=self.spawn(revoke)
                    self.assertTrue(contended.wait(10),'revoke did not contend on actual COMMIT lock')
                    self.assertFalse(closed.is_set());self.assertFalse(reviewer.state.revoked.is_set())
                finally:
                    resume.set();self.drain(thread)
                    if closer is not None:self.drain(closer)
            self.assertEqual(close_errors,[]);self.assertTrue(reviewer.state.revoked.is_set())
            self.assertTrue(all(isinstance(e,life.WorkerLifetimeError) for e in errors),errors)
            self.assertEqual(worker.kb.get_task(conn,worker.task_id).status,'done')
            self.assertEqual(conn.execute('SELECT status FROM task_runs WHERE id=2').fetchone()[0],'done')
            self.assertTrue(reviewer.handoff.verify_downstream_approval(worker.board,worker.task_id)['ok'])
            with self.assertRaises(life.WorkerLifetimeError):reviewer.read()
            print('APPROVAL_EVIDENCE',json.dumps(dict(case='commit_before_'+reason,durable=True,revoked=True,returned_success=bool(results))),flush=True)

    def test_approval_commit_wins_before_close_remains_durable(self): self._commit_wins('close')
    def test_approval_commit_wins_before_interrupt_remains_durable(self): self._commit_wins('interrupt')

    def _timeout(self,mode):
        from agent import tool_executor as executor
        from tools.daemon_pool import DaemonThreadPoolExecutor
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn);ready,resume,expired=(threading.Event() for _ in range(3))
            messages=[];futures=[];original=life.commit_approval;native_submit=DaemonThreadPoolExecutor.submit
            def at_fence(*args,**kw):
                self.assertTrue(args[1].in_transaction);ready.set()
                self.assertTrue(resume.wait(10));return original(*args,**kw)
            def submit(pool,*args,**kw):
                f=native_submit(pool,*args,**kw);futures.append(f);return f
            call=SimpleNamespace(tool_calls=[SimpleNamespace(id='approval-timeout',function=SimpleNamespace(name='factory_review_approve',arguments=json.dumps({'summary':'OFFLINE'})))])
            # Disable only health telemetry: _touch_activity writes independent Kanban
            # heartbeats, not the approval transaction under test. SQL stays real.
            with patch.object(reviewer.agent,'_touch_activity'),patch.object(life,'commit_approval',side_effect=at_fence),patch.object(executor,'time',PhaseClock(expired)),patch.object(executor,'_resolve_sequential_tool_timeout',return_value=.1),patch.object(executor,'_resolve_concurrent_tool_timeout',return_value=.1),patch.object(executor,'_SEQUENTIAL_INTERRUPT_POLL_SECONDS',.02),patch.object(DaemonThreadPoolExecutor,'submit',submit):
                thread,done,errors,_=self.spawn(lambda:getattr(executor,'execute_tool_calls_'+mode)(reviewer.agent,call,messages,worker.task_id,finalize=False))
                try:
                    self.assertTrue(ready.wait(10));expired.set();self.assertTrue(done.wait(10),'executor waited for abandoned approval')
                    self.assertTrue(reviewer.state.revoked.is_set());self.assertEqual(tables(conn),before)
                    self.assertEqual(len(messages),1);self.assertEqual(messages[0].get('effect_disposition'),'unknown')
                    retained=json.loads(json.dumps(messages))
                finally:resume.set();self.drain(thread,futures)
            self.assertEqual(errors,[]);self.assertEqual(tables(conn),before)
            self.assertEqual(messages,retained,'late completion rewrote timeout result')

    def test_sequential_approval_timeout_prevents_commit(self): self._timeout('sequential')
    def test_concurrent_approval_timeout_prevents_commit(self): self._timeout('concurrent')

    def _commit_error(self,durable):
        import sqlite3
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn);events=[]
            def after(c): events.append('committed');raise OSError('LOST_ACK_TEST')
            def connect(*args,**kw):
                c=worker.connect(*args,**kw)
                if durable:return ConnectionProbe(c,after=after)
                def auth(action,a,*_):
                    if action==sqlite3.SQLITE_TRANSACTION and a=='COMMIT':
                        events.append('denied');return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                c.set_authorizer(auth);return c
            with patch.object(worker.kb,'connect',side_effect=connect):
                with self.assertRaises(life.WorkerLifetimeError):reviewer.approve()
            self.assertTrue(reviewer.state.revoked.is_set())
            self.assertIn('outcome unknown',reviewer.state.failure_reason)
            self.assertEqual(events,['committed' if durable else 'denied'])
            if durable:
                self.assertEqual(worker.kb.get_task(conn,worker.task_id).status,'done')
                self.assertTrue(reviewer.handoff.verify_downstream_approval(worker.board,worker.task_id)['ok'])
            else:self.assertEqual(tables(conn),before)
            print('APPROVAL_EVIDENCE',json.dumps(dict(case='lost_ack' if durable else 'commit_denied',durable=durable,revoked=True)),flush=True)

    def test_approval_commit_denied_revokes_and_rolls_back(self): self._commit_error(False)
    def test_approval_lost_ack_revokes_but_retains_durable_approval(self): self._commit_error(True)

    def test_same_thread_unload_after_sql_rolls_back(self):
        for plugin in ('factory-execution-guards','factory-repository-readonly'):
            with self.subTest(plugin=plugin),approval_fixture() as (worker,reviewer,conn):
                before=tables(conn);observed=[];original=worker.kb.complete_task
                def complete(*args,**kw):
                    result=original(*args,**kw);self.assertTrue(result);observed.append('SQL')
                    self.assertEqual(tables(conn),before);reviewer.manager.unload(plugin);return result
                with patch.object(worker.kb,'complete_task',side_effect=complete):
                    with self.assertRaises(life.WorkerLifetimeError):reviewer.approve()
                self.assertEqual(observed,['SQL']);self.assertEqual(tables(conn),before)


class ProfileBoundaryTests(unittest.TestCase):
    def _drift(self,profile,field):
        with tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile) as fx:
            initial=os.environ.get(field);os.environ[field]='different_binding'
            with self.assertRaises(life.WorkerLifetimeError):fx.read()
            self.assertTrue(fx.state.revoked.is_set())
            if initial is None:os.environ.pop(field,None)
            else:os.environ[field]=initial
            with self.assertRaises(life.WorkerLifetimeError):fx.read()
    def test_reviewer_task_drift_revokes_and_restore_does_not_rearm(self): self._drift('reviewer-gpt','HERMES_KANBAN_TASK')
    def test_reviewer_board_drift_revokes_and_restore_does_not_rearm(self): self._drift('reviewer-gpt','HERMES_KANBAN_BOARD')
    def test_release_workspace_drift_revokes_and_restore_does_not_rearm(self): self._drift('release-manager','FACTORY_RELEASE_WORKSPACE')
    def test_release_task_drift_revokes_and_restore_does_not_rearm(self): self._drift('release-manager','FACTORY_RELEASE_TASK')

    def test_both_pre_hooks_and_guard_post_are_mandatory_exactly_once(self):
        # Underlying functions are wrapped, not the captured callback identities.
        for profile in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile) as fx:
                from hermes_cli.plugins import invoke_hook
                # readonly exports a direct function alias: rebinding the defining
                # module would NOT observe it. Trace the original code object.
                seen=[];code=fx.readonly.on_pre_tool_call.__code__;old=sys.getprofile()
                def trace(frame,event,arg):
                    if event=='call' and frame.f_code is code:seen.append('readonly-pre')
                with patch.object(fx.module._guard,'on_pre_tool_call',wraps=fx.module._guard.on_pre_tool_call) as pre,patch.object(fx.module._guard,'on_post_tool_call',wraps=fx.module._guard.on_post_tool_call) as post:
                    sys.setprofile(trace)
                    try:
                        self.assertIn('PROFILE_READ_OK',fx.read())
                        self.assertEqual(invoke_hook('pre_tool_call',tool_name='factory_repo_read',args={}),[])
                        self.assertEqual(invoke_hook('post_tool_call',tool_name='factory_repo_read',args={},result='ignored'),[])
                    finally:sys.setprofile(old)
                self.assertEqual(pre.call_count,1);self.assertEqual(post.call_count,1);self.assertEqual(seen,['readonly-pre'])

    def test_raw_registry_is_forbidden_for_both_profiles(self):
        for profile in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile):
                with self.assertRaises(life.WorkerLifetimeError):prior.registry.dispatch('factory_repo_read',{'path':'tracked.txt'})

    def test_release_has_no_approval_capability(self):
        with tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),'release-manager') as fx:
            with patch.object(fx.handoff,'guarded_reviewer_complete',side_effect=AssertionError('APPROVAL_FORBIDDEN')) as approve:
                with self.assertRaises(life.WorkerLifetimeError):fx.approve()
            approve.assert_not_called()

    def test_raw_bound_approval_handler_does_not_reach_sql(self):
        with approval_fixture() as (worker,reviewer,conn):
            before=tables(conn)
            with patch.object(worker.kb,'complete_task',wraps=worker.kb.complete_task) as sql:
                result=reviewer.state.handlers['factory_review_approve']({'summary':'RAW'})
            self.assertIn('error',json.loads(result));sql.assert_not_called();self.assertEqual(tables(conn),before)

    def _foreign_owner(self,table,field,value):
        with approval_fixture() as (worker,reviewer,conn):
            # Reassignment happened BEFORE the approval began; retain that state.
            key='id' if table=='task_runs' else 'current_run_id'
            conn.execute(f'UPDATE {table} SET {field}=? WHERE {key}=2',(value,));conn.commit()
            before=tables(conn)
            with patch.object(worker.kb,'complete_task',wraps=worker.kb.complete_task) as complete:
                result=json.loads(reviewer.approve())
            self.assertIn('error',result,'stale reviewer must not approve after ownership changes')
            complete.assert_not_called();self.assertEqual(tables(conn),before)

    def test_reassigned_task_pid_cannot_approve(self): self._foreign_owner('tasks','worker_pid',os.getpid()+100000)
    def test_reassigned_run_pid_cannot_approve(self): self._foreign_owner('task_runs','worker_pid',os.getpid()+100000)
    def test_missing_claim_lock_cannot_approve(self): self._foreign_owner('tasks','claim_lock',None)
    def test_mismatched_claim_lock_cannot_approve(self): self._foreign_owner('task_runs','claim_lock','other-owner')

    def test_missing_constructor_proof_refuses_all_three_ambient_profiles(self):
        for profile in ('coder-claude','reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),patch.dict(os.environ,{'HERMES_PROFILE':profile}):
                with self.assertRaisesRegex(life.WorkerLifetimeError,'constructor proof'):life.check_agent(None)

class ProfileOperationTests(unittest.TestCase):
    def test_copied_context_cannot_use_approval_from_another_thread(self):
        with approval_fixture() as (worker,reviewer,conn):
            original=life.commit_approval;observed=[]
            def at_commit(*args,**kw):
                done=threading.Event();errors=[]
                def foreign():
                    try:life.approval_operation(reviewer.handoff.guarded_reviewer_complete)
                    except BaseException as exc:errors.append(exc)
                    finally:done.set()
                thread=threading.Thread(target=contextvars.copy_context().run,args=(foreign,),daemon=True)
                thread.start();thread.join(3)
                self.assertFalse(thread.is_alive(),'foreign thread waited for owned discovery lock')
                self.assertTrue(done.is_set());self.assertEqual(len(errors),1)
                self.assertIsInstance(errors[0],life.WorkerLifetimeError)
                observed.append('foreign refused');return original(*args,**kw)
            with patch.object(life,'commit_approval',side_effect=at_commit):
                self.assertTrue(json.loads(reviewer.approve())['ok'])
            self.assertEqual(observed,['foreign refused'])
            self.assertFalse(reviewer.state.revoked.is_set())

    def test_finished_approval_operation_cannot_commit_again(self):
        with approval_fixture() as (worker,reviewer,conn):
            operations=[];original=life.commit_approval
            def at_commit(op,*args,**kw):operations.append(op);return original(op,*args,**kw)
            with patch.object(life,'commit_approval',side_effect=at_commit):
                self.assertTrue(json.loads(reviewer.approve())['ok'])
            self.assertEqual(len(operations),1);op=operations[0];self.assertTrue(op.finished)
            token=life._CURRENT.set(op)
            try:
                from unittest.mock import Mock
                target=Mock(wraps=conn)
                with self.assertRaises(life.WorkerLifetimeError):
                    life.commit_approval(op,target,reviewer.handoff.guarded_reviewer_complete)
                target.execute.assert_not_called()
            finally:life._CURRENT.reset(token)

    def test_replaced_readonly_callback_revokes_even_after_restoration(self):
        for profile in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile) as fx:
                with patch.object(fx.readonly,'on_pre_tool_call',lambda *a,**kw:None):
                    with self.assertRaises(life.WorkerLifetimeError):fx.read()
                self.assertTrue(fx.state.revoked.is_set())
                with self.assertRaises(life.WorkerLifetimeError):fx.read()

    def test_both_profiles_real_builder_and_final_capability_validation(self):
        import copy
        from unittest.mock import Mock
        for profile in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile) as fx:
                payload=fx.agent._build_api_kwargs([]);callback=Mock(return_value='OFFLINE_CALLBACK')
                self.assertIn(fx.state.envelope(payload),fx.state.payloads)
                self.assertEqual(life.checked_model_call(fx.agent,payload,callback),'OFFLINE_CALLBACK')
                callback.assert_called_once();self.assertFalse(fx.state.revoked.is_set())
                changed=copy.deepcopy(payload);changed['extra_body']={'tools':[{'type':'unapproved'}]}
                with self.assertRaises(life.WorkerLifetimeError):life.checked_model_call(fx.agent,changed,callback)
                callback.assert_called_once();self.assertTrue(fx.state.revoked.is_set())

    def test_both_profiles_closed_model_call_never_reaches_client(self):
        from unittest.mock import Mock
        for profile in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as td,NativeProtectedProfile(Path(td),profile) as fx:
                payload=fx.agent._build_api_kwargs([]);callback=Mock();fx.agent.close()
                with self.assertRaises(life.WorkerLifetimeError):life.checked_model_call(fx.agent,payload,callback)
                callback.assert_not_called()

    def _moa_start(self,resolved):
        spec=importlib.util.spec_from_file_location('profile_moa_start_fixture',ROOT/'hermes/tests_review_revisions/test_profile_start.py')
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        fx=module.ProfileStartTests()
        for name in ('reviewer-gpt','release-manager'):
            with self.subTest(profile=name),fx.profile(name,real_catalog=True):
                if resolved:fx.transform=lambda a:setattr(a,'provider','moa')
                with self.assertRaisesRegex(prior.boundary.WorkerStartError,'MoA'):
                    module.fx.run_agent.AIAgent(enabled_toolsets=[module.GUARD,module.READONLY,'kanban'],
                        disabled_toolsets=sorted(module.DISABLED),provider='bedrock' if resolved else ' MoA ')
                self.assertEqual(fx.called,1 if resolved else 0)

    def test_both_profiles_explicit_moa_refused_before_initializer(self):self._moa_start(False)
    def test_both_profiles_resolved_moa_refused_before_lifetime_binding(self):self._moa_start(True)

if __name__=='__main__': unittest.main()
