"""Pełny natywny schemat/SQL w lokalnej bazie; claim i attestation są syntetyczne.

Nie uruchamia dispatchera, Claude'a ani providera. Zastany WAL jest warunkiem
fixture, nie zmianą polityki połączeń w produkcie. Każdy odczyt receipt korzysta
z niezależnego połączenia do tej samej tymczasowej bazy.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any
from unittest.mock import patch


class NativeReviewDatabase:
    """Kontrolowane zadanie w pełnym schemacie natywnego modułu kanban_db."""

    def __init__(
        self, root: Path, *, task_id: str = "t_fixture", run_id: int = 7,
        board: str = "isolated", workspace: Path | None = None,
    ) -> None:
        self.root = root.resolve(strict=True)
        self.task_id, self.run_id, self.board = task_id, run_id, board
        self.workspace = workspace or self.root / "repo" / ".worktrees" / task_id
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = self.workspace.resolve(strict=True)
        self.repo = self.workspace.parent.parent
        assert self.workspace == self.repo / ".worktrees" / task_id
        self.stack = ExitStack()
        self.home = self.root / ".hermes" / "profiles" / "coder-claude"
        self.home.mkdir(parents=True, exist_ok=True)
        self.stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.root), "HERMES_HOME": str(self.home),
            "HERMES_PROFILE": "coder-claude", "HERMES_KANBAN_TASK": task_id,
            "HERMES_KANBAN_RUN_ID": str(run_id), "HERMES_KANBAN_BOARD": board,
            "HERMES_KANBAN_WORKSPACE": str(self.workspace),
            "HERMES_KANBAN_HOME": str(self.root / "kanban-home"),
            "TERMINAL_ENV": "local", "PYTHONDONTWRITEBYTECODE": "1",
        }))
        try:
            native = Path(os.environ["FACTORY_NATIVE_ROOT"]).resolve(strict=True)
            if str(native) not in sys.path:
                self.stack.enter_context(patch.object(sys, "path", [str(native), *sys.path]))
            from hermes_cli import kanban_db as kb
            self.kb = kb
            kb.create_board(board)
            self.db = kb.kanban_db_path(board=board)
            with kb.connect_closing(board=board) as conn:
                with patch.object(kb, "_new_task_id", return_value=task_id):
                    actual = kb.create_task(
                        conn, title="OFFLINE NATIVE FIXTURE", assignee="coder-claude",
                        body=f"WORKSPACE: worktree:{self.repo}\n", workspace_kind="worktree",
                        workspace_path=str(self.workspace), initial_status="running", board=board,
                    )
                assert actual == task_id
                now = int(time.time())
                conn.execute(
                    "UPDATE tasks SET status='running',current_run_id=?,claim_lock=?,claim_expires=?,worker_pid=? WHERE id=?",
                    (run_id, "fixture-owner", now + 600, os.getpid(), task_id),
                )
                conn.execute(
                    "INSERT INTO task_runs(id,task_id,profile,status,started_at,metadata,claim_lock,claim_expires,worker_pid) "
                    "VALUES(?,?,'coder-claude','running',?,?,?,?,?)",
                    (run_id, task_id, now - 1, json.dumps(self.metadata), "fixture-owner", now + 600, os.getpid()),
                )
                conn.commit()
                assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
            self.stack.enter_context(patch.object(kb, "connect", side_effect=self.connect))
            if not (self.workspace / ".git").exists():
                (self.workspace / "tracked.txt").write_text("approved\n", encoding="utf-8")
                env = {"PATH": os.defpath, "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
                for args in (["init", "-q"], ["add", "."],
                             ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]):
                    subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(self.workspace), *args],
                                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except BaseException:
            self.stack.close()
            raise

    @property
    def metadata(self) -> dict[str, str]:
        return {"task_id": self.task_id, "workspace": str(self.workspace), "workspace_path": str(self.workspace)}

    def connect(self, db_path: Any = None, *, board: str | None = None) -> sqlite3.Connection:
        """Otwórz zawsze nowe połączenie; odrzucaj inne plansze i ścieżki."""
        if board not in (None, self.board) or (db_path is not None and Path(db_path) != self.db):
            raise AssertionError("fixture attempted another database")
        conn = sqlite3.connect(self.db, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def close(self) -> None:
        # Zatrzymaj i opróżnij natywną kolejkę przed usunięciem katalogu testu.
        logging_module = sys.modules.get("hermes_logging")
        if logging_module is not None:
            logging_module._reset_queued_handlers()
        self.stack.close()

    def __enter__(self) -> NativeReviewDatabase:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def bind_unit_guard(self, entry: Any) -> None:
        """Podłącz rzeczywistą bazę do testu hooka, bez fałszywego prepare/COMMIT."""
        entry._review_intent.invalidate()
        self.stack.enter_context(patch.object(entry._handoff, "_evidence_root", side_effect=lambda: entry._guard.EVIDENCE_ROOT))

    def start_reviewer(self, seal: dict[str, Any]) -> int:
        """Modeluj claim dispatchera; samo przekazanie i receipt są już trwałe."""
        rid = self.run_id + 1
        metadata = {
            **self.metadata, "factory_handoff_schema": 3, "factory_handoff_board": self.board,
            "factory_handoff_seal_id": seal["seal_id"], "factory_handoff_git_head": seal["git_head"],
            "factory_handoff_content_state_sha256": seal["content_state_sha256"],
            "factory_handoff_implementer_run_id": self.run_id,
        }
        with self.kb.connect_closing(board=self.board) as conn:
            conn.execute(
                "UPDATE tasks SET status='running',assignee='reviewer-gpt',current_run_id=?,"
                "worker_pid=?,claim_lock='reviewer-fixture',claim_expires=? WHERE id=?",
                (rid, os.getpid(), int(time.time()) + 600, self.task_id),
            )
            conn.execute(
                "INSERT INTO task_runs(id,task_id,profile,status,started_at,metadata,worker_pid,claim_lock) "
                "VALUES(?,?,'reviewer-gpt','running',?,?,?,'reviewer-fixture')",
                (rid, self.task_id, int(time.time()), json.dumps(metadata), os.getpid()),
            )
            conn.commit()
        os.environ.update(HERMES_PROFILE="reviewer-gpt", HERMES_KANBAN_RUN_ID=str(rid))
        return rid


class NativeReviewWorker(NativeReviewDatabase):
    """Rzeczywisty initializer, loader, registry, invoke, adapter i SQL; bez modelu."""

    def __init__(self, root: Path, **kwargs: Any) -> None:
        super().__init__(root, **kwargs)
        self.agent: Any = None
        self.connect_spies: list[Any] = []
        try:
            import factory_worker_start as start
            plugin = Path(__file__).resolve().parents[1] / "plugins" / start.PLUGIN
            shutil.copytree(plugin, self.home / "plugins" / start.PLUGIN)
            config = {
                "toolsets": sorted(start.TOOLSETS), "tools": {"tool_search": {"enabled": "off"}},
                "mcp_servers": {}, "plugins": {"enabled": [start.PLUGIN], "disabled": [],
                "entries": {start.PLUGIN: {"allow_tool_override": True}}},
                "factory": {"execution_backend": "claude-code", "claude_model_class": "sonnet"},
                "model": {"context_length": 131072}, "compression": {"enabled": False},
                "agent": {"environment_probe": False},
            }
            (self.home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            import factory_worker_lifetime as life
            from hermes_cli.plugins import get_plugin_manager
            import run_agent
            self.manager = get_plugin_manager()
            self.start, self.life = start, life
            for name in ("connect", "connect_ex"):
                spy = self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError("NETWORK_FORBIDDEN")))
                self.connect_spies.append(spy)
            self.agent = run_agent.AIAgent(
                enabled_toolsets=sorted(start.TOOLSETS), provider="bedrock", api_mode="bedrock_converse",
                model="local-fixture", api_key="test-not-real", base_url="http://127.0.0.1:9/v1",
                skip_memory=True, skip_context_files=True, skip_background_review=True,
                quiet_mode=True, platform="cli", max_iterations=1,
            )
            self.state = life.state_for(self.agent)
            self.entry = self.state.proof.module
            self.guard, self.handoff = self.entry._guard, self.entry._handoff
            binary = self.root / "SYNTHETIC_CLAUDE_NOT_EXECUTABLE"
            binary.write_text("test only; no CLI executed\n", encoding="utf-8")
            self.cli_identity = (str(binary), hashlib.sha256(binary.read_bytes()).hexdigest())
            self.stack.enter_context(patch.object(self.guard, "_canonical_claude_identity", return_value=self.cli_identity))
        except BaseException:
            self.close()
            raise

    def attest(self, evidence: dict[str, Any] | None = None, *, register_nonce: bool = True) -> dict[str, Any]:
        """Write synthetic evidence and optionally inject a synthetic nonce.

        This BYPASSES production of CLI provenance: no command is executed.
        Downstream file/nonce checking is real; this does not prove the evidence
        can only be earned by genuine execution. register_nonce=False exercises
        the explicit negative (well-formed file alone must not authorize).
        """
        h = self.handoff
        state = h.workspace_content_state(str(self.workspace))
        assert state is not None
        if evidence is None:
            evidence = {
                "schema": 6, "profile": "coder-claude", "task_id": self.task_id, "run_id": str(self.run_id),
                "model_class": "sonnet", "workspace": str(self.workspace), "execution_cwd": str(self.workspace),
                "terminal_args_sha256": "1" * 64, "claude_binary": self.cli_identity[0],
                "claude_binary_sha256": self.cli_identity[1], "session_id": "SYNTHETIC_NO_CLAUDE_SESSION",
                "success": True, "command_sha256": "3" * 64, "attestation_id": "4" * 64,
                "git_head_before": state[0], "git_head_after": state[0],
                "workspace_content_state_before_sha256": state[1], "workspace_content_state_after_sha256": state[1],
                "recorded_at": int(time.time()),
            }
        path = h.execution_evidence_path(self.board, self.task_id, self.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
        key = self.guard._attestation_key("coder-claude", self.task_id, str(self.run_id))
        if register_nonce:
            self.guard._COMPLETED_ATTESTATIONS[key] = {key: evidence[key] for key in (
                "attestation_id", "command_sha256", "execution_cwd", "terminal_args_sha256")}
        assert self.guard._evidence_exists("coder-claude", self.task_id) is register_nonce
        return evidence

    def publish(self) -> dict[str, Any]:
        args = {"summary": "OFFLINE NATIVE HANDOFF", "reviewer": "reviewer-gpt", "metadata": self.metadata}
        raw = self.agent._invoke_tool("kanban_request_review", args, self.task_id)
        assert json.loads(raw) == {"ok": True, "task_id": self.task_id, "run_id": self.run_id, "status": "review"}, raw
        seal = self.handoff.load_handoff_seal(self.board, self.task_id, self.run_id)
        assert seal["schema"] == 3
        with self.kb.connect_closing(board=self.board) as conn:
            assert self.kb.get_task(conn, self.task_id).worker_pid is None
            assert conn.execute("SELECT worker_pid FROM task_runs WHERE id=?", (self.run_id,)).fetchone()[0] is None
            assert conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='factory_handoff_committed'",
                                (self.task_id,)).fetchone()[0] == 1
        return seal

    def close(self) -> None:
        try:
            if self.agent is not None:
                self.agent.close()
            if hasattr(self, "manager"):
                self.manager.unload(self.start.PLUGIN)
            for spy in self.connect_spies:
                spy.assert_not_called()
        finally:
            super().close()
