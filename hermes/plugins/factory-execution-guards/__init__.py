"""Software Factory execution boundary guards for privileged profiles."""
from __future__ import annotations

from .guard import on_pre_tool_call, on_transform_terminal_output


def register(ctx) -> None:
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        raise RuntimeError("Hermes plugin hook registration unavailable")
    register_hook("pre_tool_call", on_pre_tool_call)
    register_hook("transform_terminal_output", on_transform_terminal_output)
