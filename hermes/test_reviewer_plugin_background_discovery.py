"""The guards plugin must load for the scoped reviewer profiles on the REAL Hermes 0.20.4 CLI startup order.

G10 (first live coder-claude -> reviewer-gpt handoff): a dispatcher-spawned `hermes -p reviewer-gpt ... chat -q` starts plugin
discovery in the background "plugin-discovery" thread BEFORE the main thread imports the builtin tools. The guards plugin read
the native Kanban registry at registration, found it empty ("Hermes native Kanban registry unavailable"), was disabled, and the
reviewer profile start refused ("required plugins unavailable"). verify_reviewer_capabilities did not catch it because it imports
the Kanban tools before discovery. These probes run the exact background-discovery order in a fresh interpreter, no model."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUARD = "factory-execution-guards"
READONLY = "factory-repository-readonly"

PROBE = """import json, sys
sys.path.insert(0, sys.argv[1])
assert 'tools.kanban_tools' not in sys.modules and 'model_tools' not in sys.modules
from hermes_cli.plugins import start_background_plugin_discovery, _join_background_discovery, get_plugin_manager
start_background_plugin_discovery()            # exactly what hermes_cli/main.py does for `chat`
_join_background_discovery(timeout=90)
manager = get_plugin_manager()
guard = manager._plugins.get(sys.argv[2])
from tools.registry import registry
kanban = sorted(e.name for e in registry.get_all_entries() if e.toolset == 'kanban')
print(json.dumps(dict(loaded=guard is not None, enabled=bool(guard and guard.enabled), error=(guard.error if guard else 'missing'),
                      module=bool(guard and guard.module is not None), kanban=kanban)))
"""


def probe(profile: str, plugins: tuple[str, ...]) -> dict:
    native = Path(os.environ["FACTORY_NATIVE_ROOT"])
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / ".hermes/profiles" / profile
        home.mkdir(parents=True)
        for name in plugins:
            shutil.copytree(ROOT / "plugins" / name, home / "plugins" / name)
        config = {"plugins": {"enabled": list(plugins), "disabled": [], "entries": {GUARD: {"allow_tool_override": True}}},
                  "toolsets": list(plugins) + ["kanban"], "mcp_servers": {}, "tools": {"tool_search": {"enabled": "off"}}}
        (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
        env.update(HOME=td, HERMES_HOME=str(home), HERMES_PROFILE=profile, PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run([sys.executable, "-B", "-c", PROBE, str(native), GUARD], env=env, cwd=td, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


class ReviewerPluginBackgroundDiscovery(unittest.TestCase):
    def assert_loaded(self, result: dict) -> None:
        self.assertTrue(result["loaded"], result)
        self.assertIsNone(result["error"], result)
        self.assertTrue(result["enabled"] and result["module"], result)
        self.assertTrue(result["kanban"], result)

    def test_reviewer_gpt_guard_loads_in_background_discovery(self):
        self.assert_loaded(probe("reviewer-gpt", (GUARD, READONLY)))

    def test_release_manager_guard_loads_in_background_discovery(self):
        self.assert_loaded(probe("release-manager", (GUARD, READONLY)))


if __name__ == "__main__":
    unittest.main()
