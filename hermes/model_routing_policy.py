from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


OPENAI_IMPLEMENTER = "coder"
CLAUDE_IMPLEMENTER = "coder-claude"
OPENAI_REVIEWER = "reviewer-gpt"
CLAUDE_REVIEWER = "reviewer-claude"
GROK_REVIEWER = "critic"

IMPLEMENTER_VENDOR = {
    OPENAI_IMPLEMENTER: "openai",
    CLAUDE_IMPLEMENTER: "anthropic",
}

REVIEWER_VENDOR = {
    OPENAI_REVIEWER: "openai",
    CLAUDE_REVIEWER: "anthropic",
    GROK_REVIEWER: "xai",
    "auditor-gpt": "openai",
    "auditor-grok": "xai",
}


@dataclass(frozen=True)
class ReviewRoute:
    implementer: str
    security_sensitive: bool
    required_reviewers: tuple[str, ...]


def required_reviewers(implementer: str, *, security_sensitive: bool) -> tuple[str, ...]:
    """Return the fail-closed required reviewer set for one implementation profile."""
    if implementer not in IMPLEMENTER_VENDOR:
        raise ValueError(f"unknown implementer profile: {implementer}")

    if security_sensitive:
        # Deep security review is always OpenAI. If OpenAI implemented the change,
        # add Grok as a second independent cross-vendor reviewer; Claude is never
        # used as the security reviewer.
        if implementer == OPENAI_IMPLEMENTER:
            return (OPENAI_REVIEWER, GROK_REVIEWER)
        return (OPENAI_REVIEWER,)

    if implementer == OPENAI_IMPLEMENTER:
        return (CLAUDE_REVIEWER,)
    return (OPENAI_REVIEWER,)


def validate_review_route(
    implementer: str,
    reviewers: Sequence[str],
    *,
    security_sensitive: bool,
) -> list[str]:
    errors: list[str] = []
    try:
        required = required_reviewers(implementer, security_sensitive=security_sensitive)
    except ValueError as exc:
        return [str(exc)]

    actual = tuple(reviewers)
    missing = [reviewer for reviewer in required if reviewer not in actual]
    if missing:
        errors.append(f"missing_required_reviewers:{','.join(missing)}")

    if len(set(actual)) != len(actual):
        errors.append("duplicate_reviewers")

    unknown = [reviewer for reviewer in actual if reviewer not in REVIEWER_VENDOR]
    if unknown:
        errors.append(f"unknown_reviewers:{','.join(unknown)}")

    if security_sensitive and CLAUDE_REVIEWER in actual:
        errors.append("anthropic_security_reviewer_forbidden")

    if not security_sensitive and len(actual) == 1 and actual:
        reviewer = actual[0]
        reviewer_vendor = REVIEWER_VENDOR.get(reviewer)
        implementer_vendor = IMPLEMENTER_VENDOR[implementer]
        if reviewer_vendor == implementer_vendor:
            errors.append("normal_review_must_be_cross_vendor")

    return errors
