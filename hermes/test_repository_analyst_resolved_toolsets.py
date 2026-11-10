"""The repository-analyst disabled list must hold against the ORIGINAL native worker toolset resolver.

Hermes 0.20.4 adds the default-on `bfl` toolset to every CLI surface that does not disable it; the dispatcher pins the
resolved list as `--toolsets`, so an analyst profile built from the bootstrap list would gain `bfl`. The live
verify_repository_analyst_isolation.sh refuses that; this test catches it offline with the real resolver."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ALLOWED = {"factory-repository-readonly", "no_mcp", "kanban"}


def bootstrap_disabled() -> list[str]:
    text = (ROOT / "bootstrap_repository_analyst_isolation.sh").read_text()
    return json.loads(re.search(r"^EXPECTED_DISABLED='(\[.*\])'$", text, re.M).group(1))


class RepositoryAnalystResolvedToolsets(unittest.TestCase):
    def test_bootstrap_and_live_verifier_lists_agree(self):
        disabled = bootstrap_disabled()
        verify = (ROOT / "verify_repository_analyst_isolation.sh").read_text()
        boot = (ROOT / "bootstrap_repository_analyst_isolation.sh").read_text()
        words = " ".join(disabled)
        self.assertIn("expect_list_exact agent.disabled_toolsets " + words + "\n", verify)
        self.assertIn("expect_list_exact agent.disabled_toolsets " + words + "\n", boot)
        self.assertIn("for denied in " + words + "; do", verify)

    def test_original_native_resolver_adds_no_capability_to_analyst(self):
        native = Path(os.environ["FACTORY_NATIVE_ROOT"])
        self.assertTrue((native / "hermes_cli/kanban_db.py").is_file())
        config = {
            "model": {"provider": "openai-codex", "default": "gpt-test"},
            "toolsets": ["factory-repository-readonly"],
            "platform_toolsets": {"cli": ["factory-repository-readonly", "no_mcp"]},
            "agent": {"disabled_toolsets": bootstrap_disabled()},
            "plugins": {"enabled": ["factory-repository-readonly"], "disabled": []},
            "mcp_servers": {},
        }
        probe = ("import json,sys\nsys.path.insert(0,sys.argv[2])\nfrom hermes_cli import kanban_db as kb\n"
                 "print(json.dumps(kb._resolve_worker_cli_toolsets(sys.argv[1])))\n")
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "repository-analyst"
            home.mkdir()
            import yaml
            (home / "config.yaml").write_text(yaml.safe_dump(config))
            # Exact reviewed plugin bytes, as install_factory_plugins.sh places them: the resolver adds `bfl` only when
            # the plugin toolset is valid (without it the CLI falls back to a different default surface).
            shutil.copytree(ROOT / "plugins/factory-repository-readonly", home / "plugins/factory-repository-readonly")
            env = {k: v for k, v in os.environ.items() if k not in ("HERMES_HOME", "HERMES_PROFILE")}
            env.update(HOME=td, PYTHONDONTWRITEBYTECODE="1")
            run = subprocess.run([sys.executable, "-I", "-c", probe, str(home), str(native)], env=env, cwd=td,
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        resolved = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertIsNotNone(resolved)
        self.assertIn("factory-repository-readonly", resolved)
        self.assertEqual(sorted(set(resolved) - ALLOWED), [], resolved)


if __name__ == "__main__":
    unittest.main()
