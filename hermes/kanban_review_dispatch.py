from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from kanban_runtime_contract import (
    _HANDOFF,
    normalize_snapshot,
    resolved_implementation_worktree,
    validate_routed_review_handoff,
)
from model_routing_policy import DuplicateJsonKey, route_from_payload, strict_json_loads


_EXPECTED_HERMES_VERSION = "0.20.4"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SAVEPOINT_NAME = "software_factory_targeted_review_claim"
_TEST_MUTATION_HOOK = None


class _AtomicClaimDrift(RuntimeError):
    """Raised to roll back an atomically revalidated reviewer claim."""


class _SavepointConnection:
    """Adapt Hermes' nested BEGIN/COMMIT claim transaction to a SAVEPOINT.

    ``dispatch_review`` holds an outer ``BEGIN IMMEDIATE`` while it validates
    security-relevant task/provenance fields and calls Hermes' native
    ``claim_review_task``. Hermes' helper opens its own transaction, so this
    proxy hides the already-open outer transaction from that helper, reports
    only its own savepoint state through ``in_transaction``, and translates
    only its transaction-control statements. Every other connection operation
    is delegated unchanged.
    """

    def __init__(self, conn):
        self._conn = conn
        self._active = False

    @property
    def in_transaction(self) -> bool:
        """Expose only the nested savepoint state, not the outer transaction.

        Hermes 0.20.4 ``write_txn`` refuses implicit nesting by checking this
        attribute before issuing ``BEGIN IMMEDIATE``. Returning ``False`` until
        the proxy intercepts that BEGIN lets the native helper reach the
        savepoint adapter. Returning ``True`` while the savepoint is active also
        preserves Hermes' exception-time rollback guard.
        """
        return self._active

    def execute(self, sql: str, parameters=()):
        normalized = " ".join(str(sql).strip().upper().split())
        if normalized == "BEGIN IMMEDIATE":
            if self._active:
                raise RuntimeError("nested targeted-review savepoint already active")
            result = self._conn.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")
            self._active = True
            return result
        if normalized == "COMMIT":
            if not self._active:
                raise RuntimeError("targeted-review savepoint commit without begin")
            result = self._conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
            self._active = False
            return result
        if normalized == "ROLLBACK":
            if not self._active:
                return self._conn.execute("SELECT 1")
            try:
                result = self._conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT_NAME}")
                self._conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
                return result
            finally:
                self._active = False
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


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


def _json_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = strict_json_loads(value)
    except (DuplicateJsonKey, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _metadata_workspace(metadata: Mapping[str, Any]) -> str | None:
    values = [metadata[key] for key in ("workspace_path", "workspace") if key in metadata]
    if not values or not all(isinstance(value, str) and value == values[0] for value in values):
        return None
    return values[0]


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _string_sequence(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _live_snapshot(board: str, task_id: str) -> Mapping[str, Any]:
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise RuntimeError("task-id: invalid")
    try:
        board = _HANDOFF.canonical_board(board)
        env = dict(os.environ)
        env["HERMES_KANBAN_BOARD"] = board
        result = subprocess.run(
            ["hermes", "kanban", "show", task_id, "--json"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            env=env,
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


def _locked_provenance_drift(
    conn,
    *,
    task_id: str,
    expected_implementer: str,
    expected_reviewer: str,
    expected_workspace: str,
    expected_run_id: int,
) -> list[str]:
    """Revalidate durable handoff provenance under the outer writer lock."""
    drift: list[str] = []
    event = conn.execute(
        "SELECT run_id, payload FROM task_events "
        "WHERE task_id = ? AND kind = 'review_requested' "
        "ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    event_run_id = _row_value(event, "run_id")
    event_payload = _json_mapping(_row_value(event, "payload"))
    if type(event_run_id) is not int or event_run_id != expected_run_id:
        drift.append("review_requested_event_changed_after_validation")
    if (
        event_payload is None
        or event_payload.get("implementer") != expected_implementer
        or event_payload.get("reviewer") != expected_reviewer
    ):
        drift.append("review_requested_payload_changed_after_validation")

    run = conn.execute(
        "SELECT id, profile, outcome, metadata FROM task_runs "
        "WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    run_id = _row_value(run, "id")
    metadata = _json_mapping(_row_value(run, "metadata"))
    if type(run_id) is not int or run_id != expected_run_id:
        drift.append("implementer_run_changed_after_validation")
    if (
        _row_value(run, "profile") != expected_implementer
        or _row_value(run, "outcome") != "review_requested"
    ):
        drift.append("implementer_run_provenance_changed_after_validation")
    if metadata is None:
        drift.append("implementer_run_metadata_changed_after_validation")
    else:
        if metadata.get("task_id") != task_id:
            drift.append("implementer_run_task_changed_after_validation")
        metadata_workspace = _metadata_workspace(metadata)
        if metadata_workspace != expected_workspace:
            drift.append("implementer_run_workspace_changed_after_validation")
    return drift


def _task_drift(
    current,
    *,
    task_id: str,
    task_snapshot: Mapping[str, Any],
    expected_reviewer: str,
    expected_workspace: str,
) -> list[str]:
    drift: list[str] = []
    if current is None:
        return ["task_missing"]
    if current.id != task_id:
        drift.append("task_id_changed_after_validation")
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
    # Hermes 0.20.4 closes the implementer run when request_review moves the
    # card to review, so the canonical pre-review-claim state is None. Any
    # active run at this point means state changed after routed validation.
    if current.current_run_id is not None:
        drift.append("active_run_appeared_after_validation")
    if current.body != task_snapshot.get("body"):
        drift.append("task_body_changed_after_validation")
    if getattr(current, "branch_name", None) != task_snapshot.get("branch_name"):
        drift.append("task_branch_changed_after_validation")
    expected_skills = _string_sequence(task_snapshot.get("skills"))
    current_skills = _string_sequence(getattr(current, "skills", None))
    if expected_skills is None or current_skills is None or current_skills != expected_skills:
        drift.append("task_skills_changed_after_validation")
    return drift


def _claimed_task_drift(
    claimed,
    *,
    task_id: str,
    task_snapshot: Mapping[str, Any],
    expected_reviewer: str,
    expected_workspace: str,
    expected_implementer_run: int,
) -> list[str]:
    """Verify the native claim returned exactly the row that was authorized."""
    if claimed is None:
        return ["targeted_review_claim_failed"]
    drift: list[str] = []
    if claimed.id != task_id:
        drift.append("claimed_task_id_mismatch")
    if claimed.status != "running":
        drift.append("claimed_review_status_mismatch")
    if claimed.assignee != expected_reviewer:
        drift.append("claimed_review_assignee_mismatch")
    if claimed.workspace_kind != "worktree" or claimed.workspace_path != expected_workspace:
        drift.append("claimed_review_workspace_mismatch")
    if claimed.body != task_snapshot.get("body"):
        drift.append("claimed_task_body_mismatch")
    if getattr(claimed, "branch_name", None) != task_snapshot.get("branch_name"):
        drift.append("claimed_task_branch_mismatch")
    expected_skills = _string_sequence(task_snapshot.get("skills"))
    claimed_skills = _string_sequence(getattr(claimed, "skills", None))
    if expected_skills is None or claimed_skills is None or claimed_skills != expected_skills:
        drift.append("claimed_task_skills_mismatch")
    reviewer_run_id = claimed.current_run_id
    if type(reviewer_run_id) is not int or reviewer_run_id == expected_implementer_run:
        drift.append("reviewer_run_id_missing_or_reused")
    return drift


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
        "write_txn",
        "set_workspace_path",
        "set_branch_name",
        "review_dispatch_enabled",
        "_resolve_worktree_workspace",
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


def _seal_drift(
    *,
    board: str,
    task_id: str,
    run_id: int,
    implementer: str,
    reviewer: str,
    workspace: str,
    seal_id: str,
    content_state_sha256: str,
) -> list[str]:
    current_seal, errors = _HANDOFF.validate_handoff_seal(
        board=board,
        task_id=task_id,
        run_id=run_id,
        implementer=implementer,
        reviewer=reviewer,
        workspace=workspace,
        content_state=_HANDOFF.workspace_content_state,
        require_process_exit=True,
    )
    if current_seal is not None and (
        current_seal.get("seal_id") != seal_id
        or current_seal.get("content_state_sha256") != content_state_sha256
    ):
        errors.append("handoff_seal_identity_changed")
    return errors


def dispatch_review(task_id: str, *, board: str, snapshot: Mapping[str, Any] | None = None, kb=None) -> int:
    """Atomically validate and spawn exactly one routed same-card reviewer."""
    _assert_expected_hermes_version()
    kb = kb or _load_kanban_db()
    if kb.review_dispatch_enabled():
        raise RuntimeError(
            "kanban.review_dispatch must be false before targeted review dispatch"
        )

    board = _HANDOFF.canonical_board(board)
    if not callable(getattr(kb, "board_exists", None)) or not kb.board_exists(board):
        raise RuntimeError("explicit board does not exist")
    live = snapshot if snapshot is not None else _live_snapshot(board, task_id)
    errors = validate_routed_review_handoff(live, board=board)
    if errors:
        print("RUNTIME_CONTRACT_DRIFT: " + "; ".join(errors))
        return 2

    route, route_errors = route_from_payload(live)
    if route is None or route_errors or len(route.required_reviewers) != 1:
        details = route_errors or ["same_card_review_requires_exactly_one_reviewer"]
        print("MODEL_ROUTING_DRIFT: " + "; ".join(details))
        return 2

    expected_implementer = route.implementer
    expected_reviewer = route.required_reviewers[0]
    expected_workspace = resolved_implementation_worktree(live)
    expected_implementer_run = _latest_review_requested_run_id(live)
    task_snapshot = normalize_snapshot(live)
    if not expected_workspace or expected_implementer_run is None:
        print("RUNTIME_CONTRACT_DRIFT: handoff_provenance_missing")
        return 2
    seal, seal_errors = _HANDOFF.validate_handoff_seal(
        board=board,
        task_id=task_id,
        run_id=expected_implementer_run,
        implementer=expected_implementer,
        reviewer=expected_reviewer,
        workspace=expected_workspace,
        content_state=_HANDOFF.workspace_content_state,
        require_process_exit=True,
    )
    if seal is None or seal_errors:
        print("RUNTIME_CONTRACT_DRIFT: " + "; ".join(seal_errors or ["handoff_seal_missing"]))
        return 2

    claimed = None
    reviewer_run_id = None
    with kb.connect_closing(board=board) as conn:
        try:
            # BEGIN IMMEDIATE serializes every writer from the final durable
            # provenance recheck through Hermes' native exact-task claim.
            with kb.write_txn(conn):
                if callable(_TEST_MUTATION_HOOK):
                    _TEST_MUTATION_HOOK("before_locked_validation")
                current = kb.get_task(conn, task_id)
                drift = _task_drift(
                    current,
                    task_id=task_id,
                    task_snapshot=task_snapshot,
                    expected_reviewer=expected_reviewer,
                    expected_workspace=expected_workspace,
                )
                drift.extend(
                    _locked_provenance_drift(
                        conn,
                        task_id=task_id,
                        expected_implementer=expected_implementer,
                        expected_reviewer=expected_reviewer,
                        expected_workspace=expected_workspace,
                        expected_run_id=expected_implementer_run,
                    )
                )
                if drift:
                    raise _AtomicClaimDrift("; ".join(dict.fromkeys(drift)))
                locked_seal_errors = _seal_drift(
                    board=board,
                    task_id=task_id,
                    run_id=expected_implementer_run,
                    implementer=expected_implementer,
                    reviewer=expected_reviewer,
                    workspace=expected_workspace,
                    seal_id=seal["seal_id"],
                    content_state_sha256=seal["content_state_sha256"],
                )
                if locked_seal_errors:
                    raise _AtomicClaimDrift("; ".join(locked_seal_errors))
                if callable(_TEST_MUTATION_HOOK):
                    _TEST_MUTATION_HOOK("before_claim")
                preclaim_seal_errors = _seal_drift(
                    board=board,
                    task_id=task_id,
                    run_id=expected_implementer_run,
                    implementer=expected_implementer,
                    reviewer=expected_reviewer,
                    workspace=expected_workspace,
                    seal_id=seal["seal_id"],
                    content_state_sha256=seal["content_state_sha256"],
                )
                if preclaim_seal_errors:
                    raise _AtomicClaimDrift("; ".join(preclaim_seal_errors))

                claimed = kb.claim_review_task(_SavepointConnection(conn), task_id)
                if callable(_TEST_MUTATION_HOOK):
                    _TEST_MUTATION_HOOK("during_claim")
                post_claim_drift = _claimed_task_drift(
                    claimed,
                    task_id=task_id,
                    task_snapshot=task_snapshot,
                    expected_reviewer=expected_reviewer,
                    expected_workspace=expected_workspace,
                    expected_implementer_run=expected_implementer_run,
                )
                if post_claim_drift:
                    raise _AtomicClaimDrift("; ".join(post_claim_drift))
                reviewer_run_id = claimed.current_run_id
                postclaim_seal_errors = _seal_drift(
                    board=board,
                    task_id=task_id,
                    run_id=expected_implementer_run,
                    implementer=expected_implementer,
                    reviewer=expected_reviewer,
                    workspace=expected_workspace,
                    seal_id=seal["seal_id"],
                    content_state_sha256=seal["content_state_sha256"],
                )
                if postclaim_seal_errors:
                    raise _AtomicClaimDrift("; ".join(postclaim_seal_errors))
                reviewer_row = conn.execute(
                    "SELECT metadata FROM task_runs WHERE id = ? AND task_id = ?",
                    (reviewer_run_id, task_id),
                ).fetchone()
                reviewer_metadata = dict(_json_mapping(_row_value(reviewer_row, "metadata")) or {})
                reviewer_metadata.update(
                    {
                        "factory_handoff_schema": _HANDOFF.HANDOFF_SCHEMA,
                        "factory_handoff_board": board,
                        "factory_handoff_seal_id": seal["seal_id"],
                        "factory_handoff_git_head": seal["git_head"],
                        "factory_handoff_content_state_sha256": seal["content_state_sha256"],
                        "factory_handoff_implementer_run_id": expected_implementer_run,
                    }
                )
                updated = conn.execute(
                    "UPDATE task_runs SET metadata = ? WHERE id = ? AND task_id = ?",
                    (json.dumps(reviewer_metadata, sort_keys=True), reviewer_run_id, task_id),
                )
                if getattr(updated, "rowcount", None) != 1:
                    raise _AtomicClaimDrift("reviewer_run_metadata_update_failed")
                stored_row = conn.execute(
                    "SELECT metadata FROM task_runs WHERE id = ? AND task_id = ?",
                    (reviewer_run_id, task_id),
                ).fetchone()
                if _json_mapping(_row_value(stored_row, "metadata")) != reviewer_metadata:
                    raise _AtomicClaimDrift("reviewer_run_metadata_readback_mismatch")
        except _AtomicClaimDrift as exc:
            print(f"RUNTIME_CONTRACT_DRIFT: {exc}")
            return 2

        # The committed claim object contains the atomically authorized values;
        # later row mutations cannot substitute a different spawned profile.
        try:
            if callable(_TEST_MUTATION_HOOK):
                _TEST_MUTATION_HOOK("before_spawn")
            spawn_seal_errors = _seal_drift(
                board=board,
                task_id=task_id,
                run_id=expected_implementer_run,
                implementer=expected_implementer,
                reviewer=expected_reviewer,
                workspace=expected_workspace,
                seal_id=seal["seal_id"],
                content_state_sha256=seal["content_state_sha256"],
            )
            if spawn_seal_errors:
                raise RuntimeError("; ".join(spawn_seal_errors))
            workspace, resolved_branch_name = kb._resolve_worktree_workspace(
                claimed,
                board=board,
            )
            resolved_workspace = str(Path(workspace).resolve(strict=True))
            if resolved_workspace != expected_workspace:
                raise RuntimeError(
                    f"resolved review workspace drift: expected={expected_workspace!r} "
                    f"actual={resolved_workspace!r}"
                )
            kb.set_workspace_path(conn, claimed.id, resolved_workspace)
            kb.set_branch_name(
                conn,
                claimed.id,
                resolved_branch_name
                or (claimed.branch_name or "").strip()
                or f"wt/{claimed.id}",
            )
            claimed.skills = list(
                dict.fromkeys([*(claimed.skills or []), "sdlc-review"])
            )
            final_seal_errors = _seal_drift(
                board=board,
                task_id=task_id,
                run_id=expected_implementer_run,
                implementer=expected_implementer,
                reviewer=expected_reviewer,
                workspace=expected_workspace,
                seal_id=seal["seal_id"],
                content_state_sha256=seal["content_state_sha256"],
            )
            if final_seal_errors:
                raise RuntimeError("; ".join(final_seal_errors))
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
    parser.add_argument("--board", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _TASK_ID_RE.fullmatch(args.task_id):
        print("RUNTIME_CONTRACT_DRIFT: invalid_task_id")
        return 2
    try:
        return dispatch_review(args.task_id, board=args.board)
    except RuntimeError as exc:
        print(f"RUNTIME_CONTRACT_DRIFT: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
