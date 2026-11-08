"""Negatywne próby i rzeczywiste zakończenie procesu, bez modeli i produkcyjnej DB."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from fixture import NATIVE_FUNCTIONS
from test_transaction import TransactionFixture


class ConnectionProxy:
    """Wstrzyknięcie konkretnej awarii przy zachowaniu tego samego SQLite connection."""
    def __init__(self,conn,hook):self.conn=conn;self.hook=hook
    def execute(self,sql,*args):
        action=self.hook(self.conn,sql)
        if action is not None:return action
        return self.conn.execute(sql,*args)
    def __getattr__(self,name):return getattr(self.conn,name)


class TestFailureCases(TransactionFixture):
    def test_actual_commit_denied_by_sqlite_authorizer_rolls_back(self):
        self.assertIsNone(self.before());original=self.receipt.append_handoff_receipt
        def deny(conn,seal):
            original(conn,seal)
            conn.set_authorizer(lambda action,a,b,c,d: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_TRANSACTION and a=='COMMIT' else sqlite3.SQLITE_OK)
        with patch.object(self.receipt,'append_handoff_receipt',side_effect=deny):self.refused()
        self.assertFalse(self.seal_path.exists())

    def test_file_not_authorized_before_real_commit(self):
        self.assertIsNone(self.before());original=self.receipt.append_handoff_receipt;observed=[]
        def check(conn,seal):
            original(conn,seal)
            self.assertTrue(self.seal_path.exists())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_events WHERE kind='factory_handoff_committed'").fetchone()[0],1)
            with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)
            observed.append(True)
        with patch.object(self.receipt,'append_handoff_receipt',side_effect=check):self.assertTrue(self.call().get('ok'))
        self.assertEqual(observed,[True]);self.h.load_handoff_seal(self.board,self.tid,1)

    def test_disk_error_after_link_does_not_authorize_orphan(self):
        self.assertIsNone(self.before());original=self.h._atomic_write
        def after_link(path,payload):original(path,payload);raise OSError('after link fixture')
        with patch.object(self.h,'_atomic_write',side_effect=after_link):self.refused()
        self.assertTrue(self.seal_path.exists())
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_receipt_nonce_tampering_rejected(self):
        self.positive();rows=self.rows();receipt=rows['task_events'][-1];value=json.loads(receipt['payload']);value['transaction_id']='0'*64
        self.sql('UPDATE task_events SET payload=? WHERE id=?',(json.dumps(value),receipt['id']))
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_duplicate_receipt_rejected(self):
        self.positive()
        self.sql("INSERT INTO task_events(task_id,run_id,kind,payload,created_at) SELECT task_id,run_id,kind,payload,created_at FROM task_events WHERE kind='factory_handoff_committed'")
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_old_schema_two_rejected_not_upgraded(self):
        self.positive();value=json.loads(self.seal_path.read_text());value['schema']=2
        self.seal_path.write_text(json.dumps(value))
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_db_replaced_after_pre_rejected(self):
        self.assertIsNone(self.before())
        with self.conn() as conn:conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        old=self.db.with_suffix('.old');self.db.rename(old);self.db.write_bytes(old.read_bytes())
        self.refused()

    def test_hardlinked_db_rejected(self):
        os.link(self.db,self.root/'db-alias')
        self.assertEqual(self.before()['action'],'block')

    def test_delete_journal_refused_without_changing_mode(self):
        with self.conn() as c:c.execute('PRAGMA journal_mode=DELETE')
        self.assertIsNone(self.before());self.refused()
        with self.conn() as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'delete')

    def test_profile_change_after_pre_rejected(self):
        self.assertIsNone(self.before());os.environ['HERMES_PROFILE']='reviewer-gpt';self.refused()

    def test_process_start_change_after_pre_rejected(self):
        self.assertIsNone(self.before())
        with patch.object(self.h,'_process_identity',return_value=(os.getpid(),'1')):self.refused()

    def test_body_changed_after_native_goal_check_rejected(self):
        self.assertIsNone(self.before());original=self.ns['_goal_mode_handoff_rejection']
        def change(task,evidence):
            result=original(task,evidence);self.sql("UPDATE tasks SET body=body||char(10)||'ACCEPTANCE: different'")
            return result
        self.ns['_goal_mode_handoff_rejection']=change
        result=self.call();self.assertIn('error',result)
        rows=self.rows();self.assertEqual(rows['tasks'][0]['status'],'running');self.assertEqual(rows['task_events'],[])
        self.assertFalse(self.seal_path.exists())

    def test_wrong_run_id_after_pre_rejected(self):
        self.assertIsNone(self.before());self.ns['_worker_run_id']=lambda tid:1000001;self.refused()

    def test_native_database_layer_child_guard_preserved(self):
        self.assertIsNone(self.before());os.environ['HERMES_DELEGATED_CHILD_CONTEXT']='1';self.refused()

    def test_exact_native_parent_guard_preserved(self):
        self.assertIsNone(self.before())
        with patch.dict(NATIVE_FUNCTIONS,{'_parents_satisfied':lambda c,t:False}):self.refused()

    def test_claim_lock_changed_after_pre_rejected(self):
        self.assertIsNone(self.before());self.sql("UPDATE tasks SET claim_lock='different'");self.sql("UPDATE task_runs SET claim_lock='different'");self.refused()

    def test_started_at_changed_after_pre_rejected(self):
        self.assertIsNone(self.before());self.sql('UPDATE task_runs SET started_at=started_at-1');self.refused()

    def test_attestation_forgery_after_pre_rejected(self):
        self.assertIsNone(self.before());self.evidence['attestation_id']='f'*64;self.write_evidence();self.refused()

    def test_evidence_session_bytes_after_pre_rejected(self):
        self.assertIsNone(self.before());self.evidence['session_id']='SYNTHETIC_OTHER';self.write_evidence();self.refused()

    def test_original_native_adapter_is_unchanged_bytecode_and_globals(self):
        code=self.native.__code__;ns=dict(self.native.__globals__);self.positive()
        self.assertIs(self.native.__code__,code);self.assertEqual(self.native.__globals__,ns)
        self.assertIs(self.kb.request_review,NATIVE_FUNCTIONS['request_review'])

    def test_native_result_malformed_rolls_back(self):
        self.assertIsNone(self.before());self.ns['_ok']=lambda **fields:'{"ok":true,"ok":true}'
        self.refused()

    def test_native_result_wrong_task_rolls_back(self):
        self.assertIsNone(self.before());self.ns['_ok']=lambda **fields:json.dumps({**fields,'ok':True,'task_id':'other'})
        self.refused()

    def test_native_end_pid_resurrection_rejected(self):
        self.assertIsNone(self.before());original=self.kb.latest_run
        def restore(conn,tid):
            conn.execute('UPDATE task_runs SET worker_pid=?',(os.getpid(),));return original(conn,tid)
        with patch.object(self.kb,'latest_run',side_effect=restore):self.refused()

    def test_native_event_removed_before_publication_rolls_back(self):
        self.assertIsNone(self.before());original=self.kb.latest_run
        def delete(conn,tid):conn.execute('DELETE FROM task_events');return original(conn,tid)
        with patch.object(self.kb,'latest_run',side_effect=delete):self.refused()

    def test_new_pre_hook_between_publish_and_commit_invalidates(self):
        self.assertIsNone(self.before());original=self.h._atomic_write
        def invalidate(path,payload):
            original(path,payload);self.plugin.on_pre_tool_call(tool_name='kanban_show',args={})
        with patch.object(self.h,'_atomic_write',side_effect=invalidate):self.refused()

    def test_commit_window_serializes_thread_invalidation(self):
        self.assertIsNone(self.before());intent=self.ri.take(self.args);entered=threading.Event();finished=threading.Event()
        def invalidate():entered.set();self.ri.invalidate();finished.set()
        with self.ri.commit_window(intent):
            t=threading.Thread(target=invalidate);t.start();self.assertTrue(entered.wait(2));self.assertFalse(finished.is_set())
        t.join(2);self.assertFalse(t.is_alive());self.assertTrue(finished.is_set())
        with self.assertRaises(self.h.HandoffError):
            with self.ri.commit_window(intent):pass

    def test_symlink_seal_destination_never_overwritten(self):
        self.assertIsNone(self.before());self.seal_path.parent.mkdir(parents=True,exist_ok=True)
        target=self.root/'unrelated';target.write_text('KEEP');self.seal_path.symlink_to(target)
        self.refused();self.assertEqual(target.read_text(),'KEEP');self.assertTrue(self.seal_path.is_symlink())

    def test_compatibility_changed_source_refused(self):
        self.assertIsNone(self.before())
        with patch.object(self.kb,'__file__',str(self.binary)):self.refused()

    def _crash(self,stage):
        baseline_path=self.root/'child_before.json'
        child=os.fork()
        if child==0:
            try:
                with self.conn() as conn:NATIVE_FUNCTIONS['_set_worker_pid'](conn,self.tid,os.getpid())
                baseline_path.write_text(json.dumps(self.rows()))
                self.assertIsNone(self.before())
                if stage=='after_file':
                    original=self.h._atomic_write
                    def end(path,payload):original(path,payload);os._exit(73)
                    self.h._atomic_write=end
                else:
                    def hook(conn,sql):
                        if sql=='COMMIT':
                            if stage=='after_commit':conn.execute(sql)
                            os._exit(73)
                    self.conn_proxy=lambda conn:ConnectionProxy(conn,hook)
                self.call();os._exit(91)
            except BaseException:
                import traceback;traceback.print_exc();os._exit(92)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            pid,status=os.waitpid(child,os.WNOHANG)
            if pid:break
            time.sleep(.02)
        else:
            os.kill(child,9);os.waitpid(child,0);self.fail('Proces testowy nie zakończył się w limicie')
        self.assertEqual(os.waitstatus_to_exitcode(status),73)
        self.assertTrue(self.seal_path.exists())
        baseline=json.loads(baseline_path.read_text())
        if stage=='after_commit':
            self.assertEqual(self.rows()['tasks'][0]['status'],'review')
            seal,errors=self.h.validate_handoff_seal(board=self.board,task_id=self.tid,run_id=1,
                implementer='coder-claude',reviewer='reviewer-gpt',workspace=str(self.workspace),
                content_state=self.g._workspace_content_state,require_process_exit=True)
            self.assertEqual(errors,[]);self.assertIsNotNone(seal)
        else:
            self.assertEqual(self.rows(),baseline)
            with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    @unittest.skipUnless(hasattr(os,'fork'),'Wymagany Linux/POSIX')
    def test_real_process_crash_after_file_leaves_no_authority(self):self._crash('after_file')

    @unittest.skipUnless(hasattr(os,'fork'),'Wymagany Linux/POSIX')
    def test_real_process_crash_after_receipt_before_commit_rolls_back(self):self._crash('before_commit')

    @unittest.skipUnless(hasattr(os,'fork'),'Wymagany Linux/POSIX')
    def test_real_process_crash_after_commit_preserves_durable_handoff(self):self._crash('after_commit')

    def test_exception_after_successful_commit_is_reported_without_fake_rollback(self):
        self.assertIsNone(self.before())
        def hook(conn,sql):
            if sql=='COMMIT':
                conn.execute(sql)
                raise OSError('fixture: acknowledgement failure after durable commit')
        self.conn_proxy=lambda conn:ConnectionProxy(conn,hook)
        result=self.call();self.assertIn('error',result)
        self.assertEqual(self.rows()['tasks'][0]['status'],'review')
        self.assertIsNone(self.rows()['task_runs'][0]['worker_pid'])
        self.assertIn('factory_handoff_committed',[e['kind'] for e in self.rows()['task_events']])
        self.assertFalse(self.seal_path.exists())
        self.assertNotIn(self.key,self.g._COMPLETED_ATTESTATIONS)
        self.assertIsNone(self.ri._PENDING)
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)
        self.assertEqual(self.before()['action'],'block')

    def test_close_exception_still_consumes_attempt_authority(self):
        self.assertIsNone(self.before())
        class CloseRaises:
            def __init__(inner,conn):inner.conn=conn
            def __getattr__(inner,name):return getattr(inner.conn,name)
            def close(inner):inner.conn.close();raise OSError('fixture close acknowledgement')
        self.conn_proxy=CloseRaises
        with patch.object(self.h,'_atomic_write',side_effect=OSError('fixture publication')):self.refused()
        self.assertIsNone(self.ri._PENDING)

    def test_distinct_process_writer_observes_busy_under_authority_lock(self):
        import subprocess
        import sys
        self.assertIsNone(self.before());original=self.kb.request_review;observed=[]
        program='''import sqlite3,sys
conn=sqlite3.connect(sys.argv[1],timeout=0)
try:
    conn.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as exc:
    print(exc.sqlite_errorcode)
    sys.exit(0 if exc.sqlite_errorcode==sqlite3.SQLITE_BUSY else 2)
else:
    conn.rollback()
    sys.exit(3)
finally:
    conn.close()
'''
        def contend(conn,tid,**kwargs):
            result=subprocess.run([sys.executable,'-B','-c',program,str(self.db)],
                                  stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5,
                                  close_fds=True,env={'PATH':os.defpath,'HOME':str(self.home)})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),str(sqlite3.SQLITE_BUSY));observed.append(True)
            return original(conn,tid,**kwargs)
        with patch.object(self.kb,'request_review',side_effect=contend):self.assertTrue(self.call().get('ok'))
        self.assertEqual(observed,[True]);self.h.load_handoff_seal(self.board,self.tid,1)
