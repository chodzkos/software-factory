#!/usr/bin/env python3
"""Secure, bounded, workspace-confined repository map for Software Factory."""
from __future__ import annotations

import argparse
import os
import re
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


def is_probably_binary(path: Path, sample_size: int = 4096) -> bool:
    try:
        with path.open("rb") as f:
            sample = f.read(sample_size)
    except OSError:
        return True
    return b"\x00" in sample


def symbols_for(content: str, ext: str, max_symbols: int) -> str:
    rule = SYMBOL_RULES.get(ext)
    if not rule:
        return ""
    found: list[str] = []
    for match in rule.finditer(content):
        name = next((group for group in match.groups() if group), None)
        if name and name not in found:
            found.append(name)
    shown = found[:max_symbols]
    if not shown:
        return ""
    extra = f" +{len(found) - len(shown)}" if len(found) > len(shown) else ""
    return "  → " + ", ".join(shown) + extra


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bounded workspace-confined repository map.")
    parser.add_argument("--workspace", required=True, help="Authoritative Kanban workspace path")
    parser.add_argument("path", nargs="?", default=".", help="Workspace-relative target")
    parser.add_argument("--max-files", type=int, default=500, help="Maximum filenames examined")
    parser.add_argument("--max-dirs", type=int, default=2000, help="Maximum directories visited")
    parser.add_argument("--max-file-bytes", type=int, default=1_048_576)
    parser.add_argument("--max-total-bytes", type=int, default=8_388_608)
    parser.add_argument("--max-symbols", type=int, default=12)
    args = parser.parse_args(argv)
    for name in ("max_files", "max_dirs", "max_file_bytes", "max_total_bytes", "max_symbols"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be > 0")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    workspace_arg = Path(args.workspace).expanduser()
    if workspace_arg.is_symlink():
        raise SystemExit("ERROR: workspace symlink refused")
    workspace = workspace_arg.resolve(strict=True)
    if not workspace.is_dir():
        raise SystemExit("ERROR: workspace must be a directory")

    raw_target = Path(args.path)
    if raw_target.is_absolute():
        raise SystemExit("ERROR: target must be workspace-relative")
    unresolved_target = workspace / raw_target
    if unresolved_target.is_symlink():
        raise SystemExit("ERROR: target symlink refused")
    target = unresolved_target.resolve(strict=True)
    if not target.is_dir() or not is_within(target, workspace):
        raise SystemExit("ERROR: target escapes workspace or is not a directory")
    if target.name in SKIP_DIRS or target.name.startswith("."):
        raise SystemExit("ERROR: refusing hidden/generated target")

    rows: list[tuple[str, int, str]] = []
    total_bytes = 0
    total_lines = 0
    files_seen = 0
    dirs_seen = 0
    truncated = False
    stop = False

    for dirpath, dirnames, filenames in os.walk(target, topdown=True, followlinks=False):
        dirs_seen += 1
        if dirs_seen > args.max_dirs:
            truncated = True
            break
        current = Path(dirpath)
        safe_dirs = []
        for dirname in sorted(dirnames):
            child = current / dirname
            if dirname in SKIP_DIRS or dirname.startswith(".") or child.is_symlink():
                continue
            try:
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_dir() and is_within(resolved, workspace):
                safe_dirs.append(dirname)
        dirnames[:] = safe_dirs

        for filename in sorted(filenames):
            files_seen += 1
            if files_seen > args.max_files:
                truncated = True
                stop = True
                break
            path = current / filename
            if path.is_symlink() or is_secret_like(path):
                continue
            ext = path.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_file() or not is_within(resolved, workspace):
                continue
            try:
                size = resolved.stat().st_size
            except OSError:
                continue
            if size > args.max_file_bytes:
                truncated = True
                continue
            if total_bytes + size > args.max_total_bytes:
                truncated = True
                stop = True
                break
            if is_probably_binary(resolved):
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            total_bytes += size
            lines = content.count("\n") + 1
            total_lines += lines
            rel = resolved.relative_to(workspace).as_posix()
            rows.append((sanitize(rel), lines, symbols_for(content, ext, args.max_symbols)))
        if stop:
            dirnames[:] = []
            break

    print("# Repo map: .")
    print(f"# {len(rows)} files · {total_lines:,} lines · {total_bytes:,} bytes scanned")
    if truncated:
        print("# [truncated by configured safety limits]")
    for rel, lines, symbols in rows:
        print(f"{rel:<56}{lines:>8}L{symbols}")
    print("# navigate by map: open only files relevant to the assigned task")
    return 0


if __name__ == "__main__":
    sys.exit(main())
