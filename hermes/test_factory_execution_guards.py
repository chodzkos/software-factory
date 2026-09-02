from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).parent / "plugins" / "factory-execution-guards"
SPEC = importlib.util.spec_from_file_location(
    "factory_execution_guards_effective",
    PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(PACKAGE_DIR)],
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN
SPEC.loader.exec_module(PLUGIN)
GUARD = PLUGIN._guard

FAKE_CLAUDE = ("/opt/claude-code/claude", "a" * 64)
WORKSPACE = "/tmp"
CONTENT_STATE = ("1" * 40, "2" * 64)
PROMPT = f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {WORKSPACE}\nPerform the assigned implementation."
REVIEW_PROMPT = f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {WORKSPACE}\nReview the assigned change."
CODER_TOOLS = PLUGIN._coder_tools(WORKSPACE)
CODER_CMD = (
    f"claude -p '{PROMPT}' --model sonnet --output-format json --safe-mode "
    f"--permission-mode dontAsk --allowedTools '{CODER_TOOLS}' --max-turns 2"
)
REVIEW_CMD = (
    f"claude -p '{REVIEW_PROMPT}' --model sonnet --output-format json --safe-mode "
    f"--permission-mode plan --allowedTools '{PLUGIN._READONLY_TOOLS}' --max-turns 2"
)


class ExecutionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        GUARD._PENDING_ATTESTATIONS.clear()
        GUARD._COMPLETED_ATTESTATIONS.clear()

    def _guard_patches(self, *, content_state=CONTENT_STATE):
        return (
            patch.object(GUARD, "_canonical_claude_identity", return_value=FAKE_CLAUDE),
            patch.object(GUARD, "_workspace", return_value=WORKSPACE),
            patch.object(GUARD, "_workspace_content_state", return_value=content_state),
            patch.object(PLUGIN.Path, "cwd", return_value=Path(WORKSPACE)),
        )

    def _env(self, profile: str, task: str = "t_guard") -> dict[str, str]:
        return {"HERMES_PROFILE": profile, "HERMES_KANBAN_TASK": task, "HERMES_KANBAN_RUN_ID": "77", "HERMES_KANBAN_WORKSPACE": WORKSPACE}

    def _call(self, profile: str, tool: str, args=None, *, task="t_guard", content_state=CONTENT_STATE):
        effective_args = dict(args or {})
        if profile in {"coder-claude", "reviewer-claude", "architect-claude-opus"} and tool == "terminal":
            effective_args.setdefault("workdir", WORKSPACE)
        patches=self._guard_patches(content_state=content_state)
        with patch.dict(os.environ, self._env(profile, task), clear=False), patches[0], patches[1], patches[2], patches[3]:
            return PLUGIN.on_pre_tool_call(tool_name=tool, args=effective_args, task_id=task)

    def _execute_claude(
        self,
        profile: str,
        *,
        command: str,
        terminal_output: str,
        exit_code: int = 0,
        hook_task_id: str = "t_guard",
        kanban_task_id: str = "t_guard",
        before_state=CONTENT_STATE,
        after_state=CONTENT_STATE,
    ):
        env = self._env(profile, kanban_task_id)
        result = json.dumps({"output": terminal_output, "exit_code": exit_code, "cwd": WORKSPACE})
        terminal_args = {"command": command, "workdir": WORKSPACE}
        patches=self._guard_patches(content_state=before_state)
        with patch.dict(os.environ, env, clear=False), patches[0], patches[1], patches[2], patches[3]:
            pre = PLUGIN.on_pre_tool_call(tool_name="terminal", args=terminal_args, task_id=hook_task_id)
        if pre is not None:
            return pre
        patches=self._guard_patches(content_state=after_state)
        with patch.dict(os.environ, env, clear=False), patches[0], patches[1], patches[2], patches[3]:
            PLUGIN.on_post_tool_call(
                tool_name="terminal", args=terminal_args, result=result,
                task_id=hook_task_id, duration_ms=1,
            )
        return None

    def test_runtime_controller_only_allows_exact_wrapper_operations(self):
        wrapper = str(Path.home() / ".hermes/profiles/runtime-controller/kanban_runtime_cli.sh")
        self.assertIsNone(self._call("runtime-controller", "terminal", {"command": f"{wrapper} validate-routing-live --task-id t_x"}))
        for command in (
            "git status", "python3 -c 'print(1)'", "hermes kanban show t_x", "curl https://example.com",
            f"{wrapper} show t_x ; id",
            f"{wrapper} show t_x\nid",
            f"{wrapper} validate-routed-handoff --actual-json x",
            f"{wrapper} validate-routing-live --actual-json x",
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

    def test_coder_claude_requires_workspace_scoped_edit_schema(self):
        self.assertEqual(CODER_TOOLS, "Read,Glob,Grep,Edit(//tmp/**)")
        self.assertIsNone(self._call("coder-claude", "terminal", {"command": CODER_CMD}))
        bad_commands = (
            CODER_CMD.replace("--model sonnet", "--model opus"),
            CODER_CMD.replace("claude ", "./claude ", 1),
            CODER_CMD.replace("claude ", "/tmp/claude ", 1),
            CODER_CMD + " --model opus",
            CODER_CMD.replace(" --safe-mode", ""),
            CODER_CMD.replace("--permission-mode dontAsk", "--permission-mode acceptEdits"),
            CODER_CMD.replace(f" --allowedTools '{CODER_TOOLS}'", ""),
            CODER_CMD.replace(CODER_TOOLS, "Read,Write,Edit,Glob,Grep"),
            CODER_CMD.replace(CODER_TOOLS, "Read,Glob,Grep,Edit(//tmp-other/**)"),
            CODER_CMD.replace(CODER_TOOLS, CODER_TOOLS + ",Bash"),
            CODER_CMD + " --dangerously-skip-permissions",
            CODER_CMD + " --settings /tmp/settings.json",
            CODER_CMD + " --resume previous",
            CODER_CMD.replace("TASK_ID: t_guard", "TASK_ID: t_guard_evil"),
            CODER_CMD.replace("RUN_ID: 77", "RUN_ID: 177"),
            CODER_CMD.replace(f"WORKSPACE: {WORKSPACE}", f"WORKSPACE: {WORKSPACE}/evil"),
        )
        for command in bad_commands:
            with self.subTest(command=command):
                blocked = self._call("coder-claude", "terminal", {"command": command})
                self.assertEqual(blocked and blocked.get("action"), "block")
        for tool in ("write_file", "patch", "execute_code"):
            blocked = self._call("coder-claude", tool, {"path": "x", "content": "x"})
            self.assertEqual(blocked and blocked.get("action"), "block")

    def test_reviewer_and_architect_are_shell_free_plan_mode(self):
        self.assertEqual(PLUGIN._READONLY_TOOLS, "Read,Glob,Grep")
        self.assertIsNone(self._call("reviewer-claude", "terminal", {"command": REVIEW_CMD}))
        opus = REVIEW_CMD.replace("--model sonnet", "--model opus")
        self.assertIsNone(self._call("architect-claude-opus", "terminal", {"command": opus}))
        for profile, base in (("reviewer-claude", REVIEW_CMD), ("architect-claude-opus", opus)):
            bad = (
                base.replace("--allowedTools 'Read,Glob,Grep'", "--allowedTools 'Read,Edit'"),
                base.replace(" --allowedTools 'Read,Glob,Grep'", ""),
                base.replace("--permission-mode plan", "--permission-mode acceptEdits"),
                base.replace(" --safe-mode", ""),
                base + " --dangerously-skip-permissions",
                base + " --allowedTools 'Read,Write'",
            )
            for command in bad:
                with self.subTest(profile=profile, command=command):
                    blocked = self._call(profile, "terminal", {"command": command})
                    self.assertEqual(blocked and blocked.get("action"), "block")

    def test_coder_review_requires_successful_in_process_attestation(self):
        with tempfile.TemporaryDirectory() as td, patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
            blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
            self.assertEqual(blocked and blocked.get("action"), "block")
            result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123", "result": "done"})
            self.assertIsNone(self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result))
            self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
            evidence = list(Path(td).glob("*.json"))
            self.assertEqual(len(evidence), 1)
            payload = json.loads(evidence[0].read_text())
            self.assertEqual(payload["schema"], 6)
            self.assertEqual(payload["task_id"], "t_guard")
            self.assertEqual(payload["run_id"], "77")
            self.assertEqual(payload["workspace"], WORKSPACE)

    def test_forged_evidence_without_memory_attestation_cannot_unlock_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(GUARD, "EVIDENCE_ROOT", root):
                fake = {
                    "schema": 6, "profile": "coder-claude", "task_id": "t_guard", "run_id": "77",
                    "model_class": "sonnet", "workspace": WORKSPACE, "claude_binary": FAKE_CLAUDE[0],
                    "claude_binary_sha256": FAKE_CLAUDE[1], "session_id": "forged", "command_sha256": "c" * 64,
                    "attestation_id": "d" * 64, "git_head_after": CONTENT_STATE[0],
                    "workspace_content_state_after_sha256": CONTENT_STATE[1], "success": True,
                    "execution_cwd": WORKSPACE, "terminal_args_sha256": "e" * 64,
                }
                (root / "t_guard__77__coder-claude.json").write_text(json.dumps(fake))
                blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_content_mutation_after_evidence_invalidates_handoff(self):
        with tempfile.TemporaryDirectory() as td, patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
            result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123"})
            self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result)
            changed_content = (CONTENT_STATE[0], "9" * 64)
            blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"}, content_state=changed_content)
            self.assertEqual(blocked and blocked.get("action"), "block")

    def test_new_claude_command_invalidates_prior_completed_attestation(self):
        with tempfile.TemporaryDirectory() as td, patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
            result = json.dumps({"type": "result", "subtype": "success", "session_id": "sess-123"})
            self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=result)
            self.assertIsNone(self._call("coder-claude", "kanban_request_review", {"summary": "ready"}))
            self.assertIsNone(self._call("coder-claude", "terminal", {"command": CODER_CMD}))
            blocked = self._call("coder-claude", "kanban_request_review", {"summary": "ready"})
            self.assertEqual(blocked and blocked.get("action"), "block")

    def test_failed_or_malformed_terminal_result_does_not_create_evidence(self):
        with tempfile.TemporaryDirectory() as td, patch.object(GUARD, "EVIDENCE_ROOT", Path(td)):
            failed = json.dumps({"type": "result", "subtype": "error_max_turns", "session_id": "sess-x"})
            self._execute_claude("coder-claude", command=CODER_CMD, terminal_output=failed)
            self.assertEqual(list(Path(td).glob("*.json")), [])
            GUARD._PENDING_ATTESTATIONS.clear()
            self._execute_claude("coder-claude", command=CODER_CMD, terminal_output="not-json")
            self.assertEqual(list(Path(td).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
