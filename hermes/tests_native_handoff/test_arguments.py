"""Zamknięte argumenty i tożsamość przed rozpoczęciem natywnej mutacji."""
from __future__ import annotations
import copy
import os
import unittest
from test_transaction import TransactionFixture


class TestArguments(TransactionFixture):
    def test_invalid_metadata_and_identity_table(self):
        bad=[
            ('metadata omitted',lambda a:a.pop('metadata')),
            ('metadata null',lambda a:a.update(metadata=None)),
            ('metadata wrong task',lambda a:a['metadata'].update(task_id='other')),
            ('workspace conflict',lambda a:a['metadata'].update(workspace_path='/tmp/other')),
            ('workspace alias',lambda a:a['metadata'].update(workspace=str(self.workspace)+'/')),
            ('wrong board',lambda a:a.update(board='another')),
            ('wrong task',lambda a:a.update(task_id='other')),
            ('wrong reviewer',lambda a:a.update(reviewer='reviewer-claude')),
            ('reviewer omitted',lambda a:a.pop('reviewer')),
            ('force argument',lambda a:a.update(force=True)),
            ('blank summary',lambda a:a.update(summary='  ')),
            ('non-JSON value',lambda a:a['metadata'].update(bad=set())),
        ]
        before=self.rows()
        for label,mutate in bad:
            with self.subTest(case=label):
                args=copy.deepcopy(self.args);mutate(args)
                self.assertEqual(self.before(args)['action'],'block')
                self.assertEqual(self.rows(),before);self.assertFalse(self.seal_path.exists())
                self.assertIsNone(self.ri._PENDING)

    def test_explicit_canonical_task_and_board_success(self):
        self.args.update(task_id=self.tid,board=self.board);self.positive()

    def test_cancelled_active_run_after_pre_refused(self):
        self.assertIsNone(self.before())
        self.sql("UPDATE task_runs SET status='cancelled',outcome='cancelled'")
        self.sql("UPDATE tasks SET status='blocked'")
        self.refused()

    def test_current_run_must_be_exact_integer(self):
        self.sql('UPDATE tasks SET current_run_id=2')
        self.assertEqual(self.before()['action'],'block')
