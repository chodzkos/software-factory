#!/usr/bin/env python3
"""Atomically remove inherited Hermes config keys from a profile YAML file."""
from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml


def remove_key(root: dict[str, Any], dotted: str) -> bool:
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        raise ValueError("empty config key")
    current: Any = root
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        parents.append((current, part))
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("keys", nargs="+")
    args = parser.parse_args()

    path = args.config.expanduser()
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"ERROR: profile config missing or symlinked: {path}")
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SystemExit("ERROR: profile config root must be a mapping")

    changed = False
    for key in args.keys:
        changed = remove_key(data, key) or changed
    if not changed:
        return 0

    st = path.stat()
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.S_IMODE(st.st_mode))
        os.replace(temp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
