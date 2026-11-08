"""Linux supervisor for one mutation-capable Claude implementation process."""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


def _handoff_module():
    path = Path(__file__).resolve().parent / "handoff.py"
    spec = importlib.util.spec_from_file_location("factory_supervisor_handoff", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("handoff module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_HANDOFF = _handoff_module()
mutation_lease = _HANDOFF.mutation_lease


def _become_subreaper() -> None:
    """Przejmij osieroconych potomków, także po setsid/double-fork."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise RuntimeError(f"PR_SET_CHILD_SUBREAPER failed: errno={ctypes.get_errno()}")


def _reap_adopted_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid <= 0:
            return


def _child_exited_unreaped(pid: int) -> bool:
    """Obserwuj exit bez reap, aby leader PID/PGID nie mógł zostać użyty ponownie."""
    info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    return info is not None


def _identity(pid: int) -> str | None:
    try:
        return _HANDOFF._process_identity(pid)[1]
    except Exception:
        return None


def _identity_alive(pid: int, start: str) -> bool:
    return _HANDOFF.process_identity_state(pid, start) == "alive"


def _ambient_board(kb) -> str | None:
    """Odczytaj trwały wybór planszy bez env override workera."""
    try:
        path = kb.current_board_path()
        if not path.exists():
            return kb.DEFAULT_BOARD
        value = path.read_text(encoding="utf-8").strip()
        return value if value else kb.DEFAULT_BOARD
    except Exception:
        return None


def _descendants(root_pid: int) -> dict[int, str]:
    """Zbierz tożsamości potomków przez bezpośredni odczyt Linux /proc."""
    parents: dict[int, int] = {}
    starts: dict[int, str] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            text = (entry / "stat").read_text(encoding="ascii")
            end = text.rfind(")")
            fields = text[end + 2 :].split()
            parents[pid] = int(fields[1])
            starts[pid] = fields[19]
        except (OSError, ValueError, IndexError):
            continue
    selected: dict[int, str] = {}
    frontier = {root_pid}
    while frontier:
        children = {pid for pid, ppid in parents.items() if ppid in frontier and pid not in selected}
        for pid in children:
            selected[pid] = starts[pid]
        frontier = children
    return selected


def _group_members(pgid: int) -> dict[int, str]:
    """Zbierz żywe tożsamości nadal należące do utworzonej process-group."""
    members: dict[int, str] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return members
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if os.getpgid(pid) == pgid:
                start = _identity(pid)
                if start is not None:
                    members[pid] = start
        except (ProcessLookupError, PermissionError):
            continue
    return members


def _terminate_owned_tree(leader_pid: int, leader_start: str, known: dict[int, str]) -> None:
    """Sygnalizuj wyłącznie zapamiętane PID/starttime należące do tej sesji."""
    known.update(_descendants(leader_pid))
    known.update(_group_members(leader_pid))
    owned_group = False
    if _identity_alive(leader_pid, leader_start):
        try:
            owned_group = os.getpgid(leader_pid) == leader_pid
        except (ProcessLookupError, PermissionError):
            pass
    if not owned_group:
        for pid, start in known.items():
            if _identity_alive(pid, start):
                try:
                    if os.getpgid(pid) == leader_pid:
                        owned_group = True
                        break
                except (ProcessLookupError, PermissionError):
                    pass
    if owned_group:
        try:
            os.killpg(leader_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not any(_identity_alive(pid, start) for pid, start in known.items()):
            return
        time.sleep(0.02)
    for pid, start in sorted(known.items(), reverse=True):
        if _identity_alive(pid, start):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def supervise(
    command: Sequence[str], *, board: str, task_id: str, run_id: int,
    workspace: str, worker_pid: int, worker_start: str, poll_seconds: float = 0.05,
) -> int:
    """Uruchom proces w osobnej sesji i pilnuj autoryzacji do pełnego wyjścia drzewa."""
    board = _HANDOFF.canonical_board(board)
    if not command or command[0] != "claude":
        raise RuntimeError("supervisor accepts only attested Claude argv")
    if os.environ.get("HERMES_KANBAN_BOARD", "").strip() != board:
        raise RuntimeError("supervisor board environment mismatch")
    if os.environ.get("HERMES_KANBAN_TASK", "").strip() != task_id:
        raise RuntimeError("supervisor task environment mismatch")
    if _HANDOFF._true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", "")) != run_id:
        raise RuntimeError("supervisor run environment mismatch")
    if os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip() != workspace:
        raise RuntimeError("supervisor workspace environment mismatch")
    if not _identity_alive(worker_pid, worker_start) or not _HANDOFF.active_coder_run_matches():
        raise RuntimeError("supervisor initial authorization invalid")
    kb = _HANDOFF._load_kanban_db()
    if _ambient_board(kb) != board:
        raise RuntimeError("supervisor ambient board changed")

    with _HANDOFF.mutation_lease(board, task_id, workspace, blocking=False):
        _become_subreaper()
        child = subprocess.Popen(list(command), cwd=workspace, start_new_session=True, close_fds=True)
        leader_start = _identity(child.pid)
        if leader_start is None or os.getpgid(child.pid) != child.pid:
            _terminate_owned_tree(child.pid, leader_start or "0", {})
            child.wait()
            raise RuntimeError("supervisor process-session identity unavailable")
        known = {child.pid: leader_start}
        authorized = True
        reaped = False
        try:
            while not _child_exited_unreaped(child.pid):
                known.update(_descendants(child.pid))
                known.update(_descendants(os.getpid()))
                authorized = bool(
                    _identity_alive(worker_pid, worker_start)
                    and _HANDOFF.active_coder_run_matches()
                    and _ambient_board(kb) == board
                )
                if not authorized:
                    _terminate_owned_tree(child.pid, leader_start, known)
                    break
                time.sleep(poll_seconds)
            known.update(_descendants(child.pid))
            known.update(_descendants(os.getpid()))
            known.update(_group_members(child.pid))
            if any(_identity_alive(pid, start) for pid, start in known.items() if pid != child.pid):
                _terminate_owned_tree(child.pid, leader_start, known)
                authorized = False
            rc = child.wait()
            reaped = True
            if not authorized:
                _reap_adopted_children()
                return 125
            _reap_adopted_children()
            return int(rc)
        finally:
            if not reaped:
                _terminate_owned_tree(child.pid, leader_start, known)
                child.wait()
            _reap_adopted_children()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        worker_pid, worker_start = _HANDOFF.active_coder_worker_identity()
        return supervise(
            command, board=args.board, task_id=args.task_id, run_id=args.run_id,
            workspace=args.workspace, worker_pid=worker_pid, worker_start=worker_start,
        )
    except Exception as exc:
        print(f"SUPERVISOR_REFUSED: {exc}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
