"""Regresje transakcyjne. Oryginalne ciała SQL/tool, kontrolowane otoczenie.

Brak modeli. SQLite i Git rzeczywiste; schema6, attestation, rejestr i judge to
jawne fixture. Funkcje natywne są kompilowane bez zmiany ich AST/bytecode.
"""
from __future__ import annotations

import ast
import copy
import contextlib
import random
import secrets
import time
import importlib.util
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

from fixture import BaseFixture, NATIVE, NATIVE_FUNCTIONS


def load_functions(path, names, namespace):
    tree=ast.parse(path.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes}==set(names)
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module,str(path),'exec'),namespace)
    return namespace


class TransactionFixture(BaseFixture):
    def setUp(self):
        super().setUp()
        self.rt=self.plugin._review_transaction
        self.ri=self.plugin._review_intent
        self.receipt=self.h
        util_path=NATIVE.parent/'sqlite_util.py'
        spec=importlib.util.spec_from_file_location('_native_sqlite_fixture',util_path)
        util=importlib.util.module_from_spec(spec);spec.loader.exec_module(util)
        NATIVE_FUNCTIONS.update(contextlib=contextlib,sqlite3=sqlite3,os=os,secrets=secrets,
                                random=random,time=time,_BUSY_MAX_RETRIES=5,
                                _BUSY_RETRY_MIN_S=.020,_BUSY_RETRY_MAX_S=.150)
        load_functions(NATIVE,{'write_txn','_execute_boundary_with_retry','_is_busy_error',
                             '_check_file_length_invariant','_assert_not_delegated_child_mutation'},NATIVE_FUNCTIONS)
        with self.conn() as c:c.execute('PRAGMA journal_mode=WAL')
        tools=types.ModuleType('tools'); registry=types.ModuleType('tools.registry')
        registry.tool_error=lambda error:json.dumps({'error':str(error)})
        hermes_cli=types.ModuleType('hermes_cli');hermes_cli.sqlite_util=util
        filesafety=types.ModuleType('agent.file_safety')
        # Jedyna brakująca zależność redaktora: testy nie używają ścieżek plików .env.
        filesafety._BLOCKED_PROJECT_ENV_BASENAMES=frozenset({'.env'})
        agent=types.ModuleType('agent')
        safe_read=types.ModuleType('hermes_cli.sqlite_safe_read')
        def unexpected_delete_probe(*a,**kw):
            raise AssertionError('Test WAL nie powinien wywołać sondy nagłówka DELETE')
        safe_read.file_length_matches_header=unexpected_delete_probe
        mods={'hermes_cli.sqlite_safe_read':safe_read,'tools':tools,'tools.registry':registry,'hermes_cli':hermes_cli,
              'hermes_cli.sqlite_util':util,'agent':agent,'agent.file_safety':filesafety}
        patcher=patch.dict(sys.modules,mods);patcher.start();self.addCleanup(patcher.stop)
        red_path=NATIVE.parents[1]/'agent/redact.py'
        spec=importlib.util.spec_from_file_location('_native_redactor_fixture',red_path)
        red=importlib.util.module_from_spec(spec);spec.loader.exec_module(red)
        self.red=red
        patch_red=patch.dict(sys.modules,{'agent.redact':red});patch_red.start();self.addCleanup(patch_red.stop)
        load_functions(NATIVE,{'redact_review_value'},NATIVE_FUNCTIONS)
        self.trace=[]; self.connections=[]; self.conn_proxy=None
        def connect(board=None):
            c=sqlite3.connect(self.db,timeout=0.15);c.row_factory=sqlite3.Row;c.set_trace_callback(self.trace.append)
            self.connections.append(c)
            return self.kb,self.conn_proxy(c) if self.conn_proxy else c
        self.native_connect=connect
        get_task=self.kb.get_task
        def task(c,tid):
            t=get_task(c,tid)
            if t:
                t.goal_mode=getattr(t,'goal_mode',False);t.title=getattr(t,'title','Unit task')
            return t
        self.kb.get_task=task
        self.kb.__file__=str(NATIVE)
        self.kb.kanban_db_path=lambda board:self.db
        self.kb.request_review=NATIVE_FUNCTIONS['request_review']
        def latest(c,tid):
            row=c.execute('SELECT * FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1',(tid,)).fetchone()
            return types.SimpleNamespace(**dict(row)) if row else None
        self.kb.latest_run=latest
        toolpath=NATIVE.parents[1]/'tools/kanban_tools.py'
        ns={'__file__':str(toolpath),'os':os,'json':json,'logger':logging.getLogger('native_tool_fixture'),
            '_is_delegated_child_context':lambda:False,'_is_dispatcher_owned_worker':lambda:True,
            'tool_error':registry.tool_error,'_connect':connect,'redact_sensitive_text':red.redact_sensitive_text,
            '_goal_judge_available':lambda:False,'judge_goal':lambda **k:('done','',None,None,None)}
        names={'_handle_request_review','_reject_delegated_child_mutation','_default_task_id','_worker_run_id',
               '_stamp_worker_session_metadata','_enforce_worker_task_ownership','_goal_mode_handoff_rejection','_ok'}
        self.ns=load_functions(toolpath,names,ns);self.native=ns['_handle_request_review']

        # Explicit UNIT admission fixture. These tests isolate transaction/SQL
        # and simulated registration; they do NOT construct a real native agent.
        # Use the real lifetime module/fence but a synthetic constructor proof.
        # Full native constructor/dispatch/close ordering lives in
        # tests_worker_lifetime, including raw-adapter-without-operation denial.
        lifetime_path=Path(__file__).parents[1]/'native_integration/factory_worker_lifetime.py'
        life_spec=importlib.util.spec_from_file_location('factory_worker_lifetime',lifetime_path)
        life=importlib.util.module_from_spec(life_spec)
        life_modules=patch.dict(sys.modules,{'factory_worker_lifetime':life})
        life_modules.start();self.addCleanup(life_modules.stop);life_spec.loader.exec_module(life)
        class UnitAgent: pass
        unit_agent=UnitAgent();unit_agent.tools=[{'function':{'name':'kanban_request_review'}}]
        unit_agent.valid_tool_names={'kanban_request_review'}
        entry=types.SimpleNamespace(handler=self.native,is_async=False)
        proof=types.SimpleNamespace(module=self.plugin,verify=lambda:None,
                                    registry=types.SimpleNamespace(get_entry=lambda name:entry))
        self.unit_lifetime_state=life.WorkerLifetime(unit_agent,proof)
        self.unit_lifetime_agent=unit_agent
        import threading
        operation=life.ToolOperation(self.unit_lifetime_state,'kanban_request_review',{},
                                     used=True,admitted=True,thread_id=threading.get_ident())
        token=life._CURRENT.set(operation);self.addCleanup(life._CURRENT.reset,token)

    def call(self,args=None):
        return json.loads(self.rt.execute(self.native,self.args if args is None else args,self.g))

    def positive(self):
        self.assertIsNone(self.before())
        result=self.call();self.assertEqual(result,{'ok':True,'task_id':self.tid,'run_id':1,'status':'review'})
        seal=self.h.load_handoff_seal(self.board,self.tid,1)
        return seal

    def refused(self,*,baseline=None,args=None):
        if baseline is None:baseline=self.rows()
        result=self.call(args);self.assertIn('error',result)
        self.assertEqual(self.rows(),baseline)
        self.assertNotIn(self.key,self.g._SEALED_CODER_RUNS)
        if self.seal_path.exists():
            with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)
        self.assertNotIn(self.key,self.g._COMPLETED_ATTESTATIONS)
        return result


class TestTransaction(TransactionFixture):
    def test_native_positive_null_pid_and_durable_receipt(self):
        seal=self.positive(); rows=self.rows()
        self.assertEqual(seal['schema'],3)
        self.assertIsNone(rows['tasks'][0]['worker_pid']);self.assertIsNone(rows['task_runs'][0]['worker_pid'])
        self.assertEqual([e['kind'] for e in rows['task_events']],['review_requested','factory_handoff_committed'])
        self.assertEqual(sum(s=='BEGIN IMMEDIATE' for s in self.trace),1)
        self.assertIn('SAVEPOINT factory_native_review',self.trace)
        self.assertIn('RELEASE SAVEPOINT factory_native_review',self.trace)
        self.assertEqual(sum(s=='COMMIT' for s in self.trace),1)
        self.assertIn(self.key,self.g._SEALED_CODER_RUNS)
        self.assertNotIn(self.key,self.g._COMPLETED_ATTESTATIONS)
        self.assertEqual(os.environ['HERMES_KANBAN_STOP_NUDGE'],'0')
        self.assertIs(self.native.__globals__['_connect'],self.native_connect)

    def test_raw_owner_reassignment_pre_to_native_rejected(self):
        self.assertIsNone(self.before())
        self.sql('UPDATE tasks SET worker_pid=worker_pid+100000')
        self.sql('UPDATE task_runs SET worker_pid=worker_pid+100000')
        self.refused()

    def test_exact_native_owner_reassignment_with_spawned_event_rejected(self):
        self.assertIsNone(self.before())
        with self.conn() as conn:NATIVE_FUNCTIONS['_set_worker_pid'](conn,self.tid,os.getpid()+100000)
        self.assertIn('spawned',[e['kind'] for e in self.rows()['task_events']]);self.refused()

    def test_no_pre_authority_rejected(self):self.refused()

    def test_pre_invalidated_by_early_denial(self):
        self.assertIsNone(self.before())
        self.assertEqual(self.plugin.on_pre_tool_call(tool_name='write_file',args={'path':'bad'})['action'],'block')
        self.refused()

    def test_argument_change_rejected(self):
        self.assertIsNone(self.before());args=copy.deepcopy(self.args);args['summary']='changed';self.refused(args=args)

    def test_native_delegation_denial_preserved(self):
        self.assertIsNone(self.before());self.ns['_is_delegated_child_context']=lambda:True
        self.assertIn('delegate_task child',self.refused()['error']);self.assertEqual(self.connections,[])

    def test_native_goal_denial_preserved(self):
        self.sql('ALTER TABLE tasks ADD COLUMN goal_mode INTEGER DEFAULT 1')
        self.assertIsNone(self.before());self.ns['_goal_judge_available']=lambda:True
        self.ns['judge_goal']=lambda **kw:('continue','acceptance missing',None,None,None)
        result=self.refused();self.assertIn('Goal review handoff rejected',result['error'])
        self.assertFalse(any(s=='BEGIN IMMEDIATE' for s in self.trace))

    def test_native_session_and_redaction_preserved(self):
        os.environ['HERMES_SESSION_ID']='UNIT_WORKER_SESSION'
        self.args['summary']='unit {"password":"UNIT_SECRET_VALUE_123456"}' 
        self.args['metadata']['password']='UNIT_SECRET_VALUE_123456'
        self.positive()
        metadata=json.loads(self.rows()['task_runs'][0]['metadata'])
        self.assertEqual(metadata['worker_session_id'],'UNIT_WORKER_SESSION')
        self.assertNotIn('UNIT_SECRET_VALUE_123456',json.dumps(self.rows()))

    def test_stale_ignored_bytes_rejected_under_lock(self):
        self.assertIsNone(self.before());(self.workspace/'ignored.txt').write_text('changed');self.refused()

    def test_mutation_after_native_rolls_back_all_three_tables(self):
        self.assertIsNone(self.before());before=self.rows();original=self.h._build_handoff_seal
        def change(*args,**kwargs):
            conn=kwargs['conn'];r=conn.execute('SELECT status,worker_pid FROM task_runs').fetchone()
            self.assertEqual(tuple(r),('review',None))
            self.assertEqual(self.rows(),before)
            (self.workspace/'ignored.txt').write_text('changed while pending')
            return original(*args,**kwargs)
        with patch.object(self.h,'_build_handoff_seal',side_effect=change):self.refused(baseline=before)
        self.assertIn('ROLLBACK',self.trace)

    def test_publish_failure_rolls_back(self):
        self.assertIsNone(self.before())
        with patch.object(self.h,'_atomic_write',side_effect=OSError('disk fixture')):self.refused()

    def test_receipt_failure_rolls_back_and_removes_own_seal(self):
        self.assertIsNone(self.before())
        with patch.object(self.receipt,'append_handoff_receipt',side_effect=sqlite3.OperationalError('fixture')):self.refused()
        self.assertFalse(self.seal_path.exists())

    def test_receipt_missing_rejects_existing_file(self):
        self.positive();self.sql("DELETE FROM task_events WHERE kind='factory_handoff_committed'")
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_committed_native_event_changed_rejected(self):
        self.positive();self.sql("UPDATE task_events SET payload='{}' WHERE kind='review_requested'")
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_committed_native_metadata_changed_rejected(self):
        self.positive();self.sql("UPDATE task_runs SET metadata=?",(json.dumps({**self.meta,'extra':'change'}),))
        with self.assertRaises(self.h.HandoffError):self.h.load_handoff_seal(self.board,self.tid,1)

    def test_post_hook_cannot_fabricate_seal(self):
        self.assertIsNone(self.before());self.after();self.assertFalse(self.seal_path.exists())
        with self.assertRaises(self.h.HandoffError):self.h.create_handoff_seal(self.result,content_state=self.g._workspace_content_state)

    def test_preargs_missing_metadata_rejected_without_mutation(self):
        before=self.rows();self.args.pop('metadata')
        self.assertEqual(self.before()['action'],'block');self.assertEqual(before,self.rows())

    def test_replay_cannot_repeat_commit_or_overwrite_seal(self):
        self.positive();before=self.rows();raw=self.seal_path.read_bytes()
        self.assertIn('error',self.call());self.assertEqual(self.rows(),before);self.assertEqual(self.seal_path.read_bytes(),raw)
        self.assertEqual(self.before()['action'],'block')

    def test_competing_writer_blocked_at_native_boundary(self):
        self.assertIsNone(self.before());original=self.kb.request_review;observed=[]
        def contend(conn,tid,**kw):
            other=sqlite3.connect(self.db,timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError) as exc:other.execute('BEGIN IMMEDIATE')
                self.assertEqual(exc.exception.sqlite_errorcode,sqlite3.SQLITE_BUSY);observed.append(True)
            finally:other.close()
            return original(conn,tid,**kw)
        with patch.object(self.kb,'request_review',side_effect=contend):
            self.assertTrue(self.call().get('ok'));self.h.load_handoff_seal(self.board,self.tid,1)
        self.assertEqual(observed,[True])

if __name__=='__main__':unittest.main()
