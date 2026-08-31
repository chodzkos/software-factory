from __future__ import annotations

import importlib.util
import os
import sys
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
        env = {
            "HERMES_PROFILE": "",
            "HERMES_HOME": "",
        }
        env.update(values)
        return env

    def test_profile_cli_home_activates_protected_profile(self):
        with patch.object(PLUGIN.Path, "home", return_value=Path("/home/test")):
            with patch.dict(
                os.environ,
                self._env(HERMES_HOME="/home/test/.hermes/profiles/reviewer-claude"),
                clear=False,
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE"), "reviewer-claude")

    def test_arbitrary_profile_home_does_not_activate_guard_identity(self):
        with patch.object(PLUGIN.Path, "home", return_value=Path("/home/test")):
            with patch.dict(
                os.environ,
                self._env(HERMES_HOME="/home/test/.hermes/profiles/default"),
                clear=False,
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE", ""), "")

    def test_noncanonical_home_does_not_activate_guard_identity(self):
        with patch.object(PLUGIN.Path, "home", return_value=Path("/home/test")):
            with patch.dict(
                os.environ,
                self._env(HERMES_HOME="/tmp/reviewer-claude"),
                clear=False,
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE", ""), "")

    def test_explicit_worker_profile_wins(self):
        with patch.object(PLUGIN.Path, "home", return_value=Path("/home/test")):
            with patch.dict(
                os.environ,
                self._env(
                    HERMES_PROFILE="coder-claude",
                    HERMES_HOME="/home/test/.hermes/profiles/reviewer-claude",
                ),
                clear=False,
            ):
                PLUGIN._activate_profile_identity()
                self.assertEqual(os.environ.get("HERMES_PROFILE"), "coder-claude")

    def test_pre_hook_recovers_profile_before_guard_dispatch(self):
        seen: list[str] = []

        def fake_pre(*args, **kwargs):
            seen.append(os.environ.get("HERMES_PROFILE", ""))
            return {"action": "block", "message": "sentinel"}

        with patch.object(PLUGIN.Path, "home", return_value=Path("/home/test")):
            with patch.object(PLUGIN, "_on_pre_tool_call", side_effect=fake_pre):
                with patch.dict(
                    os.environ,
                    self._env(HERMES_HOME="/home/test/.hermes/profiles/runtime-controller"),
                    clear=False,
                ):
                    result = PLUGIN.on_pre_tool_call(tool_name="terminal", args={"command": "git status"})

        self.assertEqual(seen, ["runtime-controller"])
        self.assertEqual(result, {"action": "block", "message": "sentinel"})


if __name__ == "__main__":
    unittest.main()
