"""Regresje v0.12.0 dla rzeczywistych powierzchni narzędzi profili."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
PLUGINS = ROOT / "plugins"
REVIEWER_TOOLS = {
    "factory_repo_map",
    "factory_repo_read",
    "factory_repo_search",
    "kanban_show",
    "kanban_request_changes",
    "factory_review_approve",
}
RELEASE_TOOLS = {
    "factory_repo_map",
    "factory_repo_read",
    "factory_repo_search",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _profile(root: Path, name: str, *, include_guard: bool = True) -> Path:
    profile = root / name
    installed = profile / "plugins"
    installed.mkdir(parents=True)
    shutil.copytree(PLUGINS / "factory-repository-readonly", installed / "factory-repository-readonly")
    enabled = ["factory-repository-readonly"]
    if include_guard:
        shutil.copytree(PLUGINS / "factory-execution-guards", installed / "factory-execution-guards")
        enabled.append("factory-execution-guards")
    config = {
        "toolsets": enabled,
        "platform_toolsets": {"cli": [*enabled, *([] if name == "release-manager" else ["kanban"]), "no_mcp"]},
        "mcp_servers": {},
        "tools": {"tool_search": {"enabled": "off"}},
        "plugins": {
            "enabled": enabled,
            "entries": {
                "factory-execution-guards": {"allow_tool_override": True},
                "factory-repository-readonly": {"allow_tool_override": False},
            },
        },
        "agent": {
            "disabled_toolsets": [
                "terminal", "file", "code_execution", "web", "browser", "image_gen",
                "delegation", "computer_use", "cronjob", "skills", "vision", "todo",
                "memory", "session_search", "clarify", "messaging", "tts", "moa",
                "bfl", "x_search", "mcp",
            ]
        },
    }
    (profile / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    return profile


class ActualCapabilitySurfaceTests(unittest.TestCase):
    def test_release_manager_is_not_in_generic_profile_registry_bootstrap(self) -> None:
        text = (ROOT / "bootstrap_profiles.sh").read_text(encoding="utf-8")
        profile_line = next(line for line in text.splitlines() if line.startswith("profiles=("))
        self.assertNotIn("release-manager", profile_line)
        self.assertIn('RELEASE_PROFILE_DIR="${HOME}/.hermes/factory-profiles/release-manager"', text)
        self.assertIn("if profile_exists release-manager", text)

    def test_isolated_release_manager_cannot_resolve_through_generic_profile_registry(self) -> None:
        from hermes_cli.profiles import resolve_profile_env

        with tempfile.TemporaryDirectory(prefix="sf-v120-registry-") as td:
            root = Path(td)
            (root / "factory-profiles" / "release-manager").mkdir(parents=True)
            with patch.dict(os.environ, {"HERMES_HOME": str(root)}, clear=False):
                with self.assertRaises(FileNotFoundError):
                    resolve_profile_env("release-manager")

    def test_release_launcher_verifies_then_starts_without_kanban_worker_context(self) -> None:
        launcher = _load("v120_release_launcher", ROOT / "release_manager_start.py")
        observed: dict[str, object] = {}

        def runner(command, **kwargs):
            observed["command"] = command
            observed["env"] = kwargs["env"]
            return __import__("subprocess").CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory(prefix="sf-v120-launch-") as td:
            profile = _profile(Path(td), "release-manager")
            with patch.dict(os.environ, {"HERMES_KANBAN_FUTURE_MUTATION": "must-be-removed"}, clear=False):
                rc = launcher.launch_release_manager(
                    profile_home=profile,
                    workspace=ROOT.parent,
                    board="isolated",
                    task_id="t_release",
                    run_id=8,
                    prompt="read-only release decision",
                    runner=runner,
                )
        self.assertEqual(rc, 0)
        env = observed["env"]
        command = observed["command"]
        self.assertIsInstance(env, dict)
        self.assertIsInstance(command, list)
        assert isinstance(env, dict) and isinstance(command, list)
        self.assertNotIn("HERMES_KANBAN_TASK", env)
        self.assertNotIn("HERMES_KANBAN_FUTURE_MUTATION", env)
        self.assertEqual(env["FACTORY_RELEASE_TASK"], "t_release")
        self.assertEqual(env["FACTORY_RELEASE_WORKSPACE"], str(ROOT.parent))
        self.assertEqual(env["HERMES_HOME"], str(profile))
        self.assertNotIn("-p", command)
        self.assertEqual(command[-3:-1], ["chat", "-q"])

    def test_release_launcher_does_not_start_after_confinement_tamper(self) -> None:
        launcher = _load("v120_release_launcher_negative", ROOT / "release_manager_start.py")
        started = {"value": False}

        def runner(*_args, **_kwargs):
            started["value"] = True
            return __import__("subprocess").CompletedProcess([], 0)

        with tempfile.TemporaryDirectory(prefix="sf-v120-launch-negative-") as td:
            profile = _profile(Path(td), "release-manager")
            shutil.rmtree(profile / "plugins" / "factory-execution-guards")
            with self.assertRaises(Exception):
                launcher.launch_release_manager(
                    profile_home=profile,
                    workspace=ROOT.parent,
                    board="isolated",
                    task_id="t_release",
                    run_id=8,
                    prompt="must not start",
                    runner=runner,
                )
        self.assertFalse(started["value"])

    def test_hook_blocks_every_non_allowlisted_tool_for_both_confined_profiles(self) -> None:
        entry = _load("v120_hook_entry", PLUGINS / "factory-execution-guards" / "__init__.py")
        candidates = {
            "terminal", "process", "execute_code", "write_file", "patch", "delegate_task",
            "browser_exec", "kanban_complete", "kanban_block", "kanban_create",
            "kanban_heartbeat", "kanban_comment", "kanban_attach", "kanban_attach_url",
            "kanban_attachments", "kanban_link", "kanban_request_review", "kanban_future_mutation",
        }
        for profile, allowed in (("reviewer-gpt", REVIEWER_TOOLS), ("release-manager", RELEASE_TOOLS)):
            with self.subTest(profile=profile), patch.dict(os.environ, {"HERMES_PROFILE": profile}, clear=False):
                for name in candidates - allowed:
                    blocked = entry.on_pre_tool_call(tool_name=name, args={})
                    self.assertEqual(blocked and blocked.get("action"), "block", name)

    def test_reviewer_actual_get_tool_definitions_is_exactly_six(self) -> None:
        verifier = _load("v120_reviewer_verifier", ROOT / "verify_reviewer_capabilities.py")
        with tempfile.TemporaryDirectory(prefix="sf-v120-reviewer-") as td:
            profile = _profile(Path(td), "reviewer-gpt")
            result = verifier.verify(profile, workspace=ROOT.parent, task_id="t_surface", board="isolated", run_id=7)
        self.assertEqual(set(result["tools"]), REVIEWER_TOOLS)

    def test_release_manager_actual_surface_is_read_only_and_exact(self) -> None:
        verifier = _load("v120_release_verifier", ROOT / "verify_release_manager_capabilities.py")
        with tempfile.TemporaryDirectory(prefix="sf-v120-release-") as td:
            profile = _profile(Path(td), "release-manager")
            result = verifier.verify(profile, workspace=ROOT.parent, task_id="t_release", board="isolated", run_id=8)
        self.assertEqual(set(result["tools"]), RELEASE_TOOLS)
        self.assertEqual(result["launch_context"], "cli-chat")

    def test_reviewer_missing_disabled_tampered_or_unapproved_guard_fails_closed(self) -> None:
        verifier = _load("v120_reviewer_negative", ROOT / "verify_reviewer_capabilities.py")
        cases = ("missing", "disabled", "wrong_version", "import_failure", "registration_failure")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="sf-v120-negative-") as td:
                profile = _profile(Path(td), "reviewer-gpt")
                guard = profile / "plugins" / "factory-execution-guards"
                config_path = profile / "config.yaml"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if case == "missing":
                    shutil.rmtree(guard)
                elif case == "disabled":
                    config["plugins"]["enabled"].remove("factory-execution-guards")
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                elif case == "wrong_version":
                    manifest = guard / "plugin.yaml"
                    manifest.write_text(manifest.read_text(encoding="utf-8").replace("0.12.0", "9.9.9"), encoding="utf-8")
                elif case == "import_failure":
                    (guard / "__init__.py").write_text("raise RuntimeError('synthetic import failure')\n", encoding="utf-8")
                else:
                    config["plugins"]["entries"]["factory-execution-guards"]["allow_tool_override"] = False
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaises(Exception):
                    verifier.verify(profile, workspace=ROOT.parent, task_id="t_negative", board="isolated", run_id=7)

    def test_extra_future_kanban_tool_fails_exact_set_equality(self) -> None:
        verifier = _load("v120_reviewer_future", ROOT / "verify_reviewer_capabilities.py")
        with tempfile.TemporaryDirectory(prefix="sf-v120-future-") as td:
            profile = _profile(Path(td), "reviewer-gpt")
            future = profile / "plugins" / "future-kanban"
            future.mkdir()
            (future / "plugin.yaml").write_text(
                "name: future-kanban\nversion: 1.0.0\nentrypoint: __init__:register\nprovides_tools:\n  - kanban_future_mutation\n",
                encoding="utf-8",
            )
            (future / "__init__.py").write_text(
                "def register(ctx):\n"
                " ctx.register_tool(name='kanban_future_mutation',toolset='kanban',schema={'name':'kanban_future_mutation','description':'future','parameters':{'type':'object','properties':{}}},handler=lambda args,**kw:'x')\n",
                encoding="utf-8",
            )
            config_path = profile / "config.yaml"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["plugins"]["enabled"].append("future-kanban")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "exact model tool surface mismatch"):
                verifier.verify(profile, workspace=ROOT.parent, task_id="t_future", board="isolated", run_id=7)

    def test_unconfined_other_profile_retains_native_kanban_surface(self) -> None:
        shared = _load("v120_shared_probe", ROOT / "verify_profile_capabilities.py")
        with tempfile.TemporaryDirectory(prefix="sf-v120-other-") as td:
            profile = _profile(Path(td), "other-profile", include_guard=False)
            observed = shared._actual_definitions(
                profile,
                profile="other-profile",
                workspace=ROOT.parent,
                task_id="t_other",
                board="isolated",
                run_id=3,
            )
        self.assertIn("kanban_complete", observed["tools"])
        self.assertIn("kanban_request_review", observed["tools"])


if __name__ == "__main__":
    unittest.main()
