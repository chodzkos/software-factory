from __future__ import annotations

import unittest

from model_routing_policy import (
    CLAUDE_IMPLEMENTER,
    CLAUDE_REVIEWER,
    GROK_REVIEWER,
    OPENAI_IMPLEMENTER,
    OPENAI_REVIEWER,
    parse_task_contract,
    required_reviewers,
    validate_review_route,
    validate_task_body,
)


def task_body(*, implementer: str, reviewers: str, security: str) -> str:
    return f"""## Task Contract
TYPE: feature
RISK: medium
SECURITY_SENSITIVE: {security}
ASSIGNEE: {implementer}
REPOSITORY: owner/repo
WORKSPACE: worktree:/repo
IMPLEMENTER: {implementer}
REQUIRED_REVIEWERS: {reviewers}
OPTIONAL_REVIEWERS: none
REQUIRED_EVIDENCE: tests
ACCEPTANCE_CRITERIA:
- works
"""


class ModelRoutingPolicyTests(unittest.TestCase):
    def test_normal_openai_implementation_requires_claude_review(self):
        self.assertEqual(
            required_reviewers(OPENAI_IMPLEMENTER, security_sensitive=False),
            (CLAUDE_REVIEWER,),
        )
        self.assertEqual(
            validate_review_route(
                OPENAI_IMPLEMENTER,
                [CLAUDE_REVIEWER],
                security_sensitive=False,
            ),
            [],
        )

    def test_normal_claude_implementation_requires_openai_review(self):
        self.assertEqual(
            required_reviewers(CLAUDE_IMPLEMENTER, security_sensitive=False),
            (OPENAI_REVIEWER,),
        )
        self.assertEqual(
            validate_review_route(
                CLAUDE_IMPLEMENTER,
                [OPENAI_REVIEWER],
                security_sensitive=False,
            ),
            [],
        )

    def test_normal_same_vendor_review_fails_closed(self):
        errors = validate_review_route(
            OPENAI_IMPLEMENTER,
            [OPENAI_REVIEWER],
            security_sensitive=False,
        )
        self.assertIn("missing_required_reviewers:reviewer-claude", errors)
        self.assertIn("normal_review_must_be_cross_vendor", errors)

    def test_security_sensitive_claude_implementation_requires_openai(self):
        self.assertEqual(
            required_reviewers(CLAUDE_IMPLEMENTER, security_sensitive=True),
            (OPENAI_REVIEWER,),
        )
        self.assertEqual(
            validate_review_route(
                CLAUDE_IMPLEMENTER,
                [OPENAI_REVIEWER],
                security_sensitive=True,
            ),
            [],
        )

    def test_security_sensitive_openai_implementation_adds_grok_independence(self):
        self.assertEqual(
            required_reviewers(OPENAI_IMPLEMENTER, security_sensitive=True),
            (OPENAI_REVIEWER, GROK_REVIEWER),
        )
        self.assertEqual(
            validate_review_route(
                OPENAI_IMPLEMENTER,
                [OPENAI_REVIEWER, GROK_REVIEWER],
                security_sensitive=True,
            ),
            [],
        )

    def test_claude_security_reviewer_is_forbidden(self):
        errors = validate_review_route(
            CLAUDE_IMPLEMENTER,
            [OPENAI_REVIEWER, CLAUDE_REVIEWER],
            security_sensitive=True,
        )
        self.assertIn("anthropic_security_reviewer_forbidden", errors)

    def test_unknown_profiles_fail_closed(self):
        self.assertTrue(
            validate_review_route("coder-unknown", [OPENAI_REVIEWER], security_sensitive=False)
        )
        self.assertIn(
            "unknown_reviewers:reviewer-unknown",
            validate_review_route(
                CLAUDE_IMPLEMENTER,
                [OPENAI_REVIEWER, "reviewer-unknown"],
                security_sensitive=False,
            ),
        )

    def test_task_body_is_source_of_truth_for_normal_route(self):
        body = task_body(
            implementer=OPENAI_IMPLEMENTER,
            reviewers=CLAUDE_REVIEWER,
            security="no",
        )
        route, errors = parse_task_contract(body)
        self.assertEqual(errors, [])
        self.assertIsNotNone(route)
        self.assertEqual(validate_task_body(body), [])

    def test_missing_security_sensitive_fails_closed(self):
        body = task_body(
            implementer=OPENAI_IMPLEMENTER,
            reviewers=CLAUDE_REVIEWER,
            security="no",
        ).replace("SECURITY_SENSITIVE: no\n", "")
        self.assertIn(
            "missing_contract_fields:SECURITY_SENSITIVE",
            validate_task_body(body),
        )

    def test_duplicate_routing_field_fails_closed(self):
        body = task_body(
            implementer=OPENAI_IMPLEMENTER,
            reviewers=CLAUDE_REVIEWER,
            security="no",
        ) + "IMPLEMENTER: coder-claude\n"
        self.assertIn("duplicate_contract_field:IMPLEMENTER", validate_task_body(body))

    def test_task_body_forbids_claude_security_reviewer(self):
        body = task_body(
            implementer=CLAUDE_IMPLEMENTER,
            reviewers=f"{OPENAI_REVIEWER},{CLAUDE_REVIEWER}",
            security="yes",
        )
        self.assertIn("anthropic_security_reviewer_forbidden", validate_task_body(body))


if __name__ == "__main__":
    unittest.main()
