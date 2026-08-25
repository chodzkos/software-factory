import unittest

from kanban_runtime_contract import (
    RuntimeExpectation,
    format_drift,
    validate_review_handoff,
    validate_runtime,
    validate_task_graph,
)


class RuntimeContractTests(unittest.TestCase):
    def test_matching_runtime_passes(self) -> None:
        actual = {
            "id": "t_impl",
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": 1,
            "max_runtime": 600,
            "parents": [],
        }
        expected = RuntimeExpectation(
            assignee="coder",
            workspace_kind="worktree",
            workspace_path="/repo",
            branch_name="pilot/full-flow-doc",
            max_retries=1,
            max_runtime=600,
        )
        self.assertEqual(validate_runtime(actual, expected), [])

    def test_max_retries_drift_is_rejected(self) -> None:
        actual = {
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": "pilot/full-flow-doc",
            "max_retries": 2,
            "max_runtime": 600,
            "parents": [],
        }
        expected = RuntimeExpectation(
            assignee="coder",
            workspace_kind="worktree",
            workspace_path="/repo",
            branch_name="pilot/full-flow-doc",
            max_retries=1,
            max_runtime=600,
        )
        errors = validate_runtime(actual, expected)
        self.assertTrue(any(error.startswith("max_retries:") for error in errors))

    def test_branch_drift_is_rejected(self) -> None:
        actual = {
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/repo",
            "branch_name": None,
            "max_retries": 1,
            "parents": [],
        }
        expected = RuntimeExpectation(
            assignee="coder",
            workspace_kind="worktree",
            workspace_path="/repo",
            branch_name="pilot/full-flow-doc",
            max_retries=1,
        )
        errors = validate_runtime(actual, expected)
        self.assertTrue(any(error.startswith("branch_name:") for error in errors))

    def test_missing_parent_is_rejected(self) -> None:
        actual = {
            "assignee": "critic",
            "workspace_kind": "dir",
            "workspace_path": "/repo/.worktrees/t_impl",
            "branch_name": None,
            "max_retries": 1,
            "parents": [],
        }
        expected = RuntimeExpectation(
            assignee="critic",
            workspace_kind="dir",
            workspace_path="/repo/.worktrees/t_impl",
            branch_name=None,
            max_retries=1,
            parents=("t_impl",),
        )
        errors = validate_runtime(actual, expected)
        self.assertTrue(any(error.startswith("parents:") for error in errors))

    def test_reviewer_new_worktree_is_rejected(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "resolved_workspace_path": "/repo/.worktrees/t_impl",
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
        self.assertTrue(any(error.startswith("review_workspace_kind:") for error in errors))
        self.assertTrue(any(error.startswith("review_workspace_path:") for error in errors))

    def test_exact_reviewer_dir_handoff_passes(self) -> None:
        implementation = {
            "id": "t_impl",
            "workspace_kind": "worktree",
            "resolved_workspace_path": "/repo/.worktrees/t_impl",
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
            "resolved_workspace_path": "/repo/.worktrees/t_impl",
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
        implementation = {"id": "t_impl", "workspace_kind": "worktree"}
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

    def test_pilot_6_regression_is_rejected(self) -> None:
        implementation = {
            "id": "t_36a829a9",
            "assignee": "coder",
            "workspace_kind": "worktree",
            "workspace_path": "/home/marcin/projects/software-factory",
            "resolved_workspace_path": "/home/marcin/projects/software-factory/.worktrees/t_36a829a9",
            "branch_name": None,
            "max_retries": 2,
            "max_runtime": 600,
            "parents": [],
        }
        implementation_expected = RuntimeExpectation(
            assignee="coder",
            workspace_kind="worktree",
            workspace_path="/home/marcin/projects/software-factory",
            branch_name="pilot/full-flow-doc",
            max_retries=1,
            max_runtime=600,
        )
        implementation_errors = validate_runtime(
            implementation, implementation_expected
        )
        self.assertTrue(any(error.startswith("branch_name:") for error in implementation_errors))
        self.assertTrue(any(error.startswith("max_retries:") for error in implementation_errors))

        review = {
            "id": "t_d3ebea65",
            "assignee": "critic",
            "workspace_kind": "worktree",
            "workspace_path": "/home/marcin/projects/software-factory",
            "branch_name": None,
            "max_retries": 2,
            "max_runtime": 600,
            "parents": ["t_36a829a9"],
        }
        review_expected = RuntimeExpectation(
            assignee="critic",
            workspace_kind="dir",
            workspace_path="/home/marcin/projects/software-factory/.worktrees/t_36a829a9",
            branch_name=None,
            max_retries=1,
            max_runtime=600,
            parents=("t_36a829a9",),
        )
        errors = validate_task_graph(
            review,
            review_expected,
            implementation=implementation,
            implementer_profile="coder",
            reviewer_profile="critic",
        )
        self.assertTrue(any(error.startswith("workspace_kind:") for error in errors))
        self.assertTrue(any(error.startswith("workspace_path:") for error in errors))
        self.assertTrue(any(error.startswith("max_retries:") for error in errors))
        self.assertTrue(any(error.startswith("review_workspace_kind:") for error in errors))

    def test_format_drift_is_fail_closed(self) -> None:
        self.assertEqual(format_drift([]), "RUNTIME_CONTRACT_OK")
        self.assertTrue(format_drift(["x"]).startswith("RUNTIME_CONTRACT_DRIFT:"))


if __name__ == "__main__":
    unittest.main()
