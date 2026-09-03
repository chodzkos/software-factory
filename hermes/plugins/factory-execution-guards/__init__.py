"""Software Factory execution boundary guards for privileged profiles."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from . import guard as _guard
from . import handoff as _handoff

_PROTECTED_PROFILES = frozenset({
    "runtime-controller",
    "coder-claude",
    "reviewer-claude",
    "architect-claude-opus",
    "reviewer-gpt",
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

# Claude's permission language is comma/parenthesis/glob structured.  A
# filesystem path interpolated into that grammar must use a deliberately small
# portable alphabet rather than attempting ad-hoc escaping.
_SAFE_WORKSPACE_RE = re.compile(r"^/(?:[A-Za-z0-9._@%+=:-]+/)*[A-Za-z0-9._@%+=:-]+$")
_CONTENT_STATE_DOMAIN = b"software-factory-content-state-v2"

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


def _trusted_git_binary() -> str | None:
    """Resolve Git from the platform default PATH, never the worker's PATH."""
    located = shutil.which("git", path=os.defpath)
    if not located:
        return None
    try:
        resolved = Path(located).resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return str(resolved)


def _sanitized_git_env() -> dict[str, str]:
    """Return a minimal environment that cannot redirect Git to another repo."""
    return {
        "PATH": os.defpath,
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _run_git(workspace: str, args: list[str], *, text: bool = False) -> str | bytes:
    git = _trusted_git_binary()
    if git is None:
        raise OSError("trusted git executable unavailable")
    return subprocess.run(
        [
            git,
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "diff.external=",
            "-c", "core.attributesfile=/dev/null",
            "-C", workspace,
            *args,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=text,
        timeout=20,
        env=_sanitized_git_env(),
        close_fds=True,
    ).stdout


def _frame(hasher: "hashlib._Hash", tag: bytes, payload: bytes) -> None:
    """Append one unambiguously length-framed typed value."""
    if len(tag) > 0xFFFF or len(payload) > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("content-state frame too large")
    hasher.update(len(tag).to_bytes(2, "big"))
    hasher.update(tag)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _stat_identity(st: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_mode),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_ctime_ns),
    )


def _regular_file_payload(path: Path, expected: os.stat_result) -> bytes | None:
    """Hash a regular file through a no-follow descriptor and detect read races."""
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _stat_identity(before) != _stat_identity(expected):
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        if _stat_identity(before) != _stat_identity(after) or total != int(after.st_size):
            return None
        return total.to_bytes(8, "big") + digest.digest()
    finally:
        os.close(fd)


def _symlink_payload(path: Path, expected: os.stat_result) -> bytes | None:
    try:
        target = os.fsencode(os.readlink(path))
        after = path.lstat()
    except OSError:
        return None
    if _stat_identity(expected) != _stat_identity(after) or not stat.S_ISLNK(after.st_mode):
        return None
    return target


def _content_record(root: Path, raw: bytes) -> bytes | None:
    rel = Path(os.fsdecode(raw))
    if rel.is_absolute() or ".." in rel.parts:
        return None
    path = root / rel
    record = hashlib.sha256()
    _frame(record, b"path", raw)
    try:
        st = path.lstat()
    except FileNotFoundError:
        _frame(record, b"type", b"deleted")
        return record.digest()
    except OSError:
        return None

    _frame(record, b"mode", int(st.st_mode).to_bytes(8, "big"))
    if stat.S_ISLNK(st.st_mode):
        payload = _symlink_payload(path, st)
        if payload is None:
            return None
        _frame(record, b"type", b"symlink")
        _frame(record, b"target", payload)
    elif stat.S_ISREG(st.st_mode):
        payload = _regular_file_payload(path, st)
        if payload is None:
            return None
        _frame(record, b"type", b"file")
        _frame(record, b"content-sha256", payload)
    else:
        return None
    return record.digest()


def _hardened_workspace_content_state(workspace: str) -> tuple[str, str] | None:
    """Hash HEAD, index and all tracked/untracked paths with canonical framing."""
    try:
        root = Path(workspace).resolve(strict=True)
        top_level = str(_run_git(str(root), ["rev-parse", "--show-toplevel"], text=True)).strip()
        if str(Path(top_level).resolve(strict=True)) != str(root):
            return None
        head = str(_run_git(str(root), ["rev-parse", "HEAD"], text=True)).strip()
        staged = bytes(_run_git(
            str(root), ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]
        ))
        # Deliberately omit --exclude-standard: ignored untracked files are security-relevant
        # workspace state too and must invalidate schema-6 evidence when they change.
        raw_paths = bytes(_run_git(str(root), ["ls-files", "-c", "-o", "-z"]))
    except (OSError, subprocess.SubprocessError):
        return None
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head.lower()):
        return None

    digest = hashlib.sha256()
    try:
        _frame(digest, b"domain", _CONTENT_STATE_DOMAIN)
        _frame(digest, b"head", head.encode("ascii"))
        _frame(digest, b"staged", staged)
        for raw in sorted(path for path in raw_paths.split(b"\0") if path):
            record = _content_record(root, raw)
            if record is None:
                return None
            _frame(digest, b"entry-sha256", record)
    except (OSError, ValueError):
        return None
    return head, digest.hexdigest()


def _exact_marker(prompt: str, name: str, value: str) -> bool:
    target = f"{name}: {value}"
    return sum(1 for line in prompt.splitlines() if line == target) == 1


def _claude_edit_rule(workspace: str) -> str:
    """Return one injection-safe absolute Claude Edit permission rule."""
    if not _SAFE_WORKSPACE_RE.fullmatch(workspace):
        raise ValueError("workspace contains characters unsafe for Claude permission grammar")
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


# Jeden serializer jest współdzielony przez wykonanie, handoff, dispatch i approval.
_hardened_workspace_content_state = _handoff.workspace_content_state
_guard._workspace_content_state = _handoff.workspace_content_state
_guard._parse_claude_argv = _hardened_parse_claude_argv
_guard._runtime_terminal_allowed = _runtime_terminal_allowed


def on_pre_tool_call(*args, **kwargs):
    _activate_profile_identity()
    tool_name = kwargs.get("tool_name", args[0] if args else "")
    tool_args = kwargs.get("args", args[1] if len(args) > 1 else None)
    blocked = _reject_unquoted_linebreak(tool_name, tool_args)
    if blocked is not None and os.environ.get("HERMES_PROFILE", "").strip() in _PROTECTED_PROFILES:
        return blocked
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    if profile == "reviewer-gpt" and tool_name == "kanban_complete":
        if not _handoff.reviewer_completion_authorized(
            content_state=_handoff.workspace_content_state,
        ):
            return {
                "action": "block",
                "message": "reviewer-gpt approval refused: sealed implementation bytes or reviewer run do not match",
            }
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
