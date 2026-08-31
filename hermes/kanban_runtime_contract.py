from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .model_routing_policy import DuplicateJsonKey, format_route, route_from_payload, strict_json_loads
except ImportError:
    from model_routing_policy import DuplicateJsonKey, format_route, route_from_payload, strict_json_loads

_WORKSPACE_RE = re.compile(r"^WORKSPACE:\s*(.*?)\s*$")


@dataclass(frozen=True)
class RuntimeExpectation:
    assignee: str
    workspace_kind: str
    workspace_path: str | None
    branch_name: str | None
    max_retries: int | None
    parents: tuple[str, ...] = ()


def normalize_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    if "task" in payload:
        nested = payload.get("task")
        if not isinstance(nested, Mapping):
            return {}
        task = dict(nested)
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
    if any(not isinstance(value, Mapping) for value in raw):
        return ()
    return tuple(raw)


def _latest_review_requested_event(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for event in reversed(_records(payload, "events")):
        if event.get("kind") == "review_requested":
            return event
    return None


def _latest_run(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    runs = _records(payload, "runs")
    return runs[-1] if runs else None


def validate_runtime(payload: Mapping[str, Any], expectation: RuntimeExpectation) -> list[str]:
    actual = normalize_snapshot(payload)
    if not actual:
        return ["runtime_task_missing_or_invalid"]
    errors: list[str] = []
    checks = {
        "assignee": expectation.assignee,
        "workspace_kind": expectation.workspace_kind,
        "workspace_path": expectation.workspace_path,
        "branch_name": expectation.branch_name,
        "max_retries": expectation.max_retries,
    }
    for field, expected in checks.items():
        if expected is not None and actual.get(field) != expected:
            errors.append(f"{field}: expected={expected!r} actual={actual.get(field)!r}")
    if _parents(actual) != expectation.parents:
        errors.append(f"parents: expected={expectation.parents!r} actual={_parents(actual)!r}")
    return errors


def _canonical_absolute(raw: str) -> str | None:
    """Return an existing canonical absolute non-symlink path, otherwise fail closed."""
    if not isinstance(raw, str) or not raw.startswith("/") or "\x00" in raw:
        return None
    trimmed = raw.rstrip("/") or "/"
    # Check lexical input before pathlib normalizes away '.'/'..' or duplicate separators.
    pieces = raw.split("/")
    if any(part in {".", ".."} for part in pieces) or any(part == "" for part in pieces[1:-1]):
        return None
    path = Path(trimmed)
    if not path.exists():
        return None
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            if current.is_symlink():
                return None
        resolved = str(path.resolve(strict=True))
    except OSError:
        return None
    return resolved if resolved == trimmed else None


def _declared_repository_root(task: Mapping[str, Any]) -> str | None:
    body = task.get("body")
    if not isinstance(body, str):
        return None
    values: list[str] = []
    for line in body.splitlines():
        match = _WORKSPACE_RE.match(line.strip())
        if match:
            values.append(match.group(1))
    if len(values) != 1 or not values[0].startswith("worktree:"):
        return None
    return _canonical_absolute(values[0][len("worktree:"):])


def resolved_implementation_worktree(payload: Mapping[str, Any]) -> str | None:
    task = normalize_snapshot(payload)
    if task.get("workspace_kind") != "worktree":
        return None
    task_id = task.get("id")
    path = task.get("workspace_path")
    if not isinstance(task_id, str) or not task_id or not isinstance(path, str):
        return None
    repo_root = _declared_repository_root(task)
    canonical_path = _canonical_absolute(path)
    if not repo_root or not canonical_path:
        return None
    expected = f"{repo_root}/.worktrees/{task_id}"
    return canonical_path if canonical_path == expected else None


def validate_review_handoff(payload: Mapping[str, Any], *, implementer_profile: str, reviewer_profile: str) -> list[str]:
    task = normalize_snapshot(payload)
    if not task:
        return ["implementation_task_missing_or_invalid"]
    errors: list[str] = []
    if implementer_profile == reviewer_profile:
        errors.append("implementer_and_reviewer_must_differ")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        errors.append("implementation_id_missing")
        return errors
    resolved_path = resolved_implementation_worktree(payload)
    if not resolved_path:
        errors.append("implementation_resolved_worktree_missing")
        return errors
    if task.get("assignee") != reviewer_profile:
        errors.append(f"review_assignee: expected={reviewer_profile!r} actual={task.get('assignee')!r}")
    if task.get("status") != "review":
        errors.append(f"review_status: expected='review' actual={task.get('status')!r}")

    latest_event = _latest_review_requested_event(payload)
    event_run_id: int | None = None
    if latest_event is None:
        errors.append("review_requested_event_missing_or_mismatched")
    else:
        event_payload = latest_event.get("payload")
        if (
            not isinstance(event_payload, Mapping)
            or event_payload.get("implementer") != implementer_profile
            or event_payload.get("reviewer") != reviewer_profile
        ):
            errors.append("review_requested_event_missing_or_mismatched")
        raw_event_run_id = latest_event.get("run_id")
        if type(raw_event_run_id) is not int:
            errors.append("review_requested_event_run_id_required")
        else:
            event_run_id = raw_event_run_id

    latest_run = _latest_run(payload)
    if latest_run is None or latest_run.get("profile") != implementer_profile or latest_run.get("outcome") != "review_requested":
        errors.append("current_implementer_review_run_missing_or_mismatched")
    else:
        run_id = latest_run.get("id")
        if type(run_id) is not int:
            errors.append("current_implementer_run_id_required")
        elif event_run_id is None or run_id != event_run_id:
            errors.append("review_requested_event_run_mismatch")
        metadata = latest_run.get("metadata")
        if not isinstance(metadata, Mapping):
            errors.append("implementer_review_run_metadata_required")
        else:
            metadata_path = metadata.get("workspace_path") or metadata.get("workspace")
            if metadata_path != resolved_path:
                errors.append("implementer_review_run_workspace_mismatched")
            if metadata.get("task_id") != task_id:
                errors.append("implementer_review_run_task_mismatched")
    return errors


def validate_routed_review_handoff(payload: Mapping[str, Any]) -> list[str]:
    route, route_errors = route_from_payload(payload)
    if route_errors or route is None:
        return [f"model_routing:{error}" for error in (route_errors or ["unparseable"])]
    if len(route.required_reviewers) != 1:
        return ["same_card_review_requires_exactly_one_reviewer"]
    return validate_review_handoff(
        payload,
        implementer_profile=route.implementer,
        reviewer_profile=route.required_reviewers[0],
    )


def validate_task_graph(actual: Mapping[str, Any], expectation: RuntimeExpectation) -> list[str]:
    return validate_runtime(actual, expectation)


def format_drift(errors: Sequence[str]) -> str:
    return "RUNTIME_CONTRACT_OK" if not errors else "RUNTIME_CONTRACT_DRIFT: " + "; ".join(errors)


def _json_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        parsed = strict_json_loads(value)
    except DuplicateJsonKey as exc:
        raise SystemExit(f"{label}: duplicate JSON key: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label}: expected JSON object")
    return parsed


def _live_snapshot(task_id: str) -> Mapping[str, Any]:
    """Fetch authoritative live Kanban JSON directly; callers cannot supply snapshot bytes."""
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
        raise SystemExit("task-id: invalid")
    try:
        result = subprocess.run(
            ["hermes", "kanban", "show", task_id, "--json"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"live-task: unable to fetch {task_id}") from exc
    return _json_object(result.stdout, "live-task")


def _runtime_command(args: argparse.Namespace) -> int:
    actual = _live_snapshot(args.task_id)
    expectation = RuntimeExpectation(
        args.assignee,
        args.workspace_kind,
        args.workspace_path,
        args.branch_name,
        args.max_retries,
        tuple(args.parent),
    )
    errors = validate_runtime(actual, expectation)
    print(format_drift(errors))
    return 0 if not errors else 2


def _routed_handoff_command(args: argparse.Namespace) -> int:
    actual = _live_snapshot(args.task_id)
    errors = validate_routed_review_handoff(actual)
    print(format_drift(errors))
    return 0 if not errors else 2


def _routing_live_command(args: argparse.Namespace) -> int:
    actual = _live_snapshot(args.task_id)
    route, errors = route_from_payload(actual)
    if route is None and not errors:
        errors = ["unparseable"]
    print(format_route(errors))
    return 0 if not errors else 2


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Software Factory Kanban runtime contract validator")
    sub = parser.add_subparsers(dest="command", required=True)

    runtime = sub.add_parser("runtime")
    runtime.add_argument("--task-id", required=True)
    runtime.add_argument("--assignee", required=True)
    runtime.add_argument("--workspace-kind", required=True)
    runtime.add_argument("--workspace-path", default=None)
    runtime.add_argument("--branch-name", default=None)
    runtime.add_argument("--max-retries", type=int, default=None)
    runtime.add_argument("--parent", action="append", default=[])
    runtime.set_defaults(func=_runtime_command)

    routed = sub.add_parser("routed-handoff")
    routed.add_argument("--task-id", required=True)
    routed.set_defaults(func=_routed_handoff_command)

    routing_live = sub.add_parser("routing-live")
    routing_live.add_argument("--task-id", required=True)
    routing_live.set_defaults(func=_routing_live_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
