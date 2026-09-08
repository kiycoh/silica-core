# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Every hardcoded tool name must resolve in TOOLS.

Extracted from an omniparse teardown. Its python-sdk posts to
`/parse_media/image` while the server mounts that router at `/parse_image`, and
`parse_website` sends a JSON body to an endpoint declaring a query parameter.
Nothing in that repo compares the client's paths against the served routes, so
both have been wrong since they were written.

Silica's shape of the same drift is `silica/agent/loop.py`:

    allowed = {name: TOOLS[name] for name in constraints.tools if name in TOOLS}

`if name in TOOLS` is the right runtime behaviour -- raising mid-turn over a
toolset typo would be worse than proceeding -- which is exactly why it is
silent. Rename a tool and the `/web` lane quietly runs with three tools instead
of four, or a capability worker loses its reader. No exception, no log, and a
suite that only checks that the lane still answers stays green.

So the contract is enforced statically here: every literal tool name anywhere in
the product must exist. Two passes, both precise rather than heuristic:
the named declaration sites, and an AST scan of every `tools=` literal.

ponytail: prose that names tools (prompt text, tool descriptions telling the
model to call another tool) is out of scope here. Descriptions are already
covered by test_chat_tools_keeps_every_recovery_path_it_advertises; a prompt
naming a dead tool costs one wasted model call, not a silently amputated lane.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import silica.cli  # noqa: F401  registers every tool module
from silica.tools import TOOLS

SILICA_ROOT = Path(__file__).resolve().parent.parent / "silica"


def _tool_names() -> set[str]:
    assert TOOLS, "importing silica.cli must populate the registry"
    return set(TOOLS)


# --- pass 1: the named declaration sites ------------------------------------








# --- pass 2: every `tools=` literal in the tree ------------------------------


def _string_tuples(node: ast.AST) -> list[list[str]]:
    """All-string tuples/lists reachable from `node`, including both branches of
    a conditional expression (`("a","b") if flag else ("a",)`, which is how the
    /web lane adds `plan` under steering)."""
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        # only a fully literal collection is a declaration we can check
        return [values] if len(values) == len(node.elts) and values else []
    if isinstance(node, ast.IfExp):
        return _string_tuples(node.body) + _string_tuples(node.orelse)
    return []


def _declared_tool_names() -> list[tuple[str, int, str]]:
    """Every (file, line, name) from a literal `tools=` keyword under silica/."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(SILICA_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "tools":
                    continue
                for group in _string_tuples(kw.value):
                    for name in group:
                        found.append((str(path.relative_to(SILICA_ROOT)), kw.value.lineno, name))
    return found




@pytest.mark.parametrize("site", _declared_tool_names(), ids=lambda s: f"{s[0]}:{s[1]}:{s[2]}")
def test_every_literal_tools_declaration_resolves(site):
    path, line, name = site
    assert name in TOOLS, f"{path}:{line} names tool {name!r}, which is not registered"


