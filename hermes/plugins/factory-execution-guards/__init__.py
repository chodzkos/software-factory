"""Software Factory execution boundary guards for privileged profiles."""
from __future__ import annotations

import os
from pathlib import Path

from . import guard as _guard

_PROTECTED_PROFILES = frozenset({
    "runtime-controller",
    "coder-claude",
    "reviewer-claude",
    "architect-claude-opus",
})

# Keep the runtime allowlist synchronized with the provenance-bound wrapper.
_guard.RUNTIME_OPS = frozenset({
    "create",
    "show",
    "block",
    "complete",
    "validate-runtime",
    "validate-routed-handoff",
    "validate-routing-body",
    "validate-routing-live",
})


def _activate_profile_identity() -> None:
    """Recover the logical protected profile slot used by ad-hoc `hermes -p` runs.

    Classify the raw canonical profile slot, not the resolved child target. This
    keeps the guard active even if a protected profile directory is a symlink.
    """
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


def _reject_multiline_terminal(tool_name: str, args) -> dict[str, str] | None:
    if tool_name != "terminal" or not isinstance(args, dict):
        return None
    command = args.get("command")
    if isinstance(command, str) and ("\n" in command or "\r" in command):
        return {
            "action": "block",
            "message": "Software Factory execution guard refused multiline terminal command",
        }
    return None


def on_pre_tool_call(*args, **kwargs):
    _activate_profile_identity()
    tool_name = kwargs.get("tool_name", args[0] if args else "")
    tool_args = kwargs.get("args", args[1] if len(args) > 1 else None)
    blocked = _reject_multiline_terminal(tool_name, tool_args)
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
