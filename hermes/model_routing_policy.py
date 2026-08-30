from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


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
    "quick-reviewer": "google",
    "auditor-gpt": "openai",
    "auditor-grok": "xai",
}

_CONTRACT_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*):\s*(.*?)\s*$")


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


def parse_task_contract(body: str) -> tuple[ReviewRoute | None, list[str]]:
    """Parse routing fields from the actual Markdown task body, fail-closed."""
    if not isinstance(body, str) or not body.strip():
        return None, ["task_body_missing"]

    fields: dict[str, str] = {}
    for raw_line in body.splitlines():
        match = _CONTRACT_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        key, value = match.groups()
        if key in fields:
            return None, [f"duplicate_contract_field:{key}"]
        fields[key] = value

    missing = [
        key
        for key in ("IMPLEMENTER", "REQUIRED_REVIEWERS", "SECURITY_SENSITIVE")
        if key not in fields or not fields[key]
    ]
    if missing:
        return None, [f"missing_contract_fields:{','.join(missing)}"]

    security_raw = fields["SECURITY_SENSITIVE"].lower()
    if security_raw not in {"yes", "no"}:
        return None, ["invalid_security_sensitive"]

    implementer = fields["IMPLEMENTER"]
    reviewers_raw = fields["REQUIRED_REVIEWERS"]
    reviewers = tuple(
        value.strip() for value in reviewers_raw.split(",") if value.strip() and value.strip() != "none"
    )
    if not reviewers:
        return None, ["required_reviewers_empty"]

    return ReviewRoute(
        implementer=implementer,
        security_sensitive=security_raw == "yes",
        required_reviewers=reviewers,
    ), []


def _task_body_from_json(raw: str) -> tuple[str | None, list[str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"actual_json_invalid:{exc.msg}"]
    if not isinstance(payload, Mapping):
        return None, ["actual_json_not_object"]
    task: Mapping[str, Any]
    nested = payload.get("task")
    if isinstance(nested, Mapping):
        task = nested
    else:
        task = payload
    body = task.get("body")
    if not isinstance(body, str):
        return None, ["task_body_missing"]
    return body, []


def validate_task_body(body: str) -> list[str]:
    route, errors = parse_task_contract(body)
    if errors or route is None:
        return errors or ["task_contract_unparseable"]
    return validate_review_route(
        route.implementer,
        route.required_reviewers,
        security_sensitive=route.security_sensitive,
    )


def format_route(errors: Sequence[str]) -> str:
    if not errors:
        return "MODEL_ROUTING_OK"
    return "MODEL_ROUTING_DRIFT: " + "; ".join(errors)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Software Factory model/reviewer routing policy")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task-body")
    source.add_argument("--actual-json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    if args.actual_json is not None:
        body, errors = _task_body_from_json(args.actual_json)
        if not errors and body is not None:
            errors = validate_task_body(body)
    else:
        errors = validate_task_body(args.task_body)
    print(format_route(errors))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
