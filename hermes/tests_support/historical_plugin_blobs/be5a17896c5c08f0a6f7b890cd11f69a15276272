"""Wersjonowana pieczęć przekazania implementacji do recenzji."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping

HANDOFF_SCHEMA = 2
EXECUTION_EVIDENCE_SCHEMA = 6
HANDOFF_DOMAIN = "software-factory-review-handoff-v2"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
_HANDOFF_FIELDS = frozenset(
    {
        "schema",
        "seal_id",
        "board",
        "task_id",
        "implementer_profile",
        "implementer_run_id",
        "reviewer_profile",
        "workspace",
        "git_head",
        "content_state_sha256",
        "execution_evidence_path",
        "execution_evidence_sha256",
        "attestation_id",
        "command_sha256",
        "terminal_args_sha256",
        "review_event_id",
        "review_event_created_at",
        "implementer_pid",
        "implementer_proc_start",
        "created_at",
    }
)
_TEST_APPROVAL_HOOK = None


class DuplicateJsonKey(ValueError):
    """Błąd ścisłego dekodowania JSON z powtórzonym kluczem."""


class HandoffError(RuntimeError):
    """Błąd zamkniętej walidacji pieczęci przekazania."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(key)
        result[key] = value
    return result


def strict_json_loads(value: str) -> Any:
    return json.loads(value, object_pairs_hook=_strict_object_pairs)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _true_run_id(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and _RUN_ID_RE.fullmatch(value):
        parsed = int(value)
        return parsed if str(parsed) == value else None
    return None


def canonical_board(value: Any) -> str:
    """Waliduj dokładny kanoniczny slug planszy bez aliasowania."""
    if not isinstance(value, str) or not _BOARD_RE.fullmatch(value):
        raise HandoffError("canonical board identity invalid")
    return value


def _canonical_absolute(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.startswith("/") or "\x00" in raw:
        return None
    if raw != (raw.rstrip("/") or "/"):
        return None
    pieces = raw.split("/")
    if any(part in {".", ".."} for part in pieces) or any(part == "" for part in pieces[1:]):
        return None
    try:
        path = Path(raw)
        if not path.is_dir():
            return None
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            if current.is_symlink():
                return None
        resolved = str(path.resolve(strict=True))
    except OSError:
        return None
    return resolved if resolved == raw else None


def _safe_directory(path: Path, *, create: bool = False) -> Path:
    """Zwróć absolutny katalog bez symlinków w żadnym komponencie."""
    if not path.is_absolute():
        raise HandoffError("evidence directory must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            current.mkdir(mode=0o700) if create else None
        except FileExistsError:
            pass
        except OSError as exc:
            raise HandoffError("unable to create evidence directory") from exc
        try:
            if current.is_symlink() or not current.is_dir():
                raise HandoffError("evidence directory component invalid")
        except OSError as exc:
            raise HandoffError("unable to inspect evidence directory") from exc
    return path


def _declared_repository(task: Any) -> str | None:
    body = getattr(task, "body", None)
    if not isinstance(body, str):
        return None
    values = [
        line.split(":", 1)[1].strip()[len("worktree:") :]
        for line in body.splitlines()
        if line.strip().startswith("WORKSPACE:")
        and line.split(":", 1)[1].strip().startswith("worktree:")
    ]
    return _canonical_absolute(values[0]) if len(values) == 1 else None


def _workspace_matches_task(task: Any, task_id: str, workspace: str) -> bool:
    repository = _declared_repository(task)
    return bool(
        repository
        and getattr(task, "workspace_kind", None) == "worktree"
        and getattr(task, "workspace_path", None) == workspace
        and workspace == f"{repository}/.worktrees/{task_id}"
        and _canonical_absolute(workspace) == workspace
    )


def _trusted_git_binary() -> str | None:
    located = shutil.which("git", path=os.defpath)
    if not located:
        return None
    try:
        resolved = Path(located).resolve(strict=True)
    except OSError:
        return None
    return str(resolved) if resolved.is_file() and os.access(resolved, os.X_OK) else None


def _run_git(workspace: str, args: list[str], *, text: bool = False) -> str | bytes:
    git = _trusted_git_binary()
    if git is None:
        raise OSError("trusted git unavailable")
    return subprocess.run(
        [git, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
         "-c", "diff.external=", "-c", "core.attributesfile=/dev/null",
         "-C", workspace, *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=text,
        timeout=20,
        env={"PATH": os.defpath, "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
             "XDG_CONFIG_HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
             "GIT_OPTIONAL_LOCKS": "0"},
        close_fds=True,
    ).stdout


def _frame(hasher: Any, tag: bytes, payload: bytes) -> None:
    if len(tag) > 0xFFFF or len(payload) > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("content-state frame too large")
    hasher.update(len(tag).to_bytes(2, "big"))
    hasher.update(tag)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (int(value.st_dev), int(value.st_ino), int(value.st_mode), int(value.st_size),
            int(value.st_mtime_ns), int(value.st_ctime_ns))


def _content_record(root: Path, raw: bytes) -> bytes | None:
    rel = Path(os.fsdecode(raw))
    if rel.is_absolute() or ".." in rel.parts:
        return None
    path = root / rel
    record = hashlib.sha256()
    _frame(record, b"path", raw)
    try:
        expected = path.lstat()
    except FileNotFoundError:
        _frame(record, b"type", b"deleted")
        return record.digest()
    except OSError:
        return None
    _frame(record, b"mode", int(expected.st_mode).to_bytes(8, "big"))
    if stat.S_ISLNK(expected.st_mode):
        try:
            target = os.fsencode(os.readlink(path))
            after = path.lstat()
        except OSError:
            return None
        if _stat_identity(expected) != _stat_identity(after) or not stat.S_ISLNK(after.st_mode):
            return None
        _frame(record, b"type", b"symlink")
        _frame(record, b"target", target)
        return record.digest()
    if not stat.S_ISREG(expected.st_mode):
        return None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _stat_identity(before) != _stat_identity(expected):
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        if _stat_identity(before) != _stat_identity(after) or total != after.st_size:
            return None
    finally:
        os.close(fd)
    _frame(record, b"type", b"file")
    _frame(record, b"content-sha256", total.to_bytes(8, "big") + digest.digest())
    return record.digest()


def workspace_content_state(workspace: str) -> tuple[str, str] | None:
    """Zmierz HEAD, indeks oraz wszystkie śledzone i nieśledzone bajty."""
    try:
        root = Path(workspace).resolve(strict=True)
        top = str(_run_git(str(root), ["rev-parse", "--show-toplevel"], text=True)).strip()
        if str(Path(top).resolve(strict=True)) != str(root):
            return None
        head = str(_run_git(str(root), ["rev-parse", "HEAD"], text=True)).strip()
        staged = bytes(_run_git(str(root), ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]))
        paths = bytes(_run_git(str(root), ["ls-files", "-c", "-o", "-z"]))
    except (OSError, subprocess.SubprocessError):
        return None
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head.lower()):
        return None
    digest = hashlib.sha256()
    try:
        _frame(digest, b"domain", b"software-factory-content-state-v2")
        _frame(digest, b"head", head.encode("ascii"))
        _frame(digest, b"staged", staged)
        for raw in sorted(item for item in paths.split(b"\0") if item):
            item = _content_record(root, raw)
            if item is None:
                return None
            _frame(digest, b"entry-sha256", item)
    except (OSError, ValueError):
        return None
    return head, digest.hexdigest()


def _load_kanban_db():
    try:
        from hermes_cli import kanban_db as kb
    except Exception as exc:
        raise HandoffError("installed Hermes kanban_db import failed") from exc
    required = ("get_current_board", "connect_closing", "get_task")
    if any(not callable(getattr(kb, name, None)) for name in required):
        raise HandoffError("installed Hermes kanban_db contract missing")
    return kb


def _selected_board(kb: Any) -> str:
    """Wymagaj dokładnej planszy przekazanej workerowi przez dispatcher."""
    expected = canonical_board(os.environ.get("HERMES_KANBAN_BOARD", "").strip())
    board_exists = getattr(kb, "board_exists", None)
    if not callable(board_exists) or not board_exists(expected):
        raise HandoffError("worker board does not exist")
    actual = kb.get_current_board()
    if actual != expected:
        raise HandoffError("worker board identity mismatch")
    return actual


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _strict_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = strict_json_loads(value)
    except (DuplicateJsonKey, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _run_metadata_matches(metadata: Mapping[str, Any] | None, task_id: str, workspace: str) -> bool:
    if metadata is None or metadata.get("task_id") != task_id:
        return False
    declared = [metadata[key] for key in ("workspace_path", "workspace") if key in metadata]
    return bool(declared) and all(value == workspace for value in declared)


def active_coder_run_matches() -> bool:
    """Potwierdź aktywną implementację bez zaufania do samych zmiennych env."""
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    run_id = _true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    board_name = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if (
        not _TASK_ID_RE.fullmatch(task_id)
        or run_id is None
        or not board_name
        or _canonical_absolute(workspace) != workspace
    ):
        return False
    try:
        kb = _load_kanban_db()
        board = _selected_board(kb)
        with kb.connect_closing(board=board) as conn:
            task = kb.get_task(conn, task_id)
            if task is None or not _workspace_matches_task(task, task_id, workspace):
                return False
            if (
                getattr(task, "status", None) != "running"
                or getattr(task, "assignee", None) != "coder-claude"
                or type(getattr(task, "current_run_id", None)) is not int
                or task.current_run_id != run_id
            ):
                return False
            run = conn.execute(
                "SELECT id, task_id, profile, status, ended_at, outcome, metadata "
                "FROM task_runs WHERE id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
            metadata = _strict_mapping(_row_value(run, "metadata"))
            return bool(
                type(_row_value(run, "id")) is int
                and _row_value(run, "id") == run_id
                and _row_value(run, "task_id") == task_id
                and _row_value(run, "profile") == "coder-claude"
                and _row_value(run, "status") == "running"
                and _row_value(run, "ended_at") is None
                and _row_value(run, "outcome") is None
                and _run_metadata_matches(metadata, task_id, workspace)
            )
    except Exception:
        return False


def active_coder_worker_identity() -> tuple[int, str]:
    """Zwróć tożsamość PID właściciela exact aktywnego coder runu."""
    if not active_coder_run_matches():
        raise HandoffError("active coder run unavailable")
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    run_id = _true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    kb = _load_kanban_db()
    board = _selected_board(kb)
    with kb.connect_closing(board=board) as conn:
        row = conn.execute(
            "SELECT worker_pid FROM task_runs WHERE id = ? AND task_id = ? AND status = 'running'",
            (run_id, task_id),
        ).fetchone()
    pid = _row_value(row, "worker_pid")
    if type(pid) is not int or pid <= 0:
        raise HandoffError("active coder worker PID missing")
    identity = _process_identity(pid)
    current = os.getpid()
    ancestors: set[int] = set()
    while current > 1 and current not in ancestors:
        ancestors.add(current)
        try:
            raw = Path(f"/proc/{current}/stat").read_text(encoding="ascii")
            fields = raw[raw.rfind(")") + 2 :].split()
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    if pid not in ancestors:
        raise HandoffError("active coder worker PID is not an ancestor")
    return identity


def _evidence_root() -> Path:
    return Path.home() / ".hermes" / "factory-evidence" / "claude-code"


def handoff_root() -> Path:
    return Path.home() / ".hermes" / "factory-evidence" / "review-handoff"


def lease_root() -> Path:
    return Path.home() / ".hermes" / "factory-evidence" / "mutation-leases"


def _board_scope(board: str) -> str:
    canonical = canonical_board(board)
    return hashlib.sha256(("software-factory-board-v1\0" + canonical).encode("utf-8")).hexdigest()


def handoff_path(board: str, task_id: str, run_id: int) -> Path:
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id) or _true_run_id(run_id) != run_id:
        raise HandoffError("handoff task/run path identity invalid")
    return handoff_root() / _board_scope(board) / f"{task_id}__{run_id}__coder-claude.json"


def execution_evidence_path(board: str, task_id: str, run_id: int, profile: str = "coder-claude") -> Path:
    canonical_board(board)
    if not _TASK_ID_RE.fullmatch(task_id) or _true_run_id(run_id) != run_id or profile != "coder-claude":
        raise HandoffError("execution evidence path identity invalid")
    return _evidence_root() / _board_scope(board) / f"{task_id}__{run_id}__{profile}.json"


def mutation_lease_path(board: str, task_id: str, workspace: str) -> Path:
    if not _TASK_ID_RE.fullmatch(task_id) or _canonical_absolute(workspace) != workspace:
        raise HandoffError("mutation lease identity invalid")
    workspace_hash = hashlib.sha256(workspace.encode("utf-8")).hexdigest()
    return lease_root() / _board_scope(board) / f"{task_id}__{workspace_hash}.lock"


@contextmanager
def mutation_lease(board: str, task_id: str, workspace: str, *, blocking: bool = False):
    """Utrzymuj wyłączną dzierżawę mutacji dla planszy i worktree."""
    path = mutation_lease_path(board, task_id, workspace)
    root = _safe_directory(path.parent, create=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HandoffError("mutation lease is not a regular file")
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, operation)
        except BlockingIOError as exc:
            raise HandoffError("mutation lease already held") from exc
        identity = f"{canonical_board(board)}\n{task_id}\n{workspace}\n".encode("utf-8")
        os.ftruncate(fd, 0)
        offset = 0
        while offset < len(identity):
            written = os.write(fd, identity[offset:])
            if written <= 0:
                raise HandoffError("mutation lease identity short write")
            offset += written
        os.fsync(fd)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _read_regular_file(path: Path, *, limit: int = 1024 * 1024) -> bytes:
    _safe_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise HandoffError(f"unable to open sealed file: {path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise HandoffError("sealed file type or size invalid")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise HandoffError("sealed file exceeds size limit")
        after = os.fstat(fd)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after) or total != after.st_size:
            raise HandoffError("sealed file changed during read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _load_closed_json(path: Path, fields: frozenset[str]) -> tuple[Mapping[str, Any], bytes]:
    raw = _read_regular_file(path)
    try:
        decoded = raw.decode("utf-8")
        value = strict_json_loads(decoded)
    except (UnicodeDecodeError, DuplicateJsonKey, json.JSONDecodeError) as exc:
        raise HandoffError("sealed JSON is malformed") from exc
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise HandoffError("sealed JSON schema is not closed")
    return value, raw


def _process_identity(pid: int | None = None) -> tuple[int, str]:
    actual_pid = os.getpid() if pid is None else pid
    if type(actual_pid) is not int or actual_pid <= 0:
        raise HandoffError("process PID invalid")
    path = Path(f"/proc/{actual_pid}/stat")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise HandoffError("process stat is not regular")
            raw = os.read(fd, 16385)
        finally:
            os.close(fd)
        if len(raw) > 16384:
            raise HandoffError("process stat too large")
        text = raw.decode("ascii")
        end = text.rfind(")")
        fields = text[end + 2 :].split()
        start = fields[19]
    except (OSError, UnicodeDecodeError, IndexError, HandoffError) as exc:
        raise HandoffError("process start identity unavailable") from exc
    if not start.isdigit():
        raise HandoffError("process start identity invalid")
    return actual_pid, start


def process_identity_state(pid: Any, start: Any) -> str:
    if type(pid) is not int or pid <= 0 or not isinstance(start, str) or not start.isdigit():
        return "unknown"
    try:
        _, current = _process_identity(pid)
    except HandoffError:
        return "exited" if not Path(f"/proc/{pid}").exists() else "unknown"
    return "alive" if current == start else "exited"


def _execution_evidence(board: str, task_id: str, run_id: int) -> tuple[Mapping[str, Any], Path, bytes]:
    path = execution_evidence_path(board, task_id, run_id)
    fields = frozenset(
        {
            "schema", "profile", "task_id", "run_id", "model_class", "workspace",
            "execution_cwd", "terminal_args_sha256", "claude_binary",
            "claude_binary_sha256", "session_id", "success", "command_sha256",
            "attestation_id", "git_head_before", "git_head_after",
            "workspace_content_state_before_sha256",
            "workspace_content_state_after_sha256", "recorded_at",
        }
    )
    value, raw = _load_closed_json(path, fields)
    if (
        type(value.get("schema")) is not int
        or value.get("schema") != EXECUTION_EVIDENCE_SCHEMA
        or value.get("profile") != "coder-claude"
        or value.get("model_class") != "sonnet"
        or value.get("task_id") != task_id
        or _true_run_id(value.get("run_id")) != run_id
        or value.get("success") is not True
    ):
        raise HandoffError("execution evidence identity invalid")
    for field in (
        "terminal_args_sha256", "claude_binary_sha256", "command_sha256",
        "attestation_id", "git_head_before", "git_head_after",
        "workspace_content_state_before_sha256", "workspace_content_state_after_sha256",
    ):
        expected = 40 if field.startswith("git_head") else 64
        raw_value = value.get(field)
        if not isinstance(raw_value, str) or len(raw_value) != expected or any(c not in "0123456789abcdef" for c in raw_value):
            raise HandoffError(f"execution evidence {field} invalid")
    return value, path, raw


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    root = _safe_directory(path.parent, create=True)
    tmp = root / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        raw = _canonical_json(payload) + b"\n"
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise HandoffError("sealed file short write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            existing, _ = _load_closed_json(path, _HANDOFF_FIELDS)
            if _canonical_json(existing) != _canonical_json(payload):
                raise HandoffError("different handoff seal already exists")
            tmp.unlink()
            return
        tmp.unlink()
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def create_handoff_seal(
    result: str,
    *,
    content_state: Callable[[str], tuple[str, str] | None],
) -> Mapping[str, Any]:
    """Utwórz pieczęć dopiero po potwierdzonym natywnym review_requested."""
    try:
        decoded = strict_json_loads(result)
    except (DuplicateJsonKey, json.JSONDecodeError, TypeError) as exc:
        raise HandoffError("request-review result malformed") from exc
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    run_id = _true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if (
        not isinstance(decoded, Mapping)
        or frozenset(decoded) != frozenset({"ok", "task_id", "run_id", "status"})
        or decoded.get("ok") is not True
        or decoded.get("task_id") != task_id
        or _true_run_id(decoded.get("run_id")) != run_id
        or decoded.get("status") != "review"
        or run_id is None
    ):
        raise HandoffError("request-review result identity invalid")
    kb = _load_kanban_db()
    board = _selected_board(kb)
    evidence, evidence_path, evidence_raw = _execution_evidence(board, task_id, run_id)
    state = content_state(workspace)
    if (
        state is None
        or evidence.get("workspace") != workspace
        or evidence.get("execution_cwd") != workspace
        or evidence.get("git_head_after") != state[0]
        or evidence.get("workspace_content_state_after_sha256") != state[1]
    ):
        raise HandoffError("execution evidence content binding invalid")

    with kb.connect_closing(board=board) as conn:
        task = kb.get_task(conn, task_id)
        if (
            task is None
            or getattr(task, "status", None) != "review"
            or getattr(task, "assignee", None) != "reviewer-gpt"
            or getattr(task, "current_run_id", object()) is not None
            or not _workspace_matches_task(task, task_id, workspace)
        ):
            raise HandoffError("durable review task transition invalid")
        run = conn.execute(
            "SELECT id, task_id, profile, status, worker_pid, ended_at, outcome, metadata "
            "FROM task_runs WHERE id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        run_metadata = _strict_mapping(_row_value(run, "metadata"))
        if (
            _row_value(run, "id") != run_id
            or _row_value(run, "task_id") != task_id
            or _row_value(run, "profile") != "coder-claude"
            or _row_value(run, "status") != "review"
            or type(_row_value(run, "ended_at")) is not int
            or _row_value(run, "outcome") != "review_requested"
            or not _run_metadata_matches(run_metadata, task_id, workspace)
        ):
            raise HandoffError("durable implementer run transition invalid")
        pid, proc_start = _process_identity()
        if _row_value(run, "worker_pid") != pid:
            raise HandoffError("implementer process does not own durable run PID")
        event = conn.execute(
            "SELECT id, run_id, payload, created_at FROM task_events "
            "WHERE task_id = ? AND kind = 'review_requested' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        event_payload = _strict_mapping(_row_value(event, "payload"))
        if (
            type(_row_value(event, "id")) is not int
            or _row_value(event, "run_id") != run_id
            or type(_row_value(event, "created_at")) is not int
            or event_payload is None
            or event_payload.get("implementer") != "coder-claude"
            or event_payload.get("reviewer") != "reviewer-gpt"
        ):
            raise HandoffError("native review-requested event invalid")

    core = {
        "schema": HANDOFF_SCHEMA,
        "board": board,
        "task_id": task_id,
        "implementer_profile": "coder-claude",
        "implementer_run_id": run_id,
        "reviewer_profile": "reviewer-gpt",
        "workspace": workspace,
        "git_head": state[0],
        "content_state_sha256": state[1],
        "execution_evidence_path": str(evidence_path),
        "execution_evidence_sha256": _sha256_bytes(evidence_raw),
        "attestation_id": evidence["attestation_id"],
        "command_sha256": evidence["command_sha256"],
        "terminal_args_sha256": evidence["terminal_args_sha256"],
        "review_event_id": _row_value(event, "id"),
        "review_event_created_at": _row_value(event, "created_at"),
        "implementer_pid": pid,
        "implementer_proc_start": proc_start,
        "created_at": int(time.time()),
    }
    seal_id = _sha256_bytes(HANDOFF_DOMAIN.encode("ascii") + b"\0" + _canonical_json(core))
    payload = {**core, "seal_id": seal_id}
    _atomic_write(handoff_path(board, task_id, run_id), payload)
    return payload


def load_handoff_seal(board: str, task_id: str, run_id: int) -> Mapping[str, Any]:
    board = canonical_board(board)
    value, _ = _load_closed_json(handoff_path(board, task_id, run_id), _HANDOFF_FIELDS)
    if type(value.get("schema")) is not int or value.get("schema") != HANDOFF_SCHEMA:
        raise HandoffError("handoff schema invalid")
    core = {key: value[key] for key in value if key != "seal_id"}
    expected = _sha256_bytes(HANDOFF_DOMAIN.encode("ascii") + b"\0" + _canonical_json(core))
    if value.get("seal_id") != expected:
        raise HandoffError("handoff seal digest invalid")
    if value.get("board") != board or value.get("task_id") != task_id or _true_run_id(value.get("implementer_run_id")) != run_id:
        raise HandoffError("handoff seal task/run invalid")
    for field in ("seal_id", "content_state_sha256", "execution_evidence_sha256", "attestation_id", "command_sha256", "terminal_args_sha256"):
        if not isinstance(value.get(field), str) or not _SHA256_RE.fullmatch(value[field]):
            raise HandoffError(f"handoff {field} invalid")
    if (
        not isinstance(value.get("git_head"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", value["git_head"])
        or value.get("implementer_profile") != "coder-claude"
        or value.get("reviewer_profile") != "reviewer-gpt"
        or _canonical_absolute(value.get("workspace")) != value.get("workspace")
        or not isinstance(value.get("execution_evidence_path"), str)
        or not Path(value["execution_evidence_path"]).is_absolute()
        or type(value.get("review_event_id")) is not int
        or value["review_event_id"] <= 0
        or type(value.get("review_event_created_at")) is not int
        or value["review_event_created_at"] <= 0
        or type(value.get("implementer_pid")) is not int
        or value["implementer_pid"] <= 0
        or not isinstance(value.get("implementer_proc_start"), str)
        or not value["implementer_proc_start"].isdigit()
        or type(value.get("created_at")) is not int
        or value["created_at"] <= 0
    ):
        raise HandoffError("handoff field identity invalid")
    return value


def validate_handoff_seal(
    *,
    board: str,
    task_id: str,
    run_id: int,
    implementer: str,
    reviewer: str,
    workspace: str,
    content_state: Callable[[str], tuple[str, str] | None],
    require_process_exit: bool = True,
) -> tuple[Mapping[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        board = canonical_board(board)
        seal = load_handoff_seal(board, task_id, run_id)
        evidence, evidence_path, evidence_raw = _execution_evidence(board, task_id, run_id)
    except HandoffError as exc:
        return None, [f"handoff_seal:{exc}"]
    state = content_state(workspace)
    checks = {
        "board": board,
        "implementer_profile": implementer,
        "reviewer_profile": reviewer,
        "workspace": workspace,
    }
    for field, expected in checks.items():
        if seal.get(field) != expected:
            errors.append(f"handoff_{field}_mismatch")
    if state is None or seal.get("git_head") != state[0] or seal.get("content_state_sha256") != state[1]:
        errors.append("handoff_content_state_mismatch")
    if (
        seal.get("execution_evidence_path") != str(evidence_path)
        or seal.get("execution_evidence_sha256") != _sha256_bytes(evidence_raw)
        or seal.get("attestation_id") != evidence.get("attestation_id")
        or seal.get("command_sha256") != evidence.get("command_sha256")
        or seal.get("terminal_args_sha256") != evidence.get("terminal_args_sha256")
        or seal.get("workspace") != evidence.get("workspace")
        or seal.get("git_head") != evidence.get("git_head_after")
        or seal.get("content_state_sha256")
        != evidence.get("workspace_content_state_after_sha256")
    ):
        errors.append("handoff_execution_evidence_mismatch")
    process_state = process_identity_state(seal.get("implementer_pid"), seal.get("implementer_proc_start"))
    if require_process_exit and process_state != "exited":
        errors.append(f"implementer_process_{process_state}")
    return seal, errors


def reviewer_completion_authorized(
    *,
    content_state: Callable[[str], tuple[str, str] | None],
) -> bool:
    """Powiąż zatwierdzenie z uruchomieniem recenzenta i zapieczętowanymi bajtami."""
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    reviewer_run = _true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if not _TASK_ID_RE.fullmatch(task_id) or reviewer_run is None:
        return False
    try:
        kb = _load_kanban_db()
        board = _selected_board(kb)
        with kb.connect_closing(board=board) as conn:
            task = kb.get_task(conn, task_id)
            if (
                task is None
                or getattr(task, "status", None) != "running"
                or getattr(task, "assignee", None) != "reviewer-gpt"
                or type(getattr(task, "current_run_id", None)) is not int
                or task.current_run_id != reviewer_run
                or not _workspace_matches_task(task, task_id, workspace)
            ):
                return False
            run = conn.execute(
                "SELECT id, task_id, profile, status, ended_at, outcome, metadata FROM task_runs "
                "WHERE id = ? AND task_id = ?",
                (reviewer_run, task_id),
            ).fetchone()
            metadata = _strict_mapping(_row_value(run, "metadata"))
            if (
                type(_row_value(run, "id")) is not int
                or _row_value(run, "id") != reviewer_run
                or _row_value(run, "task_id") != task_id
                or _row_value(run, "profile") != "reviewer-gpt"
                or _row_value(run, "status") != "running"
                or _row_value(run, "ended_at") is not None
                or _row_value(run, "outcome") is not None
                or metadata is None
            ):
                return False
            implementer_run = _true_run_id(metadata.get("factory_handoff_implementer_run_id"))
            if implementer_run is None:
                return False
            seal, errors = validate_handoff_seal(
                board=board,
                task_id=task_id,
                run_id=implementer_run,
                implementer="coder-claude",
                reviewer="reviewer-gpt",
                workspace=workspace,
                content_state=content_state,
                require_process_exit=True,
            )
            return bool(
                seal is not None
                and not errors
                and metadata.get("factory_handoff_schema") == HANDOFF_SCHEMA
                and metadata.get("factory_handoff_board") == board
                and metadata.get("factory_handoff_seal_id") == seal.get("seal_id")
                and metadata.get("factory_handoff_git_head") == seal.get("git_head")
                and metadata.get("factory_handoff_content_state_sha256") == seal.get("content_state_sha256")
            )
    except Exception:
        return False


class _SavepointConnection:
    """Dostosuj natywną transakcję Hermes do już utrzymywanego writer locka."""

    def __init__(self, conn):
        self._conn = conn
        self._active = False

    @property
    def in_transaction(self) -> bool:
        return self._active

    def execute(self, sql: str, parameters=()):
        normalized = " ".join(str(sql).strip().upper().split())
        name = "software_factory_guarded_approval"
        if normalized == "BEGIN IMMEDIATE":
            if self._active:
                raise HandoffError("approval savepoint already active")
            result = self._conn.execute(f"SAVEPOINT {name}")
            self._active = True
            return result
        if normalized == "COMMIT":
            if not self._active:
                raise HandoffError("approval savepoint commit without begin")
            result = self._conn.execute(f"RELEASE SAVEPOINT {name}")
            self._active = False
            return result
        if normalized == "ROLLBACK":
            if not self._active:
                return self._conn.execute("SELECT 1")
            try:
                result = self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self._conn.execute(f"RELEASE SAVEPOINT {name}")
                return result
            finally:
                self._active = False
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _reviewer_approval_context(conn: Any, kb: Any, board: str, content_state) -> tuple[str, int, str, int, Mapping[str, Any]]:
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    reviewer_run = _true_run_id(os.environ.get("HERMES_KANBAN_RUN_ID", ""))
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    task = kb.get_task(conn, task_id) if _TASK_ID_RE.fullmatch(task_id) else None
    if (
        reviewer_run is None
        or task is None
        or getattr(task, "status", None) != "running"
        or getattr(task, "assignee", None) != "reviewer-gpt"
        or getattr(task, "current_run_id", None) != reviewer_run
        or not _workspace_matches_task(task, task_id, workspace)
    ):
        raise HandoffError("active reviewer task identity invalid")
    run = conn.execute(
        "SELECT id, task_id, profile, status, ended_at, outcome, metadata FROM task_runs WHERE id = ? AND task_id = ?",
        (reviewer_run, task_id),
    ).fetchone()
    metadata = _strict_mapping(_row_value(run, "metadata"))
    if (
        _row_value(run, "id") != reviewer_run
        or _row_value(run, "profile") != "reviewer-gpt"
        or _row_value(run, "status") != "running"
        or _row_value(run, "ended_at") is not None
        or _row_value(run, "outcome") is not None
        or metadata is None
        or metadata.get("factory_handoff_board") != board
    ):
        raise HandoffError("active reviewer run identity invalid")
    implementer_run = _true_run_id(metadata.get("factory_handoff_implementer_run_id"))
    if implementer_run is None:
        raise HandoffError("implementer run binding missing")
    seal, errors = validate_handoff_seal(
        board=board,
        task_id=task_id,
        run_id=implementer_run,
        implementer="coder-claude",
        reviewer="reviewer-gpt",
        workspace=workspace,
        content_state=content_state,
        require_process_exit=True,
    )
    if seal is None or errors:
        raise HandoffError("; ".join(errors or ["handoff seal missing"]))
    if (
        metadata.get("factory_handoff_schema") != HANDOFF_SCHEMA
        or metadata.get("factory_handoff_seal_id") != seal.get("seal_id")
        or metadata.get("factory_handoff_content_state_sha256") != seal.get("content_state_sha256")
        or metadata.get("factory_handoff_git_head") != seal.get("git_head")
    ):
        raise HandoffError("reviewer metadata binding invalid")
    return task_id, reviewer_run, workspace, implementer_run, seal


def guarded_reviewer_complete(args: Mapping[str, Any], *, content_state=workspace_content_state) -> Mapping[str, Any]:
    """Atomowo zatwierdź dokładne bajty pod blokadą DB i dzierżawą worktree."""
    if not isinstance(args, Mapping) or frozenset(args) - {"summary"}:
        raise HandoffError("approval arguments are not closed")
    summary = args.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
        raise HandoffError("approval summary invalid")
    kb = _load_kanban_db()
    board = _selected_board(kb)
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    with mutation_lease(board, task_id, workspace, blocking=False):
        with kb.connect_closing(board=board) as conn:
            with kb.write_txn(conn):
                task_id, reviewer_run, workspace, implementer_run, seal = _reviewer_approval_context(conn, kb, board, content_state)
                if callable(_TEST_APPROVAL_HOOK):
                    _TEST_APPROVAL_HOOK("after_initial_validation")
                _, _, _, _, seal = _reviewer_approval_context(conn, kb, board, content_state)
                approval = {
                    "factory_approval_schema": 1,
                    "factory_approval_board": board,
                    "factory_approval_seal_id": seal["seal_id"],
                    "factory_approval_git_head": seal["git_head"],
                    "factory_approval_content_state_sha256": seal["content_state_sha256"],
                    "factory_approval_implementer_run_id": implementer_run,
                }
                if callable(_TEST_APPROVAL_HOOK):
                    _TEST_APPROVAL_HOOK("before_native_complete")
                ok = kb.complete_task(
                    _SavepointConnection(conn), task_id, summary=summary.strip(), metadata=approval,
                    expected_run_id=reviewer_run, fire_lifecycle_hook=False,
                )
                if not ok:
                    raise HandoffError("native reviewer completion refused")
                if callable(_TEST_APPROVAL_HOOK):
                    _TEST_APPROVAL_HOOK("after_native_complete")
                final_seal, errors = validate_handoff_seal(
                    board=board, task_id=task_id, run_id=implementer_run,
                    implementer="coder-claude", reviewer="reviewer-gpt", workspace=workspace,
                    content_state=content_state, require_process_exit=True,
                )
                row = conn.execute(
                    "SELECT status, outcome, metadata FROM task_runs WHERE id = ? AND task_id = ?",
                    (reviewer_run, task_id),
                ).fetchone()
                stored = _strict_mapping(_row_value(row, "metadata"))
                if (
                    final_seal is None or errors or _row_value(row, "status") != "done"
                    or _row_value(row, "outcome") != "completed" or stored is None
                    or any(stored.get(key) != value for key, value in approval.items())
                ):
                    raise HandoffError("approved bytes changed during durable completion")
    return {"ok": True, "task_id": task_id, "run_id": reviewer_run, "status": "done", **approval}


def verify_downstream_approval(board: str, task_id: str, *, content_state=workspace_content_state) -> Mapping[str, Any]:
    """Fail-closed release/ready/merge revalidation of zatwierdzonych bajtów."""
    board = canonical_board(board)
    if not _TASK_ID_RE.fullmatch(task_id):
        raise HandoffError("approval task identity invalid")
    kb = _load_kanban_db()
    if not callable(getattr(kb, "board_exists", None)) or not kb.board_exists(board):
        raise HandoffError("approval board does not exist")
    with kb.connect_closing(board=board) as conn:
        preliminary = kb.get_task(conn, task_id)
        workspace = getattr(preliminary, "workspace_path", "") if preliminary is not None else ""
    if _canonical_absolute(workspace) != workspace:
        raise HandoffError("approved workspace identity invalid")
    with mutation_lease(board, task_id, workspace, blocking=False):
        with kb.connect_closing(board=board) as conn:
            with kb.write_txn(conn):
                task = kb.get_task(conn, task_id)
                if task is None or getattr(task, "status", None) != "done" or getattr(task, "current_run_id", object()) is not None:
                    raise HandoffError("task is not durably done")
                if not _workspace_matches_task(task, task_id, workspace):
                    raise HandoffError("approved workspace identity invalid")
                row = conn.execute(
                    "SELECT status, outcome, metadata FROM task_runs WHERE task_id = ? AND profile = 'reviewer-gpt' ORDER BY id DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                metadata = _strict_mapping(_row_value(row, "metadata"))
                if _row_value(row, "status") != "done" or _row_value(row, "outcome") != "completed" or metadata is None:
                    raise HandoffError("durable approval run missing")
                implementer_run = _true_run_id(metadata.get("factory_approval_implementer_run_id"))
                if implementer_run is None or metadata.get("factory_approval_board") != board:
                    raise HandoffError("durable approval identity invalid")
                seal, errors = validate_handoff_seal(
                    board=board, task_id=task_id, run_id=implementer_run,
                    implementer="coder-claude", reviewer="reviewer-gpt", workspace=workspace,
                    content_state=content_state, require_process_exit=True,
                )
                if seal is None or errors:
                    raise HandoffError("; ".join(errors or ["approval seal missing"]))
                checks = {
                    "factory_approval_schema": 1,
                    "factory_approval_board": board,
                    "factory_approval_seal_id": seal["seal_id"],
                    "factory_approval_git_head": seal["git_head"],
                    "factory_approval_content_state_sha256": seal["content_state_sha256"],
                    "factory_approval_implementer_run_id": implementer_run,
                }
                if any(metadata.get(key) != value for key, value in checks.items()):
                    raise HandoffError("durable approval metadata mismatch")
    return {"ok": True, "board": board, "task_id": task_id, **checks}
