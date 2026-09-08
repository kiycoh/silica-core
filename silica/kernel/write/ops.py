# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Rollback inverse ops (ADR-0009 / Addendum C3).

The only op schema the core still carries: what the ws backend records so a
write made through the Obsidian bridge can be undone. The distiller's own op
grammar (write/patch/overwrite/move plans, bulk results, structured decoding)
belongs to the private pipeline, not here — the core's own undo is git.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class InverseOpKind(str, Enum):
    delete_created = "delete_created"       # undo a write: delete the note that was created
    restore_version = "restore_version"     # undo a patch: history:restore to prior version
    recreate_deleted = "recreate_deleted"   # undo a delete: recreate with prior content
    move_back = "move_back"                 # undo a move: DRIVER.move(to_path, from_path)


class InverseOp(BaseModel):
    kind: InverseOpKind
    path: str
    version: int | None = None            # for restore_version
    prior_content: str | None = None      # for recreate_deleted
    to_path: str | None = None            # for move_back: where the note was moved to
