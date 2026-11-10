"""Adwersarialna recenzja: prawdziwa pieczęć schema 3, receipt i pełny natywny SQL."""
from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes.tests_support.native_review_fixture import NativeReviewWorker
from hermes.tests_support.protected_profile_fixture import NativeProtectedProfile


class AtomicApprovalIntegrationTests(unittest.TestCase):
    @contextlib.contextmanager
    def _fixture(self, root: Path):
        with NativeReviewWorker(root, task_id="t_atomic", run_id=1) as worker:
            worker.attest()
            seal = worker.publish()
            worker.start_reviewer(seal)
            # Symulowany jest wyłącznie odczyt jądra po zakończeniu implementera.
            # Nie modyfikujemy pieczęci ani trwałego receipt, które wiążą jego start.
            identity = (seal["implementer_pid"], str(int(seal["implementer_proc_start"]) + 1))
            with NativeProtectedProfile(root, "reviewer-gpt", database=worker) as reviewer:
                self.reviewer, self.handoff = reviewer, reviewer.handoff
                with patch.object(self.handoff, "_process_identity", return_value=identity):
                    with worker.kb.connect_closing(board=worker.board) as conn:
                        self.assertTrue(self.handoff.reviewer_completion_authorized(content_state=self.handoff.workspace_content_state))
                        yield worker, conn, worker.workspace / "tracked.txt"

    @staticmethod
    def _tables(conn):
        return {name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid")]
                for name in ("tasks", "task_runs", "task_events")}

    def test_mutation_immediately_after_validation_or_during_completion_rolls_back(self):
        for phase in ("after_initial_validation", "after_native_complete"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as td, self._fixture(Path(td)) as (worker, conn, tracked):
                before = self._tables(conn)
                observed = []

                # Wstrzyknięcie należy wyłącznie do harnessu. Produkt nie udostępnia
                # wykonywalnego callbacku testowego wewnątrz transakcji zatwierdzania.
                original_context = self.handoff._reviewer_approval_context
                original_complete = worker.kb.complete_task
                def context(*args, **kwargs):
                    result = original_context(*args, **kwargs)
                    if phase == "after_initial_validation" and not observed:
                        observed.append(phase)
                        tracked.write_text("mutated\n", encoding="utf-8")
                    return result
                def complete(*args, **kwargs):
                    result = original_complete(*args, **kwargs)
                    if phase == "after_native_complete":
                        self.assertTrue(result, "natywny complete musi faktycznie wykonać SQL")
                        observed.append(phase)
                        tracked.write_text("mutated\n", encoding="utf-8")
                    return result
                with patch.object(self.handoff, "_reviewer_approval_context", side_effect=context), \
                        patch.object(worker.kb, "complete_task", side_effect=complete):
                    result = json.loads(self.reviewer.approve())
                    self.assertIn("error", result, "real registered approval must refuse mutated bytes")
                self.assertIn(phase, observed, "odmowa nie może nastąpić przed zaplanowaną mutacją")
                self.assertEqual(self._tables(conn), before)
                self.assertEqual(worker.kb.get_task(conn, "t_atomic").status, "running")
                self.assertEqual(conn.execute("SELECT status FROM task_runs WHERE id=2").fetchone()[0], "running")

    def test_unchanged_passes_but_post_done_or_delayed_mutation_fails_release_check(self):
        with tempfile.TemporaryDirectory() as td, self._fixture(Path(td)) as (worker, conn, tracked):
            self.assertTrue(json.loads(self.reviewer.approve())["ok"])
            self.assertEqual(worker.kb.get_task(conn, "t_atomic").status, "done")
            self.assertEqual(conn.execute("SELECT status FROM task_runs WHERE id=2").fetchone()[0], "done")
            self.assertTrue(self.handoff.verify_downstream_approval("isolated", "t_atomic")["ok"])
            with self.assertRaises(self.handoff.HandoffError):
                self.handoff.verify_downstream_approval("other", "t_atomic")
            tracked.write_text("post approval or delayed writer\n", encoding="utf-8")
            with self.assertRaises(self.handoff.HandoffError):
                self.handoff.verify_downstream_approval("isolated", "t_atomic")


if __name__ == "__main__":
    unittest.main()
