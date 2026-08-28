"""Software Factory Kanban artifact workspace guard.

Blocks repository-analyst ``kanban_complete`` calls that could cause the
gateway to deliver local files outside the dispatcher-assigned workspace.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

ALLOWED_PROFILE = "repository-analyst"
TARGET_TOOL = "kanban_complete"

# Mirrors the absolute-path families Hermes' Kanban notifier recognizes when
# extracting local paths from completion text. Windows absolute paths are
# always outside a POSIX Kanban workspace on the target host and are blocked.
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w:/])(?:/(?:Users|home|private|tmp|var|etc|workspace)/[^\s,;]+|"
    r"[A-Za-z]:\\[^\s,;]+)"
)
_TRAILING_PUNCTUATION = ").,;:!?]}>'\"`"


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _has_symlink_component(path: Path) -> bool:
    candidate = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts:
        if part in {path.anchor, "", "."}:
            continue
        candidate = candidate / part
        try:
            if candidate.is_symlink():
                return True
        except OSError:
            return True
    return False


def _bound_workspace() -> Path:
    if not os.environ.get("HERMES_KANBAN_TASK", "").strip():
        raise ValueError("missing Kanban task binding")
    if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE:
        raise ValueError("repository-analyst profile required")
    raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if not raw:
        raise ValueError("missing Kanban workspace binding")
    workspace = Path(raw)
    if not workspace.is_absolute():
        raise ValueError("Kanban workspace must be absolute")
    if _has_symlink_component(workspace):
        raise ValueError("Kanban workspace symlink refused")
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Kanban workspace unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("Kanban workspace must be a directory")
    return resolved


def _relative_artifact(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("artifact path must be a non-empty string")
    rel = PurePosixPath(raw)
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("artifact relative path escapes workspace")
    if not [part for part in rel.parts if part not in {"", "."}]:
        raise ValueError("artifact file path required")
    return rel


def _resolve_artifact(workspace: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("artifact path must be a non-empty string")
    candidate = Path(raw)
    if not candidate.is_absolute():
        rel = _relative_artifact(raw)
        candidate = workspace / Path(*rel.parts)
    if _has_symlink_component(candidate):
        raise ValueError("artifact symlink component refused")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise ValueError("artifact must resolve inside assigned workspace") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("artifact must be a regular file")
    return resolved


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _paths_from_text(text: str) -> Iterable[str]:
    for match in _LOCAL_PATH_RE.finditer(text):
        value = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if value:
            yield value


def _validate_completion_text(workspace: Path, args: dict[str, Any]) -> None:
    # Hermes may extract absolute local paths from completion summary/result
    # text for later media delivery. Guard every string argument, excluding the
    # explicit artifacts list which is validated separately below.
    for key, value in args.items():
        if key == "artifacts":
            continue
        for text in _iter_strings(value):
            for raw_path in _paths_from_text(text):
                # Windows paths cannot resolve inside this POSIX workspace.
                if re.match(r"^[A-Za-z]:\\", raw_path):
                    raise ValueError("completion text references path outside assigned workspace")
                candidate = Path(raw_path)
                if _has_symlink_component(candidate):
                    raise ValueError("completion text path uses symlink component")
                try:
                    resolved = candidate.resolve(strict=False)
                    resolved.relative_to(workspace)
                except ValueError as exc:
                    raise ValueError("completion text references path outside assigned workspace") from exc


def _on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any):
    if tool_name != TARGET_TOOL:
        return None
    # Scope the policy to the future isolated repository-analyst worker. Other
    # profiles retain native Hermes Kanban completion semantics.
    if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE:
        return None
    try:
        workspace = _bound_workspace()
        if not isinstance(args, dict):
            raise ValueError("kanban_complete arguments must be an object")
        artifacts = args.get("artifacts")
        if artifacts is None:
            artifacts = []
        if not isinstance(artifacts, list):
            raise ValueError("artifacts must be a list")
        for raw in artifacts:
            if not isinstance(raw, str):
                raise ValueError("artifact path must be a string")
            _resolve_artifact(workspace, raw)
        _validate_completion_text(workspace, args)
    except (ValueError, OSError) as exc:
        return _block(f"Factory Kanban artifact guard refused completion: {exc}")
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
