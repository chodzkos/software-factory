#!/usr/bin/env python3
"""Linux supervisor dla jednego mutującego procesu Claude."""
from __future__ import annotations

import argparse
import ctypes
import errno
import importlib.util
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence


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

_PTRACE_TRACEME = 0
_PTRACE_CONT = 7
_PTRACE_SETOPTIONS = 0x4200
_PTRACE_GETEVENTMSG = 0x4201
_PTRACE_O_TRACEFORK = 0x00000002
_PTRACE_O_TRACEVFORK = 0x00000004
_PTRACE_O_TRACECLONE = 0x00000008
_PTRACE_O_TRACEEXEC = 0x00000010
_PTRACE_O_TRACEEXIT = 0x00000040
_PTRACE_O_EXITKILL = 0x00100000
_PTRACE_EVENT_FORK = 1
_PTRACE_EVENT_VFORK = 2
_PTRACE_EVENT_CLONE = 3
_PTRACE_EVENT_EXEC = 4
_PTRACE_EVENT_EXIT = 6
_PTRACE_OPTIONS = (
    _PTRACE_O_TRACEFORK
    | _PTRACE_O_TRACEVFORK
    | _PTRACE_O_TRACECLONE
    | _PTRACE_O_TRACEEXEC
    | _PTRACE_O_TRACEEXIT
    | _PTRACE_O_EXITKILL
)
_WAIT_WALL = 0x40000000
_LIBC = ctypes.CDLL(None, use_errno=True)
_CLONE_UNTRACED = 0x00800000
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_JMP_JSET_K = 0x45
_BPF_RET_K = 0x06
_SECCOMP_ARCH = {
    "x86_64": (0xC000003E, 56, 435, 0x40000000),
    "aarch64": (0xC00000B7, 220, 435, 0),
}


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


class ContainmentUnavailable(RuntimeError):
    """Wymagany backend kernelowy nie może bezpiecznie uruchomić Claude."""


def containment_backend_status() -> str:
    """Potwierdź wymagane API; właściwy ptrace probe następuje przed exec Claude."""
    if not sys.platform.startswith("linux"):
        raise ContainmentUnavailable("ptrace EXITKILL containment requires Linux")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ContainmentUnavailable("pidfd signal API unavailable")
    if not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT"):
        raise ContainmentUnavailable("waitid WNOWAIT unavailable")
    if platform.machine().lower() not in _SECCOMP_ARCH:
        raise ContainmentUnavailable("seccomp escape filter unsupported on this architecture")
    return "ptrace-exitkill-seccomp"


def _install_escape_filter() -> None:
    """Zablokuj CLONE_UNTRACED i clone3 przed włączeniem nieusuwalnego trace."""
    machine = platform.machine().lower()
    try:
        audit_arch, clone_nr, clone3_nr, alternate_abi_bit = _SECCOMP_ARCH[machine]
    except KeyError:
        os._exit(126)
    instructions = (_SockFilter * 12)(
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 4),
        _SockFilter(_BPF_JMP_JEQ_K, 1, 0, audit_arch),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 0),
        _SockFilter(_BPF_JMP_JSET_K, 6, 0, alternate_abi_bit),
        _SockFilter(_BPF_JMP_JEQ_K, 5, 0, clone3_nr),
        _SockFilter(_BPF_JMP_JEQ_K, 0, 3, clone_nr),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 16),
        _SockFilter(_BPF_JMP_JSET_K, 0, 1, _CLONE_UNTRACED),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.ENOSYS),
    )
    program = _SockFprog(len(instructions), instructions)
    ctypes.set_errno(0)
    if int(_LIBC.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) != 0:
        os._exit(126)
    ctypes.set_errno(0)
    if int(_LIBC.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0)) != 0:
        os._exit(126)


def _ptrace(request: int, pid: int, address: int = 0, data: int = 0) -> int:
    ctypes.set_errno(0)
    result = int(_LIBC.ptrace(request, pid, ctypes.c_void_p(address), ctypes.c_void_p(data)))
    if result == -1:
        value = ctypes.get_errno()
        raise OSError(value, os.strerror(value))
    return result


def _trace_me_before_exec() -> None:
    """Poproś o trace; kernel zatrzyma dziecko na SIGTRAP tuż po exec."""
    _install_escape_filter()
    ctypes.set_errno(0)
    if int(_LIBC.ptrace(_PTRACE_TRACEME, 0, None, None)) == -1:
        os._exit(126)


def _set_trace_options(pid: int) -> None:
    _ptrace(_PTRACE_SETOPTIONS, pid, 0, _PTRACE_OPTIONS)


def _continue_tracee(pid: int, delivered_signal: int = 0) -> None:
    _ptrace(_PTRACE_CONT, pid, 0, delivered_signal)


def _event_child_pid(pid: int) -> int:
    value = ctypes.c_ulong()
    ctypes.set_errno(0)
    result = int(_LIBC.ptrace(_PTRACE_GETEVENTMSG, pid, None, ctypes.byref(value)))
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(value.value)


def _become_subreaper() -> None:
    """Przejmij osieroconych potomków i umożliw pełne reap po leader exit."""
    if _LIBC.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise ContainmentUnavailable(f"PR_SET_CHILD_SUBREAPER failed: errno={ctypes.get_errno()}")


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


def _open_pidfd(pid: int) -> int:
    try:
        return int(os.pidfd_open(pid, 0))
    except OSError as exc:
        raise ContainmentUnavailable(f"pidfd_open failed for traced process {pid}: {exc}") from exc


def _pidfd_kill(fd: int) -> None:
    try:
        signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
    except ProcessLookupError:
        pass


def _close_pidfds(pidfds: dict[int, int]) -> None:
    for fd in pidfds.values():
        try:
            os.close(fd)
        except OSError:
            pass
    pidfds.clear()


def _register_tracee(pid: int, identities: dict[int, str], pidfds: dict[int, int]) -> None:
    start = _identity(pid)
    if start is None:
        raise ContainmentUnavailable(f"traced process identity unavailable: {pid}")
    identities[pid] = start
    try:
        pidfds[pid] = _open_pidfd(pid)
    except ContainmentUnavailable:
        # clone(2) może zgłosić TID w tym samym thread-group; leader pidfd
        # nadal wiąże sygnał do całego procesu. Fork/vfork PID musi mieć pidfd.
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
        tgid_line = next((line for line in status.splitlines() if line.startswith("Tgid:")), "")
        try:
            tgid = int(tgid_line.split()[1])
        except (IndexError, ValueError) as exc:
            raise ContainmentUnavailable(f"traced task pidfd identity unavailable: {pid}") from exc
        if tgid == pid or tgid not in pidfds:
            raise


def _kill_traced(identities: dict[int, str], pidfds: dict[int, int]) -> None:
    """Sygnalizuj przez pidfd, a następnie reap wszystkie ptrace tasks."""
    for fd in tuple(pidfds.values()):
        _pidfd_kill(fd)
    # Wątek bez własnego pidfd ginie wraz z leaderem. Jeśli leader już wyszedł,
    # użyj start-token guard jako awaryjnego sygnału tylko dla nadal śledzonego TID.
    for pid, start in tuple(identities.items()):
        if pid not in pidfds and _identity_alive(pid, start):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _wait_initial_stop(child: subprocess.Popen[bytes], identities: dict[int, str], pidfds: dict[int, int]) -> None:
    try:
        pid, status = os.waitpid(child.pid, os.WUNTRACED)
    except ChildProcessError as exc:
        raise ContainmentUnavailable("ptrace launch child disappeared before containment") from exc
    if pid != child.pid or not os.WIFSTOPPED(status) or os.WSTOPSIG(status) != signal.SIGTRAP:
        child.returncode = os.waitstatus_to_exitcode(status)
        raise ContainmentUnavailable("ptrace launch handshake failed before Claude exec")
    _register_tracee(child.pid, identities, pidfds)
    try:
        _set_trace_options(child.pid)
        _continue_tracee(child.pid)
    except OSError as exc:
        _kill_traced(identities, pidfds)
        try:
            os.waitpid(child.pid, _WAIT_WALL)
        except ChildProcessError:
            pass
        raise ContainmentUnavailable(f"ptrace EXITKILL options unavailable: {exc}") from exc


def _install_cancellation_handlers(cancelled: dict[str, int]) -> dict[int, Any]:
    if threading.current_thread() is not threading.main_thread():
        # Library-level reproducers exercise supervise() in a helper thread.
        # Canonical CLI execution is main-threaded and installs all handlers.
        return {}
    previous: dict[int, Any] = {}

    def request(signum: int, _frame) -> None:
        if cancelled["signal"] == 0:
            cancelled["signal"] = signum

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request)
    return previous


def _restore_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _trace_until_quiescent(
    child: subprocess.Popen[bytes], *, authorized, poll_seconds: float, cancelled: dict[str, int]
) -> tuple[int, bool]:
    identities: dict[int, str] = {}
    pidfds: dict[int, int] = {}
    root_rc: int | None = None
    cancellation_started = False
    _wait_initial_stop(child, identities, pidfds)
    try:
        while identities:
            progressed = False
            while True:
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG | os.WUNTRACED | _WAIT_WALL)
                except ChildProcessError:
                    identities.clear()
                    break
                if pid <= 0:
                    break
                progressed = True
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    if pid == child.pid:
                        root_rc = os.waitstatus_to_exitcode(status)
                    identities.pop(pid, None)
                    fd = pidfds.pop(pid, None)
                    if fd is not None:
                        os.close(fd)
                    continue
                if not os.WIFSTOPPED(status):
                    continue
                event = status >> 16
                if event in {_PTRACE_EVENT_FORK, _PTRACE_EVENT_VFORK, _PTRACE_EVENT_CLONE}:
                    new_pid = _event_child_pid(pid)
                    _register_tracee(new_pid, identities, pidfds)
                try:
                    _set_trace_options(pid)
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        raise ContainmentUnavailable(f"ptrace option inheritance failed: {exc}") from exc
                if cancellation_started:
                    fd = pidfds.get(pid)
                    if fd is not None:
                        _pidfd_kill(fd)
                    try:
                        _continue_tracee(pid, signal.SIGKILL)
                    except OSError as exc:
                        if exc.errno != errno.ESRCH:
                            raise
                    continue
                stop_signal = os.WSTOPSIG(status)
                delivered = 0 if event or stop_signal in {signal.SIGSTOP, signal.SIGTRAP} else stop_signal
                try:
                    _continue_tracee(pid, delivered)
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        raise

            if not cancellation_started:
                still_authorized = bool(authorized())
                root_gone_with_survivors = root_rc is not None and bool(identities)
                if cancelled["signal"] or not still_authorized or root_gone_with_survivors:
                    cancellation_started = True
                    _kill_traced(identities, pidfds)
            if identities and not progressed:
                time.sleep(poll_seconds)

        child.returncode = root_rc if root_rc is not None else 125
        if cancellation_started:
            return 125, True
        return int(child.returncode), False
    except BaseException:
        _kill_traced(identities, pidfds)
        while identities:
            try:
                pid, status = os.waitpid(-1, _WAIT_WALL)
            except ChildProcessError:
                break
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                identities.pop(pid, None)
                fd = pidfds.pop(pid, None)
                if fd is not None:
                    os.close(fd)
            elif os.WIFSTOPPED(status):
                fd = pidfds.get(pid)
                if fd is not None:
                    _pidfd_kill(fd)
                try:
                    _continue_tracee(pid, signal.SIGKILL)
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        raise
        raise
    finally:
        _close_pidfds(pidfds)


def supervise(
    command: Sequence[str], *, board: str, task_id: str, run_id: int,
    workspace: str, worker_pid: int, worker_start: str, poll_seconds: float = 0.05,
) -> int:
    """Uruchom Claude pod ptrace EXITKILL i trzymaj lease aż do pełnego reap."""
    containment_backend_status()
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

    cancelled = {"signal": 0}
    previous = _install_cancellation_handlers(cancelled)
    try:
        with _HANDOFF.mutation_lease(board, task_id, workspace, blocking=False):
            _become_subreaper()
            child = subprocess.Popen(
                list(command), cwd=workspace, close_fds=True, preexec_fn=_trace_me_before_exec
            )
            try:
                if os.getpgid(child.pid) != os.getpgrp():
                    try:
                        os.kill(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                    raise ContainmentUnavailable("Claude escaped the outer Hermes process group")
                rc, authorization_lost = _trace_until_quiescent(
                    child,
                    authorized=lambda: (
                        _identity_alive(worker_pid, worker_start)
                        and _HANDOFF.active_coder_run_matches()
                        and _ambient_board(kb) == board
                    ),
                    poll_seconds=poll_seconds,
                    cancelled=cancelled,
                )
            except BaseException:
                if child.returncode is None:
                    try:
                        os.kill(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        child.wait(timeout=2.0)
                    except (ChildProcessError, subprocess.TimeoutExpired):
                        pass
                raise
            if cancelled["signal"]:
                return 128 + cancelled["signal"]
            if authorization_lost:
                return 125
            return rc
    finally:
        _restore_handlers(previous)


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
