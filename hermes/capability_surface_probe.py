#!/usr/bin/env python3
"""Izolowany probe dokładnej listy definitions składanej przez Hermes 0.20.4."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-home", type=Path, required=True)
    args = parser.parse_args()

    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions

    config = load_config()
    from agent.coding_context import coding_selection

    enabled = coding_selection(platform="cli", config=config)
    if enabled is None:
        enabled = sorted(_get_platform_tools(config, "cli"))
    if not enabled:
        raise RuntimeError("CLI chat toolset resolution failed")
    disabled = list((config.get("agent") or {}).get("disabled_toolsets") or ())
    definitions = get_tool_definitions(
        enabled_toolsets=list(enabled),
        disabled_toolsets=disabled,
        quiet_mode=True,
        skip_tool_search_assembly=False,
    )
    payload = {
        "tools": sorted(item["function"]["name"] for item in definitions),
        "toolsets": sorted(enabled),
        "disabled_toolsets": sorted(disabled),
        "launch_context": "cli-chat",
    }
    print("FACTORY_CAPABILITY_SURFACE " + json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
