"""Fail-closed tool and Claude-execution guards for privileged Software Factory profiles."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Optional

RUNTIME_PROFILE = "runtime-controller"
CLAUDE_NORMAL_PROFILES = frozenset({"coder-claude", "reviewer-claude"})
CLAUDE_DEEP_PROFILES = frozenset({"architect-claude-opus"})
CLAUDE_PROFILES = CLAUDE_NORMAL_PROFILES | CLAUDE_DEEP_PROFILES

RUNTIME_OPS = frozenset({
    "create", "show", "block", "complete",
    "validate-runtime", "validate-routed-handoff", "validate-routing",
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

CODER_CLAUDE_TOOLS = "Read,Write,Edit,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(python3 *)"
READONLY_CLAUDE_TOOLS = "Read,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(git show *),Bash(git log *)"
EVIDENCE_ROOT = Path.home() / ".hermes" / "factory-evidence" / "claude-code"
SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "(", ")"})
FORBIDDEN_CLAUDE_FLAGS = frozenset({
    "--dangerously-skip-permissions", "--settings", "--setting-sources",
    "--mcp-config", "--strict-mcp-config", "--plugin-dir", "--resume", "--continue",
    "--fork-session", "--worktree", "--tmux", "--debug", "--debug-file",
    "--add-dir", "--permission-mode", "--permission-prompt-tool", "--fallback-model",
})
VALUE_FLAGS = frozenset({"-p", "--print", "--model", "--output-format", "--allowedTools", "--max-turns", "--effort"})
SINGLETON_FLAGS = VALUE_FLAGS | FORBIDDEN_CLAUDE_FLAGS


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _profile() -> str:
    return os.environ.get("HERMES_PROFILE", "").strip()


def _task_id(explicit: str = "") -> str:
    kanban_task = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if kanban_task:
        return kanban_task
    return explicit.strip()


def _run_id() -> str:
    return os.environ.get("HERMES_KANBAN_RUN_ID", "").strip()


def _workspace() -> str:
    raw = os.environ.get("HERMES_KANBAN_WORKSPACE", "").strip() or os.getcwd()
    try:
        return str(Path(raw).resolve(strict=True))
    except OSError:
        return ""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_claude_identity() -> tuple[str, str] | None:
    located = shutil.which("claude")
    if not located:
        return None
    try:
        resolved = Path(located).resolve(strict=True)
        if not resolved.is_file():
            return None
        return str(resolved), _sha256_file(resolved)
    except OSError:
        return None


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


def _expected_claude_tools(profile: str) -> str:
    return CODER_CLAUDE_TOOLS if profile == "coder-claude" else READONLY_CLAUDE_TOOLS


def _parse_claude_argv(profile: str, command: str) -> dict[str, str] | None:
    tokens = _shell_tokens(command)
    # Only the literal PATH-resolved binary name is accepted. Relative and
    # absolute alternate paths such as ./claude and /tmp/claude are refused.
    if not tokens or tokens[0] != "claude" or _canonical_claude_identity() is None:
        return None

    values: dict[str, str] = {}
    seen: set[str] = set()
    index = 1
    while index < len(tokens):
        flag = tokens[index]
        if not flag.startswith("-"):
            return None
        if flag in FORBIDDEN_CLAUDE_FLAGS:
            return None
        if flag not in VALUE_FLAGS or flag in seen:
            return None
        seen.add(flag)
        if index + 1 >= len(tokens):
            return None
        value = tokens[index + 1]
        if value.startswith("-"):
            return None
        values[flag] = value
        index += 2

    prompt_flags = [flag for flag in ("-p", "--print") if flag in values]
    if len(prompt_flags) != 1:
        return None
    if values.get("--model") != _claude_model(profile):
        return None
    if values.get("--output-format") != "json":
        return None
    if values.get("--allowedTools") != _expected_claude_tools(profile):
        return None
    if "--max-turns" in values:
        try:
            turns = int(values["--max-turns"])
        except ValueError:
            return None
        if not 1 <= turns <= 64:
            return None
    if "--effort" in values and values["--effort"] not in {"low", "medium", "high"}:
        return None
    return values


def _claude_terminal_allowed(profile: str, command: str) -> bool:
    return _parse_claude_argv(profile, command) is not None


def _evidence_path(task_id: str, run_id: str, profile: str) -> Path:
    return EVIDENCE_ROOT / f"{task_id}__{run_id}__{profile}.json"


def _evidence_exists(profile: str, task_id: str) -> bool:
    run_id = _run_id()
    workspace = _workspace()
    identity = _canonical_claude_identity()
    if not task_id or not run_id or not workspace or identity is None:
        return False
    binary_path, binary_sha256 = identity
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
        and data.get("workspace") == workspace
        and data.get("claude_binary") == binary_path
        and data.get("claude_binary_sha256") == binary_sha256
        and isinstance(data.get("session_id"), str)
        and bool(data.get("session_id"))
        and isinstance(data.get("command_sha256"), str)
        and len(data.get("command_sha256")) == 64
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


def _terminal_result_output(result: str) -> str | None:
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exit_code") != 0:
        return None
    output = payload.get("output")
    return output if isinstance(output, str) else None


def on_post_tool_call(
    tool_name: str = "",
    args: Any = None,
    result: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    profile = _profile()
    if profile not in CLAUDE_PROFILES or tool_name != "terminal":
        return None
    try:
        command = _command_from_args(args)
        if _parse_claude_argv(profile, command) is None:
            return None
        output = _terminal_result_output(result)
        if output is None:
            return None
        parsed = _parse_claude_result(output)
        if parsed is None:
            return None
        tid = _task_id(task_id)
        rid = _run_id()
        workspace = _workspace()
        identity = _canonical_claude_identity()
        if not tid or not rid or not workspace or identity is None:
            return None
        binary_path, binary_sha256 = identity
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 2,
            "profile": profile,
            "task_id": tid,
            "run_id": rid,
            "model_class": _claude_model(profile),
            "workspace": workspace,
            "claude_binary": binary_path,
            "claude_binary_sha256": binary_sha256,
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
                return _block("coder-claude review request requires successful canonical Claude Code evidence")
        if profile in {"reviewer-claude", "architect-claude-opus"} and tool_name == "kanban_complete":
            if not _evidence_exists(profile, tid):
                return _block(f"{profile} completion requires successful canonical Claude Code evidence")
        return None
    except Exception:
        return _block("Software Factory execution guard refused tool call: security validation failed")
