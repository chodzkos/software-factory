from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).parent / "plugins" / "factory-execution-guards"
SPEC = importlib.util.spec_from_file_location(
    "factory_execution_guards",
    PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(PACKAGE_DIR)],
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN
SPEC.loader.exec_module(PLUGIN)


class ProfileResolutionTests(unittest.TestCase):
    def _env(self, **values: str) -> dict[str, str]:
        env = {"HERMES_PROFILE": "", "HERMES_HOME": ""}
        env.update(values)
        return env

    def test_profile_cli_home_activates_protected_profile(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td); (home/".hermes/profiles").mkdir(parents=True)
            with patch.object(PLUGIN.Path, "home", return_value=home), patch.dict(
                os.environ, self._env(HERMES_HOME=str(home/".hermes/profiles/reviewer-claude")), clear=False
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE"), "reviewer-claude")

    def test_symlinked_protected_slot_keeps_logical_identity(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td); profiles=home/".hermes/profiles"; profiles.mkdir(parents=True)
            outside=home/"outside"; outside.mkdir()
            (profiles/"reviewer-claude").symlink_to(outside, target_is_directory=True)
            with patch.object(PLUGIN.Path, "home", return_value=home), patch.dict(
                os.environ, self._env(HERMES_HOME=str(profiles/"reviewer-claude")), clear=False
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE"), "reviewer-claude")

    def test_arbitrary_and_noncanonical_home_do_not_activate(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td); (home/".hermes/profiles").mkdir(parents=True)
            for raw in (home/".hermes/profiles/default", Path("/tmp/reviewer-claude")):
                with self.subTest(raw=raw), patch.object(PLUGIN.Path, "home", return_value=home), patch.dict(
                    os.environ, self._env(HERMES_HOME=str(raw)), clear=False
                ):
                    PLUGIN._activate_profile_identity()
                    self.assertEqual(os.environ.get("HERMES_PROFILE", ""), "")

    def test_explicit_worker_profile_wins(self):
        with patch.dict(os.environ, self._env(HERMES_PROFILE="coder-claude", HERMES_HOME="/x/reviewer-claude"), clear=False):
            PLUGIN._activate_profile_identity()
            self.assertEqual(os.environ.get("HERMES_PROFILE"), "coder-claude")

    def test_multiline_terminal_is_fail_closed_before_shell_parser(self):
        with patch.dict(os.environ, self._env(HERMES_PROFILE="runtime-controller"), clear=False):
            result=PLUGIN.on_pre_tool_call(tool_name="terminal", args={"command":"echo ok\nid"})
        self.assertEqual(result and result.get("action"), "block")
        self.assertIn("multiline", result.get("message", ""))

    def _canonical_command(self, profile: str, workspace: str) -> str:
        model="opus" if profile == "architect-claude-opus" else "sonnet"
        tools=PLUGIN._CODER_TOOLS if profile == "coder-claude" else PLUGIN._READONLY_TOOLS
        mode="acceptEdits" if profile == "coder-claude" else "plan"
        prompt=f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {workspace}\nPerform the assigned task."
        return (
            f"claude -p '{prompt}' --model {model} --output-format json --safe-mode "
            f"--permission-mode {mode} --allowedTools '{tools}' --max-turns 2"
        )

    def test_hardened_claude_schema_requires_safe_mode_exact_markers_and_no_bash(self):
        with tempfile.TemporaryDirectory() as td:
            workspace=Path(td).resolve()
            command=self._canonical_command("coder-claude", str(workspace))
            with patch.dict(os.environ, {"HERMES_KANBAN_TASK":"t_guard","HERMES_KANBAN_RUN_ID":"77","HERMES_KANBAN_WORKSPACE":str(workspace)}, clear=False), \
                 patch.object(PLUGIN._guard, "_canonical_claude_identity", return_value=("/opt/claude","a"*64)), \
                 patch.object(PLUGIN.Path, "cwd", return_value=workspace):
                self.assertIsNotNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace(" --safe-mode", "")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace("TASK_ID: t_guard", "TASK_ID: t_guard_evil")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace(PLUGIN._CODER_TOOLS, PLUGIN._CODER_TOOLS+",Bash")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace("acceptEdits", "bypassPermissions")))

    def test_readonly_profiles_require_plan_mode(self):
        with tempfile.TemporaryDirectory() as td:
            workspace=Path(td).resolve()
            command=self._canonical_command("reviewer-claude", str(workspace))
            with patch.dict(os.environ, {"HERMES_KANBAN_TASK":"t_guard","HERMES_KANBAN_RUN_ID":"77","HERMES_KANBAN_WORKSPACE":str(workspace)}, clear=False), \
                 patch.object(PLUGIN._guard, "_canonical_claude_identity", return_value=("/opt/claude","a"*64)), \
                 patch.object(PLUGIN.Path, "cwd", return_value=workspace):
                self.assertIsNotNone(PLUGIN._hardened_parse_claude_argv("reviewer-claude", command))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("reviewer-claude", command.replace("--permission-mode plan", "--permission-mode acceptEdits")))

    def test_content_state_detects_assume_unchanged_tracked_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td)
            subprocess.run(["git","init","-q",str(repo)],check=True)
            subprocess.run(["git","-C",str(repo),"config","user.email","test@example.invalid"],check=True)
            subprocess.run(["git","-C",str(repo),"config","user.name","Test"],check=True)
            tracked=repo/"tracked.txt"; tracked.write_text("before\n")
            subprocess.run(["git","-C",str(repo),"add","tracked.txt"],check=True)
            subprocess.run(["git","-C",str(repo),"commit","-qm","init"],check=True)
            before=PLUGIN._hardened_workspace_content_state(str(repo))
            subprocess.run(["git","-C",str(repo),"update-index","--assume-unchanged","tracked.txt"],check=True)
            tracked.write_text("after\n")
            status=subprocess.run(["git","-C",str(repo),"status","--porcelain"],check=True,text=True,stdout=subprocess.PIPE).stdout
            self.assertEqual(status, "")
            after=PLUGIN._hardened_workspace_content_state(str(repo))
            self.assertIsNotNone(before); self.assertIsNotNone(after)
            self.assertNotEqual(before[1], after[1])


if __name__ == "__main__":
    unittest.main()
