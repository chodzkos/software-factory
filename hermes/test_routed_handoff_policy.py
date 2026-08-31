from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes.kanban_runtime_contract import validate_routed_review_handoff


class RoutedHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.worktree = self.repo / ".worktrees" / "t_route"
        self.worktree.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def normal_body(self) -> str:
        return f"""## Task Contract
TYPE: feature
RISK: medium
SECURITY_SENSITIVE: no
WORKSPACE: worktree:{self.repo}
IMPLEMENTER: coder
REQUIRED_REVIEWERS: reviewer-claude
"""

    def snapshot(self, *, body: str | None = None, assignee: str = "reviewer-claude", event_reviewer: str = "reviewer-claude") -> dict:
        body = body if body is not None else self.normal_body()
        return {
            "task": {
                "id": "t_route", "assignee": assignee, "status": "review", "workspace_kind": "worktree",
                "workspace_path": str(self.worktree), "body": body,
            },
            "events": [{"kind": "review_requested", "payload": {"implementer": "coder", "reviewer": event_reviewer}, "run_id": 21}],
            "runs": [{"id": 21, "profile": "coder", "outcome": "review_requested", "metadata": {"workspace_path": str(self.worktree), "task_id": "t_route"}}],
        }

    def test_declared_cross_vendor_handoff_passes(self):
        self.assertEqual(validate_routed_review_handoff(self.snapshot()), [])

    def test_actual_reviewer_cannot_differ_from_task_body(self):
        errors = validate_routed_review_handoff(self.snapshot(assignee="reviewer-gpt", event_reviewer="reviewer-gpt"))
        self.assertTrue(any(error.startswith("review_assignee:") for error in errors))
        self.assertIn("review_requested_event_missing_or_mismatched", errors)

    def test_security_openai_implementer_is_rejected_before_handoff(self):
        body = self.normal_body().replace("SECURITY_SENSITIVE: no", "SECURITY_SENSITIVE: yes").replace("REQUIRED_REVIEWERS: reviewer-claude", "REQUIRED_REVIEWERS: reviewer-gpt")
        errors = validate_routed_review_handoff(self.snapshot(body=body, assignee="reviewer-gpt", event_reviewer="reviewer-gpt"))
        self.assertIn("model_routing:security_sensitive_openai_implementer_forbidden", errors)

    def test_extra_declared_reviewer_is_rejected_before_handoff(self):
        body = self.normal_body().replace("REQUIRED_REVIEWERS: reviewer-claude", "REQUIRED_REVIEWERS: reviewer-claude,reviewer-gpt")
        errors = validate_routed_review_handoff(self.snapshot(body=body))
        self.assertTrue(any("reviewer_set_mismatch:" in error for error in errors))

    def test_event_run_id_is_mandatory_and_boolean_rejected(self):
        payload = self.snapshot(); payload["events"][0].pop("run_id")
        errors = validate_routed_review_handoff(payload)
        self.assertIn("review_requested_event_run_id_required", errors)
        payload = self.snapshot(); payload["events"][0]["run_id"] = True; payload["runs"][0]["id"] = 1
        self.assertIn("review_requested_event_run_id_required", validate_routed_review_handoff(payload))

    def test_run_metadata_workspace_and_task_are_mandatory(self):
        payload = self.snapshot(); payload["runs"][0]["metadata"] = None
        self.assertIn("implementer_review_run_metadata_required", validate_routed_review_handoff(payload))
        payload = self.snapshot(); payload["runs"][0]["metadata"] = {"workspace_path": str(self.repo / ".worktrees" / "t_other"), "task_id": "t_route"}
        self.assertIn("implementer_review_run_workspace_mismatched", validate_routed_review_handoff(payload))
        payload = self.snapshot(); payload["runs"][0]["metadata"]["task_id"] = "other"
        self.assertIn("implementer_review_run_task_mismatched", validate_routed_review_handoff(payload))

    def test_lexical_nonexistent_symlink_or_wrong_repo_worktree_is_rejected(self):
        candidates = (
            f"{self.repo}/.worktrees/t_route/../../escape",
            str(self.root / "other" / ".worktrees" / "t_route"),
            str(self.repo / ".worktrees" / "missing"),
        )
        for path in candidates:
            payload = self.snapshot(); payload["task"]["workspace_path"] = path; payload["runs"][0]["metadata"]["workspace_path"] = path
            self.assertIn("implementation_resolved_worktree_missing", validate_routed_review_handoff(payload))

        repo2 = self.root / "repo2"; repo2.mkdir(); outside = self.root / "outside"; (outside / "t_route").mkdir(parents=True)
        (repo2 / ".worktrees").symlink_to(outside, target_is_directory=True)
        body = self.normal_body().replace(str(self.repo), str(repo2))
        payload = self.snapshot(body=body)
        path = str(repo2 / ".worktrees" / "t_route")
        payload["task"]["workspace_path"] = path; payload["runs"][0]["metadata"]["workspace_path"] = path
        self.assertIn("implementation_resolved_worktree_missing", validate_routed_review_handoff(payload))

    def test_duplicate_workspace_field_fails_routing(self):
        body = self.normal_body() + f"WORKSPACE: worktree:{self.repo}\n"
        errors = validate_routed_review_handoff(self.snapshot(body=body))
        self.assertTrue(any("duplicate_workspace" in error or "model_routing:" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
