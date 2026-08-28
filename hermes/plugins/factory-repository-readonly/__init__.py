"""Software Factory read-only repository tools for Hermes repository-analyst."""
from __future__ import annotations

from .kanban_guard import on_pre_tool_call
from .repository_tools import (
    MAP_SCHEMA,
    READ_SCHEMA,
    SEARCH_SCHEMA,
    check_available,
    handle_map,
    handle_read,
    handle_search,
)


def register(ctx) -> None:
    for name, schema, handler, emoji in (
        ("factory_repo_map", MAP_SCHEMA, handle_map, "🗺️"),
        ("factory_repo_read", READ_SCHEMA, handle_read, "📖"),
        ("factory_repo_search", SEARCH_SCHEMA, handle_search, "🔎"),
    ):
        ctx.register_tool(
            name=name,
            toolset="factory-repository-readonly",
            schema=schema,
            handler=handler,
            check_fn=check_available,
            emoji=emoji,
        )
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
