"""Spawn ordering and single-reaper checks for repository_parent_native_spawn_v1 (source-only).

The release path runs on a spawner built without its constructor, with doubles for the
controller, the native row checks and the process identity. Real things here are the
exec-gate pipe, the release token write and the native hook call order. No worker, model or
native database is involved.
"""
from __future__ import annotations
import ast
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

HERMES = Path(__file__).resolve().parents[1]
PARENT = HERMES / 'parent'
SPAWN = PARENT / 'repository_parent_native_spawn_v1.py'
sys.path[:0] = [str(PARENT), str(HERMES), str(Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True))]
import repository_execution_io as e  # noqa: E402
import repository_parent_native_spawn_v1 as spawnmod  # noqa: E402


class _Fence:
    def fence(self):
        return contextlib.nullcontext()


class _Control:
    def __init__(self):
        self.launcher = types.SimpleNamespace(parent_service=lambda ctx: _Fence(),
                                              finalizer=types.SimpleNamespace(fj_ensure_intent=self._intent))
        self.adapter = types.SimpleNamespace(locked=lambda conn: contextlib.nullcontext('fresh'))
        self.intents = []

    def _intent(self, *args):
        self.intents.append(args)

    def connection(self):
        return contextlib.nullcontext('own')


class _Conn:
    in_transaction = False


class ReleaseOrderChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sf-spawn-order-')
        self.addCleanup(self.tmp.cleanup)
        self.rd, wr = os.pipe()
        os.set_blocking(self.rd, False)  # a regression must fail, never hang
        self.addCleanup(os.close, self.rd)
        self.events = []
        s = object.__new__(spawnmod.ParentNativeSpawner)
        s.lock = threading.RLock()
        s.control = _Control()
        s.ctx = types.SimpleNamespace(spec='spec', binding={'db': 'db'})
        s.tokens = {}
        s._parent = lambda: s.ctx
        s._row = lambda *a, **k: None
        s.kb = types.SimpleNamespace(_fire_worker_spawned_hook=self._hook)
        a = spawnmod._Attempt(types.SimpleNamespace(task_id='t1', run_id=7), Path(self.tmp.name))
        a.owned = object(); a.gatefd = wr; a.token = b'k' * 32; a.birth = 'birth'
        a.process = types.SimpleNamespace(pid=4242)
        self.addCleanup(lambda: a.gatefd is not None and os.close(a.gatefd))
        a.plan = 'plan'; a.pins = 'pins'
        s.attempts = {('t1', 7): a}
        self.s, self.a = s, a
        self.claimed = types.SimpleNamespace(id='t1', current_run_id=7, assignee='coder-claude')
        self.hook_error = None
        patches = (patch.object(spawnmod.workers, 'process_identity', lambda pid: 'birth'),
                   patch.object(spawnmod, 'check_plan', lambda *a, **k: None))
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def _hook(self, conn, task, workspace, pid, *, board):
        self.events.append(dict(released=self.a.released, gate_closed=self.a.gatefd is None,
                                token_cleared=self.a.token is None, pid=pid, board=board,
                                token_sent=self.pending()))
        if self.hook_error is not None:
            raise self.hook_error

    def pending(self):
        try:
            return os.read(self.rd, 64)
        except BlockingIOError:
            return None

    def release(self):
        return self.s._release_and_hook(_Conn(), self.claimed, '/w', 4242, board='b')

    def test_hook_runs_once_after_token_reached_gate(self):
        result = self.release()
        self.assertEqual(self.events, [dict(released=True, gate_closed=True, token_cleared=True,
                                            pid=4242, board='b', token_sent=b'k' * 32)])
        self.assertEqual(result, spawnmod.NativeSpawnedWorker('t1', 7, 4242))
        self.assertIs(self.s.tokens[('t1', 7)], result)
        self.assertTrue((Path(self.tmp.name) / 'RELEASE_CONSUMED.json').exists())

    def test_hook_failure_is_recorded_not_a_fault(self):
        self.hook_error = RuntimeError('observer broke')
        result = self.release()
        self.assertEqual(result, spawnmod.NativeSpawnedWorker('t1', 7, 4242))
        self.assertFalse(self.a.failed)
        self.assertEqual(self.s.control.intents, [])
        note = json.loads((Path(self.tmp.name) / 'SPAWNED_HOOK_FAILED.json').read_text())
        self.assertEqual((note['schema'], note['error_type'], note['pid']), ('sf-parent-spawned-hook/v1', 'RuntimeError', 4242))
        self.assertFalse((Path(self.tmp.name) / 'FAULT.json').exists())

    def test_refusal_before_release_never_fires_hook(self):
        with patch.object(spawnmod.workers, 'process_identity', lambda pid: 'other'):
            with self.assertRaisesRegex(e.ExecutionRefused, 'spawn_release_process_drift'):
                self.release()
        self.assertEqual(self.events, [])
        self.assertTrue(self.a.failed and not self.a.released)
        self.assertEqual(os.read(self.rd, 64), b'')  # gate closed without token: EOF -> exit 125
        self.assertEqual(len(self.s.control.intents), 1)

    def test_interrupt_after_release_is_recorded_not_a_fault(self):
        self.hook_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as cm:
            self.release()
        self.s._fault(self.a, cm.exception)  # what spawn_coder's outer handler does
        self.assertTrue(self.a.released)
        self.assertFalse(self.a.failed)
        self.assertEqual(self.s.control.intents, [])
        self.assertFalse((Path(self.tmp.name) / 'FAULT.json').exists())
        note = json.loads((Path(self.tmp.name) / 'POST_RELEASE_ERROR.json').read_text())
        self.assertEqual((note['primary_error_type'], note['pid']), ('KeyboardInterrupt', 4242))
        self.assertIs(self.s._attempt.__func__, spawnmod.ParentNativeSpawner._attempt)
        self.s._parent = lambda: self.s.ctx
        self.assertIs(self.s._attempt(self.s.tokens[('t1', 7)]), self.a)

    def test_real_parent_check_refuses_with_reaper_loaded(self):
        s = object.__new__(spawnmod.ParentNativeSpawner)
        s.pid = os.getpid()
        s.control = types.SimpleNamespace(verify=lambda: self.fail('verify reached before reaper check'))
        m = types.ModuleType('sf_fake_reaper'); m.__file__ = '/native/hermes_cli/kanban.py'
        with patch.dict(sys.modules, {'sf_fake_reaper': m}):
            with self.assertRaisesRegex(e.ExecutionRefused, 'spawn_global_reaper_in_process'):
                spawnmod.ParentNativeSpawner._parent(s)

    def test_hook_call_is_textually_after_release_write(self):
        fn = next(n for n in ast.walk(ast.parse(SPAWN.read_text(encoding='utf-8')))
                  if isinstance(n, ast.FunctionDef) and n.name == '_release_and_hook')
        src = ast.get_source_segment(SPAWN.read_text(encoding='utf-8'), fn)
        self.assertEqual(src.count('_fire_worker_spawned_hook('), 1)
        self.assertLess(src.index('os.write(a.gatefd,a.token)'), src.index('_fire_worker_spawned_hook('))


class SingleReaperChecks(unittest.TestCase):
    def fake(self, path):
        m = types.ModuleType('sf_fake_reaper'); m.__file__ = path
        return patch.dict(sys.modules, {'sf_fake_reaper': m})

    def test_clean_process_has_no_global_reaper(self):
        self.assertEqual(spawnmod.global_reaper_modules(), [])
        spawnmod.require_single_reaper()

    def test_each_reaper_module_is_detected(self):
        for rel in spawnmod.GLOBAL_REAPER_MODULES:
            with self.subTest(rel=rel), self.fake('/native/' + rel):
                self.assertEqual(spawnmod.global_reaper_modules(), ['sf_fake_reaper'])
                with self.assertRaisesRegex(e.ExecutionRefused, 'spawn_global_reaper_in_process'):
                    spawnmod.require_single_reaper()

    def test_similar_paths_are_not_reapers(self):
        for path in ('/native/gateway/kanban_watchers_test.py', '/native/hermes_cli/kanban_db.py',
                     '/native/gateway/session_context.py', None):
            with self.subTest(path=path), self.fake(path):
                self.assertEqual(spawnmod.global_reaper_modules(), [])

    def test_known_reaper_inventory(self):
        self.assertEqual(spawnmod.GLOBAL_REAPER_MODULES, ('gateway/kanban_watchers.py', 'gateway/run.py',
                                                         'hermes_cli/kanban.py', 'plugins/kanban/dashboard/plugin_api.py'))

    def test_native_callers_of_global_reaper_are_all_listed(self):
        # Every native module that calls dispatch_once/reap_worker_zombies/run_daemon (other than
        # kanban_db itself, which only defines them) must be in the inventory.
        native = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
        callers = set()
        for path in native.rglob('*.py'):
            rel = path.relative_to(native).as_posix()
            if rel.startswith(('tests/', '.venv/')) or '/tests/' in rel or rel == 'hermes_cli/kanban_db.py':
                continue
            text = '\n'.join(line for line in path.read_text(encoding='utf-8', errors='replace').splitlines()
                             if not line.lstrip().startswith('#'))  # code, not comments
            if any(k in text for k in ('dispatch_once', 'reap_worker_zombies', 'run_daemon(', 'waitpid(-1')):
                callers.add(rel)
        self.assertTrue(callers, 'no native reaper callers found')
        self.assertLessEqual(callers, set(spawnmod.GLOBAL_REAPER_MODULES))

    def test_constructor_and_parent_check_require_single_reaper(self):
        tree = ast.parse(SPAWN.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ParentNativeSpawner')
        for name in ('__init__', '_parent'):
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
            calls = [c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
            self.assertIn('require_single_reaper', calls, name)


if __name__ == '__main__':
    unittest.main()
