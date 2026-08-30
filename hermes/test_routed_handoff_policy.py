from __future__ import annotations

import unittest

from hermes.kanban_runtime_contract import validate_routed_review_handoff


def snapshot(*, body: str, assignee: str, event_reviewer: str) -> dict:
    return {
        "task": {
            "id": "t_route",
            "assignee": assignee,
            "status": "review",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_route",
            "body": body,
        },
        "events": [
            {
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": event_reviewer},
                "run_id": 21,
            }
        ],
        "runs": [
            {
                "id": 21,
                "profile": "coder",
                "outcome": "review_requested",
                "metadata": {"workspace_path": "/repo/.worktrees/t_route"},
            }
        ],
    }


NORMAL = """## Task Contract
TYPE: feature
RISK: medium
SECURITY_SENSITIVE: no
IMPLEMENTER: coder
REQUIRED_REVIEWERS: reviewer-claude
"""


class RoutedHandoffTests(unittest.TestCase):
    def test_declared_cross_vendor_handoff_passes(self):
        self.assertEqual(
            validate_routed_review_handoff(
                snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
            ),
            [],
        )

    def test_actual_reviewer_cannot_differ_from_task_body(self):
        errors = validate_routed_review_handoff(
            snapshot(body=NORMAL, assignee="reviewer-gpt", event_reviewer="reviewer-gpt")
        )
        self.assertTrue(any(error.startswith("review_assignee:") for error in errors))
        self.assertIn("review_requested_event_missing_or_mismatched", errors)

    def test_security_openai_implementer_is_rejected_before_handoff(self):
        body = NORMAL.replace("SECURITY_SENSITIVE: no", "SECURITY_SENSITIVE: yes").replace(
            "REQUIRED_REVIEWERS: reviewer-claude", "REQUIRED_REVIEWERS: reviewer-gpt"
        )
        errors = validate_routed_review_handoff(
            snapshot(body=body, assignee="reviewer-gpt", event_reviewer="reviewer-gpt")
        )
        self.assertIn(
            "model_routing:security_sensitive_openai_implementer_forbidden",
            errors,
        )

    def test_extra_declared_reviewer_is_rejected_before_handoff(self):
        body = NORMAL.replace(
            "REQUIRED_REVIEWERS: reviewer-claude",
            "REQUIRED_REVIEWERS: reviewer-claude,reviewer-gpt",
        )
        errors = validate_routed_review_handoff(
            snapshot(body=body, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        )
        self.assertTrue(any("reviewer_set_mismatch:" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
