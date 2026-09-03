from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes import kanban_runtime_contract as contract


HANDOFF = contract._HANDOFF


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, run, event):
        self.run = run
        self.event = event

    def execute(self, sql, parameters=()):
        if "FROM task_runs" in sql:
            return _Cursor(self.run)
        if "FROM task_events" in sql:
            return _Cursor(self.event)
        raise AssertionError(sql)


class _Kanban:
    def __init__(self, task, run, event):
        self.task = task
        self.conn = _Connection(run, event)

    def get_current_board(self):
        return "isolated"

    def connect_closing(self, *, board=None):
        return contextlib.nullcontext(self.conn)

    def get_task(self, conn, task_id):
        return self.task if task_id == self.task.id else None


class HandoffSealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pr23_nudge01_unit_")
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.workspace = self.repo / ".worktrees" / "t_seal"
        self.workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.name", "Test"], check=True)
        (self.workspace / "README.md").write_text("sealed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workspace), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "-qm", "base"], check=True)
        self.evidence_root = self.root / "evidence" / "claude-code"
        self.handoff_root = self.root / "evidence" / "review-handoff"
        self.evidence_root.mkdir(parents=True)
        self.state = HANDOFF.workspace_content_state(str(self.workspace))
        assert self.state is not None

    def tearDown(self):
        self.tmp.cleanup()

    def _evidence(self, run_id=17):
        head, content = self.state
        payload = {
            "schema": 6,
            "profile": "coder-claude",
            "task_id": "t_seal",
            "run_id": str(run_id),
            "model_class": "sonnet",
            "workspace": str(self.workspace),
            "execution_cwd": str(self.workspace),
            "terminal_args_sha256": "1" * 64,
            "claude_binary": "/opt/claude",
            "claude_binary_sha256": "2" * 64,
            "session_id": "session",
            "success": True,
            "command_sha256": "3" * 64,
            "attestation_id": "4" * 64,
            "git_head_before": head,
            "git_head_after": head,
            "workspace_content_state_before_sha256": content,
            "workspace_content_state_after_sha256": content,
            "recorded_at": int(time.time()),
        }
        path = self.evidence_root / f"t_seal__{run_id}__coder-claude.json"
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _task(self, *, status="review", assignee="reviewer-gpt", current_run_id=None):
        body = f"WORKSPACE: worktree:{self.repo}\nIMPLEMENTER: coder-claude\nREQUIRED_REVIEWERS: reviewer-gpt\nSECURITY_SENSITIVE: yes\n"
        return SimpleNamespace(
            id="t_seal",
            body=body,
            status=status,
            assignee=assignee,
            current_run_id=current_run_id,
            workspace_kind="worktree",
            workspace_path=str(self.workspace),
        )

    def _patch_roots(self):
        return (
            patch.object(HANDOFF, "_evidence_root", return_value=self.evidence_root),
            patch.object(HANDOFF, "handoff_root", return_value=self.handoff_root),
        )

    def _create_seal(self):
        self._evidence()
        pid, _ = HANDOFF._process_identity()
        run = {
            "id": 17,
            "task_id": "t_seal",
            "profile": "coder-claude",
            "status": "review",
            "worker_pid": pid,
            "ended_at": int(time.time()),
            "outcome": "review_requested",
            "metadata": json.dumps({"task_id": "t_seal", "workspace_path": str(self.workspace)}),
        }
        event = {
            "id": 31,
            "run_id": 17,
            "payload": json.dumps({"implementer": "coder-claude", "reviewer": "reviewer-gpt"}),
            "created_at": int(time.time()),
        }
        kb = _Kanban(self._task(), run, event)
        result = json.dumps({"ok": True, "task_id": "t_seal", "run_id": 17, "status": "review"})
        roots = self._patch_roots()
        env = {
            "HERMES_PROFILE": "coder-claude",
            "HERMES_KANBAN_TASK": "t_seal",
            "HERMES_KANBAN_RUN_ID": "17",
            "HERMES_KANBAN_WORKSPACE": str(self.workspace),
            "HERMES_KANBAN_BOARD": "isolated",
        }
        with roots[0], roots[1], patch.object(HANDOFF, "_load_kanban_db", return_value=kb), patch.dict(os.environ, env, clear=False):
            return HANDOFF.create_handoff_seal(result, content_state=HANDOFF.workspace_content_state)

    def test_active_coder_gate_accepts_only_exact_live_task_run_and_workspace(self):
        run = {
            "id": 17,
            "task_id": "t_seal",
            "profile": "coder-claude",
            "status": "running",
            "ended_at": None,
            "outcome": None,
            "metadata": json.dumps({"task_id": "t_seal", "workspace_path": str(self.workspace)}),
        }
        env = {
            "HERMES_KANBAN_TASK": "t_seal",
            "HERMES_KANBAN_RUN_ID": "17",
            "HERMES_KANBAN_WORKSPACE": str(self.workspace),
            "HERMES_KANBAN_BOARD": "isolated",
        }
        variants = (
            (self._task(status="running", assignee="coder-claude", current_run_id=17), run, True),
            (self._task(status="review", assignee="reviewer-gpt", current_run_id=None), run, False),
            (self._task(status="running", assignee="other", current_run_id=17), run, False),
            (self._task(status="running", assignee="coder-claude", current_run_id=18), run, False),
            (self._task(status="running", assignee="coder-claude", current_run_id=17), {**run, "ended_at": 1}, False),
            (self._task(status="running", assignee="coder-claude", current_run_id=17), {**run, "outcome": "review_requested"}, False),
            (self._task(status="running", assignee="coder-claude", current_run_id=17), {**run, "profile": "coder"}, False),
            (self._task(status="running", assignee="coder-claude", current_run_id=17), {**run, "metadata": "{}"}, False),
        )
        for task, candidate_run, expected in variants:
            with self.subTest(expected=expected, task=task, run=candidate_run):
                kb = _Kanban(task, candidate_run, {})
                with patch.object(HANDOFF, "_load_kanban_db", return_value=kb), patch.dict(os.environ, env, clear=False):
                    self.assertIs(HANDOFF.active_coder_run_matches(), expected)
        for malformed in ("", "0", "01", "1.0", "true", " 17"):
            with self.subTest(run_id=malformed), patch.dict(os.environ, {**env, "HERMES_KANBAN_RUN_ID": malformed}, clear=False):
                self.assertFalse(HANDOFF.active_coder_run_matches())
        with patch.object(HANDOFF, "_load_kanban_db", return_value=_Kanban(variants[0][0], run, {})), patch.dict(
            os.environ, {**env, "HERMES_KANBAN_BOARD": "wrong"}, clear=False
        ):
            self.assertFalse(HANDOFF.active_coder_run_matches())

    def test_successful_transition_creates_one_closed_schema_seal(self):
        seal = self._create_seal()
        self.assertEqual(seal["schema"], 1)
        self.assertEqual(seal["implementer_run_id"], 17)
        self.assertEqual(len(list(self.handoff_root.glob("*.json"))), 1)
        loaded = json.loads(next(self.handoff_root.glob("*.json")).read_text())
        self.assertEqual(set(loaded), HANDOFF._HANDOFF_FIELDS)

    def test_malformed_or_wrong_request_review_result_creates_no_seal(self):
        self._evidence()
        roots = self._patch_roots()
        for result in (
            '{"ok":true,"ok":false,"task_id":"t_seal","run_id":17,"status":"review"}',
            json.dumps({"ok": 1, "task_id": "t_seal", "run_id": 17, "status": "review"}),
            json.dumps({"ok": True, "task_id": "other", "run_id": 17, "status": "review"}),
            json.dumps({"ok": True, "task_id": "t_seal", "run_id": True, "status": "review"}),
            json.dumps({"ok": True, "task_id": "t_seal", "run_id": 17, "status": "review", "extra": 1}),
        ):
            with self.subTest(result=result), roots[0], roots[1], patch.dict(os.environ, {
                "HERMES_KANBAN_TASK": "t_seal", "HERMES_KANBAN_RUN_ID": "17",
                "HERMES_KANBAN_WORKSPACE": str(self.workspace)}, clear=False):
                with self.assertRaises(HANDOFF.HandoffError):
                    HANDOFF.create_handoff_seal(result, content_state=HANDOFF.workspace_content_state)
        self.assertFalse(self.handoff_root.exists())

    def test_live_process_blocks_then_exact_exit_allows_and_pid_reuse_is_safe(self):
        seal = self._create_seal()
        roots = self._patch_roots()
        with roots[0], roots[1]:
            _, alive_errors = HANDOFF.validate_handoff_seal(
                task_id="t_seal", run_id=17, implementer="coder-claude",
                reviewer="reviewer-gpt", workspace=str(self.workspace),
                content_state=HANDOFF.workspace_content_state,
            )
            self.assertIn("implementer_process_alive", alive_errors)
            stored = self.handoff_root / "t_seal__17__coder-claude.json"
            payload = json.loads(stored.read_text())
            payload["implementer_proc_start"] = str(int(seal["implementer_proc_start"]) + 1)
            core = {key: payload[key] for key in payload if key != "seal_id"}
            payload["seal_id"] = hashlib.sha256(
                HANDOFF.HANDOFF_DOMAIN.encode("ascii") + b"\0" + HANDOFF._canonical_json(core)
            ).hexdigest()
            stored.write_text(json.dumps(payload, sort_keys=True) + "\n")
            _, reused_errors = HANDOFF.validate_handoff_seal(
                task_id="t_seal", run_id=17, implementer="coder-claude",
                reviewer="reviewer-gpt", workspace=str(self.workspace),
                content_state=HANDOFF.workspace_content_state,
            )
            self.assertNotIn("implementer_process_alive", reused_errors)
            self.assertNotIn("implementer_process_unknown", reused_errors)

    def test_content_drift_and_tampered_evidence_fail_closed(self):
        self._create_seal()
        roots = self._patch_roots()
        with roots[0], roots[1]:
            (self.workspace / "README.md").write_text("drift\n", encoding="utf-8")
            _, errors = HANDOFF.validate_handoff_seal(
                task_id="t_seal", run_id=17, implementer="coder-claude",
                reviewer="reviewer-gpt", workspace=str(self.workspace),
                content_state=HANDOFF.workspace_content_state,
                require_process_exit=False,
            )
            self.assertIn("handoff_content_state_mismatch", errors)
            (self.workspace / "README.md").write_text("sealed\n", encoding="utf-8")
            self._evidence().write_text('{"schema":6,"schema":6}\n')
            _, evidence_errors = HANDOFF.validate_handoff_seal(
                task_id="t_seal", run_id=17, implementer="coder-claude",
                reviewer="reviewer-gpt", workspace=str(self.workspace),
                content_state=HANDOFF.workspace_content_state,
                require_process_exit=False,
            )
            self.assertTrue(any(error.startswith("handoff_seal:") for error in evidence_errors))

    def test_closed_seal_rejects_unknown_schema_types_partial_and_symlink(self):
        self._create_seal()
        roots = self._patch_roots()
        stored = self.handoff_root / "t_seal__17__coder-claude.json"
        original = stored.read_bytes()
        unknown = json.loads(original)
        unknown["unknown"] = True
        variants = (
            b'{"schema":1,"schema":1}\n',
            b'{"schema":1}\n',
            original.replace(b'"schema":1', b'"schema":true'),
            json.dumps(unknown, sort_keys=True).encode("utf-8") + b"\n",
            original[:-8],
        )
        with roots[0], roots[1]:
            for raw in variants:
                with self.subTest(raw=raw[:40]):
                    stored.write_bytes(raw)
                    with self.assertRaises(HANDOFF.HandoffError):
                        HANDOFF.load_handoff_seal("t_seal", 17)
            stored.unlink()
            victim = self.root / "victim.json"
            victim.write_bytes(original)
            stored.symlink_to(victim)
            with self.assertRaises(HANDOFF.HandoffError):
                HANDOFF.load_handoff_seal("t_seal", 17)

    def test_reviewer_completion_binds_exact_run_metadata_and_current_bytes(self):
        seal = self._create_seal()
        roots = self._patch_roots()
        stored = self.handoff_root / "t_seal__17__coder-claude.json"
        payload = json.loads(stored.read_text())
        payload["implementer_proc_start"] = str(int(seal["implementer_proc_start"]) + 1)
        core = {key: payload[key] for key in payload if key != "seal_id"}
        payload["seal_id"] = hashlib.sha256(
            HANDOFF.HANDOFF_DOMAIN.encode("ascii") + b"\0" + HANDOFF._canonical_json(core)
        ).hexdigest()
        stored.write_text(json.dumps(payload, sort_keys=True) + "\n")
        metadata = {
            "factory_handoff_schema": 1,
            "factory_handoff_seal_id": payload["seal_id"],
            "factory_handoff_content_state_sha256": payload["content_state_sha256"],
            "factory_handoff_implementer_run_id": 17,
        }
        reviewer_run = {
            "id": 18,
            "task_id": "t_seal",
            "profile": "reviewer-gpt",
            "status": "running",
            "ended_at": None,
            "outcome": None,
            "metadata": json.dumps(metadata),
        }
        kb = _Kanban(
            self._task(status="running", assignee="reviewer-gpt", current_run_id=18),
            reviewer_run,
            {},
        )
        env = {
            "HERMES_KANBAN_TASK": "t_seal",
            "HERMES_KANBAN_RUN_ID": "18",
            "HERMES_KANBAN_WORKSPACE": str(self.workspace),
            "HERMES_KANBAN_BOARD": "isolated",
        }
        with roots[0], roots[1], patch.object(HANDOFF, "_load_kanban_db", return_value=kb), patch.dict(
            os.environ, env, clear=False
        ):
            self.assertTrue(
                HANDOFF.reviewer_completion_authorized(
                    content_state=HANDOFF.workspace_content_state
                )
            )
            reviewer_run["metadata"] = json.dumps({**metadata, "factory_handoff_seal_id": "0" * 64})
            self.assertFalse(
                HANDOFF.reviewer_completion_authorized(
                    content_state=HANDOFF.workspace_content_state
                )
            )
            reviewer_run["metadata"] = json.dumps(metadata)
            (self.workspace / "README.md").write_text("review drift\n", encoding="utf-8")
            self.assertFalse(
                HANDOFF.reviewer_completion_authorized(
                    content_state=HANDOFF.workspace_content_state
                )
            )

    def test_real_short_lived_child_process_identity(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            pid, start = HANDOFF._process_identity(child.pid)
            self.assertEqual(HANDOFF.process_identity_state(pid, start), "alive")
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertEqual(HANDOFF.process_identity_state(pid, start), "exited")


if __name__ == "__main__":
    unittest.main()
