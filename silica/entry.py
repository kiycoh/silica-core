# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""`silica` console-script entry."""
from __future__ import annotations


def main() -> int:
    from silica.cli import main as cli_main
    return cli_main()
