from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
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

    def test_unquoted_linebreak_is_fail_closed_before_shell_parser(self):
        with patch.dict(os.environ, self._env(HERMES_PROFILE="runtime-controller"), clear=False):
            result=PLUGIN.on_pre_tool_call(tool_name="terminal", args={"command":"echo ok\nid"})
        self.assertEqual(result and result.get("action"), "block")
        self.assertIn("unquoted terminal line break", result.get("message", ""))

    def _canonical_command(self, profile: str, workspace: str) -> str:
        model="opus" if profile == "architect-claude-opus" else "sonnet"
        tools=PLUGIN._coder_tools(workspace) if profile == "coder-claude" else PLUGIN._READONLY_TOOLS
        mode="dontAsk" if profile == "coder-claude" else "plan"
        prompt=f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {workspace}\nPerform the assigned task."
        return (
            f"claude -p '{prompt}' --model {model} --output-format json --safe-mode "
            f"--permission-mode {mode} --allowedTools '{tools}' --max-turns 2"
        )

    def test_hardened_claude_schema_requires_scoped_edit_safe_mode_exact_markers(self):
        with tempfile.TemporaryDirectory() as td:
            workspace=Path(td).resolve()
            command=self._canonical_command("coder-claude", str(workspace))
            exact_tools=PLUGIN._coder_tools(str(workspace))
            self.assertIn(f"Edit(/{workspace}/**)", exact_tools)
            with patch.dict(os.environ, {"HERMES_KANBAN_TASK":"t_guard","HERMES_KANBAN_RUN_ID":"77","HERMES_KANBAN_WORKSPACE":str(workspace)}, clear=False), \
                 patch.object(PLUGIN._guard, "_canonical_claude_identity", return_value=("/opt/claude","a"*64)), \
                 patch.object(PLUGIN.Path, "cwd", return_value=workspace):
                self.assertIsNotNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace(" --safe-mode", "")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace("TASK_ID: t_guard", "TASK_ID: t_guard_evil")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace(exact_tools, "Read,Write,Edit,Glob,Grep")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace(exact_tools, exact_tools+",Bash")))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command.replace("dontAsk", "acceptEdits")))

    def test_workspace_permission_grammar_injection_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = (Path(td) / "repo),Bash(*),Edit(").resolve()
            workspace.mkdir()
            with self.assertRaises(ValueError):
                PLUGIN._coder_tools(str(workspace))
            injected_tools = f"Read,Glob,Grep,Edit(/{workspace}/**)"
            self.assertIn("Bash(*)", injected_tools)
            prompt = f"TASK_ID: t_guard\nRUN_ID: 77\nWORKSPACE: {workspace}\nPerform the assigned task."
            command = (
                f"claude -p '{prompt}' --model sonnet --output-format json --safe-mode "
                f"--permission-mode dontAsk --allowedTools '{injected_tools}' --max-turns 2"
            )
            with patch.dict(os.environ, {"HERMES_KANBAN_TASK":"t_guard","HERMES_KANBAN_RUN_ID":"77","HERMES_KANBAN_WORKSPACE":str(workspace)}, clear=False), \
                 patch.object(PLUGIN._guard, "_canonical_claude_identity", return_value=("/opt/claude","a"*64)), \
                 patch.object(PLUGIN.Path, "cwd", return_value=workspace):
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("coder-claude", command))

    def test_readonly_profiles_require_plan_mode(self):
        with tempfile.TemporaryDirectory() as td:
            workspace=Path(td).resolve()
            command=self._canonical_command("reviewer-claude", str(workspace))
            with patch.dict(os.environ, {"HERMES_KANBAN_TASK":"t_guard","HERMES_KANBAN_RUN_ID":"77","HERMES_KANBAN_WORKSPACE":str(workspace)}, clear=False), \
                 patch.object(PLUGIN._guard, "_canonical_claude_identity", return_value=("/opt/claude","a"*64)), \
                 patch.object(PLUGIN.Path, "cwd", return_value=workspace):
                self.assertIsNotNone(PLUGIN._hardened_parse_claude_argv("reviewer-claude", command))
                self.assertIsNone(PLUGIN._hardened_parse_claude_argv("reviewer-claude", command.replace("--permission-mode plan", "--permission-mode acceptEdits")))

    def _init_repo(self, repo: Path, content: str = "before\n") -> None:
        subprocess.run(["git","init","-q",str(repo)],check=True)
        subprocess.run(["git","-C",str(repo),"config","user.email","test@example.invalid"],check=True)
        subprocess.run(["git","-C",str(repo),"config","user.name","Test"],check=True)
        tracked=repo/"tracked.txt"; tracked.write_text(content)
        subprocess.run(["git","-C",str(repo),"add","tracked.txt"],check=True)
        subprocess.run(["git","-C",str(repo),"commit","-qm","init"],check=True)

    def _legacy_ambiguous_content_state(self, repo: Path) -> str:
        head=subprocess.run(["git","-C",str(repo),"rev-parse","HEAD"],check=True,text=True,stdout=subprocess.PIPE).stdout.strip()
        staged=subprocess.run(["git","-C",str(repo),"diff","--cached","--binary","--no-ext-diff","--no-textconv","HEAD","--"],check=True,stdout=subprocess.PIPE).stdout
        raw_paths=subprocess.run(["git","-C",str(repo),"ls-files","-c","-o","-z"],check=True,stdout=subprocess.PIPE).stdout
        digest=hashlib.sha256()
        digest.update(b"HEAD\0"+head.encode("ascii")+b"\0STAGED\0"+staged+b"\0FILES\0")
        for raw in sorted(path for path in raw_paths.split(b"\0") if path):
            path=repo/Path(os.fsdecode(raw)); st=path.lstat()
            digest.update(raw+b"\0")
            digest.update(f"MODE:{stat.S_IFMT(st.st_mode):o}:{stat.S_IMODE(st.st_mode):o}\0".encode("ascii"))
            digest.update(b"FILE\0"+path.read_bytes()+b"\0")
        return digest.hexdigest()

    def test_content_state_detects_assume_unchanged_tracked_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td); self._init_repo(repo)
            tracked=repo/"tracked.txt"
            before=PLUGIN._hardened_workspace_content_state(str(repo))
            subprocess.run(["git","-C",str(repo),"update-index","--assume-unchanged","tracked.txt"],check=True)
            tracked.write_text("after\n")
            status=subprocess.run(["git","-C",str(repo),"status","--porcelain"],check=True,text=True,stdout=subprocess.PIPE).stdout
            self.assertEqual(status, "")
            after=PLUGIN._hardened_workspace_content_state(str(repo))
            self.assertIsNotNone(before); self.assertIsNotNone(after)
            self.assertNotEqual(before[1], after[1])

    def test_content_state_detects_gitignored_untracked_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td); self._init_repo(repo)
            (repo/".gitignore").write_text("ignored.txt\n")
            subprocess.run(["git","-C",str(repo),"add",".gitignore"],check=True)
            subprocess.run(["git","-C",str(repo),"commit","-qm","ignore fixture"],check=True)
            before=PLUGIN._hardened_workspace_content_state(str(repo))
            (repo/"ignored.txt").write_text("secret-before\n")
            hidden=PLUGIN._hardened_workspace_content_state(str(repo))
            (repo/"ignored.txt").write_text("secret-after\n")
            changed=PLUGIN._hardened_workspace_content_state(str(repo))
            self.assertIsNotNone(before); self.assertIsNotNone(hidden); self.assertIsNotNone(changed)
            self.assertNotEqual(before[1], hidden[1])
            self.assertNotEqual(hidden[1], changed[1])

    def test_content_state_uses_collision_free_record_framing(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td); self._init_repo(repo)
            a=repo/"a"; b=repo/"b"
            a.write_bytes(b"")
            a.chmod(0o640)
            a_stat=a.lstat()
            record_prefix=(
                b"\0b\0"
                + f"MODE:{stat.S_IFMT(a_stat.st_mode):o}:{stat.S_IMODE(a_stat.st_mode):o}\0".encode("ascii")
                + b"FILE\0"
            )
            a.write_bytes(b"X"+record_prefix+b"Y")
            legacy_one=self._legacy_ambiguous_content_state(repo)
            hardened_one=PLUGIN._hardened_workspace_content_state(str(repo))
            a.write_bytes(b"X")
            b.write_bytes(b"Y")
            b.chmod(stat.S_IMODE(a_stat.st_mode))
            legacy_two=self._legacy_ambiguous_content_state(repo)
            hardened_two=PLUGIN._hardened_workspace_content_state(str(repo))
            self.assertEqual(legacy_one, legacy_two)
            self.assertIsNotNone(hardened_one); self.assertIsNotNone(hardened_two)
            self.assertNotEqual(hardened_one[1], hardened_two[1])

    def test_content_state_ignores_inherited_git_repository_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            target=root/"target"; decoy=root/"decoy"
            self._init_repo(target, "target-before\n")
            self._init_repo(decoy, "decoy\n")
            target_head=subprocess.run(["git","-C",str(target),"rev-parse","HEAD"],check=True,text=True,stdout=subprocess.PIPE).stdout.strip()
            baseline=PLUGIN._hardened_workspace_content_state(str(target))
            hostile={
                "GIT_DIR": str(decoy/".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_INDEX_FILE": str(decoy/".git/index"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(decoy),
            }
            with patch.dict(os.environ, hostile, clear=False):
                before=PLUGIN._hardened_workspace_content_state(str(target))
                (target/"tracked.txt").write_text("target-after\n")
                after=PLUGIN._hardened_workspace_content_state(str(target))
            self.assertIsNotNone(baseline); self.assertIsNotNone(before); self.assertIsNotNone(after)
            self.assertEqual(before[0], target_head)
            self.assertEqual(baseline, before)
            self.assertNotEqual(before[1], after[1])


if __name__ == "__main__":
    unittest.main()
