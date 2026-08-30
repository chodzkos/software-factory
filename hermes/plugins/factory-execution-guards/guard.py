"""Fail-closed tool and Claude-execution guards for privileged Software Factory profiles."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

RUNTIME_PROFILE = "runtime-controller"
CLAUDE_NORMAL_PROFILES = frozenset({"coder-claude", "reviewer-claude"})
CLAUDE_DEEP_PROFILES = frozenset({"architect-claude-opus"})
CLAUDE_PROFILES = CLAUDE_NORMAL_PROFILES | CLAUDE_DEEP_PROFILES

RUNTIME_OPS = frozenset({"create", "show", "block", "complete", "validate-runtime", "validate-routed-handoff", "validate-routing"})

CLAUDE_ALLOWED_TOOLS = {
    "coder-claude": frozenset({"terminal", "read_file", "search_files", "skill", "kanban_show", "kanban_comment", "kanban_heartbeat", "kanban_request_review", "kanban_block"}),
    "reviewer-claude": frozenset({"terminal", "read_file", "search_files", "skill", "kanban_show", "kanban_comment", "kanban_heartbeat", "kanban_complete", "kanban_request_changes", "kanban_block"}),
    "architect-claude-opus": frozenset({"terminal", "read_file", "search_files", "skill", "kanban_show", "kanban_comment", "kanban_heartbeat", "kanban_complete", "kanban_block"}),
}

CODER_CLAUDE_TOOLS = "Read,Write,Edit,Glob,Grep,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(python3 *)"
# No Bash at all for review/architecture. This removes git --output, external
# diff/pager and similar write-capable escape hatches from Claude review runs.
READONLY_CLAUDE_TOOLS = "Read,Glob,Grep"
EVIDENCE_ROOT = Path.home() / ".hermes" / "factory-evidence" / "claude-code"
SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "(", ")"})
FORBIDDEN_CLAUDE_FLAGS = frozenset({
    "--dangerously-skip-permissions", "--settings", "--setting-sources", "--mcp-config", "--strict-mcp-config",
    "--plugin-dir", "--resume", "--continue", "--fork-session", "--worktree", "--tmux", "--debug", "--debug-file",
    "--add-dir", "--permission-mode", "--permission-prompt-tool", "--fallback-model",
})
VALUE_FLAGS = frozenset({"-p", "--print", "--model", "--output-format", "--allowedTools", "--max-turns", "--effort"})

# Trusted-process-only attestation. A delegated subprocess can write files but
# cannot manufacture the nonce/completed state held inside this Hermes worker.
_PENDING_ATTESTATIONS: dict[tuple[str, str, str], dict[str, str]] = {}
_COMPLETED_ATTESTATIONS: dict[tuple[str, str, str], dict[str, str]] = {}


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _profile() -> str:
    return os.environ.get("HERMES_PROFILE", "").strip()


def _task_id(explicit: str = "") -> str:
    kanban_task = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    return kanban_task or explicit.strip()


def _run_id() -> str:
    return os.environ.get("HERMES_KANBAN_RUN_ID", "").strip()


def _attestation_key(profile: str, task_id: str, run_id: str) -> tuple[str, str, str]:
    return profile, task_id, run_id


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
        if not resolved.is_file() or resolved.is_symlink():
            return None
        return str(resolved), _sha256_file(resolved)
    except OSError:
        return None


def _git_workspace_state(workspace: str) -> tuple[str, str] | None:
    """Return trusted HEAD + status digest without invoking a shell or diff driver."""
    try:
        head = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head.lower()):
        return None
    return head, hashlib.sha256(status).hexdigest()


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
    return len(tokens) >= 2 and tokens[0] in _runtime_wrapper_paths() and tokens[1] in RUNTIME_OPS


def _claude_model(profile: str) -> str:
    return "opus" if profile in CLAUDE_DEEP_PROFILES else "sonnet"


def _expected_claude_tools(profile: str) -> str:
    return CODER_CLAUDE_TOOLS if profile == "coder-claude" else READONLY_CLAUDE_TOOLS


def _parse_claude_argv(profile: str, command: str) -> dict[str, str] | None:
    tokens = _shell_tokens(command)
    if not tokens or tokens[0] != "claude" or _canonical_claude_identity() is None:
        return None
    values: dict[str, str] = {}
    seen: set[str] = set()
    index = 1
    while index < len(tokens):
        flag = tokens[index]
        if not flag.startswith("-") or flag in FORBIDDEN_CLAUDE_FLAGS or flag not in VALUE_FLAGS or flag in seen:
            return None
        seen.add(flag)
        if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
            return None
        values[flag] = tokens[index + 1]
        index += 2
    prompt_flags = [flag for flag in ("-p", "--print") if flag in values]
    if len(prompt_flags) != 1 or values.get("--model") != _claude_model(profile) or values.get("--output-format") != "json":
        return None
    if values.get("--allowedTools") != _expected_claude_tools(profile):
        return None
    prompt = values[prompt_flags[0]]
    task_id, run_id, workspace = _task_id(), _run_id(), _workspace()
    if task_id and task_id not in prompt:
        return None
    if run_id and run_id not in prompt:
        return None
    if workspace and workspace not in prompt:
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


def _start_attestation(profile: str, command: str, task_id: str) -> bool:
    run_id = _run_id()
    workspace = _workspace()
    identity = _canonical_claude_identity()
    git_state = _git_workspace_state(workspace) if workspace else None
    if not task_id or not run_id or not workspace or identity is None or git_state is None:
        return False
    binary_path, binary_sha256 = identity
    git_head, status_before = git_state
    key = _attestation_key(profile, task_id, run_id)
    _COMPLETED_ATTESTATIONS.pop(key, None)
    _PENDING_ATTESTATIONS[key] = {
        "nonce": secrets.token_hex(32),
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "workspace": workspace,
        "claude_binary": binary_path,
        "claude_binary_sha256": binary_sha256,
        "git_head_before": git_head,
        "workspace_state_before_sha256": status_before,
    }
    return True


def _evidence_exists(profile: str, task_id: str) -> bool:
    run_id = _run_id()
    workspace = _workspace()
    identity = _canonical_claude_identity()
    git_state = _git_workspace_state(workspace) if workspace else None
    if not task_id or not run_id or not workspace or identity is None or git_state is None:
        return False
    key = _attestation_key(profile, task_id, run_id)
    completed = _COMPLETED_ATTESTATIONS.get(key)
    if not completed:
        return False
    binary_path, binary_sha256 = identity
    current_head, current_status = git_state
    path = _evidence_path(task_id, run_id, profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("schema") == 4
        and data.get("profile") == profile
        and data.get("task_id") == task_id
        and data.get("run_id") == run_id
        and data.get("model_class") == _claude_model(profile)
        and data.get("workspace") == workspace
        and data.get("claude_binary") == binary_path
        and data.get("claude_binary_sha256") == binary_sha256
        and data.get("git_head_after") == current_head
        and data.get("workspace_state_after_sha256") == current_status
        and data.get("command_sha256") == completed.get("command_sha256")
        and data.get("attestation_id") == completed.get("attestation_id")
        and isinstance(data.get("session_id"), str)
        and bool(data.get("session_id"))
        and data.get("success") is True
    )


def _parse_claude_result(output: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("type") != "result" or value.get("subtype") != "success":
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


def on_post_tool_call(tool_name: str = "", args: Any = None, result: str = "", task_id: str = "", **_: Any) -> None:
    profile = _profile()
    if profile not in CLAUDE_PROFILES or tool_name != "terminal":
        return None
    try:
        command = _command_from_args(args)
        if _parse_claude_argv(profile, command) is None:
            return None
        tid = _task_id(task_id)
        rid = _run_id()
        key = _attestation_key(profile, tid, rid)
        pending = _PENDING_ATTESTATIONS.get(key)
        if not pending or pending.get("command_sha256") != hashlib.sha256(command.encode("utf-8")).hexdigest():
            return None
        output = _terminal_result_output(result)
        if output is None:
            return None
        parsed = _parse_claude_result(output)
        if parsed is None:
            return None
        workspace = _workspace()
        identity = _canonical_claude_identity()
        git_state = _git_workspace_state(workspace) if workspace else None
        if not tid or not rid or not workspace or identity is None or git_state is None:
            return None
        binary_path, binary_sha256 = identity
        git_head_after, status_after = git_state
        if (
            pending.get("workspace") != workspace
            or pending.get("claude_binary") != binary_path
            or pending.get("claude_binary_sha256") != binary_sha256
        ):
            return None
        attestation_id = hashlib.sha256(pending["nonce"].encode("ascii")).hexdigest()
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 4,
            "profile": profile,
            "task_id": tid,
            "run_id": rid,
            "model_class": _claude_model(profile),
            "workspace": workspace,
            "claude_binary": binary_path,
            "claude_binary_sha256": binary_sha256,
            "session_id": parsed["session_id"],
            "success": True,
            "command_sha256": pending["command_sha256"],
            "attestation_id": attestation_id,
            "git_head_before": pending["git_head_before"],
            "git_head_after": git_head_after,
            "workspace_state_before_sha256": pending["workspace_state_before_sha256"],
            "workspace_state_after_sha256": status_after,
            "recorded_at": int(time.time()),
        }
        path = _evidence_path(tid, rid, profile)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        _COMPLETED_ATTESTATIONS[key] = {
            "attestation_id": attestation_id,
            "command_sha256": pending["command_sha256"],
        }
        _PENDING_ATTESTATIONS.pop(key, None)
    except Exception:
        return None
    return None


def on_pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", **_: Any) -> Optional[dict[str, str]]:
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
        if tool_name not in CLAUDE_ALLOWED_TOOLS[profile]:
            return _block(f"{profile} tool refused: Claude-backed profile capability boundary")
        if tool_name == "terminal":
            command = _command_from_args(args)
            if not _claude_terminal_allowed(profile, command):
                return _block(f"{profile} terminal command refused by mechanical allowlist")
            if not _start_attestation(profile, command, _task_id(task_id)):
                return _block(f"{profile} Claude attestation could not be initialized")
            return None
        tid = _task_id(task_id)
        if profile == "coder-claude" and tool_name == "kanban_request_review" and not _evidence_exists(profile, tid):
            return _block("coder-claude review request requires successful in-process attested Claude Code evidence")
        if profile in {"reviewer-claude", "architect-claude-opus"} and tool_name == "kanban_complete" and not _evidence_exists(profile, tid):
            return _block(f"{profile} completion requires successful in-process attested Claude Code evidence")
        return None
    except Exception:
        return _block("Software Factory execution guard refused tool call: security validation failed")
