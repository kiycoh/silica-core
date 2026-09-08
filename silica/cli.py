# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""`silica` — the five tools of TOOLS.md as subcommands, plus the server and
the maintenance commands. Every tool subcommand prints the tool's JSON reply
and exits 1 when the reply carries `error`."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _bind_root(vault: str) -> Path:
    """The root is the folder Silica runs in unless --vault or SILICA_VAULT
    (exported) says otherwise. Bound before any module reads CONFIG."""
    from silica.config import CONFIG

    root = Path(vault or os.environ.get("SILICA_VAULT", "") or os.getcwd()).resolve()
    if not root.is_dir():
        sys.exit(f"silica: {root} is not a folder")
    os.environ["SILICA_VAULT"] = str(root)
    CONFIG.vault_path = str(root)
    return root


def _emit(result: dict) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 1 if isinstance(result, dict) and "error" in result else 0


def _parser() -> argparse.ArgumentParser:
    from silica._version import __version__

    p = argparse.ArgumentParser(prog="silica", description="Non-LLM retrieval tools for agent harnesses (see TOOLS.md).")
    p.add_argument("--vault", default="", metavar="DIR", help="root folder (default: the folder you run in)")
    p.add_argument("--version", action="version", version=f"silica {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("files", help="inventory and index state")
    s.add_argument("--folder", default="")
    s.add_argument("--status", default="", choices=["", "indexed", "changed", "excluded", "failed", "unconverted"])
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--cursor", default="")

    s = sub.add_parser("search", help="ranked passages")
    s.add_argument("query")
    s.add_argument("--folder", default="")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--per-doc", type=int, default=2)
    s.add_argument("--width", type=int, default=600)

    s = sub.add_parser("read", help="a located slice of one file")
    s.add_argument("path")
    s.add_argument("--start", type=int, default=1)
    s.add_argument("--end", type=int, default=0)
    s.add_argument("--section", default="")
    s.add_argument("--max-chars", type=int, default=16000)
    s.add_argument("--expect-version", default="")

    s = sub.add_parser("code-pack", help="AST context pack for one source file")
    s.add_argument("target")
    s.add_argument("--budget", type=int, default=24000)
    s.add_argument("--sections", default="", help="comma-separated: hierarchy,neighborhood,external,importers")

    s = sub.add_parser("write-note", help="write one note atomically and lint it")
    s.add_argument("path")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", default=None)
    g.add_argument("--body-file", default=None, help="read the body from this file ('-' = stdin)")
    s.add_argument("--frontmatter", default="", help="JSON object")
    s.add_argument("--expect-version", default="")
    s.add_argument("--create-only", action="store_true")

    s = sub.add_parser("index", help="build or refresh the index")
    s.add_argument("--rebuild", action="store_true")

    s = sub.add_parser("mcp", help="serve the tools over stdio MCP")
    s.add_argument("--extended", action="store_true", help="also serve tables and link tools")

    s = sub.add_parser("doctor", help="check this install and this root")
    s.add_argument("--json", action="store_true")

    sub.add_parser("init", help="adopt this folder: ignore file, write_dir, first index")

    s = sub.add_parser("setup", help="register the MCP server in a client")
    s.add_argument("client", choices=["claude", "codex", "opencode", "dsh"])
    s.add_argument("--dry-run", action="store_true")

    sub.add_parser("connect", help="bridge to the Obsidian desktop app")

    s = sub.add_parser("import", help="extract text from a PDF or office file into a .md beside it")
    s.add_argument("path")

    s = sub.add_parser("update", help="self-update")
    s.add_argument("--check", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    p = _parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    if a.cmd == "update":
        from silica.update import run_update
        return run_update(check_only=a.check)
    root = _bind_root(a.vault)
    import silica.core as core

    if a.cmd == "files":
        return _emit(core.files(folder=a.folder, status=a.status, limit=a.limit, cursor=a.cursor))
    if a.cmd == "search":
        return _emit(core.search(a.query, folder=a.folder, k=a.k, per_doc=a.per_doc, width=a.width))
    if a.cmd == "read":
        return _emit(core.read(a.path, start=a.start, end=a.end, section=a.section,
                               max_chars=a.max_chars, expect_version=a.expect_version))
    if a.cmd == "code-pack":
        secs = [x.strip() for x in a.sections.split(",") if x.strip()] or None
        return _emit(core.code_pack(a.target, budget_chars=a.budget, sections=secs))
    if a.cmd == "write-note":
        if a.body_file is not None:
            body = sys.stdin.read() if a.body_file == "-" else Path(a.body_file).read_text(encoding="utf-8")
        else:
            body = a.body
        fm = json.loads(a.frontmatter) if a.frontmatter else None
        return _emit(core.write_note(a.path, body, frontmatter=fm, expect_version=a.expect_version,
                                     create_only=a.create_only))
    if a.cmd == "index":
        return _emit(core.build_index(rebuild=a.rebuild))
    if a.cmd == "mcp":
        import logging

        from silica.ui.mcp import run_mcp
        logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
        return run_mcp(extended=a.extended)
    if a.cmd == "doctor":
        from silica.config import CONFIG
        from silica.onboarding import checks
        results = checks.run_checks(CONFIG)
        if a.json:
            print(json.dumps(checks.report_payload(results), ensure_ascii=False, indent=2))
        else:
            checks.render_report(results)
        return checks.exit_code(results)
    if a.cmd == "init":
        from silica.onboarding.adopt import declare_write_dir, seed_silicaignore
        ignore = seed_silicaignore(root)
        declared = declare_write_dir(root)
        built = core.build_index(rebuild=True)
        return _emit({"root": str(root), "silicaignore": str(ignore) if ignore else None,
                      "write_dir": declared, "index": built["index"], "docs": built["docs"],
                      "next": "silica setup <claude|codex|opencode|dsh> registers the MCP server"})
    if a.cmd == "setup":
        from silica.onboarding.setup_client import run_setup
        return run_setup([a.client] + (["--dry-run"] if a.dry_run else []))
    if a.cmd == "connect":
        from silica.ui.connect import run_connect
        return run_connect()
    if a.cmd == "import":
        from silica.sources.convert import convert
        try:
            rel = core._rel(a.path)
        except ValueError as e:
            return _emit(core._error("out_of_root", str(e)))
        try:
            notes = convert(str(root / rel), str((root / rel).parent))
        except Exception as e:  # converter errors are the reply, not a traceback
            return _emit(core._error("bad_argument", str(e)))
        return _emit({"root": str(root), "source": rel, "notes": notes})
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
