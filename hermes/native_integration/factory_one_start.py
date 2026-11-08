"""Funkcja one-start w chronionym workerze prywatnego zadania, przed pierwszym narzędziem.

Wywoływane wyłącznie z bramek startu (factory_worker_start, factory_profile_start)
po weryfikacji pinów źródeł, tożsamości profilu i konfiguracji. Na tablicy
`sf-job-*` instalacja jest obowiązkowa, a każdy błąd zatrzymuje start; na innych
tablicach moduł nic nie robi. Wiązanie, hook połączeń i ocena `live` pochodzą bez
zmian z przypiętej kopii finalization_core (factory_finalization_core.py).
Worker poza cgroup związanej usługi albo po żądaniu zatrzymania nie startuje.
To nie jest sandbox przeciw kodowi tego samego UID.
"""
from __future__ import annotations

import os
import re

_JOB_BOARD = re.compile(r"sf-job-[0-9a-f]{16}")
_DB_LAYOUT = ("kanban.db", None, "boards", "kanban", ".hermes", "task-home")


class OneStartError(RuntimeError):
    pass


def _need(ok: object, code: str) -> None:
    if not ok:
        raise OneStartError(code)


def install_for_worker() -> dict | None:
    """Zainstaluj hook one-start dla bazy bieżącego zadania albo zwróć None poza `sf-job-*`."""
    board = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if not _JOB_BOARD.fullmatch(board):
        return None
    import factory_finalization_core as fj

    # fj_path: ścieżka absolutna, bez '..' i bez dowiązań w żadnym składniku.
    db = fj.fj_path(os.environ.get("HERMES_KANBAN_DB", ""))
    names = (db.name, *(p.name for p in db.parents[:5]))
    _need(len(db.parts) > 7 and names == ("kanban.db", board) + _DB_LAYOUT[2:], "one_start_db_layout")
    job = db.parents[5]
    spec = fj.fj_decode(fj.fj_read(job / "task_request.json", 32 * 1024 * 1024))
    _need(type(spec) is dict and spec.get("board") == board and spec.get("db") == str(db)
          and spec.get("job") == str(job), "one_start_spec_binding")
    binding = fj.fj_install_runtime(spec)
    _need(binding.get("db") == str(db) and binding.get("board") == board, "one_start_binding")
    from hermes_cli import kanban_db as kb

    conn = kb._sqlite_connect(db)
    try:
        scope = conn.execute("SELECT sf_one_start_scope()").fetchone()[0]
    finally:
        conn.close()
    _need(scope == "live", "one_start_worker_outside_bound_service")
    return dict(binding)
