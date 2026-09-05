# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Who signs a write: the identity is inherited from the launcher, never
asserted by the model calling the tool.

Chain: SILICA_AGENT_ID (explicit fleet name) > the MCP client's own name from
the initialize handshake (+ the client's session id when it exports one) >
nothing, which the REPL reads as a person at the keyboard. A tool call that
reaches the server without a handshake identity is `agent:unknown`, never
`user`: an unnamed client is still not a human.
"""
from __future__ import annotations

import anyio
import pytest

from silica.kernel.write import notetype, templates
from silica.kernel.write.contested import contested_by_agents_only, contested_refs
from tests.test_flag_note import NOTE


@pytest.fixture(autouse=True)
def _no_identity(monkeypatch):
    monkeypatch.delenv("SILICA_AGENT_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    notetype.set_mcp_client("")
    yield
    notetype.set_mcp_client("")


def test_agent_id_is_empty_for_a_person_at_the_repl():
    assert notetype.agent_id() == ""


def test_agent_id_inherits_the_mcp_client():
    notetype.set_mcp_client("claude-code")
    assert notetype.agent_id() == "claude-code"


def test_explicit_env_wins_over_the_handshake(monkeypatch):
    notetype.set_mcp_client("claude-code")
    monkeypatch.setenv("SILICA_AGENT_ID", "coder-7")
    assert notetype.agent_id() == "coder-7"


def test_stamp_agent_uses_the_inherited_identity():
    notetype.set_mcp_client("claude-code")
    assert 'agent: "claude-code"' in templates.ensure_system_floor("some text")


def test_stamp_agent_stays_silent_at_the_repl():
    assert "agent:" not in templates.ensure_system_floor("some text")


def test_flag_note_signs_with_the_inherited_identity(tmp_vault):
    from silica.tools.notes import silica_flag_note

    notetype.set_mcp_client("claude-code:abc")
    path = tmp_vault.note("area/T.md", NOTE)
    silica_flag_note("area/T.md", reason="stale")
    refs = contested_refs(tmp_vault.read(path))
    assert refs == ["flagged: stale (by claude-code:abc, %s)" % _today()]
    assert contested_by_agents_only(refs)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# --- the MCP side: handshake -> identity ------------------------------------

def _params(name: str | None):
    if name is None:
        return None
    import mcp.types as types
    return types.InitializeRequestParams(
        protocolVersion="2025-06-18", capabilities=types.ClientCapabilities(),
        clientInfo=types.Implementation(name=name, version="0"),
    )


def test_client_identity_is_the_client_name():
    from silica.ui.mcp import client_identity
    assert client_identity(_params("claude-code"), {}) == "claude-code"


def test_client_identity_appends_the_client_session_id():
    from silica.ui.mcp import client_identity
    env = {"CLAUDE_CODE_SESSION_ID": "6cb4420e"}
    assert client_identity(_params("claude-code"), env) == "claude-code:6cb4420e"


def test_client_identity_without_a_handshake_is_an_unknown_agent():
    from silica.ui.mcp import client_identity
    assert client_identity(None, {"CLAUDE_CODE_SESSION_ID": "x"}) == "agent:unknown"
    assert client_identity(_params("  "), {}) == "agent:unknown"
    assert contested_by_agents_only(["flagged: r (by agent:unknown, 2026-09-04)"])


def test_client_identity_is_one_yaml_safe_line():
    from silica.ui.mcp import client_identity
    assert client_identity(_params("x\ninjected: true"), {}) == "x"


def test_served_tool_signs_with_the_handshake_identity(tmp_vault):
    """End to end over the in-memory transport: the client names itself once
    in initialize, and a later silica_flag_note carries that name."""
    import mcp.types as types
    from mcp.shared.memory import create_connected_server_and_client_session

    from silica.ui.mcp import make_server

    path = tmp_vault.note("area/T.md", NOTE)

    async def go():
        async with create_connected_server_and_client_session(
            make_server(all_tools=True),
            client_info=types.Implementation(name="probe-client", version="1"),
        ) as session:
            await session.call_tool("silica_flag_note", {"name": "area/T.md", "reason": "stale"})

    anyio.run(go)
    refs = contested_refs(tmp_vault.read(path))
    assert refs and "(by probe-client, " in refs[0], refs
