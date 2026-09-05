# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""`silica` console-script entry.

The two harness hooks (`hook`, `capture`, ADR-0025) run inside someone else's
session at every start and end. Importing `silica.cli` costs 3.3 s on this
machine (2026-09-04: agent loop, prompt toolkit, rich) for a hook whose own
work is 0.2 s, so they are dispatched here before that module is touched.
Everything else is `silica.cli.main`, unchanged. Fail-open covers the import
too: a hook that exits non-zero or prints a traceback lands in the host
session as an error.
"""
from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["hook"]:
        try:
            from silica.hook import run_hook
            return run_hook(args[1:], sys.stdin.read())
        except Exception:
            return 0
    if args[:1] == ["capture"]:
        try:
            from silica.capture import run_capture
            return run_capture(sys.stdin.read())
        except Exception:
            return 0
    from silica.cli import main as cli_main
    return cli_main()
