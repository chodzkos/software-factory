#!/usr/bin/env python3
"""Secure, bounded, workspace-confined repository map for Software Factory."""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__",
    ".next", "target", "vendor", ".idea", ".vscode", "coverage", ".cache",
}
ALLOWED_EXTENSIONS = {
    ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java",
    ".rb", ".sh", ".bash", ".zsh", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".kt", ".kts", ".swift", ".php", ".scala",
}
SECRET_NAMES = {
    "id_rsa", "id_dsa", "id_ed25519", "credentials.json", "service-account.json",
    "service_account.json", "secrets.json", "secrets.yaml", "secrets.yml",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db", ".dump"}
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SYMBOL_RULES = {
    ".py": re.compile(r"^(?:class|def|async def)\s+(\w+)", re.M),
    ".js": re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+(\w+)|^(?:export\s+)?const\s+(\w+)\s*=", re.M),
    ".ts": re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+(\w+)|^(?:export\s+)?const\s+(\w+)\s*=", re.M),
    ".go": re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)|^type\s+(\w+)", re.M),
    ".rs": re.compile(r"^(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+(\w+)", re.M),
    ".java": re.compile(r"^\s*(?:public|protected)\s+(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+(\w+)", re.M),
    ".rb": re.compile(r"^\s*(?:class|module|def)\s+([\w.?!]+)", re.M),
    ".sh": re.compile(r"^(?:function\s+)?(\w+)\s*\(\)", re.M),
}
SYMBOL_RULES[".jsx"] = SYMBOL_RULES[".js"]
SYMBOL_RULES[".mjs"] = SYMBOL_RULES[".js"]
SYMBOL_RULES[".tsx"] = SYMBOL_RULES[".ts"]

HARD_MAX = {
    "max_files": 2_000,
    "max_dirs": 5_000,
    "max_dir_entries": 10_000,
    "max_file_bytes": 2_097_152,
    "max_total_bytes": 16_777_216,
    "max_symbols": 100,
}


def sanitize(text: str) -> str:
    return CONTROL_RE.sub("?", text)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_secret_like(path: Path) -> bool:
    lower = path.name.lower()
    return lower.startswith(".") or lower in SECRET_NAMES or path.suffix.lower() in SECRET_SUFFIXES


def has_symlink_component(path: Path) -> bool:
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


def symbols_for(content: str, ext: str, max_symbols: int) -> str:
    rule = SYMBOL_RULES.get(ext)
    if not rule:
        return ""
    found: list[str] = []
    seen: set[str] = set()
    for match in rule.finditer(content):
        name = next((group for group in match.groups() if group), None)
        if name and name not in seen:
            seen.add(name)
            found.append(name)
    shown = found[:max_symbols]
    if not shown:
        return ""
    extra = f" +{len(found) - len(shown)}" if len(found) > len(shown) else ""
    return "  → " + ", ".join(shown) + extra


def read_regular_text(path: Path, max_bytes: int) -> tuple[str, int] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            return None
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes or b"\x00" in data[:4096]:
            return None
        try:
            return data.decode("utf-8", errors="strict"), len(data)
        except UnicodeDecodeError:
            return None
    finally:
        os.close(fd)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bounded workspace-confined repository map.")
    parser.add_argument("--workspace", required=True, help="Authoritative Kanban workspace path")
    parser.add_argument("path", nargs="?", default=".", help="Workspace-relative target")
    parser.add_argument("--max-files", type=int, default=500, help="Maximum filenames examined")
    parser.add_argument("--max-dirs", type=int, default=2000, help="Maximum directories visited")
    parser.add_argument("--max-dir-entries", type=int, default=4096, help="Maximum entries accepted in one directory")
    parser.add_argument("--max-file-bytes", type=int, default=1_048_576)
    parser.add_argument("--max-total-bytes", type=int, default=8_388_608)
    parser.add_argument("--max-symbols", type=int, default=12)
    args = parser.parse_args(argv)
    for name, ceiling in HARD_MAX.items():
        value = getattr(args, name)
        flag = f"--{name.replace('_', '-')}"
        if value <= 0:
            parser.error(f"{flag} must be > 0")
        if value > ceiling:
            parser.error(f"{flag} exceeds hard ceiling {ceiling}")
    return args


def clean_resolve(path: Path, kind: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError:
        raise SystemExit(f"ERROR: {kind} unavailable") from None


def validate_target(workspace: Path, raw_target: Path) -> Path:
    if raw_target.is_absolute():
        raise SystemExit("ERROR: target must be workspace-relative")
    if any(part == ".." for part in raw_target.parts):
        raise SystemExit("ERROR: target parent traversal refused")

    current = workspace
    meaningful = [part for part in raw_target.parts if part not in {"", "."}]
    for part in meaningful:
        if part in SKIP_DIRS or part.startswith("."):
            raise SystemExit("ERROR: refusing hidden/generated target component")
        current = current / part
        if current.is_symlink():
            raise SystemExit("ERROR: target symlink component refused")

    target = clean_resolve(workspace / raw_target, "target")
    if not target.is_dir() or not is_within(target, workspace):
        raise SystemExit("ERROR: target escapes workspace or is not a directory")
    return target


def bounded_entries(directory: Path, max_entries: int) -> tuple[list[os.DirEntry[str]], bool]:
    entries: list[os.DirEntry[str]] = []
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                entries.append(entry)
                if len(entries) > max_entries:
                    return [], True
    except OSError:
        return [], False
    entries.sort(key=lambda entry: entry.name)
    return entries, False


def main(argv=None) -> int:
    args = parse_args(argv)
    workspace_arg = Path(args.workspace).expanduser()
    if has_symlink_component(workspace_arg):
        raise SystemExit("ERROR: workspace symlink refused")
    workspace = clean_resolve(workspace_arg, "workspace")
    if not workspace.is_dir():
        raise SystemExit("ERROR: workspace must be a directory")

    target = validate_target(workspace, Path(args.path))

    rows: list[tuple[str, int, str]] = []
    total_bytes = 0
    total_lines = 0
    files_seen = 0
    dirs_seen = 0
    truncated = False
    stop = False
    stack = [target]

    while stack and not stop:
        current = stack.pop()
        dirs_seen += 1
        if dirs_seen > args.max_dirs:
            truncated = True
            break

        entries, overflow = bounded_entries(current, args.max_dir_entries)
        if overflow:
            truncated = True
            continue

        child_dirs: list[Path] = []
        for entry in entries:
            name = entry.name
            path = current / name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in SKIP_DIRS or name.startswith("."):
                        continue
                    try:
                        resolved_dir = path.resolve(strict=True)
                    except OSError:
                        continue
                    if is_within(resolved_dir, workspace):
                        child_dirs.append(resolved_dir)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            files_seen += 1
            if files_seen > args.max_files:
                truncated = True
                stop = True
                break
            if is_secret_like(path):
                continue
            ext = path.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_size > args.max_file_bytes:
                truncated = True
                continue
            if total_bytes + info.st_size > args.max_total_bytes:
                truncated = True
                stop = True
                break

            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if not is_within(resolved, workspace):
                continue
            read = read_regular_text(resolved, args.max_file_bytes)
            if read is None:
                continue
            content, size = read
            if total_bytes + size > args.max_total_bytes:
                truncated = True
                stop = True
                break

            total_bytes += size
            lines = len(content.splitlines())
            total_lines += lines
            rel = resolved.relative_to(workspace).as_posix()
            rows.append((sanitize(rel), lines, symbols_for(content, ext, args.max_symbols)))

        for child in reversed(child_dirs):
            stack.append(child)

    print("# Repo map: .")
    print(f"# {len(rows)} files · {total_lines:,} lines · {total_bytes:,} bytes scanned")
    if truncated:
        print("# [truncated by configured safety limits]")
    for rel, lines, symbols in rows:
        print(f"F {rel:<54}{lines:>8}L{symbols}")
    print("# navigate by map: open only files relevant to the assigned task")
    return 0


if __name__ == "__main__":
    sys.exit(main())
