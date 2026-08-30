from __future__ import annotations

import unittest

from model_routing_policy import (
    CLAUDE_IMPLEMENTER,
    CLAUDE_REVIEWER,
    GROK_REVIEWER,
    OPENAI_IMPLEMENTER,
    OPENAI_REVIEWER,
    required_reviewers,
    validate_review_route,
)


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


if __name__ == "__main__":
    unittest.main()
