"""Jednorazowy zamiar handoffu; autorytet właściciela sprawdzany ponownie w DB."""
from __future__ import annotations

import contextlib
import os
import threading
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import handoff as h

_LIFECYCLE_LOCK = threading.RLock()
_EPOCH = 0
_PENDING: ReviewIntent | None = None


@dataclass(frozen=True)
class ReviewIntent:
    """Niezmienny kontekst; sam w sobie NIE upoważnia do publikacji pieczęci."""
    epoch: int
    arguments: bytes
    board: str
    task_id: str
    run_id: int
    workspace: str
    pid: int
    process_start: str
    database: tuple[str, int, int]
    run_started_at: int
    claim: str
    last_event: int
    owner_snapshot: str
    evidence_sha256: str
    state: tuple[str, str]


def invalidate() -> None:
    """Unieważnij także pobrany zamiar po innym wywołaniu lub odmowie entrypointu."""
    global _EPOCH, _PENDING
    with _LIFECYCLE_LOCK:
        _EPOCH += 1
        _PENDING = None


def arguments_bytes(args: Any) -> bytes:
    if not isinstance(args, dict) or set(args) - {"task_id", "board", "summary", "reviewer", "metadata"}:
        raise h.HandoffError("review arguments are not closed")
    if not isinstance(args.get("summary"), str) or not args["summary"].strip():
        raise h.HandoffError("review summary missing")
    if args.get("reviewer") != "reviewer-gpt" or not isinstance(args.get("metadata"), dict):
        raise h.HandoffError("review reviewer/metadata missing")
    return h._canonical_json(args)


def database_identity(conn: Any, kb: Any, board: str) -> tuple[str, int, int]:
    rows = list(conn.execute("PRAGMA database_list"))
    main = [r for r in rows if r[1] == "main"]
    if len(main) != 1 or any(r[1] not in {"main", "temp"} for r in rows):
        raise h.HandoffError("review database identity invalid")
    raw = main[0][2]
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or str(path.resolve(strict=True)) != raw:
        raise h.HandoffError("review database path invalid")
    expected = kb.kanban_db_path(board=board)
    if str(expected) != raw:
        raise h.HandoffError("review connected database differs from board database")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise h.HandoffError("review database must be a single-link regular file")
    return raw, st.st_dev, st.st_ino


def _current_context(kb: Any) -> tuple[str, str, int, str, tuple[int, str]]:
    if os.environ.get("HERMES_PROFILE", "").strip() != "coder-claude":
        raise h.HandoffError("review profile invalid")
    board = h._selected_board(kb)
    tid = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    rid = h._true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    ws = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if not h._TASK_ID_RE.fullmatch(tid) or rid is None or h._canonical_absolute(ws) != ws:
        raise h.HandoffError("review worker identity invalid")
    return board, tid, rid, ws, h._process_identity()


def _active(conn: Any, kb: Any, tid: str, rid: int, ws: str, pid: int) -> tuple[int, str, int, str]:
    task = kb.get_task(conn, tid)
    run = conn.execute("SELECT * FROM task_runs WHERE id=? AND task_id=?", (rid, tid)).fetchone()
    meta = h._strict_mapping(h._row_value(run, "metadata"))
    if (
        task is None or not h._workspace_matches_task(task, tid, ws)
        or task.status != "running" or task.assignee != "coder-claude"
        or type(task.current_run_id) is not int or task.current_run_id != rid
        or type(task.worker_pid) is not int or task.worker_pid != pid
        or type(h._row_value(run, "worker_pid")) is not int or h._row_value(run, "worker_pid") != pid
        or h._row_value(run, "profile") != "coder-claude"
        or h._row_value(run, "status") != "running"
        or h._row_value(run, "ended_at") is not None or h._row_value(run, "outcome") is not None
        or not h._run_metadata_matches(meta, tid, ws)
        or type(h._row_value(run, "started_at")) is not int
        or not isinstance(task.claim_lock, str) or not task.claim_lock
        or h._row_value(run, "claim_lock") != task.claim_lock
    ):
        raise h.HandoffError("review active worker ownership invalid")
    latest = conn.execute("SELECT COALESCE(MAX(id),0) FROM task_events WHERE task_id=?", (tid,)).fetchone()[0]
    # Body/goal/metadata nie mogą zmienić się po sprawdzeniu judge i właściciela.
    # Nie wiążemy czasu heartbeat/expiry, który legalnie odświeża dispatcher.
    names = ("id", "body", "title", "goal_mode", "workspace_kind", "workspace_path",
             "assignee", "status", "claim_lock", "worker_pid", "current_run_id")
    task_snapshot = {name: getattr(task, name, None) for name in names}
    run_snapshot = {name: run[name] for name in run.keys()
                    if name not in {"claim_expires", "last_heartbeat_at", "updated_at"}}
    snapshot = h._sha256_bytes(h._canonical_json([task_snapshot, run_snapshot]))
    return run["started_at"], task.claim_lock, latest, snapshot


def _check_args(args: Mapping[str, Any], board: str, tid: str, ws: str) -> None:
    if (("task_id" in args and args["task_id"] != tid)
        or ("board" in args and args["board"] != board)
        or not h._run_metadata_matches(args.get("metadata"), tid, ws)):
        raise h.HandoffError("review arguments task/workspace/board mismatch")


def prepare(args: Any, guard: Any) -> None:
    """Zapisz wyłącznie obserwację; transakcja zapisująca musi sprawdzić ją ponownie."""
    global _PENDING
    with _LIFECYCLE_LOCK:
        epoch = _EPOCH
    raw = arguments_bytes(args)
    kb = h._load_kanban_db()
    board, tid, rid, ws, identity = _current_context(kb)
    _check_args(args, board, tid, ws)
    if not guard._evidence_exists("coder-claude", tid):
        raise h.HandoffError("review completed execution attestation missing")
    with kb.connect_closing(board=board) as conn:
        conn.execute("BEGIN")
        try:
            db = database_identity(conn, kb, board)
            started, claim, last, snapshot = _active(conn, kb, tid, rid, ws, identity[0])
        finally:
            conn.rollback()
    evidence, _, data = h._execution_evidence(board, tid, rid)
    state = guard._workspace_content_state(ws)
    if state is None or state != (evidence["git_head_after"], evidence["workspace_content_state_after_sha256"]):
        raise h.HandoffError("review content invalid")
    with _LIFECYCLE_LOCK:
        if epoch != _EPOCH:
            raise h.HandoffError("review intent invalidated during preparation")
        _PENDING = ReviewIntent(epoch, raw, board, tid, rid, ws, *identity, db, started, claim, last,
                                snapshot, h._sha256_bytes(data), state)


def take(args: Any) -> ReviewIntent:
    """Pobierz zamiar dokładnie raz, również przy niezgodnych argumentach."""
    global _PENDING
    with _LIFECYCLE_LOCK:
        intent, _PENDING = _PENDING, None
        if intent is None or intent.epoch != _EPOCH or intent.arguments != arguments_bytes(args):
            raise h.HandoffError("review one-shot intent missing or changed")
        return intent


def revalidate(intent: ReviewIntent, conn: Any, kb: Any, guard: Any, *, active: bool) -> None:
    """Sprawdź tożsamość pod writer lockiem; active=False dotyczy już zamkniętego runu."""
    if not conn.in_transaction or intent.epoch != _EPOCH:
        raise h.HandoffError("review transaction authority invalidated")
    board, tid, rid, ws, identity = _current_context(kb)
    if ((board, tid, rid, ws, *identity) !=
        (intent.board, intent.task_id, intent.run_id, intent.workspace, intent.pid, intent.process_start)
        or database_identity(conn, kb, board) != intent.database):
        raise h.HandoffError("review transaction identity changed")
    if active and _active(conn, kb, tid, rid, ws, identity[0]) != (intent.run_started_at, intent.claim, intent.last_event, intent.owner_snapshot):
        raise h.HandoffError("review owner/run/event changed before native write")
    if not guard._evidence_exists("coder-claude", tid):
        raise h.HandoffError("review in-process evidence no longer valid")
    _, _, data = h._execution_evidence(board, tid, rid)
    if h._sha256_bytes(data) != intent.evidence_sha256 or guard._workspace_content_state(ws) != intent.state:
        raise h.HandoffError("review evidence/content changed")


@contextlib.contextmanager
def commit_window(intent: ReviewIntent) -> Iterator[None]:
    """Inny pre-hook nie może unieważnić autorytetu między walidacją a COMMIT."""
    with _LIFECYCLE_LOCK:
        if intent.epoch != _EPOCH:
            raise h.HandoffError("review intent invalidated before commit")
        yield
