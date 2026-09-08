# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""`silica mcp` — stdio MCP server over the tool registry.

The default list is exactly the five tools of TOOLS.md. `--extended` adds
the tabular census and the link tools. stdout is the protocol channel:
nothing here may print to it. Only stdlib at import time; the `mcp` SDK is
imported inside run_mcp so the module loads without the [mcp] extra.
"""
from __future__ import annotations

import os
import signal
import sys
from typing import Any

CORE_TOOLS = (
    "silica_files",
    "silica_search",
    "silica_read",
    "silica_code_pack",
    "silica_write_note",
)

EXTENDED_TOOLS = (
    "silica_tables",
    "silica_query_table",
    "silica_links",
    "silica_backlinks",
    "silica_orphans",
    "silica_unresolved",
)

WRITE_TOOLS = frozenset({"silica_write_note"})

_READ_ONLY = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_ADDITIVE = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def tool_annotations(name: str) -> dict:
    return dict(_ADDITIVE if name in WRITE_TOOLS else _READ_ONLY)


def exposed_tools(extended: bool = False) -> dict[str, Any]:
    """The registry slice served over MCP, keyed by name. default ⊂ --extended."""
    import silica.core  # noqa: F401 — registration side effect
    import silica.tools.atomic  # noqa: F401
    import silica.tools.tabular  # noqa: F401
    from silica.tools import TOOLS

    out: dict[str, Any] = {}
    for n in CORE_TOOLS + (EXTENDED_TOOLS if extended else ()):
        if n not in TOOLS:
            raise KeyError(f"{n}: named in a served tier but not registered — registry drift")
        out[n] = TOOLS[n]
    return out


INSTRUCTIONS = (
    "Silica indexes the folder this server was started in and returns located "
    "evidence, never answers. silica_search gives ranked passages with path, "
    "section, line, matched_terms, coverage and terms_absent: low coverage or a "
    "discriminating term in terms_absent means the corpus does not answer, so stop "
    "or rephrase instead of reading the hits. Read a passage with silica_read "
    "before citing it and carry its version forward. silica_files says what the "
    "index skipped or could not read; an empty result under index.state=cold is "
    "not a miss. Nothing is summarised or remembered for you."
)


def parse_cli_args(args: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {"extended": False, "vault": "", "error": ""}
    it = iter(args)
    for a in it:
        if a == "--extended":
            opts["extended"] = True
        elif a == "--vault":
            opts["vault"] = next(it, "")
            if not opts["vault"]:
                opts["error"] = "--vault needs a directory"
        elif a.startswith("--vault="):
            opts["vault"] = a.split("=", 1)[1]
        else:
            opts["error"] = f"unknown flag for silica mcp: {a}"
    return opts


def make_server(extended: bool = False):
    import anyio
    import mcp.types as types
    from mcp.server.lowlevel import Server

    from silica.config import CONFIG

    tools = exposed_tools(extended)
    vault = str(getattr(CONFIG, "vault_path", "") or "").strip()
    server = Server("silica-core", instructions=INSTRUCTIONS + (f" Root: {vault}" if vault else ""))

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=t.name, description=t.description,
                           inputSchema=t.json_schema()["function"]["parameters"],
                           annotations=types.ToolAnnotations(**tool_annotations(t.name)))
                for t in tools.values()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        t = tools.get(name)
        if t is None:
            raise ValueError(f"Unknown tool: {name}")
        out = await anyio.to_thread.run_sync(lambda: t.run(**(arguments or {})))
        return [types.TextContent(type="text", text=out)]

    return server


def run_mcp(extended: bool = False) -> int:
    """Serve the tools over MCP stdio. Blocks until the client hangs up."""
    try:
        import anyio
        from mcp.server.stdio import stdio_server
    except ImportError:
        print("silica mcp needs the [mcp] extra: uv pip install 'silica-core[mcp]'", file=sys.stderr)
        return 1
    server = make_server(extended)
    n = len(exposed_tools(extended))

    async def _serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    print(f"silica mcp: serving {n} tools on stdio, waiting for a client (Ctrl+C to stop)", file=sys.stderr)
    signal.signal(signal.SIGINT, lambda *_: (sys.stderr.flush(), os._exit(0)))
    anyio.run(_serve)
    return 0
