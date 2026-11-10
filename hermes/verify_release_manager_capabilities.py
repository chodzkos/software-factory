"""Fail-closed verifier for the read-only release-manager tool surface."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .verify_profile_capabilities import verify_profile
except ImportError:
    import importlib.util
    import sys

    _shared_path = Path(__file__).resolve().parent / "verify_profile_capabilities.py"
    _shared_spec = importlib.util.spec_from_file_location("factory_verify_profile_capabilities", _shared_path)
    if _shared_spec is None or _shared_spec.loader is None:
        raise RuntimeError("shared capability verifier unavailable")
    _shared = importlib.util.module_from_spec(_shared_spec)
    sys.modules[_shared_spec.name] = _shared
    _shared_spec.loader.exec_module(_shared)
    verify_profile = _shared.verify_profile


REQUIRED_TOOLS = frozenset({
    "factory_repo_map", "factory_repo_read", "factory_repo_search",
})


def verify(
    profile_home: Path,
    *,
    workspace: Path | None = None,
    task_id: str = "t_release_capability_probe",
    board: str = "isolated",
    run_id: int = 1,
) -> dict[str, object]:
    return verify_profile(
        profile_home,
        profile="release-manager",
        expected_tools=REQUIRED_TOOLS,
        workspace=(workspace or Path(__file__).resolve().parent.parent),
        task_id=task_id,
        board=board,
        run_id=run_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile_home", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--task-id", default="t_release_capability_probe")
    parser.add_argument("--board", default="isolated")
    parser.add_argument("--run-id", type=int, default=1)
    args = parser.parse_args()
    result = verify(args.profile_home, workspace=args.workspace, task_id=args.task_id, board=args.board, run_id=args.run_id)
    print("RELEASE_MANAGER_CAPABILITY_SURFACE_OK " + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
