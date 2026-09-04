"""Fail-closed verifier for the effective reviewer-gpt worker capability surface."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

FORBIDDEN_TOOLS = frozenset({"terminal", "process", "execute_code", "write_file", "patch"})
EXPECTED_TOOLSETS = frozenset({"factory-repository-readonly", "factory-execution-guards", "kanban"})
REQUIRED_TOOLS = frozenset({
    "factory_repo_map", "factory_repo_read", "factory_repo_search",
    "factory_review_approve", "kanban_show", "kanban_request_changes",
})


def verify(profile_home: Path) -> dict[str, object]:
    from hermes_cli import kanban_db as kb
    from toolsets import resolve_toolset

    profile_home = profile_home.resolve(strict=True)
    config = yaml.safe_load((profile_home / "config.yaml").read_text(encoding="utf-8")) or {}
    resolved = kb._resolve_worker_cli_toolsets(str(profile_home))
    if resolved is None or frozenset(resolved) != EXPECTED_TOOLSETS:
        raise RuntimeError(f"reviewer worker toolsets mismatch: {resolved!r}")
    disabled = frozenset((config.get("agent") or {}).get("disabled_toolsets") or ())
    for required_disabled in ("terminal", "file", "code_execution"):
        if required_disabled not in disabled:
            raise RuntimeError(f"reviewer disabled toolset missing: {required_disabled}")
    if config.get("mcp_servers") not in ({}, None):
        raise RuntimeError("reviewer MCP servers must be empty")

    effective: set[str] = set()
    for name in resolved:
        if name in {"factory-repository-readonly", "factory-execution-guards"}:
            manifest = yaml.safe_load((profile_home / "plugins" / name / "plugin.yaml").read_text(encoding="utf-8")) or {}
            effective.update(manifest.get("provides_tools") or ())
        elif name != "no_mcp":
            effective.update(resolve_toolset(name))
    forbidden = sorted(FORBIDDEN_TOOLS & effective)
    if forbidden:
        raise RuntimeError(f"reviewer forbidden tools reachable: {forbidden}")
    missing = sorted(REQUIRED_TOOLS - effective)
    if missing:
        raise RuntimeError(f"reviewer required tools missing: {missing}")
    return {"toolsets": sorted(resolved), "tools": sorted(effective), "forbidden": forbidden}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile_home", type=Path)
    args = parser.parse_args()
    result = verify(args.profile_home)
    print("REVIEWER_CAPABILITY_SURFACE_OK " + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
