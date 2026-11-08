"""Profilowy adapter natywnego handoffu bez zmiany globalnych modułów Hermesa."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from types import FunctionType
from typing import Any

from . import handoff as h
from . import review_intent as ri

_LOG = logging.getLogger(__name__)
# Pin dotyczy zachowanych źródeł rzeczywistego Hermesa z próby kontraktu.
_NATIVE_PINS = {
    "tools/kanban_tools.py": "6066ca6acdbdc6b48258a2a31df0e4942eca883f076c9f56a10fe78370566260",
    "hermes_cli/kanban_db.py": "1f825422dd7d6cb36bca65676d2225ac55abc6dbb832b9ba0f4aa25b00d5f022",
    "hermes_cli/sqlite_util.py": "55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438",
}


def check_compatibility(native: FunctionType, kb: Any) -> None:
    """Nie zgaduj kontraktu zainstalowanej wersji przy zmianie natywnych źródeł."""
    from hermes_cli import sqlite_util
    paths = {"tools/kanban_tools.py": native.__globals__.get("__file__"),
             "hermes_cli/kanban_db.py": getattr(kb, "__file__", None),
             "hermes_cli/sqlite_util.py": sqlite_util.__file__}
    for name, raw in paths.items():
        if not raw or hashlib.sha256(Path(raw).read_bytes()).hexdigest() != _NATIVE_PINS[name]:
            raise h.HandoffError("native handoff compatibility pin mismatch")
    if native.__name__ != "_handle_request_review" or native.__closure__ is not None:
        raise h.HandoffError("native handoff function contract mismatch")


class _HeldConnection:
    """Natywny finally zamyka uchwyt logiczny; adapter rozlicza prawdziwy COMMIT."""
    def __init__(self, conn: Any):
        self.conn = conn
        self.closed = False

    def close(self) -> None:
        if self.closed:
            raise h.HandoffError("native handoff duplicate connection close")
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        if self.closed:
            raise h.HandoffError("native handoff used closed connection")
        return getattr(self.conn, name)


class _NativeSavepoint:
    """Przekaż niezmienione SQL; tylko granice pojedynczej transakcji są zagnieżdżone."""
    def __init__(self, conn: Any):
        self.conn = conn
        self.active = False
        self.completed = False

    @property
    def in_transaction(self) -> bool:
        return self.active

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "BEGIN IMMEDIATE" and not parameters:
            if self.active or self.completed or not self.conn.in_transaction:
                raise h.HandoffError("native handoff unexpected transaction begin")
            result = self.conn.execute("SAVEPOINT factory_native_review")
            self.active = True
            return result
        if normalized == "COMMIT" and not parameters:
            if not self.active:
                raise h.HandoffError("native handoff commit without begin")
            result = self.conn.execute("RELEASE SAVEPOINT factory_native_review")
            self.active, self.completed = False, True
            return result
        if normalized == "ROLLBACK" and not parameters:
            if self.active:
                self.conn.execute("ROLLBACK TO SAVEPOINT factory_native_review")
                self.conn.execute("RELEASE SAVEPOINT factory_native_review")
                self.active = False
            return self.conn.execute("SELECT 1")
        # Oryginalny write_txn sprawdza po RELEASE tryb dziennika. Obsługujemy
        # wyłącznie WAL: jego invariant nie czyta niezatwierdzonego rozmiaru DB.
        if normalized == "PRAGMA JOURNAL_MODE" and not parameters and self.completed:
            return self.conn.execute(sql)
        if normalized.split(" ", 1)[0] not in {"SELECT", "UPDATE", "INSERT"} or not self.active:
            raise h.HandoffError("native handoff unexpected SQL contract")
        return self.conn.execute(sql, parameters)


class _RequestBoundary:
    """Podstaw wyłącznie request_review w prywatnym widoku modułu DB."""
    def __init__(self, kb: Any, held: _HeldConnection, intent: ri.ReviewIntent, guard: Any):
        self.kb, self.held, self.intent, self.guard = kb, held, intent, guard
        self.called = False
        self.succeeded = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.kb, name)

    def request_review(self, conn: Any, tid: str, **kwargs: Any) -> Any:
        if self.called or conn is not self.held or tid != self.intent.task_id:
            raise h.HandoffError("native handoff call identity mismatch")
        self.called = True
        if (set(kwargs) != {"summary", "metadata", "reviewer", "expected_run_id", "with_reason"}
            or type(kwargs["expected_run_id"]) is not int or kwargs["expected_run_id"] != self.intent.run_id
            or kwargs["with_reason"] is not True or kwargs["reviewer"] != "reviewer-gpt"
            or not h._run_metadata_matches(kwargs["metadata"], tid, self.intent.workspace)):
            raise h.HandoffError("native handoff effective arguments invalid")
        raw = self.held.conn
        if raw.in_transaction:
            raise h.HandoffError("native handoff unexpected outer transaction")
        if str(raw.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            raise h.HandoffError("native handoff requires WAL; journal mode is never changed here")
        raw.execute("BEGIN IMMEDIATE")
        ri.revalidate(self.intent, raw, self.kb, self.guard, active=True)
        view = _NativeSavepoint(raw)
        result = self.kb.request_review(view, tid, **kwargs)
        self.succeeded = type(result) is tuple and result == (True, None) and view.completed and not view.active
        if not self.succeeded:
            raise h.HandoffError("native handoff transition refused")
        return result


def _remove_own_seal(path: Path, inode: tuple[int, int] | None) -> None:
    """Sprzątanie jest pomocnicze: bez committed receipt plik nie daje uprawnień."""
    if inode is None:
        return
    try:
        st = path.lstat()
        if not path.is_symlink() and (st.st_dev, st.st_ino) == inode:
            path.unlink()
    except OSError:
        _LOG.warning("factory handoff: orphan file retained; committed receipt is required")


def execute(native: FunctionType, args: Any, guard: Any, **kwargs: Any) -> str:
    """Zwróć wynik oryginalnego adaptera dopiero po zakończeniu naszej transakcji."""
    from tools.registry import tool_error
    attempt_key = guard._attestation_key("coder-claude", os.environ.get("HERMES_KANBAN_TASK", ""),
                                         os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    intent = None
    held = boundary = None
    seal_path = None
    seal_inode = None
    committed = False
    try:
        from factory_worker_lifetime import review_operation, commit_review
        operation = review_operation(guard)
        intent = ri.take(args)
        kb = h._load_kanban_db()
        check_compatibility(native, kb)
        original_connect = native.__globals__["_connect"]
        def connect(board: str | None = None) -> tuple[Any, Any]:
            nonlocal held, boundary
            if held is not None or (board is not None and board != intent.board):
                raise h.HandoffError("native handoff repeated or wrong board connection")
            actual_kb, conn = original_connect(board=board)
            held = _HeldConnection(conn)
            if actual_kb is not kb or ri.database_identity(conn, kb, intent.board) != intent.database:
                raise h.HandoffError("native handoff connected database changed")
            boundary = _RequestBoundary(kb, held, intent, guard)
            return boundary, held
        # Nowy obiekt funkcji używa oryginalnego bytecode i tych samych zależności.
        # Jedyny zmieniony symbol jest prywatny dla TEGO wywołania. Zero monkeypatch.
        namespace = dict(native.__globals__)
        namespace["_connect"] = connect
        adapted = FunctionType(native.__code__, namespace, native.__name__, native.__defaults__)
        adapted.__kwdefaults__ = native.__kwdefaults__
        with h.mutation_lease(intent.board, intent.task_id, intent.workspace, blocking=False):
            seal_path = h.handoff_path(intent.board, intent.task_id, intent.run_id)
            if seal_path.exists() or seal_path.is_symlink():
                raise h.HandoffError("handoff path already exists; manual reconciliation required")
            result = adapted(args, **kwargs)
            if boundary is None or not boundary.succeeded or held is None or not held.closed:
                # Zachowaj odmowę delegacji/goal/ownership, lecz nie rzekomy sukces bez SQL.
                if isinstance(result, str):
                    decoded = h.strict_json_loads(result)
                    if isinstance(decoded, dict) and decoded.get("ok") is not True and "error" in decoded:
                        return result
                raise h.HandoffError("native handoff result has no transaction authority")
            ri.revalidate(intent, held.conn, kb, guard, active=False)
            if ri.arguments_bytes(args) != intent.arguments:
                raise h.HandoffError("native handoff arguments changed during call")
            seal = h._build_handoff_seal(result, content_state=guard._workspace_content_state,
                                         conn=held.conn, kb=kb, intent=intent,
                                         transaction_id=secrets.token_hex(32))
            # Plik powstaje PRZED COMMIT, lecz sam nie jest autoryzacją. Receipt
            # i natywne wiersze staną się widoczne razem dopiero po prawdziwym COMMIT.
            h._atomic_write(seal_path, seal)
            st = seal_path.lstat()
            seal_inode = (st.st_dev, st.st_ino)
            raw = h._read_regular_file(seal_path)
            if raw != h._canonical_json(seal) + b"\n":
                raise h.HandoffError("handoff publication bytes changed")
            with ri.commit_window(intent):
                ri.revalidate(intent, held.conn, kb, guard, active=False)
                if ri.arguments_bytes(args) != intent.arguments:
                    raise h.HandoffError("native handoff arguments changed before commit")
                h.append_handoff_receipt(held.conn, seal)
                commit_review(operation, held.conn, guard)
                committed = True
            key = guard._attestation_key("coder-claude", intent.task_id, str(intent.run_id))
            guard._SEALED_CODER_RUNS.add(key)
            os.environ["HERMES_KANBAN_STOP_NUDGE"] = "0"
            return result
    except Exception as exc:
        # Typ, nie tekst wyjątku z natywnego toola: bez sekretów, metadata i tracebacku.
        _LOG.warning("factory handoff refused (%s); no automatic retry", type(exc).__name__)
        return tool_error("factory handoff refused; inspect retained state, do not retry automatically")
    finally:
        if held is not None:
            try:
                if held.conn.in_transaction:
                    held.conn.rollback()
            except Exception:
                _LOG.warning("factory handoff: rollback failed; inspect state, do not retry")
            try:
                held.conn.close()
            except Exception:
                _LOG.warning("factory handoff: connection close failed; inspect retained state")
        if not committed and seal_path is not None:
            _remove_own_seal(seal_path, seal_inode)
        guard._PENDING_ATTESTATIONS.pop(attempt_key, None)
        guard._COMPLETED_ATTESTATIONS.pop(attempt_key, None)
        if intent is not None:
            key = guard._attestation_key("coder-claude", intent.task_id, str(intent.run_id))
            guard._PENDING_ATTESTATIONS.pop(key, None)
            guard._COMPLETED_ATTESTATIONS.pop(key, None)
        ri.invalidate()


def register(ctx: Any, guard: Any) -> None:
    """Nadpisz tylko jeden tool w rejestrze bieżącego chronionego profilu."""
    if os.environ.get("HERMES_PROFILE", "").strip() != "coder-claude":
        return
    from tools import kanban_tools
    native = kanban_tools._handle_request_review
    kb = h._load_kanban_db()
    check_compatibility(native, kb)
    from . import review_registration
    review_registration.register(ctx, guard, native, kanban_tools._check_kanban_mode, execute)
