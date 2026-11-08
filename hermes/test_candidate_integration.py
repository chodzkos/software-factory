"""Spójność kandydata nie jest zgodą na instalację poza HOME fabryki; bootstrap odmawia bez efektów."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "hermes/plugins/factory-execution-guards"


class CandidateIntegrationTests(unittest.TestCase):
    def test_retained_historical_plugin_blobs_are_complete_and_exact(self):
        import hashlib
        import re
        root = Path(__file__).resolve().parent.parent
        script = (root / "hermes/test_factory_plugin_installer.sh").read_text()
        expected = set(re.findall(r"read_historical_blob ([0-9a-f]{40})", script))
        self.assertEqual(len(expected), 11)
        directory = root / "hermes/tests_support/historical_plugin_blobs"
        self.assertEqual({p.name for p in directory.iterdir()}, expected)
        for path in directory.iterdir():
            self.assertFalse(path.is_symlink())
            data = path.read_bytes()
            self.assertEqual(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(), path.name)

    def test_staging_manifest_has_exact_eight_source_blobs_and_factory_home_only_scope(self):
        spec = json.loads((ROOT / "hermes/plugins/manifest.json").read_text())["plugins"][PLUGIN.name]
        self.assertIs(spec["installable"], True)
        self.assertEqual(spec["activation_status"], "staging")
        self.assertEqual(spec["install_scope"], "factory-home-only")
        actual = {}
        for path in PLUGIN.iterdir():
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
            data = path.read_bytes()
            actual[path.name] = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(len(actual), 8)
        self.assertEqual(spec["files"], actual)
        self.assertNotIn(actual, spec["replace_from"])

    def test_capability_and_start_pins_match_same_current_plugin(self):
        from hermes.verify_profile_capabilities import EXPECTED_PLUGIN_SHA256
        tree = ast.parse((ROOT / "hermes/native_integration/factory_worker_start.py").read_text())
        pins = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.AnnAssign) and n.target.id == "SOURCE_PINS"))
        expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PLUGIN.iterdir()}
        self.assertEqual(pins["plugin"], expected)
        self.assertEqual(EXPECTED_PLUGIN_SHA256[PLUGIN.name], expected)
        import yaml
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        self.assertEqual(manifest["version"], "0.13.0")
        self.assertEqual(set(manifest["provides_tools"]), {"kanban_request_review", "factory_review_approve"})

    def test_candidate_bootstraps_refuse_before_any_hermes_call_or_home_write(self):
        for script in ("bootstrap_profiles.sh", "bootstrap_runtime_controller.sh"):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                binary = home / "bin" / "hermes"
                binary.parent.mkdir()
                binary.write_text('#!/bin/sh\nprintf touched > "$HOME/hermes-invoked"\nexit 89\n')
                binary.chmod(0o755)
                env = dict(os.environ, HOME=td, PATH=str(binary.parent) + os.pathsep + os.environ["PATH"])
                env.pop("FACTORY_HOME", None)
                run = subprocess.run(["bash", str(ROOT / "hermes" / script)], env=env,
                                     text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("staging plugin requires a real absolute FACTORY_HOME", run.stdout)
                self.assertFalse((home / "hermes-invoked").exists(), run.stdout)
                self.assertFalse((home / ".hermes").exists(), run.stdout)

    def test_override_permission_is_exactly_coder_reviewer_and_release(self):
        source = (ROOT / "hermes/bootstrap_profiles.sh").read_text()
        function = re.search(r"(?ms)^install_execution_guard\(\) \{\n.*?^\}", source)
        self.assertIsNotNone(function)
        for profile in ("coder-claude", "reviewer-gpt", "release-manager", "reviewer-claude", "architect-claude-opus", "runtime-controller"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as td:
                installer = Path(td) / "installer.sh"
                installer.write_text("exit 0\n")
                command = f'''set -eu
PROFILE_ROOT={td}
PLUGIN_INSTALLER={installer}
EXECUTION_GUARD=factory-execution-guards
hermes() {{ printf '%s\\n' "$*"; }}
{function.group(0)}
install_execution_guard {profile}
'''
                run = subprocess.run(["bash", "-c", command], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                flag = "--allow-tool-override" if profile in {"coder-claude", "reviewer-gpt", "release-manager"} else "--no-allow-tool-override"
                self.assertIn(f"-p {profile} plugins enable factory-execution-guards {flag}", run.stdout)


class CoderBootstrapTests(unittest.TestCase):
    def test_native_cli_config_and_dispatcher_resolve_exact_coder_surface(self):
        """Wykonaj prawdziwe komendy CLI config, bez providera, w osobnym HOME."""
        source = (ROOT / "hermes/bootstrap_profiles.sh").read_text()
        function = re.search(r"(?ms)^configure_coder_capabilities\(\) \{\n.*?^\}", source)
        self.assertIsNotNone(function, "coder bootstrap lacks explicit capability reset")
        self.assertEqual(source.count("\nconfigure_coder_capabilities\n"), 1)
        cleanup = 'remove_profile_keys coder-claude plugins.enabled plugins.disabled plugins.entries'
        self.assertIn(cleanup, source)
        self.assertLess(source.index(cleanup), source.index("for profile in coder-claude reviewer-claude architect-claude-opus; do"))
        import yaml
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            profile = home / ".hermes/profiles/coder-claude"
            profile.mkdir(parents=True)
            config_path = profile / "config.yaml"
            config_path.write_text(yaml.safe_dump({
                "model": {"provider": "openai-codex", "default": "fixture-not-called"},
                "toolsets": ["web", "browser"], "platform_toolsets": {"cli": ["hermes-cli"]},
                "mcp_servers": {"untrusted": {"command": "never-run"}},
                "agent": {"disabled_toolsets": ["terminal"], "environment_probe": False},
                "tools": {"tool_search": {"enabled": "on"}},
                "context": {"engine": "inherited-nondefault"},
                "factory": {"execution_backend": "claude-code", "claude_model_class": "sonnet"},
                "plugins": {"enabled": ["factory-execution-guards"], "disabled": [],
                            "entries": {"factory-execution-guards": {"allow_tool_override": True}}},
            }))
            env = dict(os.environ, HOME=td, HERMES_HOME=str(profile), HERMES_PROFILE="coder-claude")
            for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_BOARD"):
                env.pop(key, None)
            run = subprocess.run(["bash", "-c", "set -eu\n" + function.group(0) + "\nconfigure_coder_capabilities"],
                                 env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout)
            data = yaml.safe_load(config_path.read_text())
            expected = {"terminal", "file", "kanban", "factory-execution-guards"}
            self.assertEqual(set(data["toolsets"]), expected)
            self.assertEqual(set(data["platform_toolsets"]["cli"]), expected | {"no_mcp"})
            self.assertEqual(data["mcp_servers"], {})
            self.assertEqual(data["agent"]["disabled_toolsets"], [])
            self.assertEqual(data["tools"]["tool_search"]["enabled"], "off")
            self.assertEqual(data["known_builtin_toolsets"]["cli"], ["bfl"])
            self.assertEqual(data["context"]["engine"], "compressor")
            # Pełny loader natywny musi znać plugin. Test działa w potomku,
            # aby nie zostawić globalnego rejestru/config innych testów.
            import shutil
            shutil.copytree(PLUGIN, profile / "plugins" / PLUGIN.name)
            program = """import os, socket
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN')) as conn:
    from tools import terminal_tool, file_tools, kanban_tools
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    from hermes_cli.kanban_db import _resolve_worker_cli_toolsets
    import factory_worker_start as start
    result = _resolve_worker_cli_toolsets(os.environ['HERMES_HOME'])
    assert set(result or []) == start.TOOLSETS, repr(result)
    start._configuration(result, [])
    conn.assert_not_called()
print('NATIVE_CODER_CONFIG_AND_DISPATCHER_OK')
"""
            result = subprocess.run([os.environ["FACTORY_HERMES_PYTHON"], "-B", "-c", program], env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("NATIVE_CODER_CONFIG_AND_DISPATCHER_OK", result.stdout)



class StagingScopeTests(unittest.TestCase):
    """The staging guard installs only below FACTORY_HOME/.hermes of a marked private HOME. Dry-run only: never writes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "factory-home"
        self.home.mkdir(mode=0o700)
        self.home.chmod(0o700)
        self.mark()

    def mark(self, data=None, mode=0o600):
        marker = self.home / ".sf-factory-home"
        marker.write_text(json.dumps(data or {"schema": "sf-factory-home/v1", "factory_home": str(self.home)}))
        marker.chmod(mode)

    def run_installer(self, *, factory_home=None, home=None, dest=None):
        env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_HOME", "HERMES_PLUGINS_DIR")}
        env.update(FACTORY_HOME=str(factory_home or self.home), HOME=str(home or self.home),
                   HERMES_PLUGINS_DIR=str(dest or self.home / ".hermes/profiles/coder-claude/plugins"))
        return subprocess.run(["bash", str(ROOT / "hermes/install_factory_plugins.sh"), "--plugin", PLUGIN.name, "--dry-run", "--replace-reviewed"],
                              env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

    def refused(self, message, **kwargs):
        run = self.run_installer(**kwargs)
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertIn(message, run.stdout)
        self.assertFalse((self.home / ".hermes").exists(), run.stdout)

    def test_marked_private_factory_home_dry_run_accepts_without_writes(self):
        run = self.run_installer()
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("WOULD_INSTALL: factory-execution-guards", run.stdout)
        self.assertFalse((self.home / ".hermes").exists())

    def test_missing_relative_or_symlinked_factory_home_refused(self):
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_HOME"}
        env.update(HOME=str(self.home), HERMES_PLUGINS_DIR=str(self.home / ".hermes/plugins"))
        run = subprocess.run(["bash", str(ROOT / "hermes/install_factory_plugins.sh"), "--plugin", PLUGIN.name, "--dry-run"],
                             env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("requires a real absolute FACTORY_HOME", run.stdout)
        self.refused("requires a real absolute FACTORY_HOME", factory_home="factory-home")
        link = self.home.parent / "link-home"
        link.symlink_to(self.home)
        self.refused("requires a real absolute FACTORY_HOME", factory_home=link, home=link)

    def test_login_home_and_login_hermes_refused(self):
        login = Path(os.path.realpath(pwd.getpwuid(os.geteuid()).pw_dir))
        for target in (login, login / ".hermes"):
            with self.subTest(target=str(target)):
                self.refused("must not be, contain or lie inside the login ~/.hermes", factory_home=target, home=target,
                             dest=target / ".hermes/plugins")

    def test_group_accessible_factory_home_refused(self):
        self.home.chmod(0o750)
        self.refused("must be owned by the installer and mode 0700")

    def test_home_must_equal_factory_home(self):
        self.refused("requires HOME=FACTORY_HOME", home=self.home.parent)

    def test_marker_missing_shared_or_foreign_refused(self):
        (self.home / ".sf-factory-home").unlink()
        self.refused("marker missing or not private")
        self.mark(mode=0o644)
        self.refused("marker missing or not private")
        self.mark({"schema": "sf-factory-home/v1", "factory_home": "/elsewhere"})
        self.refused("marker does not name this home")

    def test_destination_outside_factory_hermes_refused(self):
        for dest in (self.home / "plugins", self.home.parent / "other/.hermes/plugins", self.home / ".hermes/../escape/plugins"):
            with self.subTest(dest=str(dest)):
                self.refused("destination outside FACTORY_HOME/.hermes", dest=dest)


if __name__ == "__main__":
    unittest.main()
