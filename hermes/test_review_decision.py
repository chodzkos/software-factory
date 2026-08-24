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

    def test_supported_plus_unsupported_decision_fails_closed(self) -> None:
        text = "DECISION: APPROVE\nDECISION: CHANGES_REQUESTED\n"
        result = parse_review(text)
        self.assertEqual(result.status, "REVIEW_PENDING")
        self.assertEqual(result.reason, "missing_or_multiple_decisions")

    def test_single_unsupported_decision_fails_closed(self) -> None:
        result = parse_review("DECISION: CHANGES_REQUESTED\n")
        self.assertEqual(result.status, "REVIEW_PENDING")
        self.assertEqual(result.reason, "unparseable_decision")

    def test_high_cannot_be_approved(self) -> None:
        text = "severity: HIGH\nDECISION: APPROVE\n"
        self.assertEqual(parse_review(text).status, "REVIEW_PENDING")

    def test_markdown_high_formats_cannot_be_approved(self) -> None:
        samples = (
            "- severity: HIGH",
            "- `severity`: HIGH",
            "- **severity:** HIGH",
            "- severity: `HIGH`",
            "1. **severity:** `CRITICAL`",
            "> `severity`: HIGH",
            "| severity | HIGH | impact |",
        )
        for finding in samples:
            with self.subTest(finding=finding):
                text = f"{finding}\nDECISION: APPROVE\n"
                self.assertEqual(parse_review(text).status, "REVIEW_PENDING")

    def test_non_finding_high_prose_does_not_block_approve(self) -> None:
        samples = (
            "no HIGH/CRITICAL findings",
            "HIGH/CRITICAL: none",
            "HIGH-level overview",
            "CRITICAL PATH",
            "There are no HIGH or CRITICAL issues.",
        )
        for prose in samples:
            with self.subTest(prose=prose):
                text = f"{prose}\nDECISION: APPROVE\n"
                self.assertEqual(parse_review(text).status, "APPROVE")

    def test_ox_skip_is_optional_only(self) -> None:
        text = "DECISION: SKIPPED_OX_UNAVAILABLE\n"
        self.assertEqual(parse_review(text).status, "REVIEW_PENDING")
        self.assertEqual(
            parse_review(text, allow_ox_skip=True).status,
            "SKIPPED_OX_UNAVAILABLE",
        )


if __name__ == "__main__":
    unittest.main()
