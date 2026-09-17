# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""A folder is a root only once `silica init` adopted it, and the MCP server
serves no other. Opened in a home folder on 2026-09-18, a session indexed
it whole: 65,530 documents, 389,529 vectors, 6.8 GB of RAM per server,
two servers per session, for as long as the session lived."""
from __future__ import annotations

import json

from silica_core.config import CONFIG


def test_the_gate_refuses_until_vault_yaml_appears_then_warms_up_once(tmp_path, monkeypatch):
    from silica_core.ui import mcp
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    started: list[str] = []
    monkeypatch.setattr(mcp, "configure_retrieval", started.append)  # the real one loads a model
    check = mcp.root_gate("local-hybrid")
    refused = json.loads(check())
    assert refused["error"]["code"] == "not_initialised" and "silica init" in refused["error"]["hint"]
    assert refused["root"] == str(tmp_path.resolve()) and started == []
    (tmp_path / "vault.yaml").write_text("# silica init\n", encoding="utf-8")
    assert check() is None and check() is None and started == ["local-hybrid"]


def test_over_the_wire_a_folder_without_vault_yaml_gets_not_initialised(tmp_path):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "SILICA_VAULT"}  # the root is the cwd
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; from silica_core.ui.mcp import run_mcp; sys.exit(run_mcp())"],
        cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        def send(o):
            proc.stdin.write(json.dumps(o).encode() + b"\n")
            proc.stdin.flush()
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "test", "version": "0"}}})
        assert "not a silica root" in json.loads(proc.stdout.readline())["result"]["instructions"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "silica_search", "arguments": {"query": "anything"}}})
        body = json.loads(json.loads(proc.stdout.readline())["result"]["content"][0]["text"])
        assert body["error"]["code"] == "not_initialised" and body["root"] == str(tmp_path.resolve())
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_init_marks_a_prose_folder_as_a_root(tmp_path):
    from silica_core.cli import main
    from silica_core.kernel.vault_manifest import initialised
    (tmp_path / "a.md").write_text("# a\n\nprose, nothing to declare a write_dir for\n", encoding="utf-8")
    assert not initialised(tmp_path)
    assert main(["--vault", str(tmp_path), "init"]) == 0
    assert initialised(tmp_path)


def test_a_comment_only_vault_yaml_is_an_empty_manifest(tmp_path, caplog):
    from silica_core.kernel.vault_manifest import load_manifest
    (tmp_path / "vault.yaml").write_text("# silica init\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="silica_core.kernel.vault_manifest"):
        m = load_manifest(tmp_path)
    assert m.sources == ("prose",) and m.write_dir == "" and not caplog.records
