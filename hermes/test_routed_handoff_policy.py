from __future__ import annotations

import contextlib
import io
import json
import unittest

from hermes.kanban_runtime_contract import main, validate_routed_review_handoff


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
        self.assertIn("model_routing:security_sensitive_openai_implementer_forbidden", errors)

    def test_extra_declared_reviewer_is_rejected_before_handoff(self):
        body = NORMAL.replace(
            "REQUIRED_REVIEWERS: reviewer-claude",
            "REQUIRED_REVIEWERS: reviewer-claude,reviewer-gpt",
        )
        errors = validate_routed_review_handoff(
            snapshot(body=body, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        )
        self.assertTrue(any("reviewer_set_mismatch:" in error for error in errors))

    def test_event_run_id_is_mandatory(self):
        payload = snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        payload["events"][0].pop("run_id")
        errors = validate_routed_review_handoff(payload)
        self.assertIn("review_requested_event_run_id_required", errors)
        self.assertIn("review_requested_event_run_mismatch", errors)

    def test_run_metadata_and_exact_workspace_are_mandatory(self):
        payload = snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        payload["runs"][0]["metadata"] = None
        self.assertIn("implementer_review_run_metadata_required", validate_routed_review_handoff(payload))
        payload = snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        payload["runs"][0]["metadata"] = {"workspace_path": "/repo/.worktrees/t_other"}
        self.assertIn("implementer_review_run_workspace_mismatched", validate_routed_review_handoff(payload))

    def test_lexical_worktree_escape_is_rejected(self):
        payload = snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        payload["task"]["workspace_path"] = "/repo/.worktrees/t_route/../../escape"
        payload["runs"][0]["metadata"]["workspace_path"] = payload["task"]["workspace_path"]
        self.assertIn("implementation_resolved_worktree_missing", validate_routed_review_handoff(payload))

    def test_duplicate_json_keys_fail_closed_in_routed_cli(self):
        good = snapshot(body=NORMAL, assignee="reviewer-claude", event_reviewer="reviewer-claude")
        task = json.dumps(good["task"])[1:-1]
        # Duplicate nested body key: first value is security-forbidden, second is normal.
        forbidden = NORMAL.replace("SECURITY_SENSITIVE: no", "SECURITY_SENSITIVE: yes").replace(
            "REQUIRED_REVIEWERS: reviewer-claude", "REQUIRED_REVIEWERS: reviewer-gpt"
        )
        raw = (
            '{"task":{"body":' + json.dumps(forbidden) + ',"body":' + json.dumps(NORMAL) + ',' +
            task.replace('"body": ' + json.dumps(NORMAL) + ',', '', 1) +
            '},"events":' + json.dumps(good["events"]) + ',"runs":' + json.dumps(good["runs"]) + '}'
        )
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["routed-handoff", "--actual-json", raw])
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
