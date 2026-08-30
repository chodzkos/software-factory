from __future__ import annotations

import json
import unittest

from hermes.model_routing_policy import (
    CLAUDE_IMPLEMENTER,
    CLAUDE_REVIEWER,
    OPENAI_IMPLEMENTER,
    OPENAI_REVIEWER,
    _task_body_from_json,
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
    def test_normal_openai_implementation_requires_exact_claude_review(self):
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
        self.assertTrue(
            validate_review_route(
                OPENAI_IMPLEMENTER,
                [CLAUDE_REVIEWER, OPENAI_REVIEWER],
                security_sensitive=False,
            )
        )

    def test_normal_claude_implementation_requires_exact_openai_review(self):
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
        self.assertTrue(any(error.startswith("reviewer_set_mismatch:") for error in errors))
        self.assertIn("normal_review_must_be_cross_vendor", errors)

    def test_security_sensitive_requires_claude_implementer_and_openai_reviewer(self):
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
        self.assertIn(
            "security_sensitive_openai_implementer_forbidden",
            validate_review_route(
                OPENAI_IMPLEMENTER,
                [OPENAI_REVIEWER],
                security_sensitive=True,
            ),
        )

    def test_claude_security_reviewer_is_forbidden(self):
        errors = validate_review_route(
            CLAUDE_IMPLEMENTER,
            [CLAUDE_REVIEWER],
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
                ["reviewer-unknown"],
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
        self.assertIn("missing_contract_fields:SECURITY_SENSITIVE", validate_task_body(body))

    def test_all_duplicate_routing_fields_fail_closed(self):
        base = task_body(
            implementer=OPENAI_IMPLEMENTER,
            reviewers=CLAUDE_REVIEWER,
            security="no",
        )
        for line, expected in (
            ("IMPLEMENTER: coder-claude\n", "duplicate_contract_field:IMPLEMENTER"),
            ("REQUIRED_REVIEWERS: reviewer-gpt\n", "duplicate_contract_field:REQUIRED_REVIEWERS"),
            ("SECURITY_SENSITIVE: yes\n", "duplicate_contract_field:SECURITY_SENSITIVE"),
        ):
            with self.subTest(line=line):
                self.assertIn(expected, validate_task_body(base + line))

    def test_malformed_reviewer_csv_fails_closed(self):
        for reviewers in (
            ",,reviewer-gpt,,",
            "reviewer-gpt, none",
            "reviewer-gpt,critic",
        ):
            with self.subTest(reviewers=reviewers):
                errors = validate_task_body(
                    task_body(
                        implementer=CLAUDE_IMPLEMENTER,
                        reviewers=reviewers,
                        security="yes",
                    )
                )
                self.assertTrue(errors)

    def test_nested_task_shape_is_authoritative(self):
        body = task_body(
            implementer=CLAUDE_IMPLEMENTER,
            reviewers=OPENAI_REVIEWER,
            security="yes",
        )
        raw = json.dumps({"task": None, "body": body})
        parsed, errors = _task_body_from_json(raw)
        self.assertIsNone(parsed)
        self.assertEqual(errors, ["actual_json_task_not_object"])

    def test_duplicate_json_keys_fail_closed(self):
        body = task_body(
            implementer=CLAUDE_IMPLEMENTER,
            reviewers=OPENAI_REVIEWER,
            security="yes",
        )
        raw = '{"task":{"body":"bad"},"task":{"body":' + json.dumps(body) + '}}'
        parsed, errors = _task_body_from_json(raw)
        self.assertIsNone(parsed)
        self.assertTrue(errors and errors[0].startswith("actual_json_duplicate_key:"))


if __name__ == "__main__":
    unittest.main()
