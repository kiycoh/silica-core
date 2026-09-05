# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""`silica hook <event>` — the hook-side producer for coding-agent harnesses.

Claude Code, Codex and DeepSeek Harness all run a `hooks.json` command with
the event payload on stdin and add plain stdout to the session as context on
SessionStart (ADR-0025). Plain text rather than the JSON envelope on purpose:
the envelope's field names differ per dialect, while stdout text is what all
three accept on that event.

Nothing here may fail loud. This process runs inside someone else's session,
so every exit is 0 and every problem is silence; `silica.capture` made the
same choice for the end of the session.
"""
from __future__ import annotations

import json
import os
import sys

from silica.capture import find_vault


def session_start(stdin_text: str) -> str:
    """The session's opening line about its vault, or "" when cwd has none."""
    try:
        payload = json.loads(stdin_text or "{}")
        cwd = payload.get("cwd") or os.getcwd()
    except (ValueError, AttributeError):
        return ""  # the payload is the client's; anything unreadable is "no vault"
    vault = find_vault(str(cwd))
    if vault is None:
        return ""
    from silica.ui.mcp import INSTRUCTIONS

    # The path and the loop, nothing counted: a note count needs the excludes
    # the indexes apply (a repo vault here walked 37,897 gitignored fixtures
    # as "notes"), and the count changes nothing the agent does next.
    return f"Silica vault: {vault}.\n{INSTRUCTIONS}\n{_drift_line(vault)}{_migrate_line(vault)}"


def _drift_line(vault: str) -> str:
    """Code drift since the stale snapshot, or "" (ADR-0038: detection at
    session open, from the cache and one git diff; never a recompute, never
    a write). Names at most three notes per clause: the line is a pointer to
    /stale, not the report."""
    try:
        from silica.kernel.code.codedocs import CHANGE_STRUCTURAL, drift
        d = drift(vault)
    except Exception:
        return ""  # the brief is an aid; a broken code lane must not break the session
    if not d:
        return ""
    parts = []
    if d["affected"]:
        parts.append(f"{len(d['changed'])} documented path(s) changed since the stale "
                     f"snapshot at {d['snap_head'][:8]} ({_few(d['changed'])}); notes "
                     f"to re-verify: {_few(sorted(d['affected']))}")
    structural = sorted(n for n, lvl in d["stale"].items() if lvl == CHANGE_STRUCTURAL)
    if structural:
        parts.append(f"{len(structural)} note(s) structurally stale at that snapshot: "
                     f"{_few(structural)}")
    if not parts:
        return ""
    return ("Code drift: " + "; ".join(parts) + ". Read them with silica_read_note "
            "before citing (each read carries its stale banner); /stale in the REPL "
            "refreshes the snapshot.\n")


def _migrate_line(vault: str) -> str:
    """Notes still in a shape the gate no longer writes, or "" (ADR-0039: the
    count at session open, the rewrite on /migrate --write). A walk and a
    regex, 0.06 s on 944 notes; never a write."""
    try:
        from silica.kernel.write.migrate import PROVENANCE_HEADER, count
        n = count(vault)
    except Exception:
        return ""  # the brief is an aid; a broken write lane must not break the session
    if not n:
        return ""
    return (f"Older shape: {n} note(s) still carry the {PROVENANCE_HEADER.name} block. "
            "/migrate in the REPL lists them, /migrate --write rewrites them as one "
            "revertible run.\n")


def _few(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" +{len(items) - n}" if len(items) > n else "")


_PRODUCERS = {"SessionStart": session_start}


def run_hook(argv: list[str], stdin_text: str) -> int:
    producer = _PRODUCERS.get(argv[0] if argv else "")
    if producer is None:
        return 0  # an event this version has nothing to say about is not an error
    text = producer(stdin_text)
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0
