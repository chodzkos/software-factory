"""Native dispatch claim/worktree binding; no LLM, Claude or production state.

Uses complete native SQL/schema and dispatch_once. One test uses _default_spawn
with an inert child; callback tests deliberately do not start a worker. The
source guard is unchanged and consumes the actual stored run metadata.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
sys.path.insert(0, str(NATIVE))
# Import only after an isolated home exists; never use the owner's default board.
_IMPORT_HOME = tempfile.TemporaryDirectory(prefix='sf-binding-import-')
os.environ['HERMES_HOME'] = _IMPORT_HOME.name
os.environ.pop('HERMES_PROFILE', None)
from hermes_cli import kanban_db as kb
SF = Path(__file__).resolve().parents[1]
_hp = SF / 'plugins/factory-execution-guards/handoff.py'
_hspec = importlib.util.spec_from_file_location('_sf_dispatch_handoff', _hp)
handoff = importlib.util.module_from_spec(_hspec)
sys.modules[_hspec.name] = handoff
_hspec.loader.exec_module(handoff)


class DispatchBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sf-binding-')
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.hh = self.home / '.hermes'
        self.hh.mkdir(parents=True)
        for profile in ('coder-claude', 'generic-worker'):
            ph = self.hh / 'profiles' / profile
            ph.mkdir(parents=True)
            (ph / 'config.yaml').write_text(json.dumps({
                'kanban': {'review_dispatch': False},
                'platform_toolsets': {'cli': ['terminal', 'file', 'kanban', 'no_mcp']},
                'known_builtin_toolsets': {'cli': ['bfl']},
            }))
        (self.hh / 'config.yaml').write_text('{"kanban":{"review_dispatch":false}}\n')
        self.envpatch = patch.dict(os.environ, {
            'HOME': str(self.home), 'HERMES_HOME': str(self.hh),
            'HERMES_KANBAN_HOME': str(self.hh), 'HERMES_KANBAN_BOARD': 'binding',
            'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0', 'PYTHONDONTWRITEBYTECODE': '1',
        }, clear=True)
        self.envpatch.start()
        self.addCleanup(self.envpatch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        (self.repo / 'tracked.txt').write_text('unchanged\n')
        for argv in (['init','-q'], ['add','.'],
                     ['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture']):
            subprocess.run(['/usr/bin/git','-C',str(self.repo), *argv], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        kb.create_board('binding'); kb.set_current_board('binding')
        self.conn = kb.connect(board='binding')
        self.addCleanup(self.conn.close)
        self.tid = kb.create_task(self.conn, title='OFFLINE DISPATCH BINDING',
            body=f'WORKSPACE: worktree:{self.repo}\n', assignee='coder-claude',
            workspace_kind='worktree', workspace_path=str(self.repo),
            initial_status='blocked', board='binding')
        self.assertTrue(kb.unblock_task(self.conn, self.tid))
        # Suppress memory-pressure scheduling in a controlled test; not a claim,
        # SQL, workspace, PID or metadata replacement.
        p = patch.object(kb, '_memory_pressure_level', return_value='ok'); p.start(); self.addCleanup(p.stop)

    def rows(self):
        return {n: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {n} ORDER BY 1')]
                for n in ('tasks','task_runs','task_events')}

    def claim_workspace(self):
        self.claimed = kb.claim_task(self.conn, self.tid)
        self.assertIsNotNone(self.claimed)
        self.workspace, branch = kb._resolve_worktree_workspace(self.claimed, board='binding')
        self.workspace = str(Path(self.workspace).resolve(strict=True))
        kb.set_workspace_path(self.conn, self.tid, self.workspace)
        kb.set_branch_name(self.conn, self.tid, branch)
        return self.claimed, self.workspace

    def bind(self):
        return kb._bind_factory_coder_run(self.conn, self.claimed, self.workspace)

    def metadata(self):
        task = kb.get_task(self.conn, self.tid)
        r = self.conn.execute('SELECT metadata FROM task_runs WHERE id=?', (task.current_run_id,)).fetchone()
        return None if r[0] is None else json.loads(r[0])

    def guard_scope(self):
        t = kb.get_task(self.conn, self.tid)
        return patch.dict(os.environ, {
            'HERMES_HOME': str(self.hh/'profiles/coder-claude'), 'HERMES_PROFILE': 'coder-claude',
            'HERMES_KANBAN_TASK': self.tid, 'HERMES_KANBAN_RUN_ID': str(t.current_run_id),
            'HERMES_KANBAN_WORKSPACE': str(t.workspace_path),
        })

    def assert_refused_unchanged(self):
        before = self.rows()
        with self.assertRaisesRegex(RuntimeError, 'FACTORY_DISPATCH_BINDING_REFUSED'):
            self.bind()
        self.assertEqual(before, self.rows())

    def test_real_default_spawn_supplies_guard_metadata(self):
        child = self.root/'inert-child'
        child.write_text('#!/bin/sh\nexec /bin/sleep 30\n'); child.chmod(0o755)
        with patch.dict(os.environ, {'HERMES_BIN': str(child)}):
            result = kb.dispatch_once(self.conn, board='binding', max_spawn=1, reconcile_orphans=False)
        task = kb.get_task(self.conn,self.tid)
        try:
            self.assertEqual(len(result.spawned), 1)
            self.assertIsNotNone(task.worker_pid)
            self.assertEqual(self.metadata(), {'task_id':self.tid, 'workspace_path':task.workspace_path})
            with self.guard_scope():
                self.assertTrue(handoff.active_coder_run_matches())
            run = self.conn.execute('SELECT * FROM task_runs WHERE id=?',(task.current_run_id,)).fetchone()
            self.assertEqual(run['worker_pid'],task.worker_pid)
            self.assertEqual(run['claim_lock'],task.claim_lock)
        finally:
            if task.worker_pid:
                os.kill(task.worker_pid, signal.SIGTERM)
                try: os.waitpid(task.worker_pid,0)
                except ChildProcessError: pass

    def test_actual_spawned_child_observes_own_guard_identity(self):
        output = self.root/'child-result.json'
        child_code = self.root/'guard-child.py'
        child_code.write_text("""import importlib.util,json,os,sys,time
from pathlib import Path
sys.path.insert(0, NATIVE_PATH)
from hermes_cli import kanban_db as kb
spec=importlib.util.spec_from_file_location('_child_handoff', HANDOFF_PATH)
h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)
deadline=time.monotonic()+5
while True:
    with kb.connect_closing(board=os.environ['HERMES_KANBAN_BOARD']) as c:
        t=kb.get_task(c,os.environ['HERMES_KANBAN_TASK'])
    if t.worker_pid==os.getpid():break
    if time.monotonic()>deadline:raise RuntimeError('PID publication timeout')
    time.sleep(0.01)
identity=h.active_coder_worker_identity()
Path(OUTPUT_PATH).write_text(json.dumps({'pid':os.getpid(),'guard_pid':identity[0],
    'guard_start':identity[1],'active':h.active_coder_run_matches(),'workspace':os.getcwd()}))
""".replace('NATIVE_PATH', repr(str(NATIVE))).replace('HANDOFF_PATH',repr(str(_hp)))
           .replace('OUTPUT_PATH',repr(str(output))))
        launcher=self.root/'guard-child'
        import shlex
        launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -B '+shlex.quote(str(child_code))+' "$@"\n')
        launcher.chmod(0o755)
        with patch.dict(os.environ,{'HERMES_BIN':str(launcher)}):
            result=kb.dispatch_once(self.conn,board='binding',max_spawn=1,reconcile_orphans=False)
        task=kb.get_task(self.conn,self.tid)
        self.assertEqual(len(result.spawned),1)
        self.assertIsNotNone(task.worker_pid)
        pid, status=os.waitpid(task.worker_pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status),0)
        data=json.loads(output.read_text())
        self.assertEqual(data['pid'],pid)
        self.assertEqual(data['guard_pid'],pid)
        self.assertTrue(data['guard_start'].isdigit())
        self.assertTrue(data['active'])
        self.assertEqual(data['workspace'],task.workspace_path)

    def test_binding_committed_before_spawn_callback(self):
        observed=[]
        def spawn(task, workspace, *, board=None):
            with kb.connect_closing(board=board) as other:
                row=other.execute('SELECT metadata FROM task_runs WHERE id=?',(task.current_run_id,)).fetchone()
                observed.append(json.loads(row[0]) if row[0] else None)
            return None  # deliberately no worker; test only the pre-spawn boundary
        result=kb.dispatch_once(self.conn,board='binding',max_spawn=1,reconcile_orphans=False,spawn_fn=spawn)
        self.assertEqual(len(result.spawned),1)
        t=kb.get_task(self.conn,self.tid)
        self.assertEqual(observed,[{'task_id':self.tid,'workspace_path':t.workspace_path}])

    def test_direct_binding_closes_guard_gap(self):
        self.claim_workspace()
        with self.guard_scope(): self.assertFalse(handoff.active_coder_run_matches())
        self.bind()
        with self.guard_scope(): self.assertTrue(handoff.active_coder_run_matches())

    def test_preserves_unrelated_metadata(self):
        self.claim_workspace()
        self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            (json.dumps({'worker_session_id':'existing','extra':{'a':1}}), self.claimed.current_run_id)); self.conn.commit()
        self.bind()
        self.assertEqual(self.metadata()['extra'],{'a':1})
        self.assertEqual(self.metadata()['worker_session_id'],'existing')

    def test_matching_metadata_is_idempotent(self):
        self.claim_workspace(); self.bind(); before=self.rows(); self.bind()
        self.assertEqual(before,self.rows())

    def test_rejects_stale_current_run(self):
        self.claim_workspace()
        self.conn.execute('UPDATE tasks SET current_run_id=NULL WHERE id=?',(self.tid,)); self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_changed_task_claim(self):
        self.claim_workspace()
        self.conn.execute('UPDATE tasks SET claim_lock=? WHERE id=?',('other',self.tid)); self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_changed_run_claim(self):
        self.claim_workspace()
        self.conn.execute('UPDATE task_runs SET claim_lock=? WHERE id=?',('other',self.claimed.current_run_id));self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_nonrunning_task(self):
        self.claim_workspace()
        self.conn.execute("UPDATE tasks SET status='ready' WHERE id=?",(self.tid,));self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_ended_run(self):
        self.claim_workspace()
        self.conn.execute('UPDATE task_runs SET ended_at=1 WHERE id=?',(self.claimed.current_run_id,));self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_other_profile(self):
        self.claim_workspace()
        self.conn.execute("UPDATE task_runs SET profile='reviewer-gpt' WHERE id=?",(self.claimed.current_run_id,));self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_existing_worker_pid(self):
        self.claim_workspace()
        kb._set_worker_pid(self.conn,self.tid,os.getpid())
        self.assert_refused_unchanged()

    def test_rejects_workspace_drift(self):
        self.claim_workspace()
        kb.set_workspace_path(self.conn,self.tid,str(self.repo))
        self.assert_refused_unchanged()

    def test_rejects_noncanonical_workspace(self):
        self.claim_workspace()
        alias=self.root/'alias';alias.symlink_to(self.workspace,target_is_directory=True)
        self.workspace=str(alias);kb.set_workspace_path(self.conn,self.tid,self.workspace)
        self.assert_refused_unchanged()

    def test_rejects_missing_workspace(self):
        self.claim_workspace();self.workspace=str(self.root/'absent')
        kb.set_workspace_path(self.conn,self.tid,self.workspace)
        self.assert_refused_unchanged()

    def test_rejects_nonworktree(self):
        self.claim_workspace()
        self.conn.execute("UPDATE tasks SET workspace_kind='directory' WHERE id=?",(self.tid,));self.conn.commit()
        self.assert_refused_unchanged()

    def test_rejects_metadata_wrong_task(self):
        self.claim_workspace();self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            ('{"task_id":"other"}',self.claimed.current_run_id));self.conn.commit();self.assert_refused_unchanged()

    def test_rejects_metadata_wrong_workspace(self):
        self.claim_workspace();self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            ('{"workspace":"/other"}',self.claimed.current_run_id));self.conn.commit();self.assert_refused_unchanged()

    def test_rejects_metadata_duplicate_key(self):
        self.claim_workspace();self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            ('{"a":1,"a":2}',self.claimed.current_run_id));self.conn.commit();self.assert_refused_unchanged()

    def test_rejects_metadata_nondict(self):
        self.claim_workspace();self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            ('[]',self.claimed.current_run_id));self.conn.commit();self.assert_refused_unchanged()

    def test_rejects_metadata_invalid_json(self):
        self.claim_workspace();self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',
            ('broken',self.claimed.current_run_id));self.conn.commit();self.assert_refused_unchanged()

    def test_sql_failure_rolls_back_binding(self):
        self.claim_workspace()
        self.conn.execute("CREATE TRIGGER refuse_binding BEFORE INSERT ON task_events WHEN NEW.kind='factory_coder_bound' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.conn.commit();before=self.rows();observed=[]
        self.conn.set_trace_callback(observed.append)
        import sqlite3
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture'): self.bind()
        self.assertTrue(any(x.startswith('UPDATE task_runs SET metadata') for x in observed),
                        'rollback must follow the real binding UPDATE, not an earlier refusal')
        self.assertEqual(before,self.rows())

    def test_dispatch_does_not_spawn_on_conflicting_binding(self):
        orig=kb.claim_task
        def claimed(*args,**kwargs):
            task=orig(*args,**kwargs)
            self.conn.execute('UPDATE task_runs SET metadata=? WHERE id=?',('{"task_id":"other"}',task.current_run_id));self.conn.commit()
            return task
        with patch.object(kb,'claim_task',side_effect=claimed), patch.object(kb,'_default_spawn') as spawn:
            with self.assertRaisesRegex(RuntimeError,'FACTORY_DISPATCH_BINDING_REFUSED'):
                kb.dispatch_once(self.conn,board='binding',max_spawn=1,reconcile_orphans=False)
            spawn.assert_not_called()
        self.assertEqual(self.metadata(),{'task_id':'other'})

    def test_nonfactory_profile_is_unchanged(self):
        kb.assign_task(self.conn,self.tid,'generic-worker')
        seen=[]
        def spawn(task,workspace,*,board=None):
            seen.append(self.metadata());return None
        result=kb.dispatch_once(self.conn,board='binding',max_spawn=1,reconcile_orphans=False,spawn_fn=spawn)
        self.assertEqual(len(result.spawned),1);self.assertEqual(seen,[None])


if __name__ == '__main__': unittest.main(verbosity=2)
