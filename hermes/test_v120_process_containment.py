"""Regresje v0.12.0 dla kernelowego ograniczenia drzewa Claude."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parent / "plugins" / "factory-execution-guards"


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not path.exists():
        raise AssertionError(f"timed out waiting for {path}")


def _kill_identity(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _load_supervisor(name: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / "supervisor.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("supervisor module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class HermesOuterTimeoutTests(unittest.TestCase):
    def test_required_ptrace_exitkill_backend_is_available(self) -> None:
        module = _load_supervisor("v120_backend_probe")
        self.assertEqual(module.containment_backend_status(), "ptrace-exitkill-seccomp")

    def test_normal_success_and_nonzero_child_exit_propagate(self) -> None:
        module = _load_supervisor("v120_exit_propagation")
        with tempfile.TemporaryDirectory(prefix="sf-v120-exit-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            fake = root / "claude"
            fake.write_text("#!/usr/bin/env python3\nimport sys\nraise SystemExit(int(sys.argv[1]))\n", encoding="utf-8")
            fake.chmod(0o755)
            worker_pid, worker_start = module._HANDOFF._process_identity()
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            with patch.dict(os.environ, env, clear=False), patch.object(module._HANDOFF, "active_coder_run_matches", return_value=True), patch.object(module._HANDOFF, "_load_kanban_db", return_value=object()), patch.object(module, "_ambient_board", return_value="isolated"), patch.object(module._HANDOFF.Path, "home", return_value=root):
                self.assertEqual(module.supervise(["claude", "0"], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start), 0)
                self.assertEqual(module.supervise(["claude", "23"], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start), 23)

    def test_authorization_exception_uses_complete_cleanup_path(self) -> None:
        module = _load_supervisor("v120_exception_cleanup")
        with tempfile.TemporaryDirectory(prefix="sf-v120-exception-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            sentinel = root / "late"
            fake = root / "claude"
            fake.write_text("#!/usr/bin/env python3\nimport pathlib,sys,time\ntime.sleep(0.8)\npathlib.Path(sys.argv[1]).write_text('late')\n", encoding="utf-8")
            fake.chmod(0o755)
            worker_pid, worker_start = module._HANDOFF._process_identity()
            calls = {"count": 0}
            def authorization():
                calls["count"] += 1
                if calls["count"] > 1:
                    raise RuntimeError("synthetic authorization failure")
                return True
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            with patch.dict(os.environ, env, clear=False), patch.object(module._HANDOFF, "active_coder_run_matches", side_effect=authorization), patch.object(module._HANDOFF, "_load_kanban_db", return_value=object()), patch.object(module, "_ambient_board", return_value="isolated"), patch.object(module._HANDOFF.Path, "home", return_value=root):
                with self.assertRaisesRegex(RuntimeError, "synthetic authorization failure"):
                    module.supervise(["claude", str(sentinel)], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start, poll_seconds=0.01)
            time.sleep(0.9)
            self.assertFalse(sentinel.exists())

    def test_unavailable_ptrace_options_fail_before_claude_user_code(self) -> None:
        module = _load_supervisor("v120_unavailable_backend")
        with tempfile.TemporaryDirectory(prefix="sf-v120-unavailable-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            sentinel = root / "started"
            fake = root / "claude"
            fake.write_text(f"#!/usr/bin/env python3\nimport pathlib\npathlib.Path({str(sentinel)!r}).write_text('started')\n", encoding="utf-8")
            fake.chmod(0o755)
            worker_pid, worker_start = module._HANDOFF._process_identity()
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            with patch.dict(os.environ, env, clear=False), patch.object(module._HANDOFF, "active_coder_run_matches", return_value=True), patch.object(module._HANDOFF, "_load_kanban_db", return_value=object()), patch.object(module, "_ambient_board", return_value="isolated"), patch.object(module._HANDOFF.Path, "home", return_value=root), patch.object(module, "_set_trace_options", side_effect=OSError(1, "operation not permitted")):
                with self.assertRaises(module.ContainmentUnavailable):
                    module.supervise(["claude"], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start)
            self.assertFalse(sentinel.exists())

    def test_sigterm_of_supervisor_group_cancels_child_before_lease_release(self) -> None:
        """Modeluje LocalEnvironment: nowa sesja, TERM, po 1 s KILL fallback."""
        with tempfile.TemporaryDirectory(prefix="sf-v120-timeout-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            started = root / "started"
            sentinel = root / "late"
            fake = root / "claude"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os,pathlib,sys,time\n"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                "time.sleep(1.4)\n"
                "pathlib.Path(sys.argv[2]).write_text('late-write')\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            runner = root / "runner.py"
            runner.write_text(
                "import importlib.util,os,pathlib,sys\n"
                f"p=pathlib.Path({str(PLUGIN / 'supervisor.py')!r})\n"
                "s=importlib.util.spec_from_file_location('v120_timeout_sup',p); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m)\n"
                "m._HANDOFF.active_coder_run_matches=lambda: True\n"
                "m._HANDOFF._load_kanban_db=lambda: object()\n"
                "m._ambient_board=lambda _kb: 'isolated'\n"
                "worker_pid,worker_start=m._HANDOFF._process_identity(os.getppid())\n"
                f"raise SystemExit(m.supervise(['claude',{str(started)!r},{str(sentinel)!r}],board='isolated',task_id='t_v120',run_id=9,workspace={str(workspace)!r},worker_pid=worker_pid,worker_start=worker_start,poll_seconds=0.01))\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                PATH=f"{root}:{env.get('PATH', '')}",
                HOME=str(root),
                HERMES_KANBAN_BOARD="isolated",
                HERMES_KANBAN_TASK="t_v120",
                HERMES_KANBAN_RUN_ID="9",
                HERMES_KANBAN_WORKSPACE=str(workspace),
            )
            proc = subprocess.Popen(
                [sys.executable, str(runner)],
                cwd=workspace,
                env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = -1
            try:
                _wait_for(started)
                child_pid = int(started.read_text(encoding="utf-8"))
                self.assertEqual(os.getpgid(proc.pid), os.getpgid(child_pid))
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2.0)
                with self.assertRaises(Exception):
                    os.kill(child_pid, 0)
                time.sleep(1.5)
                self.assertFalse(sentinel.exists())
                # Flock becomes available only after the contained writer is gone.
                lease_probe = root / "lease_probe.py"
                lease_probe.write_text(
                    "import importlib.util,pathlib,sys\n"
                    f"p=pathlib.Path({str(PLUGIN / 'handoff.py')!r})\n"
                    "s=importlib.util.spec_from_file_location('v120_lease',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                    f"\nwith m.mutation_lease('isolated','t_v120',{str(workspace)!r},blocking=False): pass\n",
                    encoding="utf-8",
                )
                acquired = subprocess.run([sys.executable, str(lease_probe)], env=env, check=False)
                self.assertEqual(acquired.returncode, 0)
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=2.0)
                if child_pid > 0:
                    _kill_identity(child_pid)

    def test_sighup_of_outer_supervisor_group_cleans_child(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sf-v120-hup-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            started = root / "started"
            sentinel = root / "late"
            fake = root / "claude"
            fake.write_text("#!/usr/bin/env python3\nimport os,pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(0.8)\npathlib.Path(sys.argv[2]).write_text('late')\n", encoding="utf-8")
            fake.chmod(0o755)
            runner = root / "runner.py"
            runner.write_text(
                "import importlib.util,os,pathlib,sys\n"
                f"p=pathlib.Path({str(PLUGIN / 'supervisor.py')!r}); s=importlib.util.spec_from_file_location('v120_hup_sup',p); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m)\n"
                "m._HANDOFF.active_coder_run_matches=lambda: True; m._HANDOFF._load_kanban_db=lambda: object(); m._ambient_board=lambda _kb:'isolated'\n"
                "worker_pid,worker_start=m._HANDOFF._process_identity(os.getppid())\n"
                f"raise SystemExit(m.supervise(['claude',{str(started)!r},{str(sentinel)!r}],board='isolated',task_id='t_v120',run_id=9,workspace={str(workspace)!r},worker_pid=worker_pid,worker_start=worker_start,poll_seconds=0.01))\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            proc = subprocess.Popen([sys.executable, str(runner)], cwd=workspace, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child_pid = -1
            try:
                _wait_for(started)
                child_pid = int(started.read_text(encoding="utf-8"))
                os.killpg(proc.pid, signal.SIGHUP)
                self.assertEqual(proc.wait(timeout=2.0), 128 + signal.SIGHUP)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                time.sleep(0.9)
                self.assertFalse(sentinel.exists())
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=2.0)
                if child_pid > 0:
                    _kill_identity(child_pid)

    def test_clone3_is_forced_to_enosys_for_traced_legacy_fallback(self) -> None:
        module = _load_supervisor("v120_clone3_filter")
        with tempfile.TemporaryDirectory(prefix="sf-v120-clone3-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            observed = root / "errno"
            fake = root / "claude"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import ctypes,pathlib,sys\n"
                "libc=ctypes.CDLL(None,use_errno=True)\n"
                "x32=libc.syscall(0x40000038,0x00800000|17,0,0,0,0); x32_errno=ctypes.get_errno()\n"
                "result=libc.syscall(435,0,0); value=ctypes.get_errno()\n"
                "pathlib.Path(sys.argv[1]).write_text(f'{x32_errno},{value}')\n"
                "raise SystemExit(0 if x32==-1 and x32_errno==38 and result==-1 and value==38 else 9)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            worker_pid, worker_start = module._HANDOFF._process_identity()
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            with patch.dict(os.environ, env, clear=False), patch.object(module._HANDOFF, "active_coder_run_matches", return_value=True), patch.object(module._HANDOFF, "_load_kanban_db", return_value=object()), patch.object(module, "_ambient_board", return_value="isolated"), patch.object(module._HANDOFF.Path, "home", return_value=root):
                rc = module.supervise(["claude", str(observed)], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start)
            self.assertEqual(rc, 0)
            self.assertEqual(observed.read_text(encoding="utf-8"), "38,38")

    def test_clone_untraced_flag_cannot_create_an_escaped_writer(self) -> None:
        module = _load_supervisor("v120_clone_untraced")
        with tempfile.TemporaryDirectory(prefix="sf-v120-untraced-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            descendant_file = root / "descendant-pid"
            sentinel = root / "late"
            fake = root / "claude"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import ctypes,os,pathlib,signal,sys,time\n"
                "pidfile,sentinel=sys.argv[1:3]\n"
                "libc=ctypes.CDLL(None,use_errno=True)\n"
                "result=libc.syscall(56,0x00800000|signal.SIGCHLD,0,0,0,0)\n"
                "if result==0:\n"
                " pathlib.Path(pidfile).write_text(str(os.getpid())); os.setsid(); time.sleep(0.8); pathlib.Path(sentinel).write_text('clone-untraced'); os._exit(0)\n"
                "if result<0: os._exit(77)\n"
                "os._exit(0)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            worker_pid, worker_start = module._HANDOFF._process_identity()
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            descendant_pid = -1
            try:
                with patch.dict(os.environ, env, clear=False), patch.object(module._HANDOFF, "active_coder_run_matches", return_value=True), patch.object(module._HANDOFF, "_load_kanban_db", return_value=object()), patch.object(module, "_ambient_board", return_value="isolated"), patch.object(module._HANDOFF.Path, "home", return_value=root):
                    rc = module.supervise(["claude", str(descendant_file), str(sentinel)], board="isolated", task_id="t_v120", run_id=9, workspace=str(workspace), worker_pid=worker_pid, worker_start=worker_start, poll_seconds=0.01)
                self.assertEqual(rc, 77)
                self.assertFalse(descendant_file.exists())
                time.sleep(0.9)
                self.assertFalse(sentinel.exists())
            finally:
                if descendant_pid > 0:
                    _kill_identity(descendant_pid)

    def _exercise_ready_double_fork(self, trigger: str) -> None:
        """Separate active-run loss from root exit; never assume fork timing."""
        self.assertIn(trigger, {"active", "leader", "positive-control"})
        with tempfile.TemporaryDirectory(prefix="sf-v120-setsid-") as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            active = root / "active"
            active.write_text("yes", encoding="utf-8")
            descendant_file = root / "descendant-pid"
            write_gate = root / "allow-write"
            leader_exit = root / "leader-exit"
            sentinel = root / "late"
            child_log = root / "supervisor.log"
            fake = root / "claude"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os,pathlib,sys,time\n"
                "pid_file,write_gate,leader_exit,sentinel=map(pathlib.Path,sys.argv[1:5])\n"
                "leader=os.getpid()\n"
                "first=os.fork()\n"
                "if first==0:\n"
                " os.setsid(); second=os.fork()\n"
                " if second==0:\n"
                "  pending=pid_file.with_suffix('.pending')\n"
                "  pending.write_text(json.dumps({'pid':os.getpid(),'leader':leader,'sid':os.getsid(0)}))\n"
                "  pending.replace(pid_file)\n"
                "  while not write_gate.exists(): time.sleep(0.01)\n"
                "  pending=sentinel.with_suffix('.pending'); pending.write_text('late-setsid'); pending.replace(sentinel)\n"
                "  while True: time.sleep(0.01)\n"
                " os._exit(0)\n"
                # Keep the original leader alive until the test explicitly ends
                # its run or requests leader exit. Immediate root exit is a
                # DIFFERENT cancellation trigger from active-run revocation.
                "while not leader_exit.exists(): time.sleep(0.01)\n"
                "os._exit(0)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            runner = root / "runner.py"
            runner.write_text(
                "import importlib.util,os,pathlib,sys\n"
                f"p=pathlib.Path({str(PLUGIN / 'supervisor.py')!r})\n"
                "s=importlib.util.spec_from_file_location('v120_setsid_sup',p); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m)\n"
                f"m._HANDOFF.active_coder_run_matches=lambda: pathlib.Path({str(active)!r}).exists()\n"
                "m._HANDOFF._load_kanban_db=lambda: object(); m._ambient_board=lambda _kb: 'isolated'\n"
                "worker_pid,worker_start=m._HANDOFF._process_identity(os.getppid())\n"
                f"raise SystemExit(m.supervise(['claude',{str(descendant_file)!r},{str(write_gate)!r},{str(leader_exit)!r},{str(sentinel)!r}],board='isolated',task_id='t_v120',run_id=9,workspace={str(workspace)!r},worker_pid=worker_pid,worker_start=worker_start,poll_seconds=0.01))\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(PATH=f"{root}:{env.get('PATH', '')}", HOME=str(root), HERMES_KANBAN_BOARD="isolated", HERMES_KANBAN_TASK="t_v120", HERMES_KANBAN_RUN_ID="9", HERMES_KANBAN_WORKSPACE=str(workspace))
            descendant_pid = -1
            descendant_fd = None
            with child_log.open("wb") as output:
                proc = subprocess.Popen([sys.executable, str(runner)], cwd=workspace, env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                try:
                    # Readiness is an atomic publication by the real grandchild,
                    # after setsid/double-fork. A dead supervisor is not readiness.
                    deadline = time.monotonic() + 5.0
                    while not descendant_file.exists():
                        self.assertIsNone(proc.poll(), child_log.read_text(encoding="utf-8", errors="replace"))
                        self.assertLess(time.monotonic(), deadline, "grandchild did not become ready; " + child_log.read_text(encoding="utf-8", errors="replace"))
                        time.sleep(0.01)
                    identity = json.loads(descendant_file.read_text(encoding="utf-8"))
                    descendant_pid = identity["pid"]
                    descendant_fd = os.pidfd_open(descendant_pid, 0)
                    self.assertIsNone(proc.poll(), "supervisor exited before cancellation")
                    os.kill(identity["leader"], 0)
                    os.kill(descendant_pid, 0)
                    self.assertEqual(os.getsid(descendant_pid), identity["sid"])
                    self.assertNotEqual(os.getsid(descendant_pid), os.getsid(proc.pid))
                    self.assertNotEqual(os.getpgid(descendant_pid), os.getpgid(proc.pid))
                    self.assertTrue(active.exists())
                    self.assertFalse(sentinel.exists())
                    if trigger == "positive-control":
                        # Prove that a ready, still-authorized descendant really
                        # can execute the sentinel write; absence is not vacuous.
                        write_gate.write_text("allowed", encoding="utf-8")
                        _wait_for(sentinel)
                        self.assertEqual(sentinel.read_text(encoding="utf-8"), "late-setsid")
                        self.assertIsNone(proc.poll())
                        active.unlink()
                    elif trigger == "active":
                        active.unlink()
                    else:
                        leader_exit.write_text("exit", encoding="utf-8")
                        self.assertTrue(active.exists())
                    try:
                        rc = proc.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        self.fail("supervisor did not complete cancellation; " + child_log.read_text(encoding="utf-8", errors="replace"))
                    self.assertEqual(rc, 125, child_log.read_text(encoding="utf-8", errors="replace"))
                    # Require complete reap, not merely a non-running zombie.
                    with self.assertRaises(ProcessLookupError):
                        os.kill(descendant_pid, 0)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(identity["leader"], 0)
                    if trigger != "positive-control":
                        write_gate.write_text("after-cancellation", encoding="utf-8")
                        time.sleep(0.15)
                        self.assertFalse(sentinel.exists())
                    if trigger == "leader":
                        self.assertTrue(active.exists(), "leader-exit test must not revoke authorization")
                finally:
                    if proc.poll() is None:
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            proc.wait(timeout=2.0)
                    if descendant_fd is not None:
                        try:
                            signal.pidfd_send_signal(descendant_fd, signal.SIGKILL, None, 0)
                        except ProcessLookupError:
                            pass
                        finally:
                            os.close(descendant_fd)

    def test_active_run_loss_kills_rapid_double_fork_setsid_descendant(self) -> None:
        self._exercise_ready_double_fork("active")

    def test_leader_exit_kills_ready_double_fork_setsid_descendant(self) -> None:
        self._exercise_ready_double_fork("leader")

    def test_authorized_ready_double_fork_can_reach_writer_positive_control(self) -> None:
        self._exercise_ready_double_fork("positive-control")

if __name__ == "__main__":
    unittest.main()
