"""Hook one-start w bramkach startu (opcja B) na prawdziwej prywatnej bazie zadania.

Baza, wiązanie i hook pochodzą z niezmienionego factory_finalization_core
(fj_initialize, fj_install_runtime). Jedyny zastępnik: fj_live_scope, czyli
obserwacja cgroup związanej usługi systemd — tylko w testach pozytywnych,
z tą samą regułą zatrzymania co ControlFixture.live_double. Bez modelu i sieci.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('one_start_worker_base', HERE/'test_native_worker_start.py')
base = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = base; _spec.loader.exec_module(base)
sys.path.insert(0, str(HERE.parent/'tests_support'))
import factory_finalization_core as fj
import factory_one_start as one
import factory_profile_start as profile_start
from hermes_cli import kanban_db as kb
from protected_profile_fixture import NativeProtectedProfile

BOARD = 'sf-job-' + '0123456789abcdef'


def make_job(root: Path, board: str = BOARD) -> dict:
    """Prywatne zadanie w układzie fj_binding_base; baza tworzona przez fj_initialize."""
    job, public = root/'job', root/'public'
    job.mkdir(mode=0o700); public.mkdir(mode=0o700)
    db = job/'task-home/.hermes/kanban/boards'/board/'kanban.db'
    spec = dict(job=str(job), public=str(public/'source-job'), db=str(db), job_id='one-start-test',
                board=board, manifest='m'*64, loop_limits={'coding_cycles': 3})
    request = fj.fj_bytes(spec); (job/'task_request.json').write_bytes(request)
    driver = b'# INERT one-start fixture\n'; (job/'source_job_driver.py').write_bytes(driver)
    descriptor = dict(schema=1, backend='systemd-user-v1', job_id=spec['job_id'], token='b'*32,
                      uid=os.geteuid(), boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      public=str(public), job=str(job), candidate_manifest=spec['manifest'],
                      request_sha256=fj.fj_sha(request), driver_sha256=fj.fj_sha(driver))
    raw = fj.fj_bytes(descriptor)
    binding = fj.fj_initialize(kb, spec, raw)
    for parent in (job, public):
        (parent/'managed_job.json').write_bytes(raw)
        (parent/'finalization_binding.json').write_bytes(fj.fj_bytes(binding))
    return dict(job=job, public=public, db=db, spec=spec, binding=binding)


def live_double(binding):
    """Zastępnik wyłącznie obserwacji cgroup; zatrzymanie nadal daje 'denied'."""
    public = Path(binding['public'])
    return 'denied' if any((public/n).exists() for n in ('stop_requested.json', 'finalization_intent.json')) else 'live'


class _HookState(unittest.TestCase):
    """Przywraca natywny konektor; hook jest jednorazowy na proces."""
    def setUp(self):
        self._native = kb._sqlite_connect
        self.addCleanup(self._restore)

    def _restore(self):
        kb._sqlite_connect = self._native
        if hasattr(kb, '_sf_finalization_binding'):
            del kb._sf_finalization_binding


class OneStartInstallTests(_HookState):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix='sf-one-start-'); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'HERMES_HOME': str(self.root/'.hermes')})
        env.start(); self.addCleanup(env.stop)
        self.job = make_job(self.root)
        env = patch.dict(os.environ, {'HERMES_KANBAN_BOARD': BOARD, 'HERMES_KANBAN_DB': str(self.job['db'])})
        env.start(); self.addCleanup(env.stop)

    def live(self):
        return patch.object(fj, 'fj_live_scope', side_effect=live_double)

    def scope(self):
        conn = kb._sqlite_connect(self.job['db'])
        try:
            return conn.execute('SELECT sf_one_start_scope()').fetchone()[0]
        finally:
            conn.close()

    def test_noop_outside_private_job_board(self):
        with patch.dict(os.environ, {'HERMES_KANBAN_BOARD': 'fixture'}):
            self.assertIsNone(one.install_for_worker())
        self.assertIs(kb._sqlite_connect, self._native)
        self.assertFalse(hasattr(kb, '_sf_finalization_binding'))

    def test_installs_original_hook_on_private_job_board(self):
        with self.live():
            binding = one.install_for_worker()
            self.assertEqual(binding, self.job['binding'])
            self.assertEqual(self.scope(), 'live')
        self.assertEqual(kb._sf_finalization_binding, self.job['binding'])

    def test_native_write_needs_hook(self):
        conn = self._native(self.job['db'])
        try:
            with self.assertRaisesRegex(Exception, 'sf_one_start_scope'):
                conn.execute("INSERT INTO tasks(id) VALUES('x')")
        finally:
            conn.close()

    def test_outside_bound_service_refused(self):
        # Real fj_live_scope: this test process is not in the bound service cgroup.
        with self.assertRaisesRegex(one.OneStartError, 'one_start_worker_outside_bound_service'):
            one.install_for_worker()

    def test_stop_requested_refused(self):
        (self.job['public']/'stop_requested.json').write_text('{}')
        with self.live(), self.assertRaisesRegex(one.OneStartError, 'one_start_worker_outside_bound_service'):
            one.install_for_worker()

    def test_database_layout_mismatch_refused(self):
        other = self.root/'elsewhere'/BOARD/'kanban.db'; other.parent.mkdir(parents=True)
        with patch.dict(os.environ, {'HERMES_KANBAN_DB': str(other)}), self.live(), \
             self.assertRaisesRegex(one.OneStartError, 'one_start_db_layout'):
            one.install_for_worker()
        self.assertIs(kb._sqlite_connect, self._native)

    def test_board_and_database_must_agree(self):
        with patch.dict(os.environ, {'HERMES_KANBAN_BOARD': 'sf-job-' + 'f'*16}), self.live(), \
             self.assertRaisesRegex(one.OneStartError, 'one_start_db_layout'):
            one.install_for_worker()

    def test_request_binding_mismatch_refused(self):
        spec = dict(self.job['spec'], job=str(self.root/'other-job'))
        (self.job['job']/'task_request.json').write_bytes(fj.fj_bytes(spec))
        with self.live(), self.assertRaisesRegex(one.OneStartError, 'one_start_spec_binding'):
            one.install_for_worker()
        self.assertIs(kb._sqlite_connect, self._native)

    def test_changed_request_refused_by_original_binding_check(self):
        spec = dict(self.job['spec'], loop_limits={'coding_cycles': 4})
        (self.job['job']/'task_request.json').write_bytes(fj.fj_bytes(spec))
        with self.live(), self.assertRaisesRegex(fj.FjRefused, 'finalization_driver_or_request_changed'):
            one.install_for_worker()

    def test_symlinked_path_refused(self):
        alias = self.root/'alias'; alias.symlink_to(self.job['job'], target_is_directory=True)
        db = alias/self.job['db'].relative_to(self.job['job'])
        with patch.dict(os.environ, {'HERMES_KANBAN_DB': str(db)}), self.live(), \
             self.assertRaisesRegex(fj.FjRefused, 'finalization_symlink'):
            one.install_for_worker()

    def test_replaced_database_refused(self):
        db = self.job['db']; copy = db.with_name('copy.db'); shutil.copyfile(db, copy); os.replace(copy, db)
        with self.live(), self.assertRaisesRegex(fj.FjRefused, 'finalization_database_replaced'):
            one.install_for_worker()

    def test_second_install_refused(self):
        with self.live():
            one.install_for_worker()
            with self.assertRaisesRegex(fj.FjRefused, 'finalization_hook_already_installed'):
                one.install_for_worker()


class OneStartGateTests(_HookState):
    """Bramki startu wołają instalację przed narzędziami; poza tablicą zadania nic się nie zmienia."""
    save_config = base.NativeWorkerStartTests.save_config
    initializer = base.NativeWorkerStartTests.initializer
    construct = base.NativeWorkerStartTests.construct

    def setUp(self):
        _HookState.setUp(self)
        base.NativeWorkerStartTests.setUp(self)
        self.job = make_job(self.root)
        self.job_env = patch.dict(os.environ, {'HERMES_KANBAN_BOARD': BOARD, 'HERMES_KANBAN_DB': str(self.job['db'])})
        self.job_env.start()

    def tearDown(self):
        self.job_env.stop()
        base.NativeWorkerStartTests.tearDown(self)

    def database(self, root):
        return types.SimpleNamespace(board=BOARD, task_id='t_fixture', run_id=6, workspace=root/'workspace')

    def test_coder_gate_installs_before_any_tool(self):
        with patch.object(fj, 'fj_live_scope', side_effect=live_double):
            self.construct()
        self.assertEqual(self.called, 1)
        self.assertEqual(kb._sf_finalization_binding, self.job['binding'])

    def test_coder_gate_refuses_outside_bound_service_before_init(self):
        with self.assertRaisesRegex(base.boundary.WorkerStartError, 'FACTORY_WORKER_START_REFUSED: OneStartError'):
            self.construct()
        self.assertEqual(self.called, 0)

    def test_reviewer_gate_refuses_outside_bound_service(self):
        root = self.root/'reviewer-a'; root.mkdir()
        with self.assertRaisesRegex(Exception, 'OneStartError'):
            NativeProtectedProfile(root, 'reviewer-gpt', database=self.database(root))

    def test_reviewer_gate_installs_before_tools(self):
        root = self.root/'reviewer-b'; root.mkdir()
        with patch.object(fj, 'fj_live_scope', side_effect=live_double):
            fixture = NativeProtectedProfile(root, 'reviewer-gpt', database=self.database(root))
        try:
            self.assertEqual(kb._sf_finalization_binding, self.job['binding'])
        finally:
            fixture.close()


if __name__ == '__main__':
    unittest.main()
