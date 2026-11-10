#!/usr/bin/env python3
"""Kanoniczny fail-closed start read-only decyzji release-manager."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Callable

try:
    from .verify_profile_capabilities import _assert_hermes_version, _hermes_python
    from .verify_release_manager_capabilities import verify
except ImportError:
    import sys

    _module_dir = str(Path(__file__).resolve().parent)
    sys.path.insert(0, _module_dir)
    try:
        from verify_profile_capabilities import _assert_hermes_version, _hermes_python
        from verify_release_manager_capabilities import verify
    finally:
        sys.path.remove(_module_dir)


def launch_release_manager(
    *,
    profile_home: Path,
    workspace: Path,
    board: str,
    task_id: str,
    run_id: int,
    prompt: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Zweryfikuj actual definitions i natychmiast uruchom exact read-only profil."""
    if not prompt.strip():
        raise RuntimeError("release decision prompt is empty")
    profile_home = profile_home.resolve(strict=True)
    workspace = workspace.resolve(strict=True)
    verify(profile_home, workspace=workspace, task_id=task_id, board=board, run_id=run_id)
    python = _hermes_python()
    _assert_hermes_version(python)
    hermes_script = python.parent.parent.parent / "hermes"
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith("HERMES_KANBAN_"):
            env.pop(name, None)
    env.update(
        HERMES_HOME=str(profile_home),
        HERMES_PROFILE="release-manager",
        FACTORY_RELEASE_TASK=task_id,
        FACTORY_RELEASE_WORKSPACE=str(workspace),
        PYTHONDONTWRITEBYTECODE="1",
    )
    result = runner(
        [str(python), str(hermes_script), "chat", "-q", prompt],
        check=False,
        env=env,
        cwd=str(workspace),
        text=True,
    )
    return int(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-home", type=Path, default=Path.home() / ".hermes" / "factory-profiles" / "release-manager")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    return launch_release_manager(
        profile_home=args.profile_home,
        workspace=args.workspace,
        board=args.board,
        task_id=args.task_id,
        run_id=args.run_id,
        prompt=args.prompt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
