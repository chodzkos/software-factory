"""Fail-closed tool and terminal guards for privileged Software Factory profiles."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any, Optional

RUNTIME_PROFILE = "runtime-controller"
CLAUDE_NORMAL_PROFILES = frozenset({"coder-claude", "reviewer-claude"})
CLAUDE_DEEP_PROFILES = frozenset({"architect-claude-opus"})
CLAUDE_PROFILES = CLAUDE_NORMAL_PROFILES | CLAUDE_DEEP_PROFILES

RUNTIME_OPS = frozenset({
    "create", "show", "block", "complete",
    "validate-runtime", "validate-handoff", "validate-routed-handoff", "validate-routing",
})

CLAUDE_ALLOWED_TOOLS = {
    "coder-claude": frozenset({
        "terminal", "read_file", "search_files", "skill",
        "kanban_show", "kanban_comment", "kanban_heartbeat",
        "kanban_request_review", "kanban_block",
    }),
    "reviewer-claude": frozenset({
        "terminal", "read_file", "search_files", "skill",
        "kanban_show", "kanban_comment", "kanban_heartbeat",
        "kanban_complete", "kanban_request_changes", "kanban_block",
    }),
    "architect-claude-opus": frozenset({
        "terminal", "read_file", "search_files", "skill",
        "kanban_show", "kanban_comment", "kanban_heartbeat",
        "kanban_complete", "kanban_block",
    }),
}

READONLY_PROGRAMS = frozenset({"pwd", "ls", "find", "grep", "wc", "od", "test"})
READONLY_GIT_SUBCOMMANDS = frozenset({"status", "diff", "rev-parse", "show", "log"})
SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "(", ")"})
EVIDENCE_ROOT = Path.home() / ".hermes" / "factory-evidence" / "claude-code"


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _profile() -> str:
    return os.environ.get("HERMES_PROFILE", "").strip()


def _task_id(explicit: str = "") -> str:
    return (explicit or os.environ.get("HERMES_KANBAN_TASK", "")).strip()


def _run_id() -> str:
    return os.environ.get("HERMES_KANBAN_RUN_ID", "").strip()


def _command_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        raise ValueError("tool args must be an object")
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("terminal command missing")
    return command


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    if not tokens:
        raise ValueError("empty terminal command")
    for token in tokens:
        if token in SHELL_OPERATORS or token.startswith((";", "&&", "||", "|", ">", "<")):
            raise ValueError("shell operator refused")
        if "$(" in token or "`" in token:
            raise ValueError("command substitution refused")
    return tokens


def _runtime_wrapper_paths() -> frozenset[str]:
    absolute = str(Path.home() / ".hermes" / "profiles" / RUNTIME_PROFILE / "kanban_runtime_cli.sh")
    return frozenset({absolute, "~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh"})


def _runtime_terminal_allowed(command: str) -> bool:
    tokens = _shell_tokens(command)
    if len(tokens) < 2 or tokens[0] not in _runtime_wrapper_paths():
        return False
    return tokens[1] in RUNTIME_OPS


def _claude_model(profile: str) -> str:
    return "opus" if profile in CLAUDE_DEEP_PROFILES else "sonnet"


def _claude_binary(token: str) -> bool:
    return token == "claude" or Path(token).name == "claude"


def _claude_terminal_allowed(profile: str, command: str) -> bool:
    tokens = _shell_tokens(command)
    program = tokens[0]

    if _claude_binary(program):
        required_model = _claude_model(profile)
        if "-p" not in tokens and "--print" not in tokens:
            return False
        if "--model" not in tokens:
            return False
        try:
            model = tokens[tokens.index("--model") + 1]
        except (ValueError, IndexError):
            return False
        if model != required_model:
            return False
        if "--output-format" not in tokens:
            return False
        try:
            output_format = tokens[tokens.index("--output-format") + 1]
        except (ValueError, IndexError):
            return False
        if output_format != "json":
            return False
        joined = " ".join(tokens)
        if profile == "reviewer-claude" and "Write" in joined:
            return False
        return True

    if program in READONLY_PROGRAMS:
        return True
    if program == "git" and len(tokens) >= 2 and tokens[1] in READONLY_GIT_SUBCOMMANDS:
        return True
    return False


def _evidence_path(task_id: str, run_id: str, profile: str) -> Path:
    safe = f"{task_id}__{run_id}__{profile}.json"
    return EVIDENCE_ROOT / safe


def _evidence_exists(profile: str, task_id: str) -> bool:
    run_id = _run_id()
    if not task_id or not run_id:
        return False
    path = _evidence_path(task_id, run_id, profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("profile") == profile
        and data.get("task_id") == task_id
        and data.get("run_id") == run_id
        and data.get("model_class") == _claude_model(profile)
        and isinstance(data.get("session_id"), str)
        and bool(data.get("session_id"))
        and data.get("success") is True
    )


def _parse_claude_result(output: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if value.get("type") != "result" or value.get("subtype") != "success":
        return None
    if not isinstance(value.get("session_id"), str) or not value["session_id"]:
        return None
    return value


def on_transform_terminal_output(
    command: str = "",
    output: str = "",
    exit_code: int = -1,
    task_id: str | None = None,
    **_: Any,
) -> None:
    """Persist durable evidence only for a successful canonical Claude Code result."""
    profile = _profile()
    if profile not in CLAUDE_PROFILES or exit_code != 0:
        return None
    try:
        tokens = _shell_tokens(command)
        if not tokens or not _claude_binary(tokens[0]):
            return None
        if not _claude_terminal_allowed(profile, command):
            return None
        parsed = _parse_claude_result(output)
        if parsed is None:
            return None
        tid = _task_id(task_id or "")
        rid = _run_id()
        if not tid or not rid:
            return None
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "profile": profile,
            "task_id": tid,
            "run_id": rid,
            "model_class": _claude_model(profile),
            "session_id": parsed["session_id"],
            "success": True,
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "recorded_at": int(time.time()),
        }
        path = _evidence_path(tid, rid, profile)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        return None
    return None


def on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    **_: Any,
) -> Optional[dict[str, str]]:
    try:
        profile = _profile()
        if not isinstance(tool_name, str):
            raise ValueError("invalid tool name")

        if profile == RUNTIME_PROFILE:
            if tool_name != "terminal":
                return _block("runtime-controller may only execute the guarded terminal wrapper")
            command = _command_from_args(args)
            if not _runtime_terminal_allowed(command):
                return _block("runtime-controller terminal command refused by mechanical allowlist")
            return None

        if profile not in CLAUDE_PROFILES:
            return None

        allowed = CLAUDE_ALLOWED_TOOLS[profile]
        if tool_name not in allowed:
            return _block(f"{profile} tool refused: Claude-backed profile capability boundary")

        if tool_name == "terminal":
            command = _command_from_args(args)
            if not _claude_terminal_allowed(profile, command):
                return _block(f"{profile} terminal command refused by mechanical allowlist")
            return None

        tid = _task_id(task_id)
        if profile == "coder-claude" and tool_name == "kanban_request_review":
            if not _evidence_exists(profile, tid):
                return _block("coder-claude review request requires successful Claude Code run evidence")
        if profile in {"reviewer-claude", "architect-claude-opus"} and tool_name == "kanban_complete":
            if not _evidence_exists(profile, tid):
                return _block(f"{profile} completion requires successful Claude Code run evidence")
        return None
    except Exception:
        return _block("Software Factory execution guard refused tool call: security validation failed")
