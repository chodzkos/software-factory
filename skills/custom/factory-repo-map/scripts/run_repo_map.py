#!/usr/bin/env python3
"""Authoritative Hermes Kanban binder for factory-repo-map."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import repo_map

ALLOWED_PROFILE = "repository-analyst"
FIXED_LIMITS = [
    "--max-files", "500",
    "--max-dirs", "2000",
    "--max-dir-entries", "4096",
    "--max-file-bytes", "1048576",
    "--max-total-bytes", "8388608",
    "--max-symbols", "12",
]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise SystemExit("usage: run_repo_map.py [workspace-relative-target]")
    target = args[0] if args else "."

    task_id = os.environ.get("HERMES_KANBAN_TASK", "")
    workspace_raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "")
    profile = os.environ.get("HERMES_PROFILE", "")

    if not task_id:
        raise SystemExit("ERROR: missing authoritative Kanban task binding")
    if profile != ALLOWED_PROFILE:
        raise SystemExit("ERROR: factory-repo-map is repository-analyst only")
    if not workspace_raw:
        raise SystemExit("ERROR: missing authoritative Kanban workspace binding")

    workspace = Path(workspace_raw)
    if not workspace.is_absolute():
        raise SystemExit("ERROR: authoritative Kanban workspace must be absolute")

    return repo_map.main(["--workspace", workspace_raw, target, *FIXED_LIMITS])


if __name__ == "__main__":
    raise SystemExit(main())
