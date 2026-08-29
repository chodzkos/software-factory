"""Workspace-confinement and Kanban-authority guard for repository-analyst."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

ALLOWED_PROFILE = "repository-analyst"
ALLOWED_KANBAN_TOOLS = frozenset({
    "kanban_show",
    "kanban_comment",
    "kanban_block",
    "kanban_heartbeat",
    "kanban_complete",
})
MAX_ARG_DEPTH = 64
MAX_ARG_NODES = 4096

# Deliberately broader than Hermes gateway extraction: any token that looks
# like an absolute POSIX path, a home-relative path, or a Windows drive path
# is treated as path-bearing data and must remain inside the assigned
# workspace. The lookbehind avoids matching the path portion of http(s) URLs.
_LOCAL_PATH_RE = re.compile(
    r"(?<![/\w:.])(?:~/|/|[A-Za-z]:[/\\])[^\s,;]+"
)
_TRAILING_PUNCTUATION = "'\"`)]}>.!?:"
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _bound_workspace() -> Path:
    if not os.environ.get("HERMES_KANBAN_TASK", "").strip():
        raise ValueError("missing Kanban task binding")
    if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE:
        raise ValueError("repository-analyst profile required")
    raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if not raw:
        raise ValueError("missing Kanban workspace binding")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("Kanban workspace must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError("Kanban workspace symlink refused")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Kanban workspace must be a directory")
    return resolved


def _inside(workspace: Path, raw: str, *, must_exist: bool) -> bool:
    try:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return False
        if os.name != "nt" and _WINDOWS_DRIVE_RE.match(raw):
            return False
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            if current.is_symlink():
                return False
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(workspace)
        if must_exist and (not resolved.is_file() or resolved.is_symlink()):
            return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _iter_strings(value: Any) -> Iterable[str]:
    """Yield strings from nested JSON-like values with hard traversal bounds."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen = 0
    while stack:
        item, depth = stack.pop()
        seen += 1
        if seen > MAX_ARG_NODES:
            raise ValueError("kanban completion arguments exceed traversal limit")
        if depth > MAX_ARG_DEPTH:
            raise ValueError("kanban completion arguments exceed nesting limit")
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend((child, depth + 1) for child in item)


def _artifact_strings(args: dict) -> tuple[str, ...]:
    artifacts = args.get("artifacts")
    if artifacts is None:
        return ()
    if isinstance(artifacts, str):
        return (artifacts,)
    if not isinstance(artifacts, (list, tuple)):
        raise ValueError("artifacts must be a string or list of file paths")
    values: list[str] = []
    for value in artifacts:
        if not isinstance(value, str):
            raise ValueError("artifact path must be a string")
        values.append(value)
    return tuple(values)


def _paths_from_text(text: str) -> Iterable[str]:
    for match in _LOCAL_PATH_RE.finditer(text):
        candidate = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if candidate:
            yield candidate


def _outside_local_paths(workspace: Path, args: dict) -> list[str]:
    bad: list[str] = []
    for raw in _artifact_strings(args):
        if not _inside(workspace, raw, must_exist=True):
            bad.append(raw)
    for text in _iter_strings(args):
        for candidate in _paths_from_text(text):
            if not _inside(workspace, candidate, must_exist=False):
                bad.append(candidate)
    return bad


def on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    # Hermes swallows plugin-hook exceptions and otherwise continues the tool
    # call. For repository-analyst, the entire security decision therefore must
    # convert every ordinary exception into a block rather than fail open.
    try:
        if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE:
            return None
        if not isinstance(tool_name, str):
            raise ValueError("invalid tool name")

        # Dispatcher workers receive a broad native Kanban surface independently
        # of profile toolsets. repository-analyst only needs task-local lifecycle
        # operations; fail closed for every other current or future kanban_* tool.
        if tool_name.startswith("kanban_") and tool_name not in ALLOWED_KANBAN_TOOLS:
            return _block("repository-analyst may only use task-local Kanban lifecycle tools")

        if tool_name != "kanban_complete":
            return None

        workspace = _bound_workspace()
        if not isinstance(args, dict):
            raise ValueError("invalid kanban_complete arguments")
        bad = _outside_local_paths(workspace, args)
        if bad:
            return _block(
                "kanban completion refused: local artifact/path must stay inside the assigned workspace"
            )
        return None
    except Exception:
        return _block("repository-analyst tool call refused: security validation failed")
