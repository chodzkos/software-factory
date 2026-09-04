"""Adversarial regressions for the v0.11.0 security findings."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "plugins" / "factory-execution-guards"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ENTRY = _load("factory_execution_guards_v110", PLUGIN / "__init__.py")
HANDOFF = ENTRY._handoff
SUPERVISOR = _load("factory_execution_supervisor_tests_v110", PLUGIN / "supervisor.py")


class ReviewerCapabilityBoundaryTests(unittest.TestCase):
    def test_reviewer_blocks_every_generic_mutation_or_execution_surface(self):
        forbidden = [
            ("terminal", {"command": "hermes kanban complete t1"}),
            ("terminal", {"command": "python3 -c 'from hermes_cli import kanban_db'"}),
            ("terminal", {"command": "sqlite3 /tmp/kanban.db 'update tasks set status=done'"}),
            ("execute_code", {"code": "from hermes_cli import kanban_db"}),
            ("execute_code", {"code": "import sqlite3"}),
            ("write_file", {"path": "x", "content": "x"}),
            ("patch", {"path": "x", "old_string": "a", "new_string": "b"}),
        ]
        with patch.dict(os.environ, {"HERMES_PROFILE": "reviewer-gpt"}, clear=False):
            for tool, args in forbidden:
                with self.subTest(tool=tool, args=args):
                    result = ENTRY.on_pre_tool_call(tool_name=tool, args=args)
                    self.assertIsInstance(result, dict)
                    self.assertEqual(result.get("action"), "block")

    def test_real_hermes_resolver_proves_canonical_reviewer_surface(self):
        verifier = _load("reviewer_capability_verifier_v110", ROOT / "verify_reviewer_capabilities.py")
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / "reviewer-gpt"
            plugins = profile / "plugins"
            plugins.mkdir(parents=True)
            for name in ("factory-repository-readonly", "factory-execution-guards"):
                shutil.copytree(ROOT / "plugins" / name, plugins / name)
            config = {
                "toolsets": ["factory-repository-readonly", "factory-execution-guards"],
                "platform_toolsets": {"cli": ["factory-repository-readonly", "factory-execution-guards", "kanban", "no_mcp"]},
                "mcp_servers": {},
                "agent": {"disabled_toolsets": ["terminal", "file", "code_execution", "bfl", "x_search"]},
                "plugins": {"enabled": ["factory-repository-readonly", "factory-execution-guards"]},
            }
            (profile / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            hermes_source = str(Path.home() / ".hermes" / "hermes-agent")
            sys.path.insert(0, hermes_source)
            try:
                result = verifier.verify(profile)
            finally:
                sys.path.remove(hermes_source)
        self.assertEqual(result["forbidden"], [])
        self.assertIn("factory_review_approve", result["tools"])


class BoardBindingTests(unittest.TestCase):
    def test_schema_two_board_scoped_paths_cannot_collide(self):
        self.assertEqual(HANDOFF.HANDOFF_SCHEMA, 2)
        with tempfile.TemporaryDirectory() as home:
            with patch.object(Path, "home", return_value=Path(home)):
                alpha = HANDOFF.handoff_path("alpha", "t_same", 1)
                beta = HANDOFF.handoff_path("beta", "t_same", 1)
        self.assertNotEqual(alpha, beta)
        self.assertNotEqual(alpha.parent, beta.parent)


class ProcessSupervisionTests(unittest.TestCase):
    def test_supervisor_api_is_installed_for_full_lifetime_authorization(self):
        supervisor_path = PLUGIN / "supervisor.py"
        self.assertTrue(supervisor_path.is_file())
        supervisor = _load("factory_execution_supervisor_v110", supervisor_path)
        self.assertTrue(callable(getattr(supervisor, "supervise", None)))
        self.assertTrue(callable(getattr(supervisor, "mutation_lease", None)))

    def test_reclaim_kills_grandchild_prevents_late_write_and_holds_lease(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            started = root / "started"
            sentinel = root / "late"
            fake = root / "claude"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib,subprocess,sys,time\n"
                "started,sentinel=sys.argv[1:3]\n"
                "subprocess.Popen([sys.executable,'-c',"
                "'import pathlib,sys,time; time.sleep(0.7); pathlib.Path(sys.argv[1]).write_text(\\\"late\\\")',sentinel])\n"
                "pathlib.Path(started).write_text('started')\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            active = {"value": True}
            result: dict[str, int] = {}
            worker_pid, worker_start = HANDOFF._process_identity()
            env = {
                "PATH": f"{root}:{os.environ.get('PATH', '')}",
                "HERMES_KANBAN_BOARD": "isolated",
                "HERMES_KANBAN_TASK": "t_supervise",
                "HERMES_KANBAN_RUN_ID": "9",
                "HERMES_KANBAN_WORKSPACE": str(workspace),
            }

            def run_supervisor():
                result["rc"] = SUPERVISOR.supervise(
                    ["claude", str(started), str(sentinel)], board="isolated",
                    task_id="t_supervise", run_id=9, workspace=str(workspace),
                    worker_pid=worker_pid, worker_start=worker_start, poll_seconds=0.01,
                )

            with patch.dict(os.environ, env, clear=False), patch.object(
                SUPERVISOR._HANDOFF, "active_coder_run_matches", side_effect=lambda: active["value"]
            ), patch.object(SUPERVISOR, "_ambient_board", return_value="isolated"), patch.object(
                SUPERVISOR._HANDOFF, "_load_kanban_db", return_value=object()
            ), patch.object(SUPERVISOR._HANDOFF.Path, "home", return_value=root):
                thread = threading.Thread(target=run_supervisor, daemon=True)
                thread.start()
                deadline = time.monotonic() + 5
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(started.exists())
                probe = root / "lease_probe.py"
                probe.write_text(
                    "import importlib.util,pathlib,sys\n"
                    f"p=pathlib.Path({str(PLUGIN / 'handoff.py')!r})\n"
                    "s=importlib.util.spec_from_file_location('lease_probe_handoff',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                    f"\ntry:\n with m.mutation_lease('isolated','t_supervise',{str(workspace)!r}): pass\nexcept m.HandoffError: raise SystemExit(7)\nraise SystemExit(0)\n",
                    encoding="utf-8",
                )
                probe_env = dict(os.environ)
                probe_env["HOME"] = str(root)
                overlap = __import__("subprocess").run([sys.executable, str(probe)], env=probe_env, check=False)
                self.assertEqual(overlap.returncode, 7)
                active["value"] = False
                thread.join(5)
                self.assertFalse(thread.is_alive())
            time.sleep(0.9)
            self.assertEqual(result.get("rc"), 125)
            self.assertFalse(sentinel.exists())

    def test_normal_supervised_execution_succeeds_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            fake = root / "claude"
            fake.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
            fake.chmod(0o755)
            worker_pid, worker_start = HANDOFF._process_identity()
            env = {
                "PATH": f"{root}:{os.environ.get('PATH', '')}",
                "HERMES_KANBAN_BOARD": "isolated", "HERMES_KANBAN_TASK": "t_supervise",
                "HERMES_KANBAN_RUN_ID": "9", "HERMES_KANBAN_WORKSPACE": str(workspace),
            }
            with patch.dict(os.environ, env, clear=False), patch.object(
                SUPERVISOR._HANDOFF, "active_coder_run_matches", return_value=True
            ), patch.object(SUPERVISOR, "_ambient_board", return_value="isolated"), patch.object(
                SUPERVISOR._HANDOFF, "_load_kanban_db", return_value=object()
            ), patch.object(SUPERVISOR._HANDOFF.Path, "home", return_value=root):
                rc = SUPERVISOR.supervise(
                    ["claude"], board="isolated", task_id="t_supervise", run_id=9,
                    workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start,
                )
                with SUPERVISOR._HANDOFF.mutation_lease("isolated", "t_supervise", str(workspace)):
                    pass
            self.assertEqual(rc, 0)

    def test_worker_death_and_board_switch_terminate_running_tree(self):
        import subprocess
        for trigger in ("worker_death", "board_switch"):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as td:
                root = Path(td); workspace = root / "workspace"; workspace.mkdir()
                started = root / "started"; fake = root / "claude"
                fake.write_text(
                    "#!/usr/bin/env python3\nimport pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text('started')\ntime.sleep(30)\n",
                    encoding="utf-8",
                ); fake.chmod(0o755)
                worker = subprocess.Popen(["sleep", "30"])
                worker_pid, worker_start = HANDOFF._process_identity(worker.pid)
                ambient = {"value": "isolated"}; result = {}
                env = {"PATH":f"{root}:{os.environ.get('PATH','')}","HERMES_KANBAN_BOARD":"isolated","HERMES_KANBAN_TASK":"t_supervise","HERMES_KANBAN_RUN_ID":"9","HERMES_KANBAN_WORKSPACE":str(workspace)}
                def run():
                    result["rc"] = SUPERVISOR.supervise(["claude",str(started)],board="isolated",task_id="t_supervise",run_id=9,workspace=str(workspace),worker_pid=worker_pid,worker_start=worker_start,poll_seconds=0.01)
                try:
                    with patch.dict(os.environ,env,clear=False),patch.object(SUPERVISOR._HANDOFF,"active_coder_run_matches",return_value=True),patch.object(SUPERVISOR,"_ambient_board",side_effect=lambda _kb: ambient["value"]),patch.object(SUPERVISOR._HANDOFF,"_load_kanban_db",return_value=object()),patch.object(SUPERVISOR._HANDOFF.Path,"home",return_value=root):
                        thread=threading.Thread(target=run,daemon=True); thread.start(); deadline=time.monotonic()+5
                        while not started.exists() and time.monotonic()<deadline: time.sleep(0.01)
                        self.assertTrue(started.exists())
                        if trigger=="worker_death": worker.terminate(); worker.wait(timeout=5)
                        else: ambient["value"]="other"
                        thread.join(5); self.assertFalse(thread.is_alive())
                    self.assertEqual(result.get("rc"),125)
                finally:
                    if worker.poll() is None: worker.terminate(); worker.wait(timeout=5)


class AtomicApprovalTests(unittest.TestCase):
    def test_guarded_completion_and_downstream_revalidation_are_exposed(self):
        self.assertTrue(callable(getattr(HANDOFF, "guarded_reviewer_complete", None)))
        self.assertTrue(callable(getattr(HANDOFF, "verify_downstream_approval", None)))


if __name__ == "__main__":
    unittest.main()
