"""Real filesystem/SQLite adversarial tests for guarded approval."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes.test_v110_security_findings import HANDOFF


class ApprovalKB:
    def __init__(self, conn, repo: Path, workspace: Path):
        self.conn = conn
        self.repo = repo
        self.workspace = workspace
        self.task_status = "running"
        self.current_run_id = 2
        self.complete_hook = None

    def board_exists(self, board): return board == "isolated"
    def get_current_board(self): return "isolated"
    def connect_closing(self, *, board=None): return contextlib.nullcontext(self.conn)
    def get_task(self, _conn, task_id):
        if task_id != "t_atomic": return None
        return SimpleNamespace(
            id="t_atomic", body=f"WORKSPACE: worktree:{self.repo}\n", status=self.task_status,
            assignee="reviewer-gpt", current_run_id=self.current_run_id,
            workspace_kind="worktree", workspace_path=str(self.workspace),
        )

    @contextlib.contextmanager
    def write_txn(self, target):
        target.execute("BEGIN IMMEDIATE")
        try:
            yield
            target.execute("COMMIT")
        except Exception:
            target.execute("ROLLBACK")
            self.task_status = "running"; self.current_run_id = 2
            raise

    def complete_task(self, target, task_id, *, summary, metadata, expected_run_id, fire_lifecycle_hook):
        target.execute("BEGIN IMMEDIATE")
        row = target.execute("SELECT metadata FROM task_runs WHERE id=2").fetchone()
        merged = json.loads(row["metadata"]); merged.update(metadata)
        target.execute(
            "UPDATE task_runs SET status='done',ended_at=1,outcome='completed',metadata=? WHERE id=2",
            (json.dumps(merged),),
        )
        self.task_status = "done"; self.current_run_id = None
        if callable(self.complete_hook): self.complete_hook()
        target.execute("COMMIT")
        return expected_run_id == 2


class AtomicApprovalIntegrationTests(unittest.TestCase):
    def _fixture(self, root: Path):
        repo = root / "repo"; workspace = repo / ".worktrees" / "t_atomic"; workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
        tracked = workspace / "tracked.txt"; tracked.write_text("approved\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
        state = HANDOFF.workspace_content_state(str(workspace)); assert state is not None
        evidence = {
            "schema": 6, "profile": "coder-claude", "task_id": "t_atomic", "run_id": "1",
            "model_class": "sonnet", "workspace": str(workspace), "execution_cwd": str(workspace),
            "terminal_args_sha256": "1"*64, "claude_binary": "/opt/claude", "claude_binary_sha256": "2"*64,
            "session_id": "session", "success": True, "command_sha256": "3"*64, "attestation_id": "4"*64,
            "git_head_before": state[0], "git_head_after": state[0],
            "workspace_content_state_before_sha256": state[1], "workspace_content_state_after_sha256": state[1],
            "recorded_at": 1,
        }
        evidence_path = HANDOFF.execution_evidence_path("isolated", "t_atomic", 1)
        evidence_path.parent.mkdir(parents=True); evidence_raw = HANDOFF._canonical_json(evidence)+b"\n"; evidence_path.write_bytes(evidence_raw)
        core = {
            "schema":2,"board":"isolated","task_id":"t_atomic","implementer_profile":"coder-claude","implementer_run_id":1,
            "reviewer_profile":"reviewer-gpt","workspace":str(workspace),"git_head":state[0],"content_state_sha256":state[1],
            "execution_evidence_path":str(evidence_path),"execution_evidence_sha256":hashlib.sha256(evidence_raw).hexdigest(),
            "attestation_id":"4"*64,"command_sha256":"3"*64,"terminal_args_sha256":"1"*64,
            "review_event_id":1,"review_event_created_at":1,"implementer_pid":99999999,"implementer_proc_start":"1","created_at":1,
        }
        core["seal_id"] = hashlib.sha256(HANDOFF.HANDOFF_DOMAIN.encode("ascii")+b"\0"+HANDOFF._canonical_json(core)).hexdigest()
        HANDOFF._atomic_write(HANDOFF.handoff_path("isolated","t_atomic",1), core)
        conn = sqlite3.connect(root/"kanban.db"); conn.row_factory=sqlite3.Row
        conn.execute("CREATE TABLE task_runs(id INTEGER PRIMARY KEY,task_id TEXT,profile TEXT,status TEXT,ended_at INTEGER,outcome TEXT,metadata TEXT)")
        metadata={"factory_handoff_schema":2,"factory_handoff_board":"isolated","factory_handoff_seal_id":core["seal_id"],"factory_handoff_git_head":state[0],"factory_handoff_content_state_sha256":state[1],"factory_handoff_implementer_run_id":1}
        conn.execute("INSERT INTO task_runs VALUES(2,'t_atomic','reviewer-gpt','running',NULL,NULL,?)",(json.dumps(metadata),)); conn.commit()
        return ApprovalKB(conn,repo,workspace),conn,workspace,tracked

    def _env(self, workspace):
        return {"HERMES_PROFILE":"reviewer-gpt","HERMES_KANBAN_BOARD":"isolated","HERMES_KANBAN_TASK":"t_atomic","HERMES_KANBAN_RUN_ID":"2","HERMES_KANBAN_WORKSPACE":str(workspace)}

    def test_mutation_immediately_after_validation_or_during_completion_rolls_back(self):
        for phase in ("after_initial_validation","after_native_complete"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as td, patch.object(Path,"home",return_value=Path(td)):
                kb,conn,workspace,tracked=self._fixture(Path(td))
                def mutate(observed):
                    if observed==phase: tracked.write_text("mutated\n",encoding="utf-8")
                with patch.dict(os.environ,self._env(workspace),clear=False),patch.object(HANDOFF,"_load_kanban_db",return_value=kb),patch.object(HANDOFF,"_TEST_APPROVAL_HOOK",side_effect=mutate):
                    with self.assertRaises(HANDOFF.HandoffError): HANDOFF.guarded_reviewer_complete({"summary":"approve"})
                self.assertEqual(kb.task_status,"running"); self.assertEqual(conn.execute("SELECT status FROM task_runs WHERE id=2").fetchone()[0],"running"); conn.close()

    def test_unchanged_passes_but_post_done_or_delayed_mutation_fails_release_check(self):
        with tempfile.TemporaryDirectory() as td, patch.object(Path,"home",return_value=Path(td)):
            kb,conn,workspace,tracked=self._fixture(Path(td))
            with patch.dict(os.environ,self._env(workspace),clear=False),patch.object(HANDOFF,"_load_kanban_db",return_value=kb):
                self.assertTrue(HANDOFF.guarded_reviewer_complete({"summary":"approve"})["ok"])
                self.assertTrue(HANDOFF.verify_downstream_approval("isolated","t_atomic")["ok"])
                with self.assertRaises(HANDOFF.HandoffError):
                    HANDOFF.verify_downstream_approval("other","t_atomic")
                tracked.write_text("post approval or delayed writer\n",encoding="utf-8")
                with self.assertRaises(HANDOFF.HandoffError): HANDOFF.verify_downstream_approval("isolated","t_atomic")
            conn.close()


if __name__ == "__main__": unittest.main()
