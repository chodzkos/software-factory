from __future__ import annotations

import contextlib
import copy
import json
import os
import sqlite3
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kanban_review_dispatch as dispatch


class FakeCursor:
    def __init__(self, row=None, *, rowcount=-1):
        self.row = row
        self.rowcount = rowcount

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
        self.reviewer_metadata: dict = {}

    def review_dispatch_enabled(self):
        return self.auto_review

    def board_exists(self, board):
        return board == "isolated"

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
        if sql.startswith("SELECT metadata FROM task_runs"):
            return FakeCursor({"metadata": json.dumps(self.reviewer_metadata)})
        if sql.startswith("UPDATE task_runs SET metadata"):
            self.reviewer_metadata = json.loads(parameters[0])
            return FakeCursor(rowcount=1)
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
    def _dispatch(self, task_id: str, *, snapshot: dict, kb: FakeKanbanDB) -> int:
        seal = {
            "board": "isolated",
            "seal_id": "a" * 64,
            "git_head": "c" * 40,
            "content_state_sha256": "b" * 64,
        }
        with patch.object(
            dispatch._HANDOFF,
            "validate_handoff_seal",
            return_value=(seal, []),
        ):
            return dispatch.dispatch_review(task_id, board="isolated", snapshot=snapshot, kb=kb)

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
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
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
                self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(kb.claimed_ids, [])

    def test_refuses_state_drift_before_claim(self):
        td, snap, kb = self._fixture(status="running")
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_refuses_active_run_before_review_claim(self):
        td, snap, kb = self._fixture(current_run_id=99)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_refuses_locked_provenance_change_before_claim(self):
        td, snap, kb = self._fixture()
        self.addCleanup(td.cleanup)
        kb.snapshot["runs"][-1]["profile"] = "coder"
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, [])
        self.assertEqual(kb.spawned, [])

    def test_rolls_back_claim_if_claimed_values_differ_from_authorized_values(self):
        td, snap, kb = self._fixture(mutate_inside_claim=True)
        self.addCleanup(td.cleanup)
        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None):
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
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
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.claimed_ids, ["t_live"])
        self.assertEqual(len(kb.failures), 1)
        self.assertIn("synthetic spawn failure", kb.failures[0][1])

    def test_mutation_before_or_during_claim_rolls_back_without_spawn(self):
        for target in ("before_locked_validation", "before_claim", "during_claim"):
            with self.subTest(target=target):
                td, snap, kb = self._fixture()
                self.addCleanup(td.cleanup)
                drifted = False

                def hook(stage):
                    nonlocal drifted
                    if stage == target:
                        drifted = True

                def seal_drift(**_):
                    return ["handoff_content_state_mismatch"] if drifted else []

                with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None), patch.object(
                    dispatch, "_TEST_MUTATION_HOOK", hook
                ), patch.object(dispatch, "_seal_drift", side_effect=seal_drift):
                    rc = self._dispatch("t_live", snapshot=snap, kb=kb)
                self.assertEqual(rc, 2)
                self.assertEqual(kb.spawned, [])
                self.assertEqual(kb.task.status, "review")
                self.assertIsNone(kb.task.current_run_id)

    def test_mutation_after_commit_before_spawn_records_fail_closed_state(self):
        td, snap, kb = self._fixture()
        self.addCleanup(td.cleanup)
        drifted = False

        def hook(stage):
            nonlocal drifted
            if stage == "before_spawn":
                drifted = True

        def seal_drift(**_):
            return ["handoff_content_state_mismatch"] if drifted else []

        with patch.object(dispatch, "_assert_expected_hermes_version", return_value=None), patch.object(
            dispatch, "_TEST_MUTATION_HOOK", hook
        ), patch.object(dispatch, "_seal_drift", side_effect=seal_drift):
            rc = self._dispatch("t_live", snapshot=snap, kb=kb)
        self.assertEqual(rc, 2)
        self.assertEqual(kb.spawned, [])
        self.assertEqual(kb.task.status, "running")
        self.assertEqual(len(kb.failures), 1)
        self.assertIn("handoff_content_state_mismatch", kb.failures[0][1])

    def test_savepoint_proxy_composes_with_outer_sqlite_transaction(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE values_table(value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        proxy = dispatch._SavepointConnection(conn)
        self.assertFalse(proxy.in_transaction)
        proxy.execute("BEGIN IMMEDIATE")
        self.assertTrue(proxy.in_transaction)
        proxy.execute("INSERT INTO values_table(value) VALUES ('inside')")
        proxy.execute("COMMIT")
        self.assertFalse(proxy.in_transaction)
        self.assertEqual(conn.execute("SELECT value FROM values_table").fetchone()[0], "inside")
        conn.execute("ROLLBACK")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0], 0)

    def test_native_hermes_write_txn_and_claim_review_task_compose(self):
        candidates = (
            Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python",
            Path.home() / ".hermes" / "hermes-agent" / ".venv" / "bin" / "python",
        )
        hermes_python = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_file() and os.access(candidate, os.X_OK)
            ),
            None,
        )
        if hermes_python is None:
            self.skipTest("Hermes-managed Python is unavailable for native claim regression")

        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            import os
            import sys
            import tempfile
            from pathlib import Path
            from unittest.mock import patch

            repo_root = Path(sys.argv[1]).resolve()
            sys.path.insert(0, str(repo_root / "hermes"))
            import kanban_review_dispatch as dispatch

            class ExpectedOuterRollback(RuntimeError):
                pass

            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                home = root / ".hermes"
                home.mkdir()
                os.environ["HOME"] = str(root)
                os.environ["HERMES_HOME"] = str(home)

                with patch.object(Path, "home", return_value=root):
                    from hermes_cli import kanban_db as kb

                    kb.init_db()
                    with kb.connect() as conn:
                        task_id = kb.create_task(
                            conn,
                            title="native targeted review savepoint regression",
                            assignee="coder-claude",
                        )
                        implementation = kb.claim_task(conn, task_id)
                        assert implementation is not None
                        implementer_run_id = kb.get_task(conn, task_id).current_run_id
                        assert type(implementer_run_id) is int
                        assert kb.request_review(
                            conn,
                            task_id,
                            summary="ready for native review claim",
                            reviewer="reviewer-gpt",
                            expected_run_id=implementer_run_id,
                        ) is True

                        before_runs = conn.execute(
                            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0]
                        before_events = conn.execute(
                            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0]

                        try:
                            with kb.write_txn(conn):
                                proxy = dispatch._SavepointConnection(conn)
                                assert proxy.in_transaction is False
                                review = kb.claim_review_task(proxy, task_id)
                                assert review is not None
                                assert review.assignee == "reviewer-gpt"
                                assert type(review.current_run_id) is int
                                assert review.current_run_id != implementer_run_id
                                assert proxy.in_transaction is False
                                raise ExpectedOuterRollback("prove native claim rollback")
                        except ExpectedOuterRollback:
                            pass

                        rolled_back = kb.get_task(conn, task_id)
                        assert rolled_back.status == "review"
                        assert rolled_back.assignee == "reviewer-gpt"
                        assert rolled_back.current_run_id is None
                        assert conn.execute(
                            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0] == before_runs
                        assert conn.execute(
                            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0] == before_events

                        with kb.write_txn(conn):
                            proxy = dispatch._SavepointConnection(conn)
                            assert proxy.in_transaction is False
                            review = kb.claim_review_task(proxy, task_id)
                            assert review is not None
                            assert proxy.in_transaction is False

                        committed = kb.get_task(conn, task_id)
                        assert committed.status == "running"
                        assert committed.assignee == "reviewer-gpt"
                        assert type(committed.current_run_id) is int
                        assert committed.current_run_id != implementer_run_id
                        assert conn.execute(
                            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0] == before_runs + 1
                        assert conn.execute(
                            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0] == before_events + 1

            print("NATIVE_HERMES_WRITE_TXN_CLAIM_OK")
            """
        )
        result = subprocess.run(
            [str(hermes_python), "-I", "-c", script, str(repo_root)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            env={
                "HOME": str(Path.home()),
                "PATH": os.defpath,
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertIn("NATIVE_HERMES_WRITE_TXN_CLAIM_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
