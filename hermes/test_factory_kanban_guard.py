#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
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

    def test_blocks_artifact_outside_workspace_or_missing_or_symlink(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td); outside = Path(od) / "notes.txt"; outside.write_text("secret\n")
            (root / "link.txt").symlink_to(outside)
            with self.env(root):
                for artifact in (str(outside), "../escape.txt", "missing.txt", "link.txt"):
                    result = self.guard.on_pre_tool_call("kanban_complete", {"artifacts": [artifact]})
                    self.assertEqual(result["action"], "block", artifact)

    def test_blocks_absolute_local_paths_in_summary_outside_workspace(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            root = Path(td); outside = Path(od) / "notes.txt"; outside.write_text("secret\n")
            inside = root / "src" / "app.py"; inside.parent.mkdir(); inside.write_text("x\n")
            with self.env(root):
                self.assertIsNone(self.guard.on_pre_tool_call("kanban_complete", {"summary": f"checked {inside}"}))
                for text in (f"see {outside}", "copied /etc/passwd", "result: /home/example/private.txt"):
                    result = self.guard.on_pre_tool_call("kanban_complete", {"summary": text})
                    self.assertEqual(result["action"], "block", text)

    def test_scans_nested_string_arguments_for_schema_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.env(root):
                result = self.guard.on_pre_tool_call("kanban_complete", {"future_field": {"nested": ["/etc/shadow"]}})
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
