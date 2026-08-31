"""Software Factory execution boundary guards for privileged profiles."""
from __future__ import annotations

import os
from pathlib import Path

from .guard import on_post_tool_call as _on_post_tool_call
from .guard import on_pre_tool_call as _on_pre_tool_call

_PROTECTED_PROFILES = frozenset({
    "runtime-controller",
    "coder-claude",
    "reviewer-claude",
    "architect-claude-opus",
})


def _activate_profile_identity() -> None:
    """Recover the selected Hermes profile from HERMES_HOME for ad-hoc `hermes -p` runs."""
    if os.environ.get("HERMES_PROFILE", "").strip():
        return
    raw_home = os.environ.get("HERMES_HOME", "").strip()
    if not raw_home:
        return
    try:
        resolved = Path(raw_home).expanduser().resolve(strict=False)
        profiles_root = (Path.home() / ".hermes" / "profiles").resolve(strict=False)
    except OSError:
        return
    if resolved.parent == profiles_root and resolved.name in _PROTECTED_PROFILES:
        os.environ["HERMES_PROFILE"] = resolved.name


def on_pre_tool_call(*args, **kwargs):
    _activate_profile_identity()
    return _on_pre_tool_call(*args, **kwargs)


def on_post_tool_call(*args, **kwargs):
    _activate_profile_identity()
    return _on_post_tool_call(*args, **kwargs)


def register(ctx) -> None:
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        raise RuntimeError("Hermes plugin hook registration unavailable")
    register_hook("pre_tool_call", on_pre_tool_call)
    register_hook("post_tool_call", on_post_tool_call)
