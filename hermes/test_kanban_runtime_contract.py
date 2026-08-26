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
            "assignee": "critic",
            "status": "review",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": 1,
        },
        "parents": ["t_gate"],
        "events": [
            {
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": "critic"},
                "run_id": 17,
            }
        ],
        "runs": [
            {
                "id": 17,
                "profile": "coder",
                "outcome": "review_requested",
                "metadata": {"workspace_path": "/repo/.worktrees/t_impl"},
            }
        ],
    }


class RuntimeContractTests(unittest.TestCase):
    def test_cli_create_snapshot_passes(self) -> None:
        actual = {
            "id": "t_impl",
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": 1,
        }
        expected = RuntimeExpectation("coder", "worktree", "/repo", "pilot/full-flow-doc", 1)
        self.assertEqual(validate_runtime(actual, expected), [])

    def test_nested_kanban_show_parents_are_normalized(self) -> None:
        payload = {
            "task": {
                "id": "t_impl",
                "assignee": "critic",
                "workspace_kind": "worktree",
                "workspace_path": "/repo/.worktrees/t_impl",
            },
            "parents": ["t_gate"],
        }
        self.assertEqual(normalize_snapshot(payload)["parents"], ["t_gate"])

    def test_assignee_drift_is_rejected(self) -> None:
        actual = {
            "assignee": "default",
            "workspace_kind": "scratch",
            "workspace_path": None,
            "branch_name": None,
            "max_retries": 1,
        }
        expected = RuntimeExpectation("coder", "scratch", None, None, 1)
        self.assertTrue(any(e.startswith("assignee:") for e in validate_runtime(actual, expected)))

    def test_max_retries_drift_is_rejected(self) -> None:
        actual = {
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": None,
        }
        expected = RuntimeExpectation("coder", "worktree", "/repo", "pilot/full-flow-doc", 1)
        self.assertTrue(any(e.startswith("max_retries:") for e in validate_runtime(actual, expected)))

    def test_branch_drift_is_rejected(self) -> None:
        actual = {
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": None,
            "max_retries": 1,
        }
        expected = RuntimeExpectation("coder", "worktree", "/repo", "pilot/full-flow-doc", 1)
        self.assertTrue(any(e.startswith("branch_name:") for e in validate_runtime(actual, expected)))

    def test_extra_parent_is_rejected_from_nested_show(self) -> None:
        payload = {
            "task": {
                "assignee": "critic",
                "workspace_kind": "worktree",
                "workspace_path": "/repo/.worktrees/t_impl",
                "max_retries": 1,
            },
            "parents": ["t_gate", "t_extra"],
        }
        expected = RuntimeExpectation(
            "critic", "worktree", "/repo/.worktrees/t_impl", None, 1, ("t_gate",)
        )
        self.assertTrue(any(e.startswith("parents:") for e in validate_runtime(payload, expected)))

    def test_post_claim_workspace_path_is_resolved_worktree(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        self.assertEqual(resolved_implementation_worktree(implementation), "/repo/.worktrees/t_impl")

    def test_repo_anchor_is_not_resolved_worktree(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
        }
        self.assertIsNone(resolved_implementation_worktree(implementation))

    def test_exact_same_card_handoff_passes(self) -> None:
        self.assertEqual(
            validate_review_handoff(
                same_card_review_snapshot(), implementer_profile="coder", reviewer_profile="critic"
            ),
            [],
        )

    def test_implementer_cannot_be_reviewer(self) -> None:
        errors = validate_review_handoff(
            same_card_review_snapshot(), implementer_profile="critic", reviewer_profile="critic"
        )
        self.assertIn("implementer_and_reviewer_must_differ", errors)

    def test_missing_resolved_worktree_fails_closed(self) -> None:
        payload = same_card_review_snapshot()
        payload["task"]["workspace_path"] = "/repo"
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("implementation_resolved_worktree_missing", errors)

    def test_missing_implementation_id_fails_closed(self) -> None:
        payload = same_card_review_snapshot()
        del payload["task"]["id"]
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("implementation_id_missing", errors)

    def test_wrong_reviewer_assignment_fails_closed(self) -> None:
        payload = same_card_review_snapshot()
        payload["task"]["assignee"] = "coder"
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertTrue(any(e.startswith("review_assignee:") for e in errors))

    def test_non_review_status_fails_closed(self) -> None:
        payload = same_card_review_snapshot()
        payload["task"]["status"] = "done"
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertTrue(any(e.startswith("review_status:") for e in errors))

    def test_review_requested_event_is_required(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"] = []
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)

    def test_latest_review_requested_event_profiles_must_match(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"].append(
            {
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": "quick-reviewer"},
                "run_id": 18,
            }
        )
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)

    def test_current_implementer_review_run_is_required(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"] = []
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_latest_run_must_be_current_implementer_review_request(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"].append(
            {"id": 18, "profile": "coder", "outcome": "crashed", "metadata": None}
        )
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_review_event_run_id_must_match_current_run(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"][0]["run_id"] = 16
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_run_mismatch", errors)

    def test_run_workspace_mismatch_fails_when_metadata_supplies_path(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"][0]["metadata"]["workspace_path"] = "/repo/.worktrees/t_other"
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("implementer_review_run_workspace_mismatched", errors)

    def test_run_workspace_metadata_is_optional_corroboration(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"][0]["metadata"] = {"worker_session_id": "session"}
        self.assertEqual(
            validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic"), []
        )

    def test_missing_run_metadata_is_allowed(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"][0]["metadata"] = None
        self.assertEqual(
            validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic"), []
        )

    def test_malformed_history_types_fail_closed_without_crash(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"] = "not-a-list"
        payload["runs"] = [None, "x", {}]
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_malformed_trailing_run_cannot_expose_stale_good_run(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"].append(None)

        errors = validate_review_handoff(
            payload,
            implementer_profile="coder",
            reviewer_profile="critic",
        )

        self.assertIn(
            "current_implementer_review_run_missing_or_mismatched",
            errors,
        )

    def test_malformed_trailing_event_cannot_expose_stale_good_event(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"].append(None)

        errors = validate_review_handoff(
            payload,
            implementer_profile="coder",
            reviewer_profile="critic",
        )

        self.assertIn(
            "review_requested_event_missing_or_mismatched",
            errors,
        )

    def test_body_summary_spoof_does_not_replace_structured_history(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"] = []
        payload["runs"] = []
        payload["task"]["body"] = "review_requested coder critic /repo/.worktrees/t_impl"
        payload["latest_summary"] = "review_requested coder critic /repo/.worktrees/t_impl"
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_stale_matching_event_does_not_override_latest_conflict(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"] = [
            {
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": "critic"},
                "run_id": 16,
            },
            {
                "kind": "review_requested",
                "payload": {"implementer": "coder", "reviewer": "quick-reviewer"},
                "run_id": 17,
            },
        ]
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("review_requested_event_missing_or_mismatched", errors)

    def test_stale_matching_run_does_not_override_latest_failure(self) -> None:
        payload = same_card_review_snapshot()
        payload["runs"] = [
            payload["runs"][0],
            {"id": 18, "profile": "coder", "outcome": "crashed", "metadata": None},
        ]
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("current_implementer_review_run_missing_or_mismatched", errors)

    def test_separate_reviewer_task_shape_is_rejected(self) -> None:
        payload = same_card_review_snapshot()
        payload["task"].update(
            {"id": "t_review", "workspace_kind": "dir", "workspace_path": "/repo/.worktrees/t_impl"}
        )
        errors = validate_review_handoff(payload, implementer_profile="coder", reviewer_profile="critic")
        self.assertIn("implementation_resolved_worktree_missing", errors)

    def test_task_graph_can_validate_same_card_handoff(self) -> None:
        payload = same_card_review_snapshot()
        expected = RuntimeExpectation(
            "critic", "worktree", "/repo/.worktrees/t_impl", "pilot/full-flow-doc", 1, ("t_gate",)
        )
        self.assertEqual(
            validate_task_graph(
                payload, expected, implementer_profile="coder", reviewer_profile="critic"
            ),
            [],
        )

    def test_pilot_6_runtime_regression_is_rejected(self) -> None:
        implementation_create = {
            "id": "t_36a829a9",
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/home/marcin/projects/software-factory",
            "branch_name": None,
            "max_retries": None,
        }
        expected = RuntimeExpectation(
            "coder",
            "worktree",
            "/home/marcin/projects/software-factory",
            "pilot/full-flow-doc",
            1,
        )
        errors = validate_runtime(implementation_create, expected)
        self.assertTrue(any(e.startswith("branch_name:") for e in errors))
        self.assertTrue(any(e.startswith("max_retries:") for e in errors))

    def test_pilot_7b_same_card_shape_passes(self) -> None:
        payload = {
            "task": {
                "id": "t_804129c2",
                "assignee": "quick-reviewer",
                "status": "review",
                "workspace_kind": "worktree",
                "workspace_path": "/home/marcin/projects/software-factory/.worktrees/t_804129c2",
            },
            "events": [
                {
                    "kind": "review_requested",
                    "payload": {"implementer": "coder", "reviewer": "quick-reviewer"},
                    "run_id": 17,
                }
            ],
            "runs": [
                {
                    "id": 17,
                    "profile": "coder",
                    "outcome": "review_requested",
                    "metadata": {
                        "workspace_path": "/home/marcin/projects/software-factory/.worktrees/t_804129c2"
                    },
                }
            ],
        }
        self.assertEqual(
            validate_review_handoff(
                payload, implementer_profile="coder", reviewer_profile="quick-reviewer"
            ),
            [],
        )

    def test_two_cycle_native_review_uses_latest_transition(self) -> None:
        payload = {
            "task": {
                "id": "t_impl",
                "assignee": "quick-reviewer",
                "status": "review",
                "workspace_kind": "worktree",
                "workspace_path": "/repo/.worktrees/t_impl",
            },
            "events": [
                {
                    "kind": "review_requested",
                    "payload": {
                        "implementer": "coder",
                        "reviewer": "critic",
                    },
                    "run_id": 10,
                },
                {
                    "kind": "changes_requested",
                    "payload": {
                        "implementer": "coder",
                        "reviewer": "critic",
                    },
                    "run_id": 11,
                },
                {
                    "kind": "review_requested",
                    "payload": {
                        "implementer": "coder",
                        "reviewer": "quick-reviewer",
                    },
                    "run_id": 12,
                },
            ],
            "runs": [
                {
                    "id": 10,
                    "profile": "coder",
                    "outcome": "review_requested",
                    "metadata": {
                        "workspace_path": "/repo/.worktrees/t_impl"
                    },
                },
                {
                    "id": 11,
                    "profile": "critic",
                    "outcome": "changes_requested",
                    "metadata": None,
                },
                {
                    "id": 12,
                    "profile": "coder",
                    "outcome": "review_requested",
                    "metadata": {
                        "workspace_path": "/repo/.worktrees/t_impl"
                    },
                },
            ],
        }

        self.assertEqual(
            validate_review_handoff(
                payload,
                implementer_profile="coder",
                reviewer_profile="quick-reviewer",
            ),
            [],
        )

        stale_errors = validate_review_handoff(
            payload,
            implementer_profile="coder",
            reviewer_profile="critic",
        )

        self.assertTrue(
            any(
                error.startswith("review_assignee:")
                for error in stale_errors
            )
        )

        self.assertIn(
            "review_requested_event_missing_or_mismatched",
            stale_errors,
        )

    def test_runtime_cli_returns_zero_on_match(self) -> None:
        actual = json.dumps(
            {
                "assignee": "coder",
                "workspace_kind": "worktree",
                "workspace_path": "/repo",
                "branch_name": "pilot/full-flow-doc",
                "max_retries": 1,
                "parents": ["t_gate"],
            }
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(
                [
                    "runtime", "--actual-json", actual,
                    "--assignee", "coder",
                    "--workspace-kind", "worktree",
                    "--workspace-path", "/repo",
                    "--branch-name", "pilot/full-flow-doc",
                    "--max-retries", "1",
                    "--parent", "t_gate",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(output.getvalue().strip(), "RUNTIME_CONTRACT_OK")

    def test_runtime_cli_returns_two_on_drift(self) -> None:
        actual = json.dumps(
            {
                "assignee": "coder",
                "workspace_kind": "worktree",
                "workspace_path": "/repo",
                "branch_name": None,
                "max_retries": None,
                "parents": ["t_gate"],
            }
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(
                [
                    "runtime", "--actual-json", actual,
                    "--assignee", "coder",
                    "--workspace-kind", "worktree",
                    "--workspace-path", "/repo",
                    "--branch-name", "pilot/full-flow-doc",
                    "--max-retries", "1",
                    "--parent", "t_gate",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertTrue(output.getvalue().startswith("RUNTIME_CONTRACT_DRIFT:"))

    def test_handoff_cli_returns_zero_on_native_same_card_match(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(
                [
                    "handoff", "--actual-json", json.dumps(same_card_review_snapshot()),
                    "--implementer-profile", "coder", "--reviewer-profile", "critic",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(output.getvalue().strip(), "RUNTIME_CONTRACT_OK")

    def test_handoff_cli_returns_two_on_missing_event(self) -> None:
        payload = same_card_review_snapshot()
        payload["events"] = []
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(
                [
                    "handoff", "--actual-json", json.dumps(payload),
                    "--implementer-profile", "coder", "--reviewer-profile", "critic",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertTrue(output.getvalue().startswith("RUNTIME_CONTRACT_DRIFT:"))

    def test_format_drift_is_fail_closed(self) -> None:
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__":
    unittest.main()
