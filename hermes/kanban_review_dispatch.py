from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from kanban_runtime_contract import (
    normalize_snapshot,
    resolved_implementation_worktree,
    validate_routed_review_handoff,
)
from model_routing_policy import DuplicateJsonKey, route_from_payload, strict_json_loads


_EXPECTED_HERMES_VERSION = "0.20.4"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _json_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        parsed = strict_json_loads(value)
    except DuplicateJsonKey as exc:
        raise RuntimeError(f"{label}: duplicate JSON key: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label}: expected JSON object")
    return parsed


def _live_snapshot(task_id: str) -> Mapping[str, Any]:
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise RuntimeError("task-id: invalid")
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
        raise RuntimeError(f"live-task: unable to fetch {task_id}") from exc
    return _json_object(result.stdout, "live-task")


def _assert_expected_hermes_version() -> None:
    try:
        result = subprocess.run(
            ["hermes", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("unable to determine Hermes version") from exc
    text = (result.stdout or "").strip()
    if not re.search(rf"(?<![0-9])v?{re.escape(_EXPECTED_HERMES_VERSION)}(?![0-9])", text):
        raise RuntimeError(
            f"targeted review dispatch is pinned to Hermes {_EXPECTED_HERMES_VERSION}; got {text!r}"
        )


def _latest_review_requested_run_id(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("events")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    for event in reversed(raw):
        if not isinstance(event, Mapping) or event.get("kind") != "review_requested":
            continue
        run_id = event.get("run_id")
        return run_id if type(run_id) is int else None
    return None


def _load_kanban_db():
    try:
        from hermes_cli import kanban_db as kb
    except Exception as exc:  # pragma: no cover - depends on installed Hermes runtime
        raise RuntimeError("unable to import installed Hermes kanban_db") from exc

    required = (
        "connect_closing",
        "get_current_board",
        "get_task",
        "claim_review_task",
        "resolve_workspace",
        "set_workspace_path",
        "review_dispatch_enabled",
        "_default_spawn",
        "_set_worker_pid",
        "_fire_worker_spawned_hook",
        "_record_spawn_failure",
    )
    missing = [name for name in required if not callable(getattr(kb, name, None))]
    if missing:
        raise RuntimeError(
            "installed Hermes targeted-review primitives missing: " + ", ".join(missing)
        )
    return kb


def dispatch_review(task_id: str, *, snapshot: Mapping[str, Any] | None = None, kb=None) -> int:
    """Validate and spawn exactly one already-routed same-card review task."""
    _assert_expected_hermes_version()
    kb = kb or _load_kanban_db()
    if kb.review_dispatch_enabled():
        raise RuntimeError(
            "kanban.review_dispatch must be false before targeted review dispatch"
        )

    live = snapshot if snapshot is not None else _live_snapshot(task_id)
    errors = validate_routed_review_handoff(live)
    if errors:
        print("RUNTIME_CONTRACT_DRIFT: " + "; ".join(errors))
        return 2

    route, route_errors = route_from_payload(live)
    if route is None or route_errors or len(route.required_reviewers) != 1:
        details = route_errors or ["same_card_review_requires_exactly_one_reviewer"]
        print("MODEL_ROUTING_DRIFT: " + "; ".join(details))
        return 2

    expected_reviewer = route.required_reviewers[0]
    expected_workspace = resolved_implementation_worktree(live)
    expected_implementer_run = _latest_review_requested_run_id(live)
    task_snapshot = normalize_snapshot(live)
    if not expected_workspace or expected_implementer_run is None:
        print("RUNTIME_CONTRACT_DRIFT: handoff_provenance_missing")
        return 2

    board = kb.get_current_board()
    claimed = None
    with kb.connect_closing(board=board) as conn:
        current = kb.get_task(conn, task_id)
        drift: list[str] = []
        if current is None:
            drift.append("task_missing")
        else:
            if current.status != "review":
                drift.append(f"review_status: expected='review' actual={current.status!r}")
            if current.assignee != expected_reviewer:
                drift.append(
                    f"review_assignee: expected={expected_reviewer!r} actual={current.assignee!r}"
                )
            if current.workspace_kind != "worktree":
                drift.append(
                    f"workspace_kind: expected='worktree' actual={current.workspace_kind!r}"
                )
            if current.workspace_path != expected_workspace:
                drift.append("review_workspace_changed_after_validation")
            if current.current_run_id != expected_implementer_run:
                drift.append("implementer_run_changed_after_validation")
            if current.body != task_snapshot.get("body"):
                drift.append("task_body_changed_after_validation")
        if drift:
            print("RUNTIME_CONTRACT_DRIFT: " + "; ".join(drift))
            return 2

        claimed = kb.claim_review_task(conn, task_id)
        if claimed is None:
            print("RUNTIME_CONTRACT_DRIFT: targeted_review_claim_failed")
            return 2

        reviewer_run_id = claimed.current_run_id
        if type(reviewer_run_id) is not int:
            kb._record_spawn_failure(
                conn,
                claimed.id,
                "targeted-review-dispatch: reviewer run id missing after claim",
            )
            print("RUNTIME_CONTRACT_DRIFT: reviewer_run_id_missing_after_claim")
            return 2

        try:
            workspace = kb.resolve_workspace(claimed, board=board)
            resolved_workspace = str(Path(workspace).resolve(strict=True))
            if resolved_workspace != expected_workspace:
                raise RuntimeError(
                    f"resolved review workspace drift: expected={expected_workspace!r} "
                    f"actual={resolved_workspace!r}"
                )
            kb.set_workspace_path(conn, claimed.id, resolved_workspace)
            claimed.skills = list(
                dict.fromkeys([*(claimed.skills or []), "sdlc-review"])
            )
            pid = kb._default_spawn(claimed, resolved_workspace, board=board)
            if pid:
                kb._set_worker_pid(conn, claimed.id, int(pid))
            kb._fire_worker_spawned_hook(
                conn,
                claimed,
                resolved_workspace,
                pid,
                board=board,
            )
        except Exception as exc:
            kb._record_spawn_failure(
                conn,
                claimed.id,
                f"targeted-review-dispatch: {exc}",
            )
            print(f"RUNTIME_CONTRACT_DRIFT: targeted_review_spawn_failed: {exc}")
            return 2

    print(
        "REVIEW_DISPATCH_OK "
        f"task_id={task_id} reviewer={expected_reviewer} "
        f"run_id={reviewer_run_id} workspace={expected_workspace}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed targeted same-card review dispatcher for Software Factory"
    )
    parser.add_argument("--task-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _TASK_ID_RE.fullmatch(args.task_id):
        print("RUNTIME_CONTRACT_DRIFT: invalid_task_id")
        return 2
    try:
        return dispatch_review(args.task_id)
    except RuntimeError as exc:
        print(f"RUNTIME_CONTRACT_DRIFT: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
