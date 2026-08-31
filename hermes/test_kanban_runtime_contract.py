import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kanban_runtime_contract as krc
from kanban_runtime_contract import (
    RuntimeExpectation,
    format_drift,
    main,
    normalize_snapshot,
    resolved_implementation_worktree,
    validate_review_handoff,
    validate_runtime,
    validate_task_graph,
)


class RuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.worktree = self.repo / ".worktrees" / "t_impl"
        self.worktree.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def task_body(self, repo: Path | str | None = None) -> str:
        base = str(repo if repo is not None else self.repo)
        return f"""## Task Contract
TYPE: feature
RISK: medium
SECURITY_SENSITIVE: no
WORKSPACE: worktree:{base}
IMPLEMENTER: coder
REQUIRED_REVIEWERS: reviewer-claude
"""

    def same_card_review_snapshot(self) -> dict:
        return {
            "task": {
                "id": "t_impl",
                "assignee": "reviewer-claude",
                "status": "review",
                "workspace_kind": "worktree",
                "workspace_path": str(self.worktree),
                "branch_name": "pilot/full-flow-doc",
                "max_retries": 1,
                "body": self.task_body(),
            },
            "parents": ["t_gate"],
            "events": [{
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": "reviewer-claude"},
                "run_id": 17,
            }],
            "runs": [{
                "id": 17,
                "profile": "coder",
                "outcome": "review_requested",
                "metadata": {"workspace_path": str(self.worktree), "task_id": "t_impl"},
            }],
        }

    def test_cli_create_snapshot_passes(self):
        actual = {"id":"t_impl","assignee":"coder","workspace_kind":"worktree","workspace_path":str(self.repo),"branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_runtime(actual, RuntimeExpectation("coder","worktree",str(self.repo),"pilot/x",1)), [])

    def test_nested_parents_normalize(self):
        self.assertEqual(normalize_snapshot({"task":{"id":"t"},"parents":["p"]})["parents"], ["p"])

    def test_runtime_field_and_parent_drift_fail(self):
        actual={"assignee":"default","workspace_kind":"worktree","workspace_path":str(self.repo),"branch_name":None,"max_retries":None,"parents":["x"]}
        errors=validate_runtime(actual, RuntimeExpectation("coder","worktree",str(self.repo),"pilot/x",1,("p",)))
        for prefix in ("assignee:","branch_name:","max_retries:","parents:"):
            self.assertTrue(any(e.startswith(prefix) for e in errors))

    def test_exact_resolved_worktree_shape_and_declared_repo(self):
        payload={"id":"t_impl","workspace_kind":"worktree","workspace_path":str(self.worktree),"body":self.task_body()}
        self.assertEqual(resolved_implementation_worktree(payload), str(self.worktree))
        bads = (
            {**payload, "workspace_path":str(self.repo)},
            {**payload, "workspace_path":str(self.worktree / "extra")},
            {**payload, "workspace_path":f"{self.repo}/.worktrees/t_impl/../../escape"},
            {**payload, "workspace_path":str(self.repo / ".worktrees" / "other")},
            {**payload, "body":self.task_body(self.root / "other")},
            {**payload, "body":self.task_body(f"{self.repo}/.")},
        )
        for bad in bads:
            with self.subTest(bad=bad):
                self.assertIsNone(resolved_implementation_worktree(bad))

    def test_nonexistent_and_symlinked_worktrees_fail_closed(self):
        payload={"id":"missing","workspace_kind":"worktree","workspace_path":str(self.repo / ".worktrees" / "missing"),"body":self.task_body()}
        self.assertIsNone(resolved_implementation_worktree(payload))

        repo2 = self.root / "repo2"
        repo2.mkdir()
        outside = self.root / "outside"
        (outside / "t_impl").mkdir(parents=True)
        (repo2 / ".worktrees").symlink_to(outside, target_is_directory=True)
        payload={"id":"t_impl","workspace_kind":"worktree","workspace_path":str(repo2 / ".worktrees" / "t_impl"),"body":self.task_body(repo2)}
        self.assertIsNone(resolved_implementation_worktree(payload))

    def test_exact_same_card_handoff_passes(self):
        self.assertEqual(validate_review_handoff(self.same_card_review_snapshot(), implementer_profile="coder", reviewer_profile="reviewer-claude"), [])

    def test_implementer_reviewer_must_differ(self):
        errors=validate_review_handoff(self.same_card_review_snapshot(), implementer_profile="reviewer-claude", reviewer_profile="reviewer-claude")
        self.assertIn("implementer_and_reviewer_must_differ", errors)

    def test_task_id_and_worktree_are_required(self):
        payload=self.same_card_review_snapshot(); del payload["task"]["id"]
        self.assertIn("implementation_id_missing", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=self.same_card_review_snapshot(); payload["task"]["workspace_path"]=str(self.repo)
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_assignee_and_review_status_are_required(self):
        payload=self.same_card_review_snapshot(); payload["task"]["assignee"]="coder"
        self.assertTrue(any(e.startswith("review_assignee:") for e in validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")))
        payload=self.same_card_review_snapshot(); payload["task"]["status"]="done"
        self.assertTrue(any(e.startswith("review_status:") for e in validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")))

    def test_event_run_id_is_required_exact_and_not_boolean(self):
        payload=self.same_card_review_snapshot(); payload["events"][0].pop("run_id")
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_run_id_required", errors)
        payload=self.same_card_review_snapshot(); payload["events"][0]["run_id"]=True; payload["runs"][0]["id"]=1
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_run_id_required", errors)
        payload=self.same_card_review_snapshot(); payload["runs"][0]["id"]=False
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("current_implementer_run_id_required", errors)

    def test_latest_implementer_run_is_required(self):
        payload=self.same_card_review_snapshot(); payload["runs"]=[]
        self.assertIn("current_implementer_review_run_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=self.same_card_review_snapshot(); payload["runs"].append({"id":18,"profile":"coder","outcome":"crashed","metadata":None})
        self.assertIn("current_implementer_review_run_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_run_metadata_workspace_and_task_id_are_mandatory(self):
        payload=self.same_card_review_snapshot(); payload["runs"][0]["metadata"]=None
        self.assertIn("implementer_review_run_metadata_required", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=self.same_card_review_snapshot(); payload["runs"][0]["metadata"]={}
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("implementer_review_run_workspace_mismatched", errors); self.assertIn("implementer_review_run_task_mismatched", errors)

    def test_malformed_history_fails_closed(self):
        payload=self.same_card_review_snapshot(); payload["events"]="bad"; payload["runs"]=[None]
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_missing_or_mismatched", errors); self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_task_graph_only_validates_runtime_fields(self):
        payload={"assignee":"coder","workspace_kind":"worktree","workspace_path":str(self.repo),"branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_task_graph(payload, RuntimeExpectation("coder","worktree",str(self.repo),"pilot/x",1)), [])

    def test_runtime_cli_fetches_live_snapshot_internally(self):
        actual={"assignee":"coder","workspace_kind":"worktree","workspace_path":str(self.repo),"branch_name":"pilot/x","max_retries":1,"parents":[]}
        out=io.StringIO()
        with patch.object(krc, "_live_snapshot", return_value=actual), contextlib.redirect_stdout(out):
            rc=main(["runtime","--task-id","t_impl","--assignee","coder","--workspace-kind","worktree","--workspace-path",str(self.repo),"--branch-name","pilot/x","--max-retries","1"])
        self.assertEqual(rc,0); self.assertEqual(out.getvalue().strip(),"RUNTIME_CONTRACT_OK")

    def test_caller_supplied_actual_json_is_not_exposed(self):
        with self.assertRaises(SystemExit):
            main(["routed-handoff","--actual-json","{}"])
        with self.assertRaises(SystemExit):
            main(["runtime","--actual-json","{}","--assignee","coder","--workspace-kind","worktree"])

    def test_live_snapshot_uses_hermes_show_and_strict_json(self):
        raw='{"task":{"id":"t_impl"}}'
        completed=type("R", (), {"stdout": raw})()
        with patch.object(krc.subprocess, "run", return_value=completed) as run:
            self.assertEqual(krc._live_snapshot("t_impl")["task"]["id"], "t_impl")
        run.assert_called_once()
        argv=run.call_args.args[0]
        self.assertEqual(argv, ["hermes","kanban","show","t_impl","--json"])
        duplicate=type("R", (), {"stdout": '{"x":1,"x":2}'})()
        with patch.object(krc.subprocess, "run", return_value=duplicate):
            with self.assertRaises(SystemExit): krc._live_snapshot("t_impl")

    def test_legacy_handoff_cli_is_not_exposed(self):
        with self.assertRaises(SystemExit): main(["handoff","--task-id","t_impl"])

    def test_format_drift_is_fail_closed(self):
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__": unittest.main()
