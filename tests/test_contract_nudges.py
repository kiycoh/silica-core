# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The three ways the plugin asks to be used: the prompt hook fires on a
question without an identifier and on nothing else; `silica setup claude`
puts its block into CLAUDE.md once, between markers; the search description
opens with the ask and stays under Claude Code's comfortable size."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _hook():
    spec = importlib.util.spec_from_file_location("prompt_hook", REPO / "hooks" / "prompt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_fires_on_a_question_without_an_identifier_only():
    h = _hook()
    assert h.nudge("Where does the code decide what to do when a tool's answer is too long?") == h.LINE
    assert h.nudge("How does the migration system handle irreversible operations?") == h.LINE
    assert h.nudge("List every call site of `best_window_spans`.") is None  # a name in backticks
    assert h.nudge("What does silica_code_pack do with a #L12 target?") is None  # snake_case, #L
    assert h.nudge("How does SessionRedirectMixin merge cookies?") is None  # camelCase
    assert h.nudge("fix the bug in silica/core.py") is None  # a path, and not a question
    assert h.nudge("") is None
    out = subprocess.run([sys.executable, str(REPO / "hooks" / "prompt.py")],
                         input=json.dumps({"prompt": "Why is the cache invalidated on every write?"}),
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == h.LINE
    assert subprocess.run([sys.executable, str(REPO / "hooks" / "prompt.py")], input="not json",
                          capture_output=True, text=True).returncode == 0
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["hooks"] == "./hooks/hooks.json"
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())
    assert "UserPromptSubmit" in hooks["hooks"]


def test_setup_claude_writes_its_block_once(tmp_path, monkeypatch):
    from silica.onboarding import guidance as g
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = g.claude_md_path()
    assert path == tmp_path / "CLAUDE.md"
    assert g.write_guidance(path) == "created" and path.read_text().startswith(g.START)
    path.write_text("# mine\n\nkeep this\n" + path.read_text() + "\nand this\n")
    assert g.write_guidance(path) == "replaced"
    text = path.read_text()
    assert text.count(g.START) == 1 and text.startswith("# mine") and text.rstrip().endswith("and this")
    path.write_text("# only mine\n")
    assert g.write_guidance(path) == "appended" and path.read_text().startswith("# only mine\n\n" + g.START)


def test_search_description_opens_with_the_ask_and_fits():
    from silica.ui.mcp import exposed_tools
    d = exposed_tools(False)["silica_search"].description
    assert d.startswith("For a question that names no identifier") and "Do not grep for the words of a question" in d
    assert len(d) < 2048
