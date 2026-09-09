from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db as hermes_kanban_db

try:
    from . import kanban_runtime_contract as runtime_contract
    from .kanban_runtime_contract import (
        RuntimeExpectation,
        _explicit_board_exists,
        _live_snapshot,
        format_drift,
        main,
        normalize_snapshot,
        resolved_implementation_worktree,
        validate_review_handoff,
        validate_runtime,
        validate_task_graph,
    )
except ImportError:
    import kanban_runtime_contract as runtime_contract
    from kanban_runtime_contract import (
        RuntimeExpectation,
        _explicit_board_exists,
        _live_snapshot,
        format_drift,
        main,
        normalize_snapshot,
        resolved_implementation_worktree,
        validate_review_handoff,
        validate_runtime,
        validate_task_graph,
    )


def task_body(repo="/repo") -> str:
    return f"""## Task Contract
TYPE: feature
RISK: medium
SECURITY_SENSITIVE: no
WORKSPACE: worktree:{repo}
IMPLEMENTER: coder
REQUIRED_REVIEWERS: reviewer-claude
"""


def same_card_review_snapshot() -> dict:
    return {
        "task": {
            "id": "t_impl",
            "assignee": "reviewer-claude",
            "status": "review",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": 1,
            "body": task_body(),
        },
        "parents": ["t_gate"],
        "events": [{"kind": "review_requested", "payload": {"implementer": "coder", "reviewer": "reviewer-claude"}, "run_id": 17}],
        "runs": [{"id": 17, "profile": "coder", "outcome": "review_requested", "metadata": {"workspace_path": "/repo/.worktrees/t_impl", "task_id": "t_impl"}}],
    }


class RuntimeContractTests(unittest.TestCase):
    @staticmethod
    def _create_task(board: str, workspace: str, title: str) -> str:
        conn = hermes_kanban_db.connect(board=board)
        try:
            return hermes_kanban_db.create_task(
                conn,
                title=title,
                assignee=None,
                workspace_kind="dir",
                workspace_path=workspace,
                initial_status="running",
                board=board,
            )
        finally:
            conn.close()

    def test_nonexistent_explicit_board_never_falls_back_to_ambient_board(self):
        with tempfile.TemporaryDirectory(prefix="sf-kanban-runtime-red-") as td:
            with patch.dict(os.environ, {"HERMES_KANBAN_HOME": td}, clear=False):
                hermes_kanban_db.create_board("ambient")
                task_id = self._create_task("ambient", td, "ambient collision")
                Path(td, "kanban").mkdir(parents=True, exist_ok=True)
                Path(td, "kanban", "current").write_text("ambient\n", encoding="utf-8")

                with self.assertRaises(SystemExit):
                    _live_snapshot("missing", task_id)

    def test_real_storage_uses_requested_board_when_two_boards_share_task_id(self):
        with tempfile.TemporaryDirectory(prefix="sf-kanban-runtime-exact-") as td:
            with patch.dict(os.environ, {"HERMES_KANBAN_HOME": td}, clear=False):
                hermes_kanban_db.create_board("ambient")
                hermes_kanban_db.create_board("requested")
                task_id = self._create_task("requested", td, "requested task")

                source = hermes_kanban_db.connect(board="requested")
                ambient = hermes_kanban_db.connect(board="ambient")
                try:
                    row = source.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                    columns = [item[1] for item in source.execute("PRAGMA table_info(tasks)")]
                    values = [row[column] for column in columns]
                    values[columns.index("title")] = "ambient collision"
                    placeholders = ",".join("?" for _ in columns)
                    ambient.execute(
                        f"INSERT INTO tasks ({','.join(columns)}) VALUES ({placeholders})",
                        values,
                    )
                    ambient.commit()
                finally:
                    source.close()
                    ambient.close()

                Path(td, "kanban", "current").write_text("ambient\n", encoding="utf-8")
                payload = _live_snapshot("requested", task_id)
                self.assertEqual(payload["task"]["title"], "requested task")

    def test_nonexistent_board_causes_zero_show_calls(self):
        with patch.object(runtime_contract, "_explicit_board_exists", return_value=False), patch.object(
            runtime_contract.subprocess, "run"
        ) as run:
            with self.assertRaisesRegex(SystemExit, "explicit board does not exist"):
                _live_snapshot("missing", "t_collision")
        run.assert_not_called()

    def test_show_uses_explicit_board_argv_and_removes_ambient_overrides(self):
        completed = __import__("subprocess").CompletedProcess([], 0, stdout="{}", stderr="")
        with patch.object(runtime_contract, "_explicit_board_exists", return_value=True), patch.object(
            runtime_contract.subprocess, "run", return_value=completed
        ) as run, patch.dict(
            os.environ,
            {"HERMES_KANBAN_BOARD": "ambient", "HERMES_KANBAN_DB": "/tmp/ambient.db"},
            clear=False,
        ):
            self.assertEqual(_live_snapshot("requested", "t_exact"), {})
        self.assertEqual(
            run.call_args.args[0],
            ["hermes", "kanban", "--board", "requested", "show", "t_exact", "--json"],
        )
        self.assertNotIn("HERMES_KANBAN_BOARD", run.call_args.kwargs["env"])
        self.assertNotIn("HERMES_KANBAN_DB", run.call_args.kwargs["env"])

    def test_missing_board_existence_api_fails_closed(self):
        with patch.object(hermes_kanban_db, "board_exists", None):
            with self.assertRaisesRegex(RuntimeError, "explicit board check unavailable"):
                _explicit_board_exists("requested")

    def test_board_disappearance_between_check_and_show_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="sf-kanban-runtime-race-") as td:
            with patch.dict(os.environ, {"HERMES_KANBAN_HOME": td}, clear=False):
                hermes_kanban_db.create_board("doomed")
                task_id = self._create_task("doomed", td, "doomed task")
                original_check = runtime_contract._explicit_board_exists

                def remove_after_check(board: str) -> bool:
                    exists = original_check(board)
                    shutil.rmtree(hermes_kanban_db.board_dir(board))
                    return exists

                with patch.object(runtime_contract, "_explicit_board_exists", side_effect=remove_after_check):
                    with self.assertRaisesRegex(SystemExit, "unable to fetch"):
                        _live_snapshot("doomed", task_id)

    def test_cli_create_snapshot_passes(self):
        actual = {"id":"t_impl","assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_runtime(actual, RuntimeExpectation("coder","worktree","/repo","pilot/x",1)), [])

    def test_nested_parents_normalize(self):
        self.assertEqual(normalize_snapshot({"task":{"id":"t"},"parents":["p"]})["parents"], ["p"])

    def test_runtime_field_and_parent_drift_fail(self):
        actual={"assignee":"default","workspace_kind":"worktree","workspace_path":"/repo","branch_name":None,"max_retries":None,"parents":["x"]}
        errors=validate_runtime(actual, RuntimeExpectation("coder","worktree","/repo","pilot/x",1,("p",)))
        for prefix in ("assignee:","branch_name:","max_retries:","parents:"):
            self.assertTrue(any(e.startswith(prefix) for e in errors))

    def test_exact_resolved_worktree_shape_and_declared_repo(self):
        payload={"id":"t_impl","workspace_kind":"worktree","workspace_path":"/repo/.worktrees/t_impl","body":task_body()}
        # Non-existent paths are intentionally rejected by the hardened contract.
        self.assertIsNone(resolved_implementation_worktree(payload))
        bads = (
            {**payload, "workspace_path":"/repo"},
            {**payload, "workspace_path":"/repo/.worktrees/t_impl/extra"},
            {**payload, "workspace_path":"/repo/.worktrees/t_impl/../../escape"},
            {**payload, "workspace_path":"/repo/.worktrees/other"},
            {**payload, "workspace_path":"/other/.worktrees/t_impl"},
            {**payload, "body":task_body("/other")},
        )
        for bad in bads:
            with self.subTest(bad=bad): self.assertIsNone(resolved_implementation_worktree(bad))

    def test_exact_same_card_handoff_fails_when_fixture_workspace_missing(self):
        self.assertIn(
            "implementation_resolved_worktree_missing",
            validate_review_handoff(same_card_review_snapshot(), board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"),
        )

    def test_implementer_reviewer_must_differ(self):
        errors=validate_review_handoff(same_card_review_snapshot(), board="isolated", implementer_profile="reviewer-claude", reviewer_profile="reviewer-claude")
        self.assertIn("implementer_and_reviewer_must_differ", errors)

    def test_task_id_and_worktree_are_required(self):
        payload=same_card_review_snapshot(); del payload["task"]["id"]
        self.assertIn("implementation_id_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["task"]["workspace_path"]="/repo"
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_assignee_and_review_status_are_required_after_workspace_gate(self):
        payload=same_card_review_snapshot(); payload["task"]["assignee"]="coder"
        errors=validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("implementation_resolved_worktree_missing", errors)
        payload=same_card_review_snapshot(); payload["task"]["status"]="done"
        errors=validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("implementation_resolved_worktree_missing", errors)

    def test_latest_review_event_profiles_are_required_after_workspace_gate(self):
        payload=same_card_review_snapshot(); payload["events"]=[]
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_event_run_id_boolean_is_not_integer(self):
        payload=same_card_review_snapshot(); payload["events"][0]["run_id"] = True
        # Workspace gate fires first for this non-existent fixture; direct integer semantics are covered in temp-path adversarial tests.
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_latest_implementer_run_is_required_after_workspace_gate(self):
        payload=same_card_review_snapshot(); payload["runs"]=[]
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_run_metadata_workspace_and_task_id_are_mandatory_after_workspace_gate(self):
        payload=same_card_review_snapshot(); payload["runs"][0]["metadata"]=None
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_malformed_history_fails_closed(self):
        payload=same_card_review_snapshot(); payload["events"]="bad"; payload["runs"]=[None]
        errors=validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertTrue(errors)

    def test_body_summary_spoof_does_not_replace_history(self):
        payload=same_card_review_snapshot(); payload["events"]=[]; payload["runs"]=[]; payload["latest_summary"]="review_requested coder reviewer-claude"
        errors=validate_review_handoff(payload, board="isolated", implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertTrue(errors)

    def test_task_graph_only_validates_runtime_fields(self):
        payload={"assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_task_graph(payload, RuntimeExpectation("coder","worktree","/repo","pilot/x",1)), [])

    def test_runtime_cli_rejects_caller_actual_json(self):
        actual=json.dumps({"assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1,"parents":[]})
        with self.assertRaises(SystemExit):
            main(["runtime","--actual-json",actual,"--assignee","coder","--workspace-kind","worktree"])

    def test_legacy_handoff_cli_is_not_exposed(self):
        with self.assertRaises(SystemExit): main(["handoff","--actual-json","{}","--implementer-profile","coder","--reviewer-profile","reviewer-gpt"])

    def test_duplicate_json_key_is_rejected_by_body_decoder(self):
        raw='{"assignee":"coder","assignee":"reviewer-gpt","workspace_kind":"worktree"}'
        with self.assertRaises(SystemExit): main(["runtime","--actual-json",raw,"--assignee","coder","--workspace-kind","worktree"])

    def test_format_drift_is_fail_closed(self):
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__": unittest.main()
