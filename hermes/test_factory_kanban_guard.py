#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes" / "plugins" / "factory-repository-readonly"


def load_guard():
    spec = importlib.util.spec_from_file_location("factory_kanban_guard_test", PLUGIN / "kanban_guard.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class KanbanWorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = load_guard()

    def env(self, root: Path, profile: str = "repository-analyst"):
        return patch.dict(os.environ, {
            "HERMES_KANBAN_TASK": "t_guard",
            "HERMES_KANBAN_WORKSPACE": str(root),
            "HERMES_PROFILE": profile,
        }, clear=True)

    def test_non_complete_and_other_profiles_are_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root):
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_comment", {"text": "/etc/passwd"}))
            with self.env(root, profile="coder"):
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"summary": "/etc/passwd"}))

    def test_missing_binding_fails_closed_for_repository_analyst_completion(self):
        with patch.dict(os.environ, {"HERMES_PROFILE": "repository-analyst"}, clear=True):
            result = self.guard.on_pre_tool_call("kanban_complete", {"summary": "done"})
            self.assertEqual(result["action"], "block")

    def test_allows_relative_and_absolute_artifacts_inside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            artifact = root / "reports" / "review.md"
            artifact.write_text("ok\n")
            with self.env(root):
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"artifacts": ["reports/review.md"]}))
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"artifacts": [str(artifact)]}))
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"artifacts": "reports/review.md"}))

    def test_blocks_artifact_outside_workspace_or_missing_or_symlink(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td); outside = Path(od) / "notes.txt"; outside.write_text("secret\n")
            (root / "link.txt").symlink_to(outside)
            with self.env(root):
                for artifact in (str(outside), "../escape.txt", "missing.txt", "link.txt"):
                    result = self.guard.on_pre_tool_call("kanban_complete", {"artifacts": [artifact]})
                    self.assertEqual(result["action"], "block", artifact)
                for malformed in (1, True, {"path": "reports/review.md"}, [123]):
                    result = self.guard.on_pre_tool_call("kanban_complete", {"artifacts": malformed})
                    self.assertEqual(result["action"], "block", repr(malformed))

    def test_blocks_all_absolute_and_home_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "src" / "app.py"; inside.parent.mkdir(); inside.write_text("x\n")
            with self.env(root):
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"summary": f"checked {inside}"}))
                samples = (
                    "/etc/passwd",
                    "/home/example/private.txt",
                    "/usr/share/doc/example/README.md",
                    "/run/user/1000/report.md",
                    "/dev/shm/report.md",
                    "/data/report.md",
                    "/app/report.md",
                    "/code/report.md",
                    "/projects/report.md",
                    "~/outside.md",
                    r"C:\Users\me\report.md",
                    "C:/Users/me/report.md",
                )
                for path in samples:
                    result = self.guard.on_pre_tool_call("kanban_complete", {"summary": f"see {path}"})
                    self.assertEqual(result["action"], "block", path)

    def test_does_not_treat_http_urls_as_local_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root):
                for url in (
                    "https://example.com/home/user/file.md",
                    "http://example.com/tmp/a.md",
                ):
                    self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"summary": url}), url)

    def test_scans_nested_string_arguments_for_schema_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root):
                for value in (
                    {"future_field": {"nested": ["/etc/shadow"]}},
                    {"metadata": {"result": {"details": "/usr/share/doc/example/README.md"}}},
                    {"future_field": [{"home": "~/outside.md"}]},
                ):
                    result = self.guard.on_pre_tool_call("kanban_complete", value)
                    self.assertEqual(result["action"], "block")

    def test_deep_or_oversized_nested_args_fail_closed_without_recursion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deep: object = "/usr/share/doc/example/README.md"
            for _ in range(self.guard.MAX_ARG_DEPTH + 20):
                deep = {"x": deep}
            many = {"metadata": ["ok"] * (self.guard.MAX_ARG_NODES + 20)}
            with self.env(root):
                for args in ({"metadata": deep}, many):
                    result = self.guard.on_pre_tool_call("kanban_complete", args)
                    self.assertEqual(result["action"], "block")

    def test_internal_validation_exception_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root), patch.object(self.guard, "_outside_local_paths", side_effect=RuntimeError("boom")):
                result = self.guard.on_pre_tool_call("kanban_complete", {"summary": "done"})
                self.assertEqual(result["action"], "block")

    def test_invalid_args_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root):
                for args in (None, [], "x"):
                    result = self.guard.on_pre_tool_call("kanban_complete", args)
                    self.assertEqual(result["action"], "block")


if __name__ == "__main__":
    unittest.main()
