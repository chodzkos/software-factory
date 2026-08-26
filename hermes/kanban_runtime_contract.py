from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RuntimeExpectation:
    assignee: str
    workspace_kind: str
    workspace_path: str | None
    branch_name: str | None
    max_retries: int | None
    parents: tuple[str, ...] = ()


def normalize_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Znormalizuj CLI JSON lub kanban_show do jednego kształtu walidatora."""

    if isinstance(payload.get("task"), Mapping):
        task = dict(payload["task"])
        if "parents" not in task and "parents" in payload:
            task["parents"] = payload.get("parents")
    else:
        task = dict(payload)

    if "parents" not in task and "parent_ids" in task:
        task["parents"] = task.get("parent_ids")
    return task


def _parents(actual: Mapping[str, Any]) -> tuple[str, ...]:
    raw = actual.get("parents", ())
    if raw is None:
        return ()
    return tuple(str(value) for value in raw)


def _records(payload: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get(key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(value for value in raw if isinstance(value, Mapping))


def validate_runtime(payload: Mapping[str, Any], expectation: RuntimeExpectation) -> list[str]:
    """Porównaj oczekiwane pola z realnym snapshotem Hermesa fail-closed."""

    actual = normalize_snapshot(payload)
    errors: list[str] = []
    checks = {
        "assignee": expectation.assignee,
        "workspace_kind": expectation.workspace_kind,
        "workspace_path": expectation.workspace_path,
        "branch_name": expectation.branch_name,
        "max_retries": expectation.max_retries,
    }
    for field, expected in checks.items():
        if expected is None:
            continue
        actual_value = actual.get(field)
        if actual_value != expected:
            errors.append(f"{field}: expected={expected!r} actual={actual_value!r}")

    actual_parents = _parents(actual)
    if actual_parents != expectation.parents:
        errors.append(f"parents: expected={expectation.parents!r} actual={actual_parents!r}")
    return errors


def resolved_implementation_worktree(payload: Mapping[str, Any]) -> str | None:
    """Po claimie Hermes zapisuje materializowany worktree bezpośrednio w workspace_path."""

    task = normalize_snapshot(payload)
    if task.get("workspace_kind") != "worktree":
        return None
    task_id = str(task.get("id") or "")
    path = task.get("workspace_path")
    if not task_id or not isinstance(path, str) or not path.startswith("/"):
        return None
    parts = PurePosixPath(path).parts
    try:
        index = parts.index(".worktrees")
    except ValueError:
        return None
    if index + 1 >= len(parts) or parts[index + 1] != task_id:
        return None
    return path.rstrip("/")


def validate_review_handoff(
    payload: Mapping[str, Any],
    *,
    implementer_profile: str,
    reviewer_profile: str,
) -> list[str]:
    """Waliduj natywny Hermes same-card handoff do independent reviewera."""

    task = normalize_snapshot(payload)
    errors: list[str] = []

    if implementer_profile == reviewer_profile:
        errors.append("implementer_and_reviewer_must_differ")

    task_id = str(task.get("id") or "")
    if not task_id:
        errors.append("implementation_id_missing")
        return errors

    resolved_path = resolved_implementation_worktree(payload)
    if not resolved_path:
        errors.append("implementation_resolved_worktree_missing")
        return errors

    if task.get("assignee") != reviewer_profile:
        errors.append(
            f"review_assignee: expected={reviewer_profile!r} actual={task.get('assignee')!r}"
        )
    if task.get("status") != "review":
        errors.append(f"review_status: expected='review' actual={task.get('status')!r}")

    matching_event = False
    for event in _records(payload, "events"):
        if event.get("kind") != "review_requested":
            continue
        event_payload = event.get("payload")
        if not isinstance(event_payload, Mapping):
            continue
        if (
            event_payload.get("implementer") == implementer_profile
            and event_payload.get("reviewer") == reviewer_profile
        ):
            matching_event = True
            break
    if not matching_event:
        errors.append("review_requested_event_missing_or_mismatched")

    matching_run = False
    for run in _records(payload, "runs"):
        if run.get("profile") != implementer_profile or run.get("outcome") != "review_requested":
            continue
        metadata = run.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("workspace_path") == resolved_path:
            matching_run = True
            break
    if not matching_run:
        errors.append("implementer_review_run_missing_or_workspace_mismatched")

    return errors


def validate_task_graph(
    actual: Mapping[str, Any],
    expectation: RuntimeExpectation,
    *,
    implementer_profile: str | None = None,
    reviewer_profile: str | None = None,
) -> list[str]:
    errors = validate_runtime(actual, expectation)
    if implementer_profile is not None or reviewer_profile is not None:
        if not implementer_profile or not reviewer_profile:
            errors.append("review_profiles_required")
        else:
            errors.extend(
                validate_review_handoff(
                    actual,
                    implementer_profile=implementer_profile,
                    reviewer_profile=reviewer_profile,
                )
            )
    return errors


def format_drift(errors: Sequence[str]) -> str:
    if not errors:
        return "RUNTIME_CONTRACT_OK"
    return "RUNTIME_CONTRACT_DRIFT: " + "; ".join(errors)


def _json_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label}: expected JSON object")
    return parsed


def _runtime_command(args: argparse.Namespace) -> int:
    actual = _json_object(args.actual_json, "actual-json")
    expectation = RuntimeExpectation(
        assignee=args.assignee,
        workspace_kind=args.workspace_kind,
        workspace_path=args.workspace_path,
        branch_name=args.branch_name,
        max_retries=args.max_retries,
        parents=tuple(args.parent),
    )
    errors = validate_runtime(actual, expectation)
    print(format_drift(errors))
    return 0 if not errors else 2


def _handoff_command(args: argparse.Namespace) -> int:
    actual = _json_object(args.actual_json, "actual-json")
    errors = validate_review_handoff(
        actual,
        implementer_profile=args.implementer_profile,
        reviewer_profile=args.reviewer_profile,
    )
    print(format_drift(errors))
    return 0 if not errors else 2


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Software Factory Kanban runtime contract validator")
    sub = parser.add_subparsers(dest="command", required=True)

    runtime = sub.add_parser("runtime", help="Validate one create/show snapshot")
    runtime.add_argument("--actual-json", required=True)
    runtime.add_argument("--assignee", required=True)
    runtime.add_argument("--workspace-kind", required=True)
    runtime.add_argument("--workspace-path", default=None)
    runtime.add_argument("--branch-name", default=None)
    runtime.add_argument("--max-retries", type=int, default=None)
    runtime.add_argument("--parent", action="append", default=[])
    runtime.set_defaults(func=_runtime_command)

    handoff = sub.add_parser("handoff", help="Validate native same-card implementer to reviewer handoff")
    handoff.add_argument("--actual-json", required=True)
    handoff.add_argument("--implementer-profile", required=True)
    handoff.add_argument("--reviewer-profile", required=True)
    handoff.set_defaults(func=_handoff_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
