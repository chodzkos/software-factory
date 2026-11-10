"""Offline, native-path regressions for the next lifetime authorization gate.

These are explicit red-gate tests, not owner/server helpers. Deadlines are
advanced only after phase events; watchdogs bound a broken test, not authority.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import CancelledError
import json
import threading
import time as real_time
import unittest
from unittest.mock import patch

import test_worker_lifetime as base


class PhaseClock:
    def __init__(self, expired: threading.Event):
        self.expired = expired

    def monotonic(self):
        return 10.0 if self.expired.is_set() else 0.0

    def __getattr__(self, name):
        return getattr(real_time, name)


class ObservedLock:
    """Delegate to the actual RLock, recording a real contended acquisition."""
    def __init__(self, lock, contended: threading.Event):
        self.lock, self.contended = lock, contended

    def acquire(self, blocking=True, timeout=-1):
        if self.lock.acquire(False):
            return True
        self.contended.set()
        if not blocking:
            return False
        return self.lock.acquire(True, timeout)

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


class NativeAbandonmentTests(unittest.TestCase):
    # Share construction/cleanup only; inheriting the original test class would
    # silently duplicate all its tests and inflate the reported test count.
    setUp = base.NativeLifecycleTests.setUp
    tearDown = base.NativeLifecycleTests.tearDown
    calls = base.NativeLifecycleTests.calls
    read = base.NativeLifecycleTests.read
    setup_review_fixture = base.NativeLifecycleTests.setup_review_fixture

    def _abandoned_executor(self, mode):
        from agent import tool_executor as executor_module
        from tools.daemon_pool import DaemonThreadPoolExecutor

        entered, release, expired, returned, contended = (
            threading.Event() for _ in range(5)
        )
        effects, futures, errors, messages = [], [], [], []
        manager = base.start.get_plugin_manager()
        executor = getattr(executor_module, 'execute_tool_calls_' + mode)
        submitted = self.calls(2 if mode == 'concurrent' else 1)
        real_submit = DaemonThreadPoolExecutor.submit

        def record_submit(pool, *args, **kwargs):
            future = real_submit(pool, *args, **kwargs)
            futures.append(future)
            return future

        def handler(**kwargs):
            effects.append(kwargs['path'])
            entered.set()
            if not release.wait(10):
                raise AssertionError('handler release phase missing')
            return 'CONTROLLED_ALREADY_STARTED_READ'

        def execute():
            try:
                executor(self.agent, submitted, messages, 't_fixture', finalize=False)
            except BaseException as exc:
                errors.append(type(exc).__name__ + ': ' + str(exc))
            finally:
                returned.set()

        captured = {}
        thread = threading.Thread(target=contextvars.copy_context().run,
                                  args=(execute,), daemon=True)
        with patch.object(manager, '_discovery_lock', ObservedLock(manager._discovery_lock, contended)), \
             patch.object(executor_module, 'time', PhaseClock(expired)), \
             patch.object(executor_module, '_resolve_sequential_tool_timeout', return_value=0.1), \
             patch.object(executor_module, '_resolve_concurrent_tool_timeout', return_value=0.1), \
             patch.object(executor_module, '_SEQUENTIAL_INTERRUPT_POLL_SECONDS', 0.02), \
             patch.object(DaemonThreadPoolExecutor, 'submit', record_submit), \
             patch.object(self.file_tools, 'read_file_tool', side_effect=handler):
            thread.start()
            try:
                self.assertTrue(entered.wait(10), 'actual registry handler did not start')
                if mode == 'concurrent':
                    self.assertTrue(contended.wait(10), 'second worker never waited on actual discovery lock')
                self.assertEqual(len(effects), 1, 'serialization must hold the other handler')
                expired.set()  # No wall-clock guess decides when the deadline expires.
                self.assertTrue(returned.wait(10), 'native executor did not return its timeout')
                captured.update(
                    mode=mode,
                    actual_handler_count_at_return=len(effects),
                    handler_still_blocked=not release.is_set(),
                    revoked_at_return=self.state.revoked.is_set(),
                    errors_at_return=list(errors),
                    messages_at_return=list(messages),
                )
            finally:
                release.set()
                thread.join(10)
                for future in list(futures):
                    try:
                        future.result(timeout=10)
                    except (self.life.WorkerLifetimeError, CancelledError):
                        pass
                self.assertFalse(thread.is_alive(), 'executor leaked after fixture release')
        captured['actual_handler_count_after_drain'] = len(effects)
        captured['all_futures_drained'] = all(f.done() for f in futures)
        captured['revoked_after_drain'] = self.state.revoked.is_set()
        print('ABANDONMENT_PHASE_EVIDENCE ' + json.dumps(captured, sort_keys=True), flush=True)
        self.assertTrue(captured['all_futures_drained'])
        self.assertTrue(all(error.startswith('WorkerLifetimeError:') for error in errors), errors)
        self.assertTrue(captured['revoked_at_return'],
                        'timeout abandoned a running protected handler without revoking this agent generation')
        self.assertEqual(captured['actual_handler_count_after_drain'], 1,
                         'queued protected handler started after the batch was abandoned')
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.read()

    def test_sequential_timeout_revokes_before_returning_abandoned_operation(self):
        self._abandoned_executor('sequential')

    def test_concurrent_timeout_revokes_and_refuses_discovery_lock_waiter(self):
        self._abandoned_executor('concurrent')

    def test_actual_close_after_native_sql_refuses_commit_and_receipt(self):
        args = self.setup_review_fixture()

        def rows():
            conn = self.review_connect(board='fixture')
            try:
                return {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                        for table in ('tasks', 'task_runs', 'task_events')}
            finally:
                conn.close()

        before = rows()
        original = self.review_kb.request_review
        phases = []

        def transition(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result, (True, None))
            self.assertTrue(args[0].conn.in_transaction, 'SQL phase is outside the real outer transaction')
            phases.append('native_sql_completed_inside_outer_transaction')
            self.agent.close()  # Actual public lifecycle method; no real provider.
            phases.append('actual_agent_close_returned')
            self.assertTrue(self.state.revoked.is_set())
            return result

        with patch.object(self.review_kb, 'request_review', side_effect=transition) as native:
            with self.assertRaises(self.life.WorkerLifetimeError):
                self.agent._invoke_tool('kanban_request_review', args, self.review_tid,
                                        tool_call_id='native_review_close')
            self.assertEqual(native.call_count, 1)
        after = rows()
        handoff = self.state.proof.module._handoff
        try:
            handoff.load_handoff_seal('fixture', self.review_tid, 7)
        except handoff.HandoffError:
            seal_accepted = False
        else:
            seal_accepted = True
        print('CLOSE_TRANSACTION_EVIDENCE ' + json.dumps({
            'phases': phases,
            'revoked': self.state.revoked.is_set(),
            'tables_unchanged': {table: after[table] == before[table] for table in before},
            'event_kinds_before': [row['kind'] for row in before['task_events']],
            'event_kinds_after': [row['kind'] for row in after['task_events']],
            'seal_accepted_by_separate_connection': seal_accepted,
        }, sort_keys=True), flush=True)
        self.assertEqual(after, before, 'close before COMMIT must leave all three tables unchanged')
        self.assertFalse(seal_accepted, 'revoked operation produced a committed authorizing seal')


if __name__ == '__main__':
    unittest.main(verbosity=2)
