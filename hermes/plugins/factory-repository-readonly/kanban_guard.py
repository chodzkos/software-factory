"""Workspace-confinement guard for repository-analyst Kanban completion."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

ALLOWED_PROFILE = "repository-analyst"
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w:/])(?:/(?:Users|home|private|tmp|var|etc|workspace|opt|srv|root|mnt|media)/[^\s,;]+|"
    r"[A-Za-z]:\\[^\s,;]+)"
)


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
    except (OSError, ValueError):
        return False


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _artifact_strings(args: dict) -> Iterable[str]:
    artifacts = args.get("artifacts")
    if artifacts is None:
        return ()
    return tuple(_iter_strings(artifacts))


def _outside_local_paths(workspace: Path, args: dict) -> list[str]:
    bad: list[str] = []
    # Artifact declarations are path-bearing by contract; require an existing
    # regular file inside the bound workspace.
    for raw in _artifact_strings(args):
        if not _inside(workspace, raw, must_exist=True):
            bad.append(raw)

    # Gateway completion delivery also extracts absolute local paths from
    # summary/result text. Refuse any such path unless it resolves inside the
    # current workspace. Scan every string argument so schema-name drift cannot
    # reopen the channel.
    for text in _iter_strings(args):
        for match in _LOCAL_PATH_RE.findall(text):
            candidate = match.rstrip("'\"`)]}>.!?:")
            if not _inside(workspace, candidate, must_exist=False):
                bad.append(candidate)
    return bad


def on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    if tool_name != "kanban_complete":
        return None
    # Scope the guard only to the future isolated repository-analyst worker.
    if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE:
        return None
    try:
        workspace = _bound_workspace()
    except Exception as exc:
        return {"action": "block", "message": f"kanban completion refused: {exc}"}
    if not isinstance(args, dict):
        return {"action": "block", "message": "kanban completion refused: invalid arguments"}
    bad = _outside_local_paths(workspace, args)
    if not bad:
        return None
    return {
        "action": "block",
        "message": "kanban completion refused: local artifact/path must stay inside the assigned workspace",
    }
