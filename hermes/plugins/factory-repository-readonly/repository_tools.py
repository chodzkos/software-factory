"""Workspace-bound read-only repository tools for Software Factory.

The model never supplies a workspace. All handlers bind to the current Hermes
Kanban worker environment and expose no shell, subprocess, write, or execution
surface.
"""
from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath
from typing import Any

from tools.registry import tool_error, tool_result

from . import repo_map

ALLOWED_PROFILE = "repository-analyst"
TOOLSET = "factory-repository-readonly"
SAFE_HIDDEN_DIRS = {".github"}
SKIP_DIRS = set(repo_map.SKIP_DIRS) | {".git", ".hg", ".svn", ".tox", ".mypy_cache", ".pytest_cache"}
TEXT_EXTENSIONS = set(repo_map.ALLOWED_EXTENSIONS) | {
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".jsonc",
    ".ini", ".cfg", ".conf", ".xml", ".html", ".css", ".scss", ".sql",
    ".gradle", ".properties", ".lock",
}
TEXT_NAMES = {
    "dockerfile", "makefile", "cmakelists.txt", "requirements.txt", "pipfile",
    "gemfile", "rakefile", "justfile", "procfile", "license", "license.md",
    "readme", "readme.md", "pyproject.toml", "package.json", "tsconfig.json",
}
MAX_READ_BYTES = 262_144
MAX_READ_LINES = 400
MAX_SEARCH_FILES = 1_000
MAX_SEARCH_DIRS = 2_000
MAX_SEARCH_DIR_ENTRIES = 4_096
MAX_SEARCH_FILE_BYTES = 262_144
MAX_SEARCH_TOTAL_BYTES = 8_388_608
MAX_SEARCH_RESULTS = 100
MAX_MATCHES_PER_FILE = 20
MAX_QUERY_CHARS = 256
MAP_LIMITS = [
    "--max-files", "500", "--max-dirs", "2000", "--max-dir-entries", "4096",
    "--max-file-bytes", "1048576", "--max-total-bytes", "8388608", "--max-symbols", "12",
]

MAP_SCHEMA = {"name":"factory_repo_map","description":"Map source files inside the current Kanban workspace. Workspace is fixed by Hermes and cannot be supplied by the caller.","parameters":{"type":"object","properties":{"target":{"type":"string","description":"Workspace-relative directory, default '.'."}},"additionalProperties":False}}
READ_SCHEMA = {"name":"factory_repo_read","description":"Read a bounded UTF-8 text file inside the current Kanban workspace. Hidden/secret files, symlinks, binaries, and paths outside the workspace are refused.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"Workspace-relative file path."},"start_line":{"type":"integer","minimum":1},"max_lines":{"type":"integer","minimum":1,"maximum":400}},"required":["path"],"additionalProperties":False}}
SEARCH_SCHEMA = {"name":"factory_repo_search","description":"Literal text search over bounded non-secret text files inside the current Kanban workspace. No regex or executable search command is exposed.","parameters":{"type":"object","properties":{"query":{"type":"string"},"target":{"type":"string"},"case_sensitive":{"type":"boolean"},"max_results":{"type":"integer","minimum":1,"maximum":100}},"required":["query"],"additionalProperties":False}}


def check_available() -> bool:
    try:
        _bound_workspace(); return True
    except Exception:
        return False


def _has_symlink_component(path: Path) -> bool:
    candidate = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts:
        if part in {path.anchor, "", "."}: continue
        candidate = candidate / part
        try:
            if candidate.is_symlink(): return True
        except OSError:
            return True
    return False


def _bound_workspace() -> Path:
    if not os.environ.get("HERMES_KANBAN_TASK", "").strip(): raise ValueError("missing Kanban task binding")
    if os.environ.get("HERMES_PROFILE", "").strip() != ALLOWED_PROFILE: raise ValueError("repository-analyst profile required")
    raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip()
    if not raw: raise ValueError("missing Kanban workspace binding")
    path = Path(raw)
    if not path.is_absolute(): raise ValueError("Kanban workspace must be absolute")
    if _has_symlink_component(path): raise ValueError("Kanban workspace symlink refused")
    try: resolved = path.resolve(strict=True)
    except OSError as exc: raise ValueError("Kanban workspace unavailable") from exc
    if not resolved.is_dir(): raise ValueError("Kanban workspace must be a directory")
    return resolved


def _secret_like(path: Path) -> bool:
    lower = path.name.lower()
    if lower.startswith(".") or lower in repo_map.SECRET_NAMES: return True
    if path.suffix.lower() in repo_map.SECRET_SUFFIXES: return True
    return any(token in lower for token in ("credential", "secret", "private_key", "access_token"))


def _validate_relative(raw: str, *, allow_dot: bool) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw.startswith("-"): raise ValueError("invalid workspace-relative path")
    p = PurePosixPath(raw)
    if p.is_absolute() or any(part == ".." for part in p.parts): raise ValueError("path must stay inside workspace")
    meaningful = [part for part in p.parts if part not in {"", "."}]
    if not meaningful and not allow_dot: raise ValueError("file path required")
    parents = meaningful if allow_dot else meaningful[:-1]
    for part in parents:
        if part in SKIP_DIRS or (part.startswith(".") and part not in SAFE_HIDDEN_DIRS): raise ValueError("hidden/generated path component refused")
    return p


def _resolve_inside(workspace: Path, raw: str, *, expect_dir: bool) -> Path:
    rel = _validate_relative(raw, allow_dot=expect_dir)
    current = workspace
    for part in [x for x in rel.parts if x not in {"", "."}]:
        current = current / part
        if current.is_symlink(): raise ValueError("symlink path component refused")
    try:
        resolved = (workspace / Path(*rel.parts)).resolve(strict=True); resolved.relative_to(workspace)
    except (OSError, ValueError) as exc: raise ValueError("path unavailable or outside workspace") from exc
    if expect_dir and not resolved.is_dir(): raise ValueError("target must be a directory")
    if not expect_dir and (not resolved.is_file() or resolved.is_symlink()): raise ValueError("target must be a regular file")
    return resolved


def _read_utf8(path: Path, max_bytes: int) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os,"O_CLOEXEC",0) | getattr(os,"O_NOFOLLOW",0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes: raise ValueError("file is not a bounded regular file")
        chunks=[]; remaining=max_bytes+1
        while remaining>0:
            chunk=os.read(fd,min(65_536,remaining))
            if not chunk: break
            chunks.append(chunk); remaining-=len(chunk)
        data=b"".join(chunks)
        if len(data)>max_bytes or b"\x00" in data: raise ValueError("binary or oversized file refused")
        try: return data.decode("utf-8",errors="strict"),len(data)
        except UnicodeDecodeError as exc: raise ValueError("non-UTF-8 file refused") from exc
    finally: os.close(fd)


def _is_text_candidate(path: Path) -> bool:
    return path.name.lower() in TEXT_NAMES or path.suffix.lower() in TEXT_EXTENSIONS


def handle_map(args: dict, **kw) -> str:
    try:
        workspace=_bound_workspace(); target=str(args.get("target") or "."); _resolve_inside(workspace,target,expect_dir=True)
        buffer=io.StringIO(); argv=["--workspace",str(workspace),*MAP_LIMITS,"--",target]
        with redirect_stdout(buffer): rc=repo_map.main(argv)
        return tool_result({"workspace_bound":True,"map":buffer.getvalue()}) if rc==0 else tool_error("repository map failed")
    except (ValueError,OSError,SystemExit) as exc: return tool_error(str(exc))


def handle_read(args: dict, **kw) -> str:
    try:
        workspace=_bound_workspace(); raw=str(args.get("path") or ""); path=_resolve_inside(workspace,raw,expect_dir=False)
        if _secret_like(path): raise ValueError("hidden/secret-like file refused")
        content,size=_read_utf8(path,MAX_READ_BYTES); start=max(1,int(args.get("start_line") or 1)); max_lines=max(1,min(MAX_READ_LINES,int(args.get("max_lines") or 200)))
        lines=content.splitlines(); shown=lines[start-1:start-1+max_lines]; rel=path.relative_to(workspace).as_posix()
        return tool_result({"path":repo_map.sanitize(rel),"start_line":start,"end_line":start+len(shown)-1 if shown else start-1,"total_lines":len(lines),"bytes":size,"truncated":start-1+len(shown)<len(lines),"content":"\n".join(shown)})
    except (ValueError,OSError) as exc: return tool_error(str(exc))


def handle_search(args: dict, **kw) -> str:
    try:
        workspace=_bound_workspace(); query=str(args.get("query") or "")
        if not query or len(query)>MAX_QUERY_CHARS or "\x00" in query: raise ValueError("query must contain 1-256 non-NUL characters")
        target=_resolve_inside(workspace,str(args.get("target") or "."),expect_dir=True); case_sensitive=bool(args.get("case_sensitive",False)); max_results=max(1,min(MAX_SEARCH_RESULTS,int(args.get("max_results") or 50))); needle=query if case_sensitive else query.casefold()
        results=[]; files_seen=dirs_seen=total_bytes=0; truncated=False; stack=[target]
        while stack and len(results)<max_results:
            current=stack.pop(); dirs_seen+=1
            if dirs_seen>MAX_SEARCH_DIRS: truncated=True; break
            entries,overflow=repo_map.bounded_entries(current,MAX_SEARCH_DIR_ENTRIES)
            if overflow: truncated=True; continue
            child_dirs=[]
            for entry in entries:
                path=current/entry.name
                try:
                    if entry.is_symlink(): continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in SKIP_DIRS or (entry.name.startswith(".") and entry.name not in SAFE_HIDDEN_DIRS): continue
                        resolved_dir=path.resolve(strict=True); resolved_dir.relative_to(workspace); child_dirs.append(resolved_dir); continue
                    if not entry.is_file(follow_symlinks=False): continue
                except (OSError,ValueError): continue
                files_seen+=1
                if files_seen>MAX_SEARCH_FILES: truncated=True; stack.clear(); break
                if _secret_like(path) or not _is_text_candidate(path): continue
                try: info=entry.stat(follow_symlinks=False)
                except OSError: continue
                if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_SEARCH_FILE_BYTES: continue
                if total_bytes+info.st_size>MAX_SEARCH_TOTAL_BYTES: truncated=True; stack.clear(); break
                try:
                    resolved=path.resolve(strict=True); resolved.relative_to(workspace); content,size=_read_utf8(resolved,MAX_SEARCH_FILE_BYTES)
                except (OSError,ValueError): continue
                total_bytes+=size; per_file=0
                for lineno,line in enumerate(content.splitlines(),1):
                    haystack=line if case_sensitive else line.casefold()
                    if needle in haystack:
                        results.append({"path":repo_map.sanitize(resolved.relative_to(workspace).as_posix()),"line":lineno,"text":repo_map.sanitize(line[:500])}); per_file+=1
                        if len(results)>=max_results: truncated=True; break
                        if per_file>=MAX_MATCHES_PER_FILE: break
            for child in reversed(child_dirs): stack.append(child)
        return tool_result({"query":query,"results":results,"files_examined":files_seen,"bytes_scanned":total_bytes,"truncated":truncated})
    except (ValueError,OSError) as exc: return tool_error(str(exc))
