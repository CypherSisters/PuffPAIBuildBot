"""Test helpers: the tools the MCP client put in the tool loop's registry.

``mcp_client`` registers server tools and its chat tools in
``puffpai.brain_tools`` with ``source="mcp"``. These helpers read them back
as a plain dict and take them out again, without touching the other tools
(the built-in ones, the find tools) that other tests rely on.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai import brain_tools


def mcp_tools() -> dict:
    """``{name: {"description", "json_schema", "fn", "needs_yes"}}`` of the
    tools the MCP client registered."""
    return {t.name: {"name": t.name, "description": t.description,
                     "json_schema": t.schema, "fn": t.fn, "needs_yes": t.needs_yes}
            for t in brain_tools.available({}) if t.source == "mcp"}


def clear_mcp_tools() -> None:
    for name in list(mcp_tools()):
        brain_tools.unregister(name)
