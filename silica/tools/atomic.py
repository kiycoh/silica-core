# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Link tools over the driver's wikilink graph (served under `--extended`).
The five default tools live in silica.core."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from silica.driver import DRIVER
from silica.tools import tool

_CAP = 200


@tool(cls="atomic")
def silica_links(name: Annotated[str, Field(description="Note name or root-relative path")]) -> list:
    """Outgoing wikilinks of a note, as root-relative paths."""
    return [r.path for r in DRIVER.links(name)]


@tool(cls="atomic")
def silica_backlinks(name: Annotated[str, Field(description="Note name or root-relative path")]) -> list:
    """Notes that link to this note, as root-relative paths."""
    return [r.path for r in DRIVER.backlinks(name)]


@tool(cls="atomic")
def silica_orphans() -> dict:
    """Notes with no incoming link: {total, orphans} capped at 200."""
    paths = [r.path for r in DRIVER.orphans()]
    return {"total": len(paths), "orphans": paths[:_CAP], "truncated": len(paths) > _CAP}


@tool(cls="atomic")
def silica_unresolved() -> list:
    """Wikilinks whose target is not a note, as {source, target}."""
    return [{"source": link.source.path, "target": link.target} for link in DRIVER.unresolved()]
