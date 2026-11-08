"""Wspólny fail-closed verifier rzeczywistej powierzchni model tools profilu."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Iterable, cast

import yaml


HERMES_VERSION = "0.20.4"
REQUIRED_DISABLED = frozenset({
    "terminal", "file", "code_execution", "web", "browser", "image_gen",
    "delegation", "computer_use", "cronjob", "skills", "vision", "todo",
    "memory", "session_search", "clarify", "messaging", "tts", "moa",
    "bfl", "x_search", "mcp",
})
EXPECTED_REVIEWER_TOOLSETS = frozenset({
    "factory-repository-readonly", "factory-execution-guards", "kanban"
})
EXPECTED_RELEASE_TOOLSETS = frozenset({
    "factory-repository-readonly", "factory-execution-guards", "kanban"
})
EXPECTED_PLUGIN_SHA256 = {
    "factory-execution-guards": {
        "__init__.py": "c595c6b4f21bbec8b01289a93c4a3e6f12b270a7c28fc950f47f4ca1bf51f5a2",
        "guard.py": "20f3631f01d5fb0e2f933e9569a49253f7748b9092e4252d69a2ece127060197",
        "handoff.py": "b0d65969f0c502139fe4d8c3fbec9121d73caedd39ed5805679ef9074a65ea93",
        "plugin.yaml": "49525d9203b2793ddf920932304ae56524ea8dddf0943e919436864de5bd16eb",
        "review_intent.py": "c53e8bb17c7e2ace531b52f14311ae4000ef5d04db3cc529c0bd65bda47a9287",
        "review_registration.py": "3edb0fc832a1928a5f882e19c3be489723bf5fc2a901b993c99a0e4bee2b568a",
        "review_transaction.py": "9749e781670f9ff9aa9423d193da1be15f132dac4c6d74d3b531b7850800276e",
        "supervisor.py": "3c3de6531fc9007cad95dc734eddcde1f7cf6cd6b202fce0a03bdb02469928ed"
    },
    "factory-repository-readonly": {
        "plugin.yaml": "b82696753bc88c1bad7eec5266a3b3d4f37152aa703b7fb850b1c08909b1c337",
        "__init__.py": "45985ac4faa072055a4e03d0ae005bd479fca55a3046de23a399067e7545af71",
        "repo_map.py": "88b7d66c33ce8fb8b80e7e8e6bbf47462abda27246386bc9767ee4d62a74ef3c",
        "repository_tools.py": "a93794aa9aeb8496ce46537a7907d9b66473ea2961efc49f3cac904502d61a80",
        "kanban_guard.py": "f38cb4038757c822eb18ec03faf0b22d8a5ae9f7fb9346410f0433650000b93d"
    }
}


def _tree_digest(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"plugin tree missing or symlinked: {root}")
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"plugin tree symlink refused: {path}")
        if path.is_file():
            if "__pycache__" in path.parts and path.suffix == ".pyc":
                continue
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _assert_plugin_copy(profile_home: Path, name: str) -> None:
    expected = EXPECTED_PLUGIN_SHA256[name]
    actual = _tree_digest(profile_home / "plugins" / name)
    if actual != expected:
        raise RuntimeError(f"installed plugin bytes mismatch: {name}")


def _hermes_python() -> Path:
    configured = os.environ.get("FACTORY_HERMES_PYTHON", "").strip()
    candidate = Path(configured) if configured else Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RuntimeError(f"Hermes Python unavailable: {candidate}")
    return candidate.absolute()


def _assert_hermes_version(python: Path) -> None:
    hermes_script = python.parent.parent.parent / "hermes"
    if not hermes_script.is_file():
        raise RuntimeError("Hermes launcher source unavailable")
    result = subprocess.run(
        [str(python), str(hermes_script), "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=15,
    )
    if HERMES_VERSION not in result.stdout:
        raise RuntimeError(f"Hermes version mismatch: {result.stdout.strip()!r}")


def _actual_definitions(
    profile_home: Path,
    *,
    profile: str,
    workspace: Path,
    task_id: str,
    board: str,
    run_id: int,
) -> dict[str, object]:
    python = _hermes_python()
    _assert_hermes_version(python)
    probe = Path(__file__).resolve().parent / "capability_surface_probe.py"
    env = dict(os.environ)
    env.update(HERMES_HOME=str(profile_home), HERMES_PROFILE=profile, PYTHONDONTWRITEBYTECODE="1")
    for name in tuple(env):
        if name.startswith("HERMES_KANBAN_") or name in {"FACTORY_RELEASE_TASK", "FACTORY_RELEASE_WORKSPACE"}:
            env.pop(name, None)
    if profile == "release-manager":
        env.update(FACTORY_RELEASE_TASK=task_id, FACTORY_RELEASE_WORKSPACE=str(workspace.resolve(strict=True)))
    else:
        env.update(
            HERMES_KANBAN_BOARD=board,
            HERMES_KANBAN_TASK=task_id,
            HERMES_KANBAN_RUN_ID=str(run_id),
            HERMES_KANBAN_WORKSPACE=str(workspace.resolve(strict=True)),
        )
    result = subprocess.run(
        [str(python), str(probe), "--profile-home", str(profile_home)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        env=env,
        cwd=str(workspace),
    )
    marker = "FACTORY_CAPABILITY_SURFACE "
    lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise RuntimeError(f"Hermes capability probe did not return one marker: {result.stdout[-2000:]}")
    value = json.loads(lines[0][len(marker):])
    if not isinstance(value, dict):
        raise RuntimeError("Hermes capability probe returned malformed payload")
    for key in ("tools", "toolsets", "disabled_toolsets"):
        items = value.get(key)
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise RuntimeError(f"Hermes capability probe returned malformed {key}")
    if value.get("launch_context") != "cli-chat":
        raise RuntimeError("Hermes capability probe did not use the CLI chat launch context")
    return value


def verify_profile(
    profile_home: Path,
    *,
    profile: str,
    expected_tools: Iterable[str],
    workspace: Path,
    task_id: str,
    board: str,
    run_id: int,
) -> dict[str, object]:
    profile_home = profile_home.resolve(strict=True)

    config = yaml.safe_load((profile_home / "config.yaml").read_text(encoding="utf-8")) or {}
    enabled_plugins = frozenset((config.get("plugins") or {}).get("enabled") or ())
    required_plugins = frozenset({"factory-repository-readonly", "factory-execution-guards"})
    if not required_plugins <= enabled_plugins:
        raise RuntimeError("required confinement plugin is disabled or missing from enabled list")
    entries = (config.get("plugins") or {}).get("entries") or {}
    if ((entries.get("factory-execution-guards") or {}).get("allow_tool_override") is not True):
        raise RuntimeError("execution guard scoped tool override opt-in missing")
    if ((entries.get("factory-repository-readonly") or {}).get("allow_tool_override") is True):
        raise RuntimeError("repository plugin must not receive tool override permission")
    _assert_plugin_copy(profile_home, "factory-repository-readonly")
    _assert_plugin_copy(profile_home, "factory-execution-guards")

    disabled: list[str] = list((config.get("agent") or {}).get("disabled_toolsets") or ())
    missing_disabled = sorted(REQUIRED_DISABLED - frozenset(disabled))
    if missing_disabled:
        raise RuntimeError(f"disabled toolsets missing: {missing_disabled}")
    if config.get("mcp_servers") not in ({}, None):
        raise RuntimeError("MCP servers must be empty")
    if ((config.get("tools") or {}).get("tool_search") or {}).get("enabled") != "off":
        raise RuntimeError("tool search must be disabled for exact capability profiles")

    observed = _actual_definitions(
        profile_home,
        profile=profile,
        workspace=workspace,
        task_id=task_id,
        board=board,
        run_id=run_id,
    )
    actual = cast(list[str], observed["tools"])
    resolved = cast(list[str], observed["toolsets"])
    observed_disabled = cast(list[str], observed["disabled_toolsets"])
    expected_toolsets = EXPECTED_RELEASE_TOOLSETS if profile == "release-manager" else EXPECTED_REVIEWER_TOOLSETS
    if frozenset(resolved) != expected_toolsets:
        raise RuntimeError(f"worker toolsets mismatch: {resolved!r}")
    if sorted(disabled) != observed_disabled:
        raise RuntimeError("probe/config disabled toolsets mismatch")
    expected = sorted(set(expected_tools))
    if actual != expected:
        raise RuntimeError(f"exact model tool surface mismatch: expected={expected!r} actual={actual!r}")
    return {
        "profile": profile,
        "launch_context": observed["launch_context"],
        "toolsets": resolved,
        "tools": actual,
        "forbidden": [],
    }
