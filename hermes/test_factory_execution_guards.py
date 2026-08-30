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

FAKE_CLAUDE = ("/opt/claude-code/claude", "a" * 64)
CODER_CMD = (
    "claude -p 'do work' --model sonnet --output-format json "
    f"--allowedTools '{GUARD.CODER_CLAUDE_TOOLS}' --max-turns 2"
)
REVIEW_CMD = (
    "claude -p 'review' --model sonnet --output-format json "
    f"--allowedTools '{GUARD.READONLY_CLAUDE_TOOLS}' --max-turns 2"
)


class ExecutionGuardTests(unittest.TestCase):
    def _guard_patches(self):
        return (
            patch.object(GUARD, "_canonical_claude_identity", return_value=FAKE_CLAUDE),
            patch.object(GUARD, "_workspace", return_value="/repo/.worktrees/t_guard"),
        )

    def _call(self, profile: str, tool: str, args=None, *, task="t_guard"):
        env = {
            "HERMES_PROFILE": profile,
            "HERMES_KANBAN_TASK": task,
            "HERMES_KANBAN_RUN_ID": "77",
        }
        p1, p2 = self._guard_patches()
        with patch.dict(os.environ, env, clear=False), p1, p2:
            return GUARD.on_pre_tool_call(tool_name=tool, args=args or {}, task_id=task)

    def _post(
        self,
        profile: str,
        *,
        command: str,
        terminal_output: str,
        exit_code: int = 0,
        hook_task_id: str = "t_guard",
        kanban_task_id: str = "t_guard",
    ):
        env = {
            "HERMES_PROFILE": profile,
            "HERMES_KANBAN_TASK": kanban_task_id,
            "HERMES_KANBAN_RUN_ID": "77",
        }
        result = json.dumps({"output": terminal_output, "exit_code": exit_code})
        p1, p2 = self._guard_patches()
        with patch.dict(os.environ, env, clear=False), p1, p2:
            GUARD.on_post_tool_call(
                tool_name="terminal",
                args={"command": command},
                result=result,
                task_id=hook_task_id,
                duration_ms=1,
            )

    def test_runtime_controller_only_allows_exact_wrapper_operations(self):
        wrapper = str(Path.home() / ".hermes/profiles/runtime-controller/kanban_runtime_cli.sh")
        self.assertIsNone(self._call("runtime-controller", "terminal", {"command": f"{wrapper} validate-routing --task-body x"}))
        for command in (
            "git status",
            "python3 -c 'print(1)'",
            "hermes kanban show t_x",
            "curl https://example.com",
            f"{wrapper} show t_x ; id",
            f"{wrapper} validate-handoff --actual-json x --implementer-profile coder --reviewer-profile reviewer-gpt",
            f"{wrapper} unknown t_x",
        ):
            with self.subTest(command=command):
                blocked = self._call("runtime-controller", "terminal", {"command": command})
                self.assertEqual(blocked and blocked.get("action"), "block")
        blocked = self._call("runtime-controller", "read_file", {"path": "x"})
        self.assertEqual(blocked and blocked.get("action"), "block")

    def test_claude_profiles_terminal_is_claude_only(self):
        for profile in ("coder-claude", "reviewer-claude", "architect-claude-opus"):
            for command in (
                "find . -delete",
                "find . -exec sh -c 'touch /tmp/x' {} +",
                "git diff --output=/tmp/x",
                "git status",
                "python3 -c 'open(\"/tmp/x\",\"w\").write(\"x\")'",
                "grep x file",
            ):
                with self.subTest(profile=profile, command=command):
                    blocked = self._call(profile, "terminal", {"command": command})
                    self.assertEqual(blocked and blocked.get("action"), "block")

    def test_coder_claude_requires_exact_canonical_command_schema(self):
        self.assertIsNone(self._call("coder-claude", "terminal", {"command": CODER_CMD}))
        bad_commands = (
            CODER_CMD.replace("--model sonnet", "--model opus"),
            CODER_CMD.replace("claude ", "./claude ", 1),
            CODER_CMD.replace("claude ", "/tmp/claude ", 1),
            CODER_CMD + " --model opus",
            CODER_CMD.replace(f" --allowedTools '{GUARD.CODER_CLAUDE_TOOLS}'", ""),
            CODER_CMD + " --dangerously-skip-permissions",
            CODER_CMD + " --settings /tmp/settings.json",
            CODER_CMD + " --resume previous",
        )
        for command in bad_commands:
            with self.subTest(command=command):
                blocked = self._call("coder-claude", "terminal", {"command": command})
                self.assertEqual(blocked and blocked.get("action"), "block")
        for tool in ("write_file", "patch", "execute_code"):
            blocked = self._call("coder-claude", tool, {"path": "x", "content": "x"})
            self.assertEqual(blocked and blocked.get("action"), "block")

    def test_reviewer_and_architect_require_exact_readonly_claude_tools(self):
        self.assertIsNone(self._call("reviewer-claude", "terminal", {"command": REVIEW_CMD}))
        opus = REVIEW_CMD.replace("--model sonnet", "--model opus")
        self.assertIsNone(self._call("architect-claude-opus", "terminal", {"command": opus}))
        for profile, base in (("reviewer-claude", REVIEW_CMD), ("architect-claude-opus", opus)):
            bad = (
                base.replace(f"--allowedTools '{GUARD.READONLY_CLAUDE_TOOLS}'", "--allowedTools 'Read,Edit'"),
                base.replace(f" --allowedTools '{GUARD.READONLY_CLAUDE_TOOLS}'", ""),
                base + " --dangerously-skip-permissions",
                base + " --allowedTools 'Read,Write'",
            )
            for command in bad:
                with self.subTest(profile=profile, command=command):
                    blocked = self._call(profile, "terminal", {"command": command})
                    self.assertEqual(blocked and blocked.get("action"), "block")

    def test_coder_review_request_requires_durable_success_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")
                claude_result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123", "result": "done"})
                self._post("coder-claude", command=CODER_CMD, terminal_output=claude_result)
                self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
                evidence = list(Path(td).glob("*.json"))
                self.assertEqual(len(evidence), 1)
                payload = json.loads(evidence[0].read_text())
                self.assertEqual(payload["schema"], 2)
                self.assertEqual(payload["task_id"], "t_guard")
                self.assertEqual(payload["run_id"], "77")
                self.assertEqual(payload["workspace"], "/repo/.worktrees/t_guard")
                self.assertEqual(payload["claude_binary"], FAKE_CLAUDE[0])
                self.assertEqual(payload["claude_binary_sha256"], FAKE_CLAUDE[1])

    def test_forged_or_stale_evidence_cannot_unlock_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(GUARD, "EVIDENCE_ROOT", root):
                fake = {
                    "schema": 2,
                    "profile": "coder-claude",
                    "task_id": "t_guard",
                    "run_id": "77",
                    "model_class": "sonnet",
                    "workspace": "/repo/.worktrees/t_guard",
                    "claude_binary": FAKE_CLAUDE[0],
                    "claude_binary_sha256": "b" * 64,
                    "session_id": "forged",
                    "command_sha256": "c" * 64,
                    "success": True,
                }
                (root / "t_guard__77__coder-claude.json").write_text(json.dumps(fake))
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_kanban_task_env_overrides_hook_session_id_for_evidence_binding(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                result = json.dumps({"type": "result", "subtype": "success", "session_id": "claude-session-456", "result": "done"})
                self._post(
                    "coder-claude",
                    command=CODER_CMD,
                    terminal_output=result,
                    hook_task_id="20260830_worker_session",
                    kanban_task_id="t_guard",
                )
                expected = Path(td) / "t_guard__77__coder-claude.json"
                unexpected = Path(td) / "20260830_worker_session__77__coder-claude.json"
                self.assertTrue(expected.is_file())
                self.assertFalse(unexpected.exists())

    def test_failed_or_malformed_terminal_result_does_not_create_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                failed = json.dumps({"type": "result", "subtype": "error_max_turns", "session_id": "sess-x"})
                self._post("coder-claude", command=CODER_CMD, terminal_output=failed)
                self._post("coder-claude", command=CODER_CMD, terminal_output="not-json")
                success = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-ok"})
                self._post("coder-claude", command=CODER_CMD, terminal_output=success, exit_code=1)
                self.assertEqual(list(Path(td).glob("*.json")), [])

    def test_non_terminal_post_tool_result_cannot_forge_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                env = {"HERMES_PROFILE": "coder-claude", "HERMES_KANBAN_TASK": "t_guard", "HERMES_KANBAN_RUN_ID": "77"}
                with patch.dict(os.environ, env, clear=False):
                    GUARD.on_post_tool_call(
                        tool_name="read_file",
                        args={"command": CODER_CMD},
                        result=json.dumps({"output": json.dumps({"type":"result","subtype":"success","session_id":"x"}), "exit_code": 0}),
                        task_id="20260830_worker_session",
                    )
                self.assertEqual(list(Path(td).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
