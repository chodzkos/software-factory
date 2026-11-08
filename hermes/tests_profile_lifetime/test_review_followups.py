"""ROUND2 review test gaps; no claim of actual CLI or model provenance."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('profile_followup_review_fixture',ROOT/'hermes/tests_review_revisions/test_review_revisions.py')
previous=importlib.util.module_from_spec(spec);sys.modules[spec.name]=previous;spec.loader.exec_module(previous)
from hermes.tests_support.native_review_fixture import NativeReviewWorker


class ReviewFollowupTests(unittest.TestCase):
    def test_h_d_final_call_reaches_its_own_validation_on_live_worker(self):
        fx=previous.ReviewRuntimeTests('test_d02_original_chat_surface_is_accepted');fx.setUp()
        try:
            payload=fx._chat();fx.life.record_model_payload(fx.agent,payload)
            self.assertFalse(fx.state.revoked.is_set(),'must not test already revoked worker')
            payload['_moa_prepared_request']={'tools':[{'type':'web_search'}]}
            client=Mock()
            with patch.object(fx.state,'envelope',wraps=fx.state.envelope) as validation:
                with self.assertRaisesRegex(fx.life.WorkerLifetimeError,'top-level extension'):
                    fx.life.checked_model_call(fx.agent,payload,client)
            validation.assert_called_once();client.assert_not_called()
            self.assertTrue(fx.state.revoked.is_set())
        finally:fx.tearDown()

    def test_evidence_file_without_injected_nonce_never_authorizes_handoff(self):
        with tempfile.TemporaryDirectory() as td,NativeReviewWorker(Path(td)) as worker:
            evidence=worker.attest(register_nonce=False)
            path=worker.handoff.execution_evidence_path(worker.board,worker.task_id,worker.run_id)
            self.assertEqual(json.loads(path.read_text()),evidence)
            key=worker.guard._attestation_key('coder-claude',worker.task_id,str(worker.run_id))
            self.assertNotIn(key,worker.guard._COMPLETED_ATTESTATIONS)
            with worker.kb.connect_closing(board=worker.board) as conn:
                tables=lambda:{n:[tuple(r) for r in conn.execute('SELECT * FROM '+n+' ORDER BY rowid')]
                               for n in ('tasks','task_runs','task_events')}
                before=tables()
                with patch.object(worker.kb,'request_review',wraps=worker.kb.request_review) as sql:
                    result=worker.agent._invoke_tool('kanban_request_review',
                        {'summary':'NO_CLI_PROVENANCE','reviewer':'reviewer-gpt','metadata':worker.metadata},worker.task_id)
                self.assertIn('error',json.loads(result));sql.assert_not_called();self.assertEqual(tables(),before)
                self.assertFalse(worker.handoff.handoff_path(worker.board,worker.task_id,worker.run_id).exists())
