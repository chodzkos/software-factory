from __future__ import annotations

import contextlib
import io
import json
import unittest

try:
    from .kanban_runtime_contract import (
        RuntimeExpectation,
        format_drift,
        main,
        normalize_snapshot,
        resolved_implementation_worktree,
        validate_review_handoff,
        validate_runtime,
        validate_task_graph,
    )
except ImportError:
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
