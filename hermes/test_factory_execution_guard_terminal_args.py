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
    "factory_execution_guards_terminal_args",
    PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(PACKAGE_DIR)],
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN
SPEC.loader.exec_module(PLUGIN)
GUARD = PLUGIN._guard

CLAUDE_PROFILES = ("coder-claude", "reviewer-claude", "architect-claude-opus")
FAKE_CLAUDE = ("/opt/claude-code/claude", "a" * 64)
CONTENT_STATE = ("1" * 40, "2" * 64)


class TerminalArgsExecutionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        GUARD._PENDING_ATTESTATIONS.clear()
        GUARD._COMPLETED_ATTESTATIONS.clear()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = (self.root / "workspace").resolve()
        self.workspace.mkdir()
        self.alias = self.root / "workspace-alias"
        self.alias.symlink_to(self.workspace, target_is_directory=True)
        self.evidence = self.root / "evidence"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _env(self, profile: str) -> dict[str, str]:
        return {
            "HERMES_PROFILE": profile,
            "HERMES_KANBAN_TASK": "t_guard",
            "HERMES_KANBAN_RUN_ID": "77",
            "HERMES_KANBAN_WORKSPACE": str(self.workspace),
        }

    def _command(self, profile: str) -> str:
        model = "opus" if profile == "architect-claude-opus" else "sonnet"
        tools = PLUGIN._coder_tools(str(self.workspace)) if profile == "coder-claude" else PLUGIN._READONLY_TOOLS
        mode = "dontAsk" if profile == "coder-claude" else "plan"
        prompt = (
            f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {self.workspace}\n"
            "Perform the assigned task."
        )
        return (
            f"claude -p '{prompt}' --model {model} --output-format json --safe-mode "
            f"--permission-mode {mode} --allowedTools '{tools}' --max-turns 2"
        )

    def _args(self, profile: str, **overrides: object) -> dict[str, object]:
        args: dict[str, object] = {
            "command": self._command(profile),
            "workdir": str(self.workspace),
        }
        args.update(overrides)
        return args

    def _patches(self):
        return (
            patch.object(GUARD, "_canonical_claude_identity", return_value=FAKE_CLAUDE),
            patch.object(GUARD, "_workspace_content_state", return_value=CONTENT_STATE),
            patch.object(PLUGIN.Path, "cwd", return_value=self.workspace),
            patch.object(GUARD, "EVIDENCE_ROOT", self.evidence),
        )

    def _pre(self, profile: str, args: object):
        patches = self._patches()
        with patch.dict(os.environ, self._env(profile), clear=False), patches[0], patches[1], patches[2], patches[3]:
            return PLUGIN.on_pre_tool_call(
                tool_name="terminal", args=args, task_id="t_guard"
            )

    def _post(self, profile: str, args: object, result_payload: dict[str, object]) -> None:
        patches = self._patches()
        with patch.dict(os.environ, self._env(profile), clear=False), patches[0], patches[1], patches[2], patches[3]:
            PLUGIN.on_post_tool_call(
                tool_name="terminal",
                args=args,
                result=json.dumps(result_payload),
                task_id="t_guard",
                duration_ms=1,
            )

    def _successful_result(self, *, cwd: object = None, include_cwd: bool = True) -> dict[str, object]:
        claude = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "session_id": "sess-123",
                "result": "done",
            }
        )
        payload: dict[str, object] = {"output": claude, "exit_code": 0, "error": None}
        if include_cwd:
            payload["cwd"] = str(self.workspace) if cwd is None else cwd
        return payload

    def _complete(self, profile: str, args: dict[str, object], result: dict[str, object] | None = None) -> None:
        self.assertIsNone(self._pre(profile, args))
        self._post(profile, args, result or self._successful_result())

    def _lifecycle(self, profile: str):
        tool = "kanban_request_review" if profile == "coder-claude" else "kanban_complete"
        patches = self._patches()
        with patch.dict(os.environ, self._env(profile), clear=False), patches[0], patches[1], patches[2], patches[3]:
            return PLUGIN.on_pre_tool_call(tool_name=tool, args={"summary": "done"}, task_id="t_guard")

    def test_all_claude_profiles_accept_exact_explicit_workdir(self):
        for profile in CLAUDE_PROFILES:
            with self.subTest(profile=profile):
                self.assertIsNone(self._pre(profile, self._args(profile)))

    def test_all_claude_profiles_reject_missing_and_noncanonical_workdirs(self):
        for profile in CLAUDE_PROFILES:
            command = self._command(profile)
            cases = {
                "missing": {"command": command},
                "different": {"command": command, "workdir": "/tmp"},
                "lexical_alias": {
                    "command": command,
                    "workdir": str(self.workspace / ".." / self.workspace.name),
                },
                "symlink_alias": {"command": command, "workdir": str(self.alias)},
                "non_string": {"command": command, "workdir": 7},
            }
            for label, args in cases.items():
                with self.subTest(profile=profile, case=label):
                    blocked = self._pre(profile, args)
                    self.assertEqual(blocked and blocked.get("action"), "block")

    def test_unknown_and_execution_affecting_terminal_args_fail_closed(self):
        refused = {
            "unknown": "value",
            "background": True,
            "pty": True,
            "notify_on_complete": True,
            "watch_patterns": ["done"],
            "session_id": "override",
            "task_id": "override",
            "force": True,
            "cwd": str(self.workspace),
            "env": {"X": "Y"},
            "host": "elsewhere",
        }
        for profile in CLAUDE_PROFILES:
            for key, value in refused.items():
                with self.subTest(profile=profile, key=key):
                    blocked = self._pre(profile, self._args(profile, **{key: value}))
                    self.assertEqual(blocked and blocked.get("action"), "block")

    def test_timeout_is_bounded_integer_not_boolean_and_is_digest_bound(self):
        profile = "coder-claude"
        for value in (None, True, False, 0, -1, 601, 1.5, "30"):
            with self.subTest(value=value):
                blocked = self._pre(profile, self._args(profile, timeout=value))
                self.assertEqual(blocked and blocked.get("action"), "block")

        args = self._args(profile, timeout=60)
        self.assertIsNone(self._pre(profile, args))
        pending = GUARD._PENDING_ATTESTATIONS[(profile, "t_guard", "77")]
        digest = pending.get("terminal_args_sha256")
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        GUARD._PENDING_ATTESTATIONS.clear()
        self.assertIsNone(self._pre(profile, self._args(profile, timeout=61)))
        self.assertNotEqual(
            digest,
            GUARD._PENDING_ATTESTATIONS[(profile, "t_guard", "77")].get("terminal_args_sha256"),
        )

    def test_pre_post_args_mismatch_creates_no_evidence(self):
        profile = "coder-claude"
        before = self._args(profile, timeout=60)
        after = self._args(profile, timeout=61)
        self.assertIsNone(self._pre(profile, before))
        self._post(profile, after, self._successful_result())
        self.assertEqual(list(self.evidence.glob("*.json")), [])
        self.assertEqual(GUARD._COMPLETED_ATTESTATIONS, {})

    def test_omitted_result_cwd_uses_validated_explicit_workdir(self):
        for profile in CLAUDE_PROFILES:
            with self.subTest(profile=profile):
                GUARD._PENDING_ATTESTATIONS.clear()
                GUARD._COMPLETED_ATTESTATIONS.clear()
                self._complete(
                    profile,
                    self._args(profile),
                    self._successful_result(include_cwd=False),
                )
                path = self.evidence / f"t_guard__77__{profile}.json"
                self.assertTrue(path.is_file(), "omitted result cwd should still create evidence")
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["execution_cwd"], str(self.workspace))
                self.assertIsNone(self._lifecycle(profile))

    def test_present_terminal_result_cwd_is_mandatory_exact_and_canonical(self):
        profile = "coder-claude"
        cases = {
            "different": self._successful_result(cwd="/tmp"),
            "lexical_alias": self._successful_result(
                cwd=str(self.workspace / ".." / self.workspace.name)
            ),
            "symlink_alias": self._successful_result(cwd=str(self.alias)),
            "non_string": self._successful_result(cwd=7),
        }
        for label, result in cases.items():
            with self.subTest(case=label):
                GUARD._PENDING_ATTESTATIONS.clear()
                GUARD._COMPLETED_ATTESTATIONS.clear()
                self._complete(profile, self._args(profile), result)
                self.assertEqual(list(self.evidence.glob("*.json")), [])
                self.assertEqual(GUARD._COMPLETED_ATTESTATIONS, {})

    def test_correct_result_cwd_creates_schema_6_evidence(self):
        for profile in CLAUDE_PROFILES:
            with self.subTest(profile=profile):
                GUARD._PENDING_ATTESTATIONS.clear()
                GUARD._COMPLETED_ATTESTATIONS.clear()
                self._complete(profile, self._args(profile, timeout=60))
                path = self.evidence / f"t_guard__77__{profile}.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], 6)
                self.assertEqual(payload["execution_cwd"], str(self.workspace))
                self.assertRegex(payload["terminal_args_sha256"], r"^[0-9a-f]{64}$")
                self.assertIsNone(self._lifecycle(profile))

    def test_schema_6_missing_or_wrong_execution_context_blocks_lifecycle(self):
        profile = "coder-claude"
        for mutation in ("missing_cwd", "missing_digest", "wrong_digest"):
            with self.subTest(mutation=mutation):
                GUARD._PENDING_ATTESTATIONS.clear()
                GUARD._COMPLETED_ATTESTATIONS.clear()
                self._complete(profile, self._args(profile))
                path = self.evidence / "t_guard__77__coder-claude.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "missing_cwd":
                    payload.pop("execution_cwd")
                elif mutation == "missing_digest":
                    payload.pop("terminal_args_sha256")
                else:
                    payload["terminal_args_sha256"] = "f" * 64
                path.write_text(json.dumps(payload), encoding="utf-8")
                blocked = self._lifecycle(profile)
                self.assertEqual(blocked and blocked.get("action"), "block")

    def test_malformed_second_claude_attempt_invalidates_completed_authorization(self):
        profile = "coder-claude"
        self._complete(profile, self._args(profile))
        self.assertIsNone(self._lifecycle(profile))
        blocked = self._pre(
            profile,
            {"command": "git status", "workdir": str(self.workspace)},
        )
        self.assertEqual(blocked and blocked.get("action"), "block")
        blocked = self._lifecycle(profile)
        self.assertEqual(blocked and blocked.get("action"), "block")

    def test_original_workdir_tmp_reproducer_is_blocked_before_attestation(self):
        profile = "coder-claude"
        blocked = self._pre(profile, self._args(profile, workdir="/tmp"))
        self.assertEqual(blocked and blocked.get("action"), "block")
        self.assertEqual(GUARD._PENDING_ATTESTATIONS, {})
        self.assertEqual(GUARD._COMPLETED_ATTESTATIONS, {})

    def test_real_hermes_0204_resolver_and_guard_cannot_diverge(self):
        install_root = Path.home() / ".hermes" / "hermes-agent"
        sys.path.insert(0, str(install_root))
        try:
            from tools import terminal_tool as hermes_terminal
        finally:
            sys.path.pop(0)

        profile = "coder-claude"
        exact_args = self._args(profile)
        with patch.object(hermes_terminal, "get_session_cwd", return_value="/session"):
            exact_cwd = hermes_terminal._resolve_command_cwd(
                workdir=str(self.workspace), default_cwd="/default", session_key="s"
            )
            override_cwd = hermes_terminal._resolve_command_cwd(
                workdir="/tmp", default_cwd=str(self.workspace), session_key="s"
            )
        self.assertEqual(exact_cwd, str(self.workspace))
        self.assertEqual(override_cwd, "/tmp")
        self.assertIsNone(self._pre(profile, exact_args))
        blocked = self._pre(profile, self._args(profile, workdir=override_cwd))
        self.assertEqual(blocked and blocked.get("action"), "block")


if __name__ == "__main__":
    unittest.main()
