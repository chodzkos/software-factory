"""Software Factory execution boundary guards for privileged profiles."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

from . import guard as _guard

_PROTECTED_PROFILES = frozenset({
    "runtime-controller",
    "coder-claude",
    "reviewer-claude",
    "architect-claude-opus",
})
_CLAUDE_PROFILES = frozenset({"coder-claude", "reviewer-claude", "architect-claude-opus"})
_CODER_READ_TOOLS = "Read,Glob,Grep"
_READONLY_TOOLS = "Read,Glob,Grep"
_REQUIRED_BOOL_FLAGS = frozenset({"--safe-mode"})
_VALUE_FLAGS = frozenset({
    "-p", "--print", "--model", "--output-format", "--allowedTools", "--max-turns", "--effort", "--permission-mode",
})
_RUNTIME_OPS = frozenset({
    "create",
    "show",
    "block",
    "complete",
    "validate-runtime",
    "validate-routed-handoff",
    "validate-routing-body",
    "validate-routing-live",
    "dispatch-review",
})

# Keep the underlying guard's constants synchronized with the hardened entrypoint.
_guard.RUNTIME_OPS = _RUNTIME_OPS
_guard.CODER_CLAUDE_TOOLS = _CODER_READ_TOOLS
_guard.READONLY_CLAUDE_TOOLS = _READONLY_TOOLS


def _activate_profile_identity() -> None:
    """Recover the logical protected profile slot used by ad-hoc `hermes -p` runs."""
    if os.environ.get("HERMES_PROFILE", "").strip():
        return
    raw_home = os.environ.get("HERMES_HOME", "").strip()
    if not raw_home:
        return
    try:
        logical = Path(raw_home).expanduser()
        profiles_root = (Path.home() / ".hermes" / "profiles").resolve(strict=False)
        logical_parent = logical.parent.resolve(strict=False)
    except OSError:
        return
    if logical_parent == profiles_root and logical.name in _PROTECTED_PROFILES:
        os.environ["HERMES_PROFILE"] = logical.name


def _has_unquoted_linebreak(command: str) -> bool:
    """Reject shell line separators while allowing newlines inside quoted arguments."""
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            if char in "\r\n" and not quote:
                return True
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "\r\n":
            return True
    return False


def _reject_unquoted_linebreak(tool_name: str, args) -> dict[str, str] | None:
    if tool_name != "terminal" or not isinstance(args, dict):
        return None
    command = args.get("command")
    if isinstance(command, str) and _has_unquoted_linebreak(command):
        return {
            "action": "block",
            "message": "Software Factory execution guard refused unquoted terminal line break",
        }
    return None


def _runtime_terminal_allowed(command: str) -> bool:
    try:
        tokens = _guard._shell_tokens(command)
    except ValueError:
        return False
    if len(tokens) < 2 or tokens[0] not in _guard._runtime_wrapper_paths() or tokens[1] not in _RUNTIME_OPS:
        return False
    op = tokens[1]
    args = tokens[2:]
    if "--actual-json" in args:
        return False
    if op in {"validate-routing-live", "validate-routed-handoff", "dispatch-review"}:
        return len(args) == 2 and args[0] == "--task-id" and bool(args[1]) and not args[1].startswith("-")
    if op == "validate-routing-body":
        return len(args) == 2 and args[0] == "--task-body" and bool(args[1])
    if op == "validate-runtime":
        return len(args) >= 4 and args[0] == "--task-id" and bool(args[1]) and "--workspace-kind" in args
    if op == "show":
        return len(args) in {1, 2} and bool(args[0]) and not args[0].startswith("-") and (len(args) == 1 or args[1] == "--json")
    return bool(args)


def _run_git(workspace: str, args: list[str], *, text: bool = False) -> str | bytes:
    return subprocess.run(
        ["git", "-C", workspace, *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=text,
        timeout=20,
    ).stdout


def _hardened_workspace_content_state(workspace: str) -> tuple[str, str] | None:
    """Hash HEAD, staged bytes, and every tracked/untracked path, including ignored paths."""
    try:
        root = Path(workspace).resolve(strict=True)
        head = str(_run_git(str(root), ["rev-parse", "HEAD"], text=True)).strip()
        staged = bytes(_run_git(
            str(root), ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]
        ))
        # Deliberately omit --exclude-standard: ignored untracked files are security-relevant
        # workspace state too and must invalidate schema-v5 evidence when they change.
        raw_paths = bytes(_run_git(str(root), ["ls-files", "-c", "-o", "-z"]))
    except (OSError, subprocess.SubprocessError):
        return None
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head.lower()):
        return None

    h = hashlib.sha256()
    h.update(b"HEAD\0" + head.encode("ascii") + b"\0STAGED\0" + staged + b"\0FILES\0")
    for raw in sorted(path for path in raw_paths.split(b"\0") if path):
        rel = Path(os.fsdecode(raw))
        if rel.is_absolute() or ".." in rel.parts:
            return None
        path = root / rel
        h.update(raw + b"\0")
        try:
            st = path.lstat()
        except FileNotFoundError:
            h.update(b"DELETED\0")
            continue
        h.update(f"MODE:{stat.S_IFMT(st.st_mode):o}:{stat.S_IMODE(st.st_mode):o}\0".encode("ascii"))
        if stat.S_ISLNK(st.st_mode):
            h.update(b"SYMLINK\0" + os.fsencode(os.readlink(path)) + b"\0")
        elif stat.S_ISREG(st.st_mode):
            h.update(b"FILE\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(chunk)
            h.update(b"\0")
        else:
            return None
    return head, h.hexdigest()


def _exact_marker(prompt: str, name: str, value: str) -> bool:
    target = f"{name}: {value}"
    return sum(1 for line in prompt.splitlines() if line == target) == 1


def _claude_edit_rule(workspace: str) -> str:
    """Return Claude Code absolute permission rule confining all built-in edits to workspace."""
    if not workspace.startswith("/") or any(ch in workspace for ch in "\r\n"):
        raise ValueError("invalid workspace for Claude edit rule")
    # Claude permission syntax uses // for filesystem-absolute Edit rules.
    return f"Edit(/{workspace}/**)"


def _coder_tools(workspace: str) -> str:
    return f"{_CODER_READ_TOOLS},{_claude_edit_rule(workspace)}"


def _hardened_parse_claude_argv(profile: str, command: str) -> dict[str, str] | None:
    try:
        tokens = _guard._shell_tokens(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "claude" or _guard._canonical_claude_identity() is None:
        return None

    values: dict[str, str] = {}
    seen: set[str] = set()
    bool_seen: set[str] = set()
    index = 1
    while index < len(tokens):
        flag = tokens[index]
        if flag in _REQUIRED_BOOL_FLAGS:
            if flag in bool_seen:
                return None
            bool_seen.add(flag)
            index += 1
            continue
        if not flag.startswith("-") or flag not in _VALUE_FLAGS or flag in seen:
            return None
        seen.add(flag)
        if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
            return None
        values[flag] = tokens[index + 1]
        index += 2

    if bool_seen != set(_REQUIRED_BOOL_FLAGS):
        return None
    prompt_flags = [flag for flag in ("-p", "--print") if flag in values]
    if len(prompt_flags) != 1:
        return None

    task_id = _guard._task_id()
    run_id = _guard._run_id()
    workspace = _guard._workspace()
    if not task_id or not run_id or not workspace:
        return None
    try:
        if str(Path.cwd().resolve(strict=True)) != workspace:
            return None
    except OSError:
        return None

    expected_model = "opus" if profile == "architect-claude-opus" else "sonnet"
    if values.get("--model") != expected_model or values.get("--output-format") != "json":
        return None
    try:
        expected_tools = _coder_tools(workspace) if profile == "coder-claude" else _READONLY_TOOLS
    except ValueError:
        return None
    if values.get("--allowedTools") != expected_tools:
        return None
    expected_mode = "dontAsk" if profile == "coder-claude" else "plan"
    if values.get("--permission-mode") != expected_mode:
        return None

    prompt = values[prompt_flags[0]]
    if not _exact_marker(prompt, "TASK_ID", task_id):
        return None
    if not _exact_marker(prompt, "RUN_ID", run_id):
        return None
    if not _exact_marker(prompt, "WORKSPACE", workspace):
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


_guard._workspace_content_state = _hardened_workspace_content_state
_guard._parse_claude_argv = _hardened_parse_claude_argv
_guard._runtime_terminal_allowed = _runtime_terminal_allowed


def on_pre_tool_call(*args, **kwargs):
    _activate_profile_identity()
    tool_name = kwargs.get("tool_name", args[0] if args else "")
    tool_args = kwargs.get("args", args[1] if len(args) > 1 else None)
    blocked = _reject_unquoted_linebreak(tool_name, tool_args)
    if blocked is not None and os.environ.get("HERMES_PROFILE", "").strip() in _PROTECTED_PROFILES:
        return blocked
    return _guard.on_pre_tool_call(*args, **kwargs)


def on_post_tool_call(*args, **kwargs):
    _activate_profile_identity()
    return _guard.on_post_tool_call(*args, **kwargs)


def register(ctx) -> None:
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        raise RuntimeError("Hermes plugin hook registration unavailable")
    register_hook("pre_tool_call", on_pre_tool_call)
    register_hook("post_tool_call", on_post_tool_call)
