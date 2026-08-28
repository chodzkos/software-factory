#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes" / "plugins" / "factory-repository-readonly"


def _install_registry_stub():
    tools = types.ModuleType("tools")
    registry = types.ModuleType("tools.registry")
    registry.tool_result = lambda payload: json.dumps({"result": payload}, ensure_ascii=False)
    registry.tool_error = lambda message, **kw: json.dumps({"error": str(message)}, ensure_ascii=False)
    tools.registry = registry
    sys.modules["tools"] = tools
    sys.modules["tools.registry"] = registry


def load_plugin():
    _install_registry_stub()
    name = "factory_repository_readonly_testpkg"
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module, sys.modules[name + ".repository_tools"]


def decode(value: str):
    return json.loads(value)


class FakeContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class RepositoryReadonlyPluginTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.rt = load_plugin()

    def bound_env(self, root: Path):
        return patch.dict(
            os.environ,
            {
                "HERMES_KANBAN_TASK": "t_readonly_test",
                "HERMES_KANBAN_WORKSPACE": str(root),
                "HERMES_PROFILE": "repository-analyst",
            },
            clear=False,
        )

    def test_registers_exact_readonly_toolset(self):
        ctx = FakeContext()
        self.plugin.register(ctx)
        self.assertEqual(
            [tool["name"] for tool in ctx.tools],
            ["factory_repo_map", "factory_repo_read", "factory_repo_search"],
        )
        self.assertEqual({tool["toolset"] for tool in ctx.tools}, {"factory-repository-readonly"})
        for tool in ctx.tools:
            self.assertIs(tool["check_fn"], self.rt.check_available)
            self.assertFalse(tool["schema"]["parameters"].get("additionalProperties", True))

    def test_missing_or_wrong_worker_binding_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.rt.check_available())
            self.assertIn("error", decode(self.rt.handle_read({"path": "x.py"})))
        with tempfile.TemporaryDirectory() as td:
            env = {
                "HERMES_KANBAN_TASK": "t",
                "HERMES_KANBAN_WORKSPACE": td,
                "HERMES_PROFILE": "coder",
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertFalse(self.rt.check_available())

    def test_map_is_workspace_bound_and_rejects_option_or_parent_targets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "ok.py").write_text("def visible(): pass\n")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "leak.py").write_text("def must_not_leak(): pass\n")
            with self.bound_env(root):
                good = decode(self.rt.handle_map({"target": "."}))
                text = good["result"]["map"]
                self.assertIn("src/ok.py", text)
                self.assertNotIn("leak.py", text)
                for target in ("../", "/", "--workspace=/", "-h"):
                    self.assertIn("error", decode(self.rt.handle_map({"target": target})), target)

    def test_read_refuses_secret_hidden_binary_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td)
            outside = Path(od)
            (root / "safe.py").write_text("line1\nline2\nline3\n")
            (root / ".npmrc").write_text("token=secret\n")
            (root / "credentials.json").write_text('{"token":"secret"}\n')
            (root / "binary.py").write_bytes(b"abc\x00secret")
            (root / "bad.py").write_bytes(b"\xff\xfe")
            (outside / "outside.py").write_text("SECRET_OUTSIDE\n")
            (root / "link.py").symlink_to(outside / "outside.py")
            with self.bound_env(root):
                good = decode(self.rt.handle_read({"path": "safe.py", "start_line": 2, "max_lines": 1}))["result"]
                self.assertEqual(good["content"], "line2")
                self.assertEqual(good["start_line"], 2)
                self.assertTrue(good["truncated"])
                for path in (".npmrc", "credentials.json", "binary.py", "bad.py", "link.py", "../outside.py", "/etc/passwd"):
                    self.assertIn("error", decode(self.rt.handle_read({"path": path})), path)

    def test_search_is_literal_bounded_and_skips_secret_generated_and_symlink(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td)
            outside = Path(od)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("needle here\nNEEDLE there\n")
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text("name: needle-ci\n")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "leak.js").write_text("needle leaked\n")
            (root / ".env").write_text("needle SECRET\n")
            (outside / "outside.py").write_text("needle OUTSIDE\n")
            (root / "outside-link.py").symlink_to(outside / "outside.py")
            with self.bound_env(root):
                result = decode(self.rt.handle_search({"query": "needle", "max_results": 10}))["result"]
                paths = {item["path"] for item in result["results"]}
                self.assertIn("src/app.py", paths)
                self.assertIn(".github/workflows/ci.yml", paths)
                self.assertNotIn("node_modules/leak.js", paths)
                self.assertNotIn(".env", paths)
                self.assertNotIn("outside-link.py", paths)
                self.assertLessEqual(len(result["results"]), 10)
                self.assertIn("error", decode(self.rt.handle_search({"query": "x" * 257})))

    def test_plugin_contains_no_execution_or_write_primitives(self):
        text = (PLUGIN / "repository_tools.py").read_text()
        for forbidden in (
            "subprocess", "os.system", "os.popen", "eval(", "exec(",
            "write_text(", "write_bytes(", "unlink(", "remove(", "rename(", "replace(",
            "socket", "urllib", "requests",
        ):
            self.assertNotIn(forbidden, text)

    def test_vendored_mapper_is_byte_identical_to_reviewed_skill_helper(self):
        canonical = ROOT / "skills" / "custom" / "factory-repo-map" / "scripts" / "repo_map.py"
        self.assertEqual((PLUGIN / "repo_map.py").read_bytes(), canonical.read_bytes())


if __name__ == "__main__":
    unittest.main()
