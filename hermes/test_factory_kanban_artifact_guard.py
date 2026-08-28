#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes" / "plugins" / "factory-kanban-artifact-guard"


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "factory_kanban_artifact_guard_test",
        PLUGIN / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class KanbanArtifactGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = load_plugin()

    def bound_env(self, root: Path, profile: str = "repository-analyst"):
        return patch.dict(
            os.environ,
            {
                "HERMES_KANBAN_TASK": "t_guard_test",
                "HERMES_KANBAN_WORKSPACE": str(root),
                "HERMES_PROFILE": profile,
            },
            clear=True,
        )

    def assert_blocked(self, result):
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("action"), "block")
        self.assertIn("Factory Kanban artifact guard refused completion", result.get("message", ""))

    def test_registers_exact_pre_tool_call_hook(self):
        ctx = FakeContext()
        self.guard.register(ctx)
        self.assertEqual(len(ctx.hooks), 1)
        self.assertEqual(ctx.hooks[0][0], "pre_tool_call")
        self.assertIs(ctx.hooks[0][1], self.guard._on_pre_tool_call)

    def test_non_target_tool_and_other_profiles_are_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.bound_env(root):
                self.assertIsNone(self.guard._on_pre_tool_call("kanban_comment", {"message": "/etc/passwd"}))
            with self.bound_env(root, profile="coder"):
                self.assertIsNone(self.guard._on_pre_tool_call("kanban_complete", {"artifacts": ["/etc/passwd"]}))

    def test_repository_analyst_completion_requires_worker_binding(self):
        env = {"HERMES_PROFILE": "repository-analyst"}
        with patch.dict(os.environ, env, clear=True):
            self.assert_blocked(self.guard._on_pre_tool_call("kanban_complete", {"artifacts": []}))

    def test_inside_workspace_artifacts_are_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            artifact = root / "reports" / "audit.md"
            artifact.write_text("ok\n")
            with self.bound_env(root):
                self.assertIsNone(self.guard._on_pre_tool_call("kanban_complete", {"artifacts": ["reports/audit.md"]}))
                self.assertIsNone(self.guard._on_pre_tool_call("kanban_complete", {"artifacts": [str(artifact)]}))

    def test_outside_parent_symlink_directory_and_malformed_artifacts_are_blocked(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td)
            outside = Path(od) / "outside.txt"
            outside.write_text("secret\n")
            (root / "inside.txt").write_text("ok\n")
            (root / "link.txt").symlink_to(outside)
            (root / "dir-only").mkdir()
            with self.bound_env(root):
                for artifacts in (
                    [str(outside)],
                    ["../outside.txt"],
                    ["/etc/passwd"],
                    ["link.txt"],
                    ["dir-only"],
                    "inside.txt",
                    [123],
                ):
                    self.assert_blocked(
                        self.guard._on_pre_tool_call("kanban_complete", {"artifacts": artifacts})
                    )

    def test_completion_text_allows_workspace_path_and_blocks_outside_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "report.md"
            inside.write_text("ok\n")
            with self.bound_env(root):
                self.assertIsNone(
                    self.guard._on_pre_tool_call(
                        "kanban_complete",
                        {"summary": f"Report saved at {inside}.", "artifacts": []},
                    )
                )
                for summary in (
                    "See /etc/passwd",
                    "Result at /home/marcin/private-note.txt",
                    r"Output C:\\Users\\User\\secret.txt",
                ):
                    self.assert_blocked(
                        self.guard._on_pre_tool_call(
                            "kanban_complete",
                            {"summary": summary, "artifacts": []},
                        )
                    )

    def test_nested_text_values_are_guarded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.bound_env(root):
                self.assert_blocked(
                    self.guard._on_pre_tool_call(
                        "kanban_complete",
                        {"metadata": {"notes": ["local: /tmp/outside.txt"]}, "artifacts": []},
                    )
                )

    def test_malformed_arguments_fail_closed_for_repository_analyst(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.bound_env(root):
                for args in (None, [], "x"):
                    self.assert_blocked(self.guard._on_pre_tool_call("kanban_complete", args))

    def test_runtime_has_no_write_execution_or_network_primitives(self):
        import ast

        tree = ast.parse((PLUGIN / "__init__.py").read_text())
        forbidden_imports = {"subprocess", "shutil", "socket", "urllib", "requests", "httpx", "aiohttp"}
        forbidden_calls = {
            "eval", "exec", "compile", "open",
            "os.system", "os.popen", "os.remove", "os.unlink", "os.rename", "os.replace",
            "Path.write_text", "Path.write_bytes", "Path.unlink", "Path.rename", "Path.replace",
            "Path.mkdir", "Path.rmdir", "Path.chmod",
        }
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    if isinstance(func.value, ast.Name):
                        calls.add(f"{func.value.id}.{func.attr}")
                    calls.add(func.attr)
        self.assertFalse(imports & forbidden_imports, imports & forbidden_imports)
        self.assertFalse(calls & forbidden_calls, calls & forbidden_calls)


if __name__ == "__main__":
    unittest.main()
