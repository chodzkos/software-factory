from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kanban_review_dispatch as dispatch


class FakeKanbanDB:
    def __init__(self, task, workspace: str, *, auto_review: bool = False, fail_spawn: bool = False):
        self.task = task
        self.workspace = workspace
        self.auto_review = auto_review
        self.fail_spawn = fail_spawn
        self.claimed_ids: list[str] = []
        self.spawned: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.worker_pids: list[tuple[str, int]] = []
        self.hooks: list[tuple[str, int | None]] = []
        self.failures: list[tuple[str, str]] = []

    def review_dispatch_enabled(self):
        return self.auto_review

    def get_current_board(self):
        return "default"

    def connect_closing(self, *, board=None):
        return contextlib.nullcontext(self)

    def get_task(self, conn, task_id):
        return self.task if task_id == self.task.id else None

    def claim_review_task(self, conn, task_id):
        self.claimed_ids.append(task_id)
        if self.task.status != "review":
            return None
        self.task.status = "running"
        self.task.current_run_id = 18
        return self.task

    def resolve_workspace(self, claimed, *, board=None):
        return Path(self.workspace)

    def set_workspace_path(self, conn, task_id, workspace):
        self.task.workspace_path = workspace

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
    def _fixture(self, *, status="review", auto_review=False, fail_spawn=False):
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
            current_run_id=17,
            skills=[],
        )
        kb = FakeKanbanDB(
            task,
            str(worktree),
            auto_review=auto_review,
            fail_spawn=fail_spawn,
        )
        return td, snap, kb

    def test_dispatches_exact_validated_review_task_only(self):
        td, snap, kb = self._fixture()
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 0)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(len(kb.spawned), 1)
        task_id, profile, workspace, skills = kb.spawned[0]
        self.assertEqual(task_id, "t_live")
        self.assertEqual(profile, "reviewer-gpt")
        self.assertEqual(workspace, snap["task"]["workspace_path"])
        self.assertIn("sdlc-review", skills)
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

    def test_spawn_failure_is_recorded_after_claim(self):
        td, snap, kb = self._fixture(fail_spawn=True)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = dispatch.dispatch_review("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(len(kb.failures), 1)
        self.assertIn("synthetic spawn failure", kb.failures[0][1])


if __name__ == "__main__":
    unittest.main()
