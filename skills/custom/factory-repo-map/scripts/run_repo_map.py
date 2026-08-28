#!/usr/bin/env python3
"""Hermes Kanban binder for the reviewed factory-repo-map implementation."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# The installed skill tree is integrity-pinned. Do not let Python mutate it by
# writing __pycache__ when importing the mapper module.
sys.dont_write_bytecode = True

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
    if not target or target.startswith("-"):
        raise SystemExit("ERROR: target must be a non-option workspace-relative path")

    task_id = os.environ.get("HERMES_KANBAN_TASK", "")
    workspace_raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "")
    profile = os.environ.get("HERMES_PROFILE", "")

    if not task_id:
        raise SystemExit("ERROR: missing Kanban task binding")
    if profile != ALLOWED_PROFILE:
        raise SystemExit("ERROR: factory-repo-map is repository-analyst only")
    if not workspace_raw:
        raise SystemExit("ERROR: missing Kanban workspace binding")

    workspace = Path(workspace_raw)
    if not workspace.is_absolute():
        raise SystemExit("ERROR: Kanban workspace must be absolute")

    # `--` prevents a relative target from being reinterpreted as mapper options.
    return repo_map.main(["--workspace", workspace_raw, *FIXED_LIMITS, "--", target])


if __name__ == "__main__":
    raise SystemExit(main())
