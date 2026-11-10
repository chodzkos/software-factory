"""Odczyt standalone i same-card approval; dispatcher/judge/rejestr są fixture."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
import unittest
from unittest.mock import patch

import test_failure_cases as failure_cases
from fixture import NATIVE, NATIVE_FUNCTIONS, PLUGIN
from test_transaction import TransactionFixture, load_functions


class TestConsumers(TransactionFixture):
    _crash=failure_cases.TestFailureCases._crash

    def test_standalone_handoff_file_reads_committed_receipt(self):
        expected=self.positive()
        # Ten sam tryb importu, co factory_handoff_seal / kanban_runtime_contract.
        spec=importlib.util.spec_from_file_location('standalone_handoff_fixture',PLUGIN/'handoff.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module,'_load_kanban_db',return_value=self.kb):
            self.assertEqual(module.load_handoff_seal(self.board,self.tid,1),expected)
            self.sql("DELETE FROM task_events WHERE kind='factory_handoff_committed'")
            with self.assertRaises(module.HandoffError):module.load_handoff_seal(self.board,self.tid,1)

    def _registry(self,*,profile='coder-claude',authorize=True):
        from registration_fixture import RegistrationHost
        host = RegistrationHost(self, profile=profile, authorize=authorize)
        self.rt.register(host, self.g)
        return host.recorder, host.schema

    def test_scoped_adapter_preserves_registered_tool_name_and_schema(self):
        recorder,schema=self._registry();self.assertEqual(len(recorder),1)
        registration=recorder[0];self.assertEqual(registration['name'],'kanban_request_review')
        self.assertEqual(registration['schema'],schema);self.assertIsNot(registration['schema'],schema);self.assertTrue(registration['override'])
        self.assertIsNone(self.before())
        result=json.loads(registration['handler'](self.args));self.assertTrue(result.get('ok'))
        self.h.load_handoff_seal(self.board,self.tid,1)

    def test_registry_override_denial_fails_registration(self):
        with self.assertRaises(self.h.HandoffError):self._registry(authorize=False)

    def test_non_coder_profile_no_handoff_override(self):
        for profile in ['reviewer-gpt','reviewer-claude','release-manager','default']:
            with self.subTest(profile=profile):self.assertEqual(self._registry(profile=profile)[0],[])

    def _setup_reviewer(self):
        # Implementer faktycznie zakończył się; claim recenzenta to jawny fixture,
        # nie dowód uruchomienia actual targeted dispatcher albo modelu GPT.
        self._crash('after_commit')
        seal=self.h.load_handoff_seal(self.board,self.tid,1)
        metadata={'factory_handoff_schema':3,'factory_handoff_board':self.board,
                  'factory_handoff_implementer_run_id':1,'factory_handoff_seal_id':seal['seal_id'],
                  'factory_handoff_content_state_sha256':seal['content_state_sha256'],
                  'factory_handoff_git_head':seal['git_head']}
        with self.conn() as conn:
            for name,typ in [('result','TEXT'),('completed_at','INTEGER'),('block_kind','TEXT'),('block_recurrences','INTEGER'),('consecutive_failures','INTEGER')]:
                conn.execute('ALTER TABLE tasks ADD COLUMN '+name+' '+typ)
            conn.execute("UPDATE tasks SET status='running',current_run_id=2,worker_pid=?,claim_lock='review-owner',claim_expires=?",(os.getpid(),int(time.time())+600))
            conn.execute('INSERT INTO task_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(2,self.tid,'reviewer-gpt','running',int(time.time()),None,None,None,json.dumps(metadata),None,'review-owner',int(time.time())+600,os.getpid()))
            conn.commit()
        os.environ['HERMES_PROFILE']='reviewer-gpt';os.environ['HERMES_KANBAN_RUN_ID']='2'
        load_functions(NATIVE,{'complete_task'},NATIVE_FUNCTIONS)
        # Brak artefaktów/children/scratch w fixture. Te otaczające podsystemy
        # pozostają kontrolowane; właściwe task/run/event SQL jest oryginalne.
        NATIVE_FUNCTIONS.update(
            _merge_completion_prose_artifacts=lambda conn,tid,metadata,**kw:dict(metadata),
            _persist_scratch_completion_artifacts=lambda *a,**kw:None,
            _scan_prose_for_phantom_ids=lambda *a:[],_clear_failure_counter=lambda *a:None,
            recompute_ready=lambda *a:0,_cleanup_workspace=lambda *a:None,
            get_task=self.kb.get_task)
        self.kb.write_txn=NATIVE_FUNCTIONS['write_txn'];self.kb.complete_task=NATIVE_FUNCTIONS['complete_task']
        # Explicit UNIT admission, not a native constructor or dispatcher.
        # Old direct SQL consumer tests now model the required reviewer operation.
        # Assertions and original SQL bodies are unchanged; full native ordering
        # is separately covered by tests_profile_lifetime and main atomic tests.
        life=sys.modules['factory_worker_lifetime']
        import threading
        class UnitReviewer: pass
        agent=UnitReviewer();agent.tools=[{'function':{'name':'factory_review_approve'}}]
        agent.valid_tool_names={'factory_review_approve'}
        entry=types.SimpleNamespace(handler=self.h.guarded_reviewer_complete,is_async=False)
        proof=types.SimpleNamespace(module=self.plugin,profile='reviewer-gpt',verify=lambda:None,
                                    registry=types.SimpleNamespace(get_entry=lambda name:entry))
        state=life.WorkerLifetime(agent,proof)
        self.unit_reviewer_agent,self.unit_reviewer_state=agent,state
        operation=life.ToolOperation(state,'factory_review_approve',{},used=True,admitted=True,
                                     thread_id=threading.get_ident())
        token=life._CURRENT.set(operation);self.addCleanup(life._CURRENT.reset,token)
        return seal

    @unittest.skipUnless(hasattr(os,'fork'),'Wymagany Linux/POSIX')
    def test_same_card_native_completion_and_downstream_receipt_validation(self):
        seal=self._setup_reviewer()
        result=self.h.guarded_reviewer_complete({'summary':'Unit approval; not real model review'})
        self.assertTrue(result['ok']);self.assertEqual(self.rows()['tasks'][0]['status'],'done')
        checked=self.h.verify_downstream_approval(self.board,self.tid)
        self.assertTrue(checked['ok']);self.assertEqual(checked['factory_approval_seal_id'],seal['seal_id'])
        self.assertEqual(self.rows()['task_runs'][0]['status'],'review')
        self.assertIsNone(self.rows()['task_runs'][0]['worker_pid'])
        self.sql("DELETE FROM task_events WHERE kind='factory_handoff_committed'")
        with self.assertRaises(self.h.HandoffError):self.h.verify_downstream_approval(self.board,self.tid)

    @unittest.skipUnless(hasattr(os,'fork'),'Wymagany Linux/POSIX')
    def test_same_card_approval_mutation_rolls_back_same_connection(self):
        self._setup_reviewer();before=self.rows();original=self.kb.complete_task;observed=[]
        def complete(conn,tid,**kwargs):
            result=original(conn,tid,**kwargs)
            self.assertEqual(conn.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'done')
            self.assertEqual(self.rows(),before)
            (self.workspace/'ignored.txt').write_text('changed during approval');observed.append(True)
            return result
        with patch.object(self.kb,'complete_task',side_effect=complete):
            with self.assertRaisesRegex(self.h.HandoffError,'approved bytes changed'):
                self.h.guarded_reviewer_complete({'summary':'Unit approval'})
        self.assertEqual(observed,[True]);self.assertEqual(self.rows(),before)
