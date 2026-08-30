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
WORKSPACE = "/repo/.worktrees/t_guard"
GIT_STATE = ("1" * 40, "2" * 64)
PROMPT = f"Work Kanban task t_guard run 77 in {WORKSPACE}."
CODER_CMD = (
    f"claude -p '{PROMPT}' --model sonnet --output-format json "
    f"--allowedTools '{GUARD.CODER_CLAUDE_TOOLS}' --max-turns 2"
)
REVIEW_CMD = (
    f"claude -p 'Review Kanban task t_guard run 77 in {WORKSPACE}.' --model sonnet --output-format json "
    f"--allowedTools '{GUARD.READONLY_CLAUDE_TOOLS}' --max-turns 2"
)


class ExecutionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        GUARD._PENDING_ATTESTATIONS.clear()
        GUARD._COMPLETED_ATTESTATIONS.clear()

    def _guard_patches(self, *, git_state=GIT_STATE):
        return (
            patch.object(GUARD, "_canonical_claude_identity", return_value=FAKE_CLAUDE),
            patch.object(GUARD, "_workspace", return_value=WORKSPACE),
            patch.object(GUARD, "_git_workspace_state", return_value=git_state),
        )

    def _env(self, profile: str, task: str = "t_guard") -> dict[str, str]:
        return {"HERMES_PROFILE": profile, "HERMES_KANBAN_TASK": task, "HERMES_KANBAN_RUN_ID": "77"}

    def _call(self, profile: str, tool: str, args=None, *, task="t_guard", git_state=GIT_STATE):
        p1, p2, p3 = self._guard_patches(git_state=git_state)
        with patch.dict(os.environ, self._env(profile, task), clear=False), p1, p2, p3:
            return GUARD.on_pre_tool_call(tool_name=tool, args=args or {}, task_id=task)

    def _execute_claude(
        self,
        profile: str,
        *,
        command: str,
        terminal_output: str,
        exit_code: int = 0,
        hook_task_id: str = "t_guard",
        kanban_task_id: str = "t_guard",
        before_state=GIT_STATE,
        after_state=GIT_STATE,
    ):
        env = self._env(profile, kanban_task_id)
        result = json.dumps({"output": terminal_output, "exit_code": exit_code})
        p1, p2, p3 = self._guard_patches(git_state=before_state)
        with patch.dict(os.environ, env, clear=False), p1, p2, p3:
            pre = GUARD.on_pre_tool_call(tool_name="terminal", args={"command": command}, task_id=hook_task_id)
        if pre is not None:
            return pre
        p1, p2, p3 = self._guard_patches(git_state=after_state)
        with patch.dict(os.environ, env, clear=False), p1, p2, p3:
            GUARD.on_post_tool_call(
                tool_name="terminal", args={"command": command}, result=result,
                task_id=hook_task_id, duration_ms=1,
            )
        return None

    def test_runtime_controller_only_allows_exact_wrapper_operations(self):
        wrapper = str(Path.home() / ".hermes/profiles/runtime-controller/kanban_runtime_cli.sh")
        self.assertIsNone(self._call("runtime-controller", "terminal", {"command": f"{wrapper} validate-routing --task-body x"}))
        for command in (
            "git status", "python3 -c 'print(1)'", "hermes kanban show t_x", "curl https://example.com",
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
                "find . -delete", "find . -exec sh -c 'touch /tmp/x' {} +", "git diff --output=/tmp/x",
                "git status", "python3 -c 'open(\"/tmp/x\",\"w\").write(\"x\")'", "grep x file",
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
            CODER_CMD.replace("run 77", "run 88"),
            CODER_CMD.replace(WORKSPACE, "/tmp/wrong-worktree"),
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

    def test_coder_review_requires_successful_in_process_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")
                result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123", "result": "done"})
                self.assertIsNone(self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result))
                self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
                evidence = list(Path(td).glob("*.json"))
                self.assertEqual(len(evidence), 1)
                payload = json.loads(evidence[0].read_text())
                self.assertEqual(payload["schema"], 4)
                self.assertEqual(payload["task_id"], "t_guard")
                self.assertEqual(payload["run_id"], "77")
                self.assertEqual(payload["workspace"], WORKSPACE)
                self.assertEqual(payload["claude_binary"], FAKE_CLAUDE[0])
                self.assertEqual(payload["claude_binary_sha256"], FAKE_CLAUDE[1])
                self.assertEqual(payload["git_head_before"], GIT_STATE[0])
                self.assertEqual(payload["git_head_after"], GIT_STATE[0])
                self.assertEqual(payload["workspace_state_after_sha256"], GIT_STATE[1])
                self.assertRegex(payload["attestation_id"], r"^[0-9a-f]{64}$")

    def test_forged_evidence_without_memory_attestation_cannot_unlock_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(GUARD, "EVIDENCE_ROOT", root):
                fake = {
                    "schema": 4, "profile": "coder-claude", "task_id": "t_guard", "run_id": "77",
                    "model_class": "sonnet", "workspace": WORKSPACE, "claude_binary": FAKE_CLAUDE[0],
                    "claude_binary_sha256": FAKE_CLAUDE[1], "session_id": "forged", "command_sha256": "c" * 64,
                    "attestation_id": "d" * 64, "git_head_after": GIT_STATE[0],
                    "workspace_state_after_sha256": GIT_STATE[1], "success": True,
                }
                (root / "t_guard__77__coder-claude.json").write_text(json.dumps(fake))
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_workspace_mutation_after_evidence_invalidates_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123"})
                self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result)
                changed = (GIT_STATE[0], "9" * 64)
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"}, git_state=changed)
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_new_claude_command_invalidates_prior_completed_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123"})
                self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result)
                self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
                self.assertIsNone(self._call("coder-claude", "terminal", {"command": CODER_CMD}))
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_kanban_task_env_overrides_hook_session_id_for_evidence_binding(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                result = json.dumps({"type": "result", "subtype": "success", "session_id": "claude-session-456", "result": "done"})
                self._execute_claude(
                    "coder-claude", command=CODER_CMD, terminal_output=result,
                    hook_task_id="20260830_worker_session", kanban_task_id="t_guard",
                )
                self.assertTrue((Path(td) / "t_guard__77__coder-claude.json").is_file())
                self.assertFalse((Path(td) / "20260830_worker_session__77__coder-claude.json").exists())

    def test_failed_or_malformed_terminal_result_does_not_create_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                failed = json.dumps({"type": "result", "subtype": "error_max_turns", "session_id": "sess-x"})
                self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=failed)
                self.assertEqual(list(Path(td).glob("*.json")), [])
                GUARD._PENDING_ATTESTATIONS.clear()
                self._execute_claude("coder-claude", command=CODER_CMD, terminal_output="not-json")
                self.assertEqual(list(Path(td).glob("*.json")), [])
                GUARD._PENDING_ATTESTATIONS.clear()
                success = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-ok"})
                self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=success, exit_code=1)
                self.assertEqual(list(Path(td).glob("*.json")), [])

    def test_non_terminal_post_tool_result_cannot_forge_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
                env = self._env("coder-claude")
                p1, p2, p3 = self._guard_patches()
                with patch.dict(os.environ, env, clear=False), p1, p2, p3:
                    GUARD.on_post_tool_call(
                        tool_name="read_file", args={"command": CODER_CMD},
                        result=json.dumps({"output": json.dumps({"type":"result","subtype":"success","session_id":"x"}), "exit_code": 0}),
                        task_id="20260830_worker_session",
                    )
                self.assertEqual(list(Path(td).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
