from __future__ import annotations

import contextlib
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kanban_review_dispatch as dispatch


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeKanbanDB:
    def __init__(
        self,
        task,
        snapshot: dict,
        workspace: str,
        *,
        auto_review: bool = False,
        fail_spawn: bool = False,
        mutate_inside_claim: bool = False,
    ):
        self.task = task
        self.snapshot = snapshot
        self.workspace = workspace
        self.auto_review = auto_review
        self.fail_spawn = fail_spawn
        self.mutate_inside_claim = mutate_inside_claim
        self.claimed_ids: list[str] = []
        self.spawned: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.worker_pids: list[tuple[str, int]] = []
        self.hooks: list[tuple[str, int | None]] = []
        self.failures: list[tuple[str, str]] = []
        self.branch_updates: list[tuple[str, str]] = []
        self.write_txn_calls = 0
        self.in_write_txn = False

    def review_dispatch_enabled(self):
        return self.auto_review

    def get_current_board(self):
        return "default"

    def connect_closing(self, *, board=None):
        return contextlib.nullcontext(self)

    @contextlib.contextmanager
    def write_txn(self, conn):
        self.write_txn_calls += 1
        before = copy.deepcopy(self.task)
        self.in_write_txn = True
        try:
            yield conn
        except Exception:
            self.task = before
            raise
        finally:
            self.in_write_txn = False

    def execute(self, sql, parameters=()):
        if "FROM task_events" in sql:
            event = self.snapshot["events"][-1]
            return FakeCursor({
                "run_id": event["run_id"],
                "payload": json.dumps(event["payload"]),
            })
        if "FROM task_runs" in sql:
            run = self.snapshot["runs"][-1]
            return FakeCursor({
                "id": run["id"],
                "profile": run["profile"],
                "outcome": run["outcome"],
                "metadata": json.dumps(run["metadata"]),
            })
        raise AssertionError(f"unexpected SQL in fake: {sql}")

    def get_task(self, conn, task_id):
        return self.task if task_id == self.task.id else None

    def claim_review_task(self, conn, task_id):
        if not self.in_write_txn:
            raise AssertionError("claim must be called under outer writer transaction")
        self.claimed_ids.append(task_id)
        if self.task.status != "review":
            return None
        if self.mutate_inside_claim:
            self.task.assignee = "coder"
            self.task.body = "MUTATED AFTER VALIDATION"
        self.task.status = "running"
        self.task.current_run_id = 18
        return self.task

    def _resolve_worktree_workspace(self, claimed, *, board=None):
        return Path(self.workspace), claimed.branch_name

    def set_workspace_path(self, conn, task_id, workspace):
        self.task.workspace_path = workspace

    def set_branch_name(self, conn, task_id, branch_name):
        self.task.branch_name = branch_name
        self.branch_updates.append((task_id, branch_name))

    def _default_spawn(self, claimed, workspace, *, board=None):
        if self.fail_spawn:
            raise RuntimeError("synthetic spawn failure")
        self.spawned.append(
            (
                claimed.id,
                claimed.assignee,
                workspace,
                tuple(claimed.skills or []),
            )
        )
        return 4242

    def _set_worker_pid(self, conn, task_id, pid):
        self.worker_pids.append((task_id, pid))

    def _fire_worker_spawned_hook(self, conn, claimed, workspace, pid, *, board=None):
        self.hooks.append((claimed.id, pid))

    def _record_spawn_failure(self, conn, task_id, error):
        self.failures.append((task_id, error))
        return False


def _snapshot(repo: Path, worktree: Path) -> dict:
    body = f"""## Task Contract
TYPE: feature
RISK: high
SECURITY_SENSITIVE: yes
ASSIGNEE: coder-claude
REPOSITORY: {repo}
WORKSPACE: worktree:{repo}
IMPLEMENTER: coder-claude
REQUIRED_REVIEWERS: reviewer-gpt
OPTIONAL_REVIEWERS: none
REQUIRED_EVIDENCE: guarded implementation and routed handoff
ACCEPTANCE_CRITERIA:
- test
"""
    return {
        "task": {
            "id": "t_live",
            "body": body,
            "assignee": "reviewer-gpt",
            "status": "review",
            "workspace_kind": "worktree",
            "workspace_path": str(worktree),
            "branch_name": "e2e/t_live",
            "skills": [],
        },
        "events": [
            {
                "kind": "review_requested",
                "payload": {
                    "implementer": "coder-claude",
                    "reviewer": "reviewer-gpt",
                },
                "run_id": 17,
            }
        ],
        "runs": [
            {
                "id": 17,
                "profile": "coder-claude",
                "outcome": "review_requested",
                "metadata": {
                    "task_id": "t_live",
                    "workspace_path": str(worktree),
                },
            }
        ],
    }


class TargetedReviewDispatchTests(unittest.TestCase):
    def _fixture(
        self,
        *,
        status="review",
        auto_review=False,
        fail_spawn=False,
        current_run_id=None,
        mutate_inside_claim=False,
    ):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        repo = root / "repo"
        worktree = repo / ".worktrees" / "t_live"
        worktree.mkdir(parents=True)
        snap = _snapshot(repo, worktree)
        task = SimpleNamespace(
            id="t_live",
            body=snap["task"]["body"],
            assignee="reviewer-gpt",
            status=status,
            workspace_kind="worktree",
            workspace_path=str(worktree),
            branch_name="e2e/t_live",
            current_run_id=current_run_id,
            skills=[],
        )
        kb = FakeKanbanDB(
            task,
            snap,
            str(worktree),
            auto_review=auto_review,
            fail_spawn=fail_spawn,
            mutate_inside_claim=mutate_inside_claim,
        )
        return td, snap, kb

    def test_dispatches_exact_validated_review_task_only(self):
        td, snap, kb = self._fixture()
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 0)
        self.assertEqual(kb.write_txn_calls, 1)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(len(kb.spawned), 1)
        task_id, profile, workspace, skills = kb.spawned[0]
        self.assertEqual(task_id, "t_live")
        self.assertEqual(profile, "reviewer-gpt")
        self.assertEqual(workspace, snap["task"]["workspace_path"])
        self.assertIn("sdlc-review", skills)
        self.assertEqual(kb.branch_updates, [("t_live", "e2e/t_live")])
        self.assertEqual(kb.worker_pids, [("t_live", 4242)])
        self.assertEqual(kb.hooks, [("t_live", 4242)])
        self.assertEqual(kb.failures, [])

    def test_refuses_when_global_review_autodispatch_is_enabled(self):
        td, snap, kb = self._fixture(auto_review=True)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "review_dispatch must be false"):
                dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(kb.claimed_ids, [])

    def test_refuses_state_drift_before_claim(self):
        td, snap, kb = self._fixture(status="running")
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_refuses_active_run_before_review_claim(self):
        td, snap, kb = self._fixture(current_run_id=99)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_refuses_locked_provenance_change_before_claim(self):
        td, snap, kb = self._fixture()
        self.addCleanup(td.cleanup)
        kb.snapshot["runs"][-1]["profile"] = "coder"
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_rolls_back_claim_if_claimed_values_differ_from_authorized_values(self):
        td, snap, kb = self._fixture(mutate_inside_claim=True)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(kb.spawned, [])
        self.assertEqual(kb.task.status, "review")
        self.assertEqual(kb.task.assignee, "reviewer-gpt")
        self.assertEqual(kb.task.body, snap["task"]["body"])
        self.assertIsNone(kb.task.current_run_id)

    def test_spawn_failure_is_recorded_after_claim(self):
        td, snap, kb = self._fixture(fail_spawn=True)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(len(kb.failures), 1)
        self.assertIn("synthetic spawn failure", kb.failures[0][1])

    def test_savepoint_proxy_composes_with_outer_sqlite_transaction(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE values_table(value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        proxy = dispatch._SavepointConnection(conn)
        proxy.execute("BEGIN IMMEDIATE")
        proxy.execute("INSERT INTO values_table(value) VALUES ('inside')")
        proxy.execute("COMMIT")
        self.assertEqual(conn.execute("SELECT value FROM values_table").fetchone()[0], "inside")
        conn.execute("ROLLBACK")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
