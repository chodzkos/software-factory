from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


GUARD_PATH = Path(__file__).parent / "plugins" / "factory-execution-guards" / "guard.py"
SPEC = importlib.util.spec_from_file_location("factory_execution_guard", GUARD_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ExecutionGuardTests(unittest.TestCase):
    def _call(self, profile: str, tool: str, args=None, *, task="t_guard"):
        env = {
            "HERMES_PROFILE": profile,
            "HERMES_KANBAN_TASK": task,
            "HERMES_KANBAN_RUN_ID": "77",
        }
        with patch.dict(os.environ, env, clear=False):
            return GUARD.on_pre_tool_call(tool_name=tool, args=args or {}, task_id=task)

    def test_runtime_controller_only_allows_exact_wrapper_operations(self):
        wrapper = str(Path.home() / ".hermes/profiles/runtime-controller/kanban_runtime_cli.sh")
        self.assertIsNone(self._call("runtime-controller", "terminal", {"command": f"{wrapper} validate-routing --task-body x"}))
        for command in (
            "git status",
            "python3 -c 'print(1)'",
            "hermes kanban show t_x",
            f"{wrapper} show t_x ; id",
            f"{wrapper} unknown t_x",
        ):
            with self.subTest(command=command):
                blocked = self._call("runtime-controller", "terminal", {"command": command})
                self.assertEqual(blocked and blocked.get("action"), "block")
        blocked = self._call("runtime-controller", "read_file", {"path": "x"})
        self.assertEqual(blocked and blocked.get("action"), "block")

    def test_coder_claude_blocks_direct_write_and_wrong_backend_command(self):
        blocked = self._call("coder-claude", "write_file", {"path": "x", "content": "x"})
        self.assertEqual(blocked and blocked.get("action"), "block")
        canonical = "claude -p 'do work' --model sonnet --output-format json --max-turns 2"
        self.assertIsNone(self._call("coder-claude", "terminal", {"command": canonical}))
        wrong = "claude -p 'do work' --model opus --output-format json"
        blocked = self._call("coder-claude", "terminal", {"command": wrong})
        self.assertEqual(blocked and blocked.get("action"), "block")

    def test_reviewer_claude_refuses_write_capability(self):
        readonly = "claude -p 'review' --model sonnet --output-format json --allowedTools 'Read,Bash(git diff *)'"
        self.assertIsNone(self._call("reviewer-claude", "terminal", {"command": readonly}))
        writing = "claude -p 'review' --model sonnet --output-format json --allowedTools 'Read,Write'"
        blocked = self._call("reviewer-claude", "terminal", {"command": writing})
        self.assertEqual(blocked and blocked.get("action"), "block")

    def test_coder_review_request_requires_durable_success_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")
                env = {
                    "HERMES_PROFILE": "coder-claude",
                    "HERMES_KANBAN_TASK": "t_guard",
                    "HERMES_KANBAN_RUN_ID": "77",
                }
                result = json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-123",
                    "result": "done",
                })
                command = "claude -p 'do work' --model sonnet --output-format json --max-turns 2"
                with patch.dict(os.environ, env, clear=False):
                    GUARD.on_transform_terminal_output(
                        command=command,
                        output=result,
                        exit_code=0,
                        task_id="t_guard",
                    )
                self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
                evidence = list(Path(td).glob("*.json"))
                self.assertEqual(len(evidence), 1)
                payload = json.loads(evidence[0].read_text())
                self.assertEqual(payload["session_id"], "sess-123")
                self.assertEqual(payload["model_class"], "sonnet")

    def test_failed_claude_result_does_not_create_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                env = {
                    "HERMES_PROFILE": "coder-claude",
                    "HERMES_KANBAN_TASK": "t_guard",
                    "HERMES_KANBAN_RUN_ID": "77",
                }
                with patch.dict(os.environ, env, clear=False):
                    GUARD.on_transform_terminal_output(
                        command="claude -p 'x' --model sonnet --output-format json",
                        output=json.dumps({"type": "result", "subtype": "error_max_turns", "session_id": "sess-x"}),
                        exit_code=0,
                        task_id="t_guard",
                    )
                self.assertEqual(list(Path(td).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
