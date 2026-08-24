#!/usr/bin/env python3
"""Testy parsera decyzji review."""

import unittest

from review_decision import parse_review


class ReviewDecisionTests(unittest.TestCase):
    def test_approve(self) -> None:
        self.assertEqual(parse_review("ok\nDECISION: APPROVE\n").status, "APPROVE")

    def test_changes_required(self) -> None:
        self.assertEqual(
            parse_review("severity: MEDIUM\nDECISION: CHANGES_REQUIRED\n").status,
            "CHANGES_REQUIRED",
        )

    def test_missing_decision_fails_closed(self) -> None:
        self.assertEqual(parse_review("looks good").status, "REVIEW_PENDING")

    def test_multiple_decisions_fail_closed(self) -> None:
        text = "DECISION: APPROVE\nDECISION: CHANGES_REQUIRED\n"
        self.assertEqual(parse_review(text).status, "REVIEW_PENDING")

    def test_high_cannot_be_approved(self) -> None:
        text = "severity: HIGH\nDECISION: APPROVE\n"
        self.assertEqual(parse_review(text).status, "REVIEW_PENDING")

    def test_ox_skip_is_optional_only(self) -> None:
        text = "DECISION: SKIPPED_OX_UNAVAILABLE\n"
        self.assertEqual(parse_review(text).status, "REVIEW_PENDING")
        self.assertEqual(
            parse_review(text, allow_ox_skip=True).status,
            "SKIPPED_OX_UNAVAILABLE",
        )


if __name__ == "__main__":
    unittest.main()
