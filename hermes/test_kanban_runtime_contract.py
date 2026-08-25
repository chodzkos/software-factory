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
        expected = RuntimeExpectation(
            assignee="coder",
            workspace_kind="worktree",
            workspace_path="/repo",
            branch_name="pilot/full-flow-doc",
            max_retries=1,
        )
        self.assertEqual(validate_runtime(actual, expected), [])

    def test_nested_kanban_show_parents_are_normalized(self) -> None:
        payload = {
            "task": {
                "id": "t_review",
                "assignee": "critic",
                "workspace_kind": "dir",
                "workspace_path": "/repo/.worktrees/t_impl",
            },
            "parents": ["t_impl"],
        }
        normalized = normalize_snapshot(payload)
        self.assertEqual(normalized["parents"], ["t_impl"])

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
                "workspace_kind": "dir",
                "workspace_path": "/repo/.worktrees/t_impl",
                "max_retries": 1,
            },
            "parents": ["t_impl", "t_extra"],
        }
        expected = RuntimeExpectation(
            "critic", "dir", "/repo/.worktrees/t_impl", None, 1, ("t_impl",)
        )
        self.assertTrue(any(e.startswith("parents:") for e in validate_runtime(payload, expected)))

    def test_post_claim_workspace_path_is_resolved_worktree(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        self.assertEqual(
            resolved_implementation_worktree(implementation),
            "/repo/.worktrees/t_impl",
        )

    def test_repo_anchor_is_not_resolved_worktree(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
        }
        self.assertIsNone(resolved_implementation_worktree(implementation))

    def test_reviewer_new_worktree_is_rejected(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        review = {
            "id": "t_review",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "parents": ["t_impl"],
        }
        errors = validate_review_handoff(
            implementation,
            review,
            implementer_profile="coder",
            reviewer_profile="critic",
        )
        self.assertTrue(any(e.startswith("review_workspace_kind:") for e in errors))
        self.assertTrue(any(e.startswith("review_workspace_path:") for e in errors))

    def test_exact_reviewer_dir_handoff_passes(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        review = {
            "id": "t_review",
            "workspace_kind": "dir",
            "workspace_path": "/repo/.worktrees/t_impl",
            "parents": ["t_impl"],
        }
        self.assertEqual(
            validate_review_handoff(
                implementation,
                review,
                implementer_profile="coder",
                reviewer_profile="critic",
            ),
            [],
        )

    def test_implementer_cannot_be_reviewer(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        review = {
            "workspace_kind": "dir",
            "workspace_path": "/repo/.worktrees/t_impl",
            "parents": ["t_impl"],
        }
        errors = validate_review_handoff(
            implementation,
            review,
            implementer_profile="coder",
            reviewer_profile="coder",
        )
        self.assertIn("implementer_and_reviewer_must_differ", errors)

    def test_missing_resolved_worktree_fails_closed(self) -> None:
        implementation = {"id": "t_impl", "workspace_kind": "worktree", "workspace_path": "/repo"}
        review = {
            "workspace_kind": "dir",
            "workspace_path": "/repo/.worktrees/t_impl",
            "parents": ["t_impl"],
        }
        errors = validate_review_handoff(
            implementation,
            review,
            implementer_profile="coder",
            reviewer_profile="critic",
        )
        self.assertIn("implementation_resolved_worktree_missing", errors)

    def test_missing_implementation_id_fails_closed(self) -> None:
        implementation = {
            "workspace_kind": "worktree",
            "workspace_path": "/repo/.worktrees/t_impl",
        }
        review = {
            "workspace_kind": "dir",
            "workspace_path": "/repo/.worktrees/t_impl",
            "parents": [],
        }
        errors = validate_review_handoff(
            implementation,
            review,
            implementer_profile="coder",
            reviewer_profile="critic",
        )
        self.assertIn("implementation_id_missing", errors)

    def test_pilot_6_regression_is_rejected(self) -> None:
        implementation_create = {
            "id": "t_36a829a9",
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/home/marcin/projects/software-factory",
            "branch_name": None,
            "max_retries": None,
        }
        implementation_expected = RuntimeExpectation(
            "coder",
            "worktree",
            "/home/marcin/projects/software-factory",
            "pilot/full-flow-doc",
            1,
        )
        implementation_errors = validate_runtime(implementation_create, implementation_expected)
        self.assertTrue(any(e.startswith("branch_name:") for e in implementation_errors))
        self.assertTrue(any(e.startswith("max_retries:") for e in implementation_errors))

        implementation_post_claim = {
            "id": "t_36a829a9",
            "workspace_kind": "worktree",
            "workspace_path": "/home/marcin/projects/software-factory/.worktrees/t_36a829a9",
        }
        review = {
            "task": {
                "id": "t_d3ebea65",
                "assignee": "critic",
                "workspace_kind": "worktree",
                "workspace_path": "/home/marcin/projects/software-factory",
                "max_retries": None,
            },
            "parents": ["t_36a829a9"],
        }
        review_expected = RuntimeExpectation(
            "critic",
            "dir",
            "/home/marcin/projects/software-factory/.worktrees/t_36a829a9",
            None,
            1,
            ("t_36a829a9",),
        )
        errors = validate_task_graph(
            review,
            review_expected,
            implementation=implementation_post_claim,
            implementer_profile="coder",
            reviewer_profile="critic",
        )
        self.assertTrue(any(e.startswith("workspace_kind:") for e in errors))
        self.assertTrue(any(e.startswith("workspace_path:") for e in errors))
        self.assertTrue(any(e.startswith("max_retries:") for e in errors))
        self.assertTrue(any(e.startswith("review_workspace_kind:") for e in errors))

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
                    "runtime",
                    "--actual-json",
                    actual,
                    "--assignee",
                    "coder",
                    "--workspace-kind",
                    "worktree",
                    "--workspace-path",
                    "/repo",
                    "--branch-name",
                    "pilot/full-flow-doc",
                    "--max-retries",
                    "1",
                    "--parent",
                    "t_gate",
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
                    "runtime",
                    "--actual-json",
                    actual,
                    "--assignee",
                    "coder",
                    "--workspace-kind",
                    "worktree",
                    "--workspace-path",
                    "/repo",
                    "--branch-name",
                    "pilot/full-flow-doc",
                    "--max-retries",
                    "1",
                    "--parent",
                    "t_gate",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertTrue(output.getvalue().startswith("RUNTIME_CONTRACT_DRIFT:"))

    def test_format_drift_is_fail_closed(self) -> None:
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__":
    unittest.main()
