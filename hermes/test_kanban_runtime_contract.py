import contextlib
import io
import json
import unittest

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
            "metadata": {"workspace_path": "/repo/.worktrees/t_impl"},
        }],
    }


class RuntimeContractTests(unittest.TestCase):
    def test_cli_create_snapshot_passes(self):
        actual = {"id":"t_impl","assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_runtime(actual, RuntimeExpectation("coder","worktree","/repo","pilot/x",1)), [])

    def test_nested_parents_normalize(self):
        payload={"task":{"id":"t"},"parents":["p"]}
        self.assertEqual(normalize_snapshot(payload)["parents"], ["p"])

    def test_runtime_field_and_parent_drift_fail(self):
        actual={"assignee":"default","workspace_kind":"worktree","workspace_path":"/repo","branch_name":None,"max_retries":None,"parents":["x"]}
        errors=validate_runtime(actual, RuntimeExpectation("coder","worktree","/repo","pilot/x",1,("p",)))
        self.assertTrue(any(e.startswith("assignee:") for e in errors))
        self.assertTrue(any(e.startswith("branch_name:") for e in errors))
        self.assertTrue(any(e.startswith("max_retries:") for e in errors))
        self.assertTrue(any(e.startswith("parents:") for e in errors))

    def test_exact_resolved_worktree_shape(self):
        self.assertEqual(resolved_implementation_worktree({"id":"t_impl","workspace_kind":"worktree","workspace_path":"/repo/.worktrees/t_impl"}), "/repo/.worktrees/t_impl")
        for bad in ("/repo", "/repo/.worktrees/t_impl/extra", "/repo/.worktrees/t_impl/../../escape", "/repo/.worktrees/other"):
            with self.subTest(bad=bad):
                self.assertIsNone(resolved_implementation_worktree({"id":"t_impl","workspace_kind":"worktree","workspace_path":bad}))

    def test_exact_same_card_handoff_passes(self):
        self.assertEqual(validate_review_handoff(same_card_review_snapshot(), implementer_profile="coder", reviewer_profile="reviewer-claude"), [])

    def test_implementer_reviewer_must_differ(self):
        errors=validate_review_handoff(same_card_review_snapshot(), implementer_profile="reviewer-claude", reviewer_profile="reviewer-claude")
        self.assertIn("implementer_and_reviewer_must_differ", errors)

    def test_task_id_and_worktree_are_required(self):
        payload=same_card_review_snapshot(); del payload["task"]["id"]
        self.assertIn("implementation_id_missing", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["task"]["workspace_path"]="/repo"
        self.assertIn("implementation_resolved_worktree_missing", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_assignee_and_review_status_are_required(self):
        payload=same_card_review_snapshot(); payload["task"]["assignee"]="coder"
        self.assertTrue(any(e.startswith("review_assignee:") for e in validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")))
        payload=same_card_review_snapshot(); payload["task"]["status"]="done"
        self.assertTrue(any(e.startswith("review_status:") for e in validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")))

    def test_latest_review_event_profiles_are_required(self):
        payload=same_card_review_snapshot(); payload["events"]=[]
        self.assertIn("review_requested_event_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["events"][0]["payload"]["reviewer"]="reviewer-gpt"
        self.assertIn("review_requested_event_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_event_run_id_is_required_and_exact(self):
        payload=same_card_review_snapshot(); payload["events"][0].pop("run_id")
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_run_id_required", errors)
        self.assertIn("review_requested_event_run_mismatch", errors)
        payload=same_card_review_snapshot(); payload["events"][0]["run_id"]=16
        self.assertIn("review_requested_event_run_mismatch", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_latest_implementer_run_is_required(self):
        payload=same_card_review_snapshot(); payload["runs"]=[]
        self.assertIn("current_implementer_review_run_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["runs"].append({"id":18,"profile":"coder","outcome":"crashed","metadata":None})
        self.assertIn("current_implementer_review_run_missing_or_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_run_metadata_and_workspace_are_mandatory(self):
        payload=same_card_review_snapshot(); payload["runs"][0]["metadata"]=None
        self.assertIn("implementer_review_run_metadata_required", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["runs"][0]["metadata"]={}
        self.assertIn("implementer_review_run_workspace_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))
        payload=same_card_review_snapshot(); payload["runs"][0]["metadata"]={"workspace_path":"/repo/.worktrees/other"}
        self.assertIn("implementer_review_run_workspace_mismatched", validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude"))

    def test_malformed_history_fails_closed(self):
        payload=same_card_review_snapshot(); payload["events"]="bad"; payload["runs"]=[None]
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_body_summary_spoof_does_not_replace_history(self):
        payload=same_card_review_snapshot(); payload["events"]=[]; payload["runs"]=[]; payload["latest_summary"]="review_requested coder reviewer-claude"
        errors=validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="reviewer-claude")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_task_graph_only_validates_runtime_fields(self):
        payload={"assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1}
        self.assertEqual(validate_task_graph(payload, RuntimeExpectation("coder","worktree","/repo","pilot/x",1)), [])

    def test_runtime_cli_returns_zero_and_two(self):
        actual=json.dumps({"assignee":"coder","workspace_kind":"worktree","workspace_path":"/repo","branch_name":"pilot/x","max_retries":1,"parents":[]})
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            rc=main(["runtime","--actual-json",actual,"--assignee","coder","--workspace-kind","worktree","--workspace-path","/repo","--branch-name","pilot/x","--max-retries","1"])
        self.assertEqual(rc,0); self.assertEqual(out.getvalue().strip(),"RUNTIME_CONTRACT_OK")
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            rc=main(["runtime","--actual-json",actual,"--assignee","other","--workspace-kind","worktree","--workspace-path","/repo"])
        self.assertEqual(rc,2); self.assertTrue(out.getvalue().startswith("RUNTIME_CONTRACT_DRIFT:"))

    def test_legacy_handoff_cli_is_not_exposed(self):
        with self.assertRaises(SystemExit):
            main(["handoff","--actual-json","{}","--implementer-profile","coder","--reviewer-profile","reviewer-gpt"])

    def test_duplicate_json_key_is_rejected_by_runtime_cli(self):
        raw='{"assignee":"coder","assignee":"reviewer-gpt","workspace_kind":"worktree"}'
        with self.assertRaises(SystemExit):
            main(["runtime","--actual-json",raw,"--assignee","coder","--workspace-kind","worktree"])

    def test_format_drift_is_fail_closed(self):
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__":
    unittest.main()
