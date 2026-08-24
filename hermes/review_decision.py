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
_SEVERITY_FIELD_RE = re.compile(r"(?i)^severity\s*[:=-]\s*(CRITICAL|HIGH)\b")
_SEVERITY_TABLE_RE = re.compile(
    r"(?i)^\|\s*severity\s*\|\s*(CRITICAL|HIGH)\s*\|"
)
_LIST_PREFIX_RE = re.compile(r"^(?:[-+*]|\d+[.)])\s+")


@dataclass(frozen=True)
class ReviewDecision:
    """Znormalizowany wynik review."""

    status: str
    reason: str


def _normalize_finding_line(line: str) -> str:
    """Usuń wyłącznie dekoracje Markdown istotne dla pól findingu."""
    normalized = line.strip()
    if normalized.startswith(">"):
        normalized = normalized[1:].lstrip()
    normalized = _LIST_PREFIX_RE.sub("", normalized, count=1)
    normalized = normalized.replace("`", "").replace("**", "").replace("__", "")
    return normalized.strip()


def _has_blocking_finding(text: str) -> bool:
    """Wykryj jawne pole severity HIGH/CRITICAL bez zgadywania z prozy."""
    for raw_line in text.splitlines():
        line = _normalize_finding_line(raw_line)
        if _SEVERITY_FIELD_RE.match(line) or _SEVERITY_TABLE_RE.match(line):
            return True
    return False


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

    if decision == "APPROVE" and _has_blocking_finding(text):
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
