"""Offline full-native ordering tests, not owner/server helpers.

Real agent/registry/adapter/SQLite paths; synthetic claim and proof fixtures are
in review_fixture. Events determine the ordering; timeouts only bound failures.
No test provider calls. No inherited test methods or inflated suite counts.
"""
from __future__ import annotations

from concurrent.futures import CancelledError
import contextvars
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_worker_lifetime as base
from test_worker_abandonment import ObservedLock, PhaseClock


class ConnectionProbe:
    """Observe the real outer COMMIT, then delegate all actual database work."""
    def __init__(self, conn, before=None, after=None):
        self.conn, self.before, self.after = conn, before, after

    def execute(self, sql, *args, **kwargs):
        if sql == 'COMMIT' and self.before:
            self.before(self.conn)
        result = self.conn.execute(sql, *args, **kwargs)
        if sql == 'COMMIT' and self.after:
            self.after(self.conn)
        return result

    def __getattr__(self, name):
        return getattr(self.conn, name)


class NativeOrderingTests(unittest.TestCase):
    setUp = base.NativeLifecycleTests.setUp
    tearDown = base.NativeLifecycleTests.tearDown
    calls = base.NativeLifecycleTests.calls
    read = base.NativeLifecycleTests.read
    setup_review_fixture = base.NativeLifecycleTests.setup_review_fixture

    def rows(self):
        conn = self.review_connect(board='fixture')
        try:
            return {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                    for table in ('tasks', 'task_runs', 'task_events')}
        finally:
            conn.close()

    def spawn(self, callback):
        errors, results = [], []
        returned = threading.Event()
        def run():
            try:
                results.append(callback())
            except BaseException as exc:
                errors.append(exc)
            finally:
                returned.set()
        thread = threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True)
        thread.start()
        return thread, returned, errors, results

    def drain(self, thread, futures=()):
        thread.join(10)
        self.assertFalse(thread.is_alive(), 'test left its controller thread running')
        for future in list(futures):
            try:
                future.result(timeout=10)
            except (self.life.WorkerLifetimeError, CancelledError):
                pass
        self.assertTrue(all(f.done() for f in futures), 'test left worker futures undrained')

    def assert_rollback(self, before):
        self.assertEqual(self.rows(), before, 'all three complete tables must roll back')
        h = self.state.proof.module._handoff
        with self.assertRaises(h.HandoffError):
            h.load_handoff_seal('fixture', self.review_tid, 7)
        self.assertFalse(h.handoff_path('fixture', self.review_tid, 7).exists())

    def _late_revoke_before_commit(self, reason):
        args = self.setup_review_fixture(); before = self.rows()
        receipt_ready, resume = threading.Event(), threading.Event()
        h = self.state.proof.module._handoff; original = h.append_handoff_receipt
        def receipt(conn, seal):
            original(conn, seal)
            self.assertTrue(conn.in_transaction)
            receipt_ready.set()
            self.assertTrue(resume.wait(10), 'missing receipt release')
        with patch.object(h, 'append_handoff_receipt', side_effect=receipt):
            thread, returned, errors, results = self.spawn(lambda: self.agent._invoke_tool(
                'kanban_request_review', args, self.review_tid, tool_call_id='late-revoke'))
            try:
                self.assertTrue(receipt_ready.wait(10))
                self.assertEqual(self.rows(), before, 'uncommitted receipt must be invisible')
                if reason == 'close':
                    self.agent.close()
                else:
                    self.agent.interrupt('ordering test', hard_cancel=True)
                self.assertTrue(self.state.revoked.is_set())
                self.assertFalse(returned.is_set())
            finally:
                resume.set(); self.drain(thread)
        self.assertEqual(len(errors), 1); self.assertIsInstance(errors[0], self.life.WorkerLifetimeError)
        self.assertEqual(results, [])
        self.assert_rollback(before)
        print('ORDERING_EVIDENCE', json.dumps({'case': reason+'_after_receipt_before_fence',
              'revoked_before_release': True, 'tables_unchanged': True, 'receipt_authority': False}), flush=True)

    def test_close_after_receipt_before_commit_rolls_back(self):
        self._late_revoke_before_commit('close')

    def test_interrupt_after_receipt_before_commit_rolls_back(self):
        self._late_revoke_before_commit('interrupt')

    def _commit_wins(self, reason):
        args = self.setup_review_fixture()
        commit_entered, resume, contended = (threading.Event() for _ in range(3))
        def before_commit(conn):
            self.assertTrue(conn.in_transaction)
            commit_entered.set()
            self.assertTrue(resume.wait(10), 'missing COMMIT release')
        native_connect = self.review_connect
        def connect(*args, **kwargs):
            return ConnectionProbe(native_connect(*args, **kwargs), before=before_commit)
        def revoke():
            if reason == 'close': self.agent.close()
            else: self.agent.interrupt('ordering test', hard_cancel=True)
        with patch.object(self.review_kb, 'connect', side_effect=connect), \
             patch.object(self.state, 'decision_lock', ObservedLock(self.state.decision_lock, contended)):
            worker, done, errors, results = self.spawn(lambda: self.agent._invoke_tool(
                'kanban_request_review', args, self.review_tid, tool_call_id='commit-first'))
            closer = None
            try:
                self.assertTrue(commit_entered.wait(10))
                closer, close_done, close_errors, _ = self.spawn(revoke)
                self.assertTrue(contended.wait(10), 'close did not contend on the actual decision lock')
                self.assertFalse(close_done.is_set(), 'close returned before the winning COMMIT')
                self.assertFalse(self.state.revoked.is_set())
            finally:
                resume.set(); self.drain(worker)
                if closer is not None: self.drain(closer)
        self.assertEqual(close_errors, [])
        self.assertTrue(self.state.revoked.is_set())
        self.assertTrue(all(isinstance(e, self.life.WorkerLifetimeError) for e in errors), errors)
        h = self.state.proof.module._handoff
        seal = h.load_handoff_seal('fixture', self.review_tid, 7)
        self.assertEqual(seal['schema'], 3)
        rows = self.rows()
        self.assertEqual(rows['tasks'][0]['status'], 'review')
        self.assertIsNone(rows['tasks'][0]['worker_pid'])
        self.assertIsNone(rows['task_runs'][0]['worker_pid'])
        kinds = [r['kind'] for r in rows['task_events']]
        self.assertEqual(kinds.count('review_requested'), 1)
        self.assertEqual(kinds.count('factory_handoff_committed'), 1)
        with self.assertRaises(self.life.WorkerLifetimeError): self.read()
        print('ORDERING_EVIDENCE', json.dumps({'case': 'commit_before_'+reason, 'commit_durable': True,
              'revoked': True, 'tool_returned_success': bool(results), 'tool_returned_refusal': bool(errors)}), flush=True)

    def test_commit_wins_before_close_and_seal_remains_durable(self):
        self._commit_wins('close')

    def test_commit_wins_before_interrupt_and_seal_remains_durable(self):
        self._commit_wins('interrupt')

    def _executor_abandon(self, mode, reason):
        from agent import tool_executor as executor
        from tools.daemon_pool import DaemonThreadPoolExecutor
        ready, release, expired, contended = (threading.Event() for _ in range(4))
        effects, futures, messages = [], [], []
        original_submit = DaemonThreadPoolExecutor.submit
        def submit(pool, *args, **kwargs):
            f = original_submit(pool, *args, **kwargs); futures.append(f); return f
        def handler(**kwargs):
            effects.append(kwargs['path']); ready.set()
            self.assertTrue(release.wait(10), 'missing read handler release')
            return 'ALREADY_ADMITTED_EFFECT'
        original_wait = executor.concurrent.futures.wait
        controller_id = []
        def broken_wait(fs, *args, **kwargs):
            if reason == 'exception' and threading.get_ident() in controller_id:
                self.assertTrue(ready.wait(10))
                raise KeyboardInterrupt('injected executor wait interruption')
            return original_wait(fs, *args, **kwargs)
        original_result = executor.concurrent.futures.Future.result
        def broken_result(future, *args, **kwargs):
            if mode == 'sequential' and reason == 'exception' and threading.get_ident() in controller_id:
                self.assertTrue(ready.wait(10))
                raise KeyboardInterrupt('injected executor wait interruption')
            return original_result(future, *args, **kwargs)
        def run():
            controller_id.append(threading.get_ident())
            return getattr(executor, 'execute_tool_calls_'+mode)(
                self.agent, self.calls(2), messages, 't_fixture', finalize=False)
        manager = base.start.get_plugin_manager()
        with patch.object(manager, '_discovery_lock', ObservedLock(manager._discovery_lock, contended)), \
             patch.object(self.file_tools, 'read_file_tool', side_effect=handler), \
             patch.object(executor, 'time', PhaseClock(expired)), \
             patch.object(executor, '_resolve_sequential_tool_timeout', return_value=.1), \
             patch.object(executor, '_resolve_concurrent_tool_timeout', return_value=.1), \
             patch.object(executor, '_SEQUENTIAL_INTERRUPT_POLL_SECONDS', .02), \
             patch.object(DaemonThreadPoolExecutor, 'submit', submit), \
             patch.object(executor.concurrent.futures, 'wait', side_effect=broken_wait), \
             patch.object(executor.concurrent.futures.Future, 'result', new=broken_result):
            thread, returned, errors, _ = self.spawn(run)
            try:
                self.assertTrue(ready.wait(10))
                if mode == 'concurrent' and reason != 'exception': self.assertTrue(contended.wait(10))
                if reason == 'interrupt':
                    self.agent.interrupt('ordering test', hard_cancel=True)
                self.assertTrue(returned.wait(10), 'executor waited for the blocked handler after abandonment')
                self.assertTrue(self.state.revoked.is_set())
                self.assertEqual(len(effects), 1)
                # A new call must refuse without waiting on the held discovery lock.
                probe, probe_done, probe_errors, _ = self.spawn(self.read)
                try:
                    self.assertTrue(probe_done.wait(3), 'revoked generation blocked behind its abandoned handler')
                finally: probe.join(1)
                self.assertEqual(len(probe_errors), 1)
                self.assertIsInstance(probe_errors[0], self.life.WorkerLifetimeError)
                retained = json.loads(json.dumps(messages))
            finally:
                release.set(); self.drain(thread, futures)
        self.assertEqual(len(effects), 1)
        self.assertEqual(messages, retained, 'late worker mutated canonical returned messages')
        self.assertTrue(all(isinstance(e, (KeyboardInterrupt, self.life.WorkerLifetimeError)) for e in errors), errors)
        if reason == 'exception':
            self.assertTrue(errors)
            if mode == 'sequential':
                self.assertEqual(messages[0].get('effect_disposition'), 'unknown',
                                 'an admitted effect must not be labelled skipped/none')
                self.assertEqual(messages[1].get('effect_disposition'), 'none')
        else:
            self.assertEqual(errors, [])
            self.assertTrue(messages)
            self.assertEqual(messages[0].get('effect_disposition'), 'unknown')
        print('ORDERING_EVIDENCE', json.dumps({'case': mode+'_'+reason, 'effects': len(effects),
              'revoked': True, 'all_futures_drained': True, 'messages': messages}, default=str), flush=True)

    def test_sequential_interrupt_revokes_without_waiting_for_handler(self):
        self._executor_abandon('sequential', 'interrupt')

    def test_concurrent_interrupt_denies_queued_handler_and_preserves_unknown(self):
        self._executor_abandon('concurrent', 'interrupt')

    def test_sequential_wait_exception_revokes_and_does_not_join_hung_worker(self):
        self._executor_abandon('sequential', 'exception')

    def test_concurrent_wait_exception_revokes_and_does_not_join_hung_workers(self):
        self._executor_abandon('concurrent', 'exception')

    def _review_timeout(self, mode):
        from agent import tool_executor as executor
        from tools.daemon_pool import DaemonThreadPoolExecutor
        args = self.setup_review_fixture(); before = self.rows()
        ready, resume, expired = (threading.Event() for _ in range(3))
        messages, futures = [], []
        h = self.state.proof.module._handoff; original = h.append_handoff_receipt
        original_submit = DaemonThreadPoolExecutor.submit
        def submit(pool, *args, **kwargs):
            f=original_submit(pool,*args,**kwargs); futures.append(f); return f
        def receipt(conn, seal):
            original(conn, seal); self.assertTrue(conn.in_transaction); ready.set()
            self.assertTrue(resume.wait(10), 'missing review release')
        call = SimpleNamespace(tool_calls=[SimpleNamespace(id='review-timeout', function=SimpleNamespace(
            name='kanban_request_review', arguments=json.dumps(args)))])
        with patch.object(h, 'append_handoff_receipt', side_effect=receipt), \
             patch.object(executor, 'time', PhaseClock(expired)), \
             patch.object(executor, '_resolve_sequential_tool_timeout', return_value=.1), \
             patch.object(executor, '_resolve_concurrent_tool_timeout', return_value=.1), \
             patch.object(executor, '_SEQUENTIAL_INTERRUPT_POLL_SECONDS', .02), \
             patch.object(DaemonThreadPoolExecutor, 'submit', submit):
            thread, returned, errors, _ = self.spawn(lambda: getattr(executor,'execute_tool_calls_'+mode)(
                self.agent, call, messages, self.review_tid, finalize=False))
            try:
                self.assertTrue(ready.wait(10)); expired.set()
                self.assertTrue(returned.wait(10))
                self.assertTrue(self.state.revoked.is_set())
                self.assertEqual(self.rows(), before)
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0]['effect_disposition'], 'unknown')
            finally:
                resume.set(); self.drain(thread, futures)
        self.assertEqual(errors, [])
        self.assert_rollback(before)

    def test_sequential_timeout_after_receipt_prevents_commit(self): self._review_timeout('sequential')

    def test_concurrent_timeout_after_receipt_prevents_commit(self): self._review_timeout('concurrent')

    def test_raw_registered_adapter_without_admitted_operation_never_reaches_sql(self):
        args=self.setup_review_fixture(); before=self.rows()
        with patch.object(self.review_kb, 'request_review', wraps=self.review_kb.request_review) as sql:
            result=self.state.handlers['kanban_request_review'](args,task_id=self.review_tid)
        self.assertIn('error',json.loads(result));sql.assert_not_called()
        self.assert_rollback(before)

    def test_close_while_pre_hook_pending_prevents_handler_admission(self):
        ready, resume = threading.Event(), threading.Event()
        original=self.guard.on_pre_tool_call
        def pre(*args, **kwargs):
            result=original(*args,**kwargs);ready.set();self.assertTrue(resume.wait(10));return result
        with patch.object(self.guard,'on_pre_tool_call',side_effect=pre), \
             patch.object(self.file_tools,'read_file_tool') as effect:
            worker, returned, errors, _=self.spawn(self.read)
            try:
                self.assertTrue(ready.wait(10));self.agent.close()
                self.assertTrue(self.state.revoked.is_set());self.assertFalse(returned.is_set())
            finally:resume.set();self.drain(worker)
            effect.assert_not_called()
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],self.life.WorkerLifetimeError)

    def _commit_failure(self, after_durable):
        import sqlite3
        args=self.setup_review_fixture();before=self.rows()
        real_connect=self.review_connect
        def after(conn):
            raise OSError('controlled loss of commit acknowledgement')
        def connect(*args,**kwargs):
            conn=real_connect(*args,**kwargs)
            if after_durable:return ConnectionProbe(conn,after=after)
            conn.set_authorizer(lambda action,a,b,c,d: sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_TRANSACTION and a=='COMMIT' else sqlite3.SQLITE_OK)
            return conn
        with patch.object(self.review_kb,'connect',side_effect=connect):
            try:self.agent._invoke_tool('kanban_request_review',args,self.review_tid,tool_call_id='commit-failure')
            except self.life.WorkerLifetimeError:pass
        self.assertTrue(self.state.revoked.is_set(),'commit error/unknown outcome must revoke, not permit reuse')
        if after_durable:
            rows=self.rows()
            self.assertEqual(rows['tasks'][0]['status'],'review')
            self.assertIsNone(rows['task_runs'][0]['worker_pid'])
            self.assertEqual([r['kind'] for r in rows['task_events']].count('factory_handoff_committed'),1)
            h=self.state.proof.module._handoff
            self.assertFalse(h.handoff_path('fixture',self.review_tid,7).exists())
            with self.assertRaises(h.HandoffError):h.load_handoff_seal('fixture',self.review_tid,7)
        else:self.assert_rollback(before)
        with self.assertRaises(self.life.WorkerLifetimeError):self.read()

    def test_commit_sql_failure_revokes_and_rolls_back(self):self._commit_failure(False)

    def test_commit_acknowledgement_failure_revokes_without_fake_rollback(self):self._commit_failure(True)

    def test_close_is_idempotent_and_keeps_first_reason(self):
        self.agent.close();reason=self.state.failure_reason
        self.agent.close();self.life.abandon_worker(self.agent,'later timeout')
        self.assertTrue(self.state.revoked.is_set());self.assertEqual(reason,'agent closed')
        self.assertEqual(self.state.failure_reason,reason)

    def test_unbound_nonprotected_object_keeps_native_abandon_behavior(self):
        class Other: pass
        self.assertFalse(self.life.abandon_worker(Other(),'not a bound worker'))

    def _general_executor(self, mode, timeout):
        import os
        from agent import tool_executor as executor
        from tools.daemon_pool import DaemonThreadPoolExecutor
        general_home=self.fixture.root/'.hermes/profiles/general';general_home.mkdir(parents=True)
        (general_home/'config.yaml').write_text(json.dumps({'tools':{'tool_search':{'enabled':'off'}},
            'model':{'context_length':131072},'compression':{'enabled':False},
            'agent':{'environment_probe':False},'plugins':{'enabled':[]}}))
        ready,release,expired=(threading.Event() for _ in range(3));messages=[];futures=[]
        real_submit=DaemonThreadPoolExecutor.submit
        def submit(pool,*args,**kwargs):
            future=real_submit(pool,*args,**kwargs);futures.append(future);return future
        def handler(**kwargs):
            ready.set();self.assertTrue(release.wait(10));return 'GENERAL_EFFECT'
        with patch.dict(os.environ,HERMES_HOME=str(general_home),HERMES_PROFILE='general',
                        HERMES_KANBAN_TASK='',HERMES_KANBAN_RUN_ID=''):
            general=base.start.run_agent.AIAgent(enabled_toolsets=['file'],provider='bedrock',
                api_mode='bedrock_converse',model='local-fixture',api_key='test-not-real',
                base_url='http://127.0.0.1:9/v1',skip_memory=True,skip_context_files=True,
                skip_background_review=True,quiet_mode=True,platform='cli',max_iterations=1)
            try:
                self.assertIsNone(self.life.state_for(general))
                with patch.object(executor,'time',PhaseClock(expired)), \
                     patch.object(executor,'_resolve_sequential_tool_timeout',return_value=.1), \
                     patch.object(executor,'_resolve_concurrent_tool_timeout',return_value=.1), \
                     patch.object(executor,'_SEQUENTIAL_INTERRUPT_POLL_SECONDS',.02), \
                     patch.object(DaemonThreadPoolExecutor,'submit',submit), \
                     patch.object(self.file_tools,'read_file_tool',side_effect=handler):
                    thread,returned,errors,_=self.spawn(lambda:getattr(executor,'execute_tool_calls_'+mode)(
                        general,self.calls(1),messages,'general-task',finalize=False))
                    try:
                        self.assertTrue(ready.wait(10))
                        if timeout:expired.set()
                        else:release.set()
                        self.assertTrue(returned.wait(10))
                        self.assertIsNone(self.life.state_for(general))
                    finally:release.set();self.drain(thread,futures)
                self.assertEqual(errors,[]);self.assertEqual(len(messages),1)
                if timeout:self.assertEqual(messages[0].get('effect_disposition'),'unknown')
                else:self.assertEqual(messages[0]['content'],'GENERAL_EFFECT')
                # The new protected-generation policy is not imposed on a
                # genuine general-profile agent after its native timeout.
                result=general._invoke_tool('read_file',{'path':str(self.path)},'general-task',tool_call_id='general-after')
                self.assertIn('FACTORY_LIFETIME_READ_OK',result)
                self.assertFalse(self.life.abandon_worker(general,'not protected'))
            finally:general.close()

    def test_general_sequential_success_preserves_native_behavior(self):self._general_executor('sequential',False)

    def test_general_concurrent_success_preserves_native_behavior(self):self._general_executor('concurrent',False)

    def test_general_sequential_timeout_does_not_impose_protected_policy(self):self._general_executor('sequential',True)

    def test_general_concurrent_timeout_does_not_impose_protected_policy(self):self._general_executor('concurrent',True)

    def test_handler_baseexception_revokes_before_native_normalization(self):
        with patch.object(self.file_tools,'read_file_tool',side_effect=SystemExit('controlled handler failure')):
            with self.assertRaises(SystemExit):self.read()
        self.assertTrue(self.state.revoked.is_set())
        with self.assertRaises(self.life.WorkerLifetimeError):self.read()


if __name__ == '__main__': unittest.main(verbosity=2)
