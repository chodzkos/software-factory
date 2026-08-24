#!/usr/bin/env python3
"""Parser decyzji review/audytu dla Software Factory."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

_DECISION_RE = re.compile(
    r"(?m)^DECISION:\s*(APPROVE|CHANGES_REQUIRED|SKIPPED_OX_UNAVAILABLE)\s*$"
)
_BLOCKING_RE = re.compile(r"(?im)^\s*(?:severity\s*[:=-]\s*)?(CRITICAL|HIGH)\b")


@dataclass(frozen=True)
class ReviewDecision:
    """Znormalizowany wynik review."""

    status: str
    reason: str


def parse_review(text: str, *, allow_ox_skip: bool = False) -> ReviewDecision:
    """Parsuj wynik review fail-closed; nie zgaduj brakującej decyzji."""
    decisions = _DECISION_RE.findall(text)
    unique = set(decisions)

    if len(decisions) != 1 or len(unique) != 1:
        return ReviewDecision("REVIEW_PENDING", "missing_or_multiple_decisions")

    decision = decisions[0]

    if decision == "SKIPPED_OX_UNAVAILABLE":
        if allow_ox_skip:
            return ReviewDecision(decision, "optional_ox_unavailable")
        return ReviewDecision("REVIEW_PENDING", "ox_skip_not_allowed")

    if decision == "APPROVE" and _BLOCKING_RE.search(text):
        return ReviewDecision("REVIEW_PENDING", "approve_with_blocking_finding")

    return ReviewDecision(decision, "parsed")


def main() -> int:
    """CLI do walidacji pliku review w workflow/CI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--allow-ox-skip", action="store_true")
    args = parser.parse_args()

    result = parse_review(
        args.path.read_text(encoding="utf-8"), allow_ox_skip=args.allow_ox_skip
    )
    print(result.status)
    print(result.reason)
    return 0 if result.status in {"APPROVE", "CHANGES_REQUIRED", "SKIPPED_OX_UNAVAILABLE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
