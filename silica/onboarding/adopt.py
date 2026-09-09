# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Vault adoption — declare the write boundary once, at the moment a path becomes
the vault.

A vault path is adopted as-is: Silica reads the folder the user pointed at, not a
subfolder it invented. What still needs deciding is where it may *write*, and the
answer differs by content:

  * a folder of prose, Obsidian vault included → notes land in place, next to
    the notes they belong with. Safe mode confines them instead to `silica`, a
    staging mirror of the vault's own tree, merged by a plain file-manager paste
    of its contents over the vault root — opt in from the settings toggle or by
    writing `write_dir: silica` in vault.yaml;
  * a source tree → notes land in `docs/silica`, which is Silica's own folder in
    that repo rather than a mirror of it. Not safe mode: a repo has a place for
    its docs whether or not the staging lane is on.

Either way the choice is declared in `vault.yaml` so it is visible, versionable
and editable instead of re-guessed every run — and reversible from one toggle in
the settings panel, which writes this same field.

No prompt: the detection picks a default, the caller prints it, and `vault.yaml`
makes it changeable. A question at adoption time would block the GUI and MCP
entry points for a decision one line of YAML already expresses.
"""
from __future__ import annotations

import logging
from pathlib import Path

from silica.kernel.recall.paths import (
    NOISE_DIRS,
    SILICAIGNORE_REL,
    looks_like_code,
)
from silica.kernel.vault_manifest import CODE_WRITE_DIR, MANIFEST_REL, SAFE_WRITE_DIR

logger = logging.getLogger(__name__)


_SILICAIGNORE_HEADER = """\
# .silicaignore — what Silica should not search in this vault, as distinct from
# what git should not commit.
#
# One pattern per line; `#` starts a comment. A bare name or glob (`archive`,
# `*.draft.md`) matches a file or folder of that NAME at any depth; a pattern
# with a `/` in it or in front (`docs/private`, `templates/*.md`, `/README.md`)
# is a root-relative path. Hidden names (.git, .venv, .obsidian) are always
# skipped, and .gitignore is deliberately NOT honoured — a gitignored folder is
# often exactly where private notes live.
#
# The list below is built in. It is here to be read and extended, so
# uncommenting a line changes nothing; add your own below it.
"""


def seed_silicaignore(vault: str | Path) -> Path | None:
    """Write the `.silicaignore` template into a source-tree vault, once.

    Returns the path when this call created it, else None: the file is already
    there (never overwrite a hand-edited one), the vault reads as prose (a notes
    folder has no vendored trees to prune), or the root is unwritable.
    """
    root = Path(vault)
    target = root / SILICAIGNORE_REL
    if not root.is_dir() or target.exists() or not looks_like_code(root):
        return None
    body = "".join(f"# {d}\n" for d in sorted(NOISE_DIRS))
    try:
        target.write_text(_SILICAIGNORE_HEADER + body, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write %s (%s) — built-in ignores still apply", target, exc)
        return None
    return target


def write_dir_for(vault: str | Path) -> str:
    """The write boundary this folder's *content* calls for when confined; ""
    ⇒ in place.

    Pure decision, no I/O beyond the detection scan. Callers that compose
    `vault.yaml` themselves (the first-run wizard) use this; `declare_write_dir`
    is the persisting variant, and the settings toggle re-derives from here
    rather than restoring a remembered value — there is no "previous write_dir"
    state to go stale.

    This answers where writes go *if* they are confined, which is why the prose
    branch says `silica` even though safe mode is off by default: it is the
    toggle's ON value, not the adoption default. `declare_write_dir` is what
    decides whether a fresh vault starts confined at all.

    A `docs/silica` that already holds notes settles it whatever the content
    ratio says: that is a vault from before the write boundary existed, and the
    same declaration every new repo gets is also its migration — the notes stay
    where they are, the vault goes back to being the folder you launched in.
    """
    root = Path(vault)
    if not root.is_dir():
        return ""
    # glob is lazy and stops at the first hit; a missing dir yields nothing.
    if next((root / CODE_WRITE_DIR).glob("**/*.md"), None):
        return CODE_WRITE_DIR
    return CODE_WRITE_DIR if looks_like_code(root) else SAFE_WRITE_DIR


def declare_write_dir(vault: str | Path) -> str | None:
    """Persist `write_dir` in `<vault>/vault.yaml` when the vault needs one.

    Returns the declared value when this call wrote it (the caller announces it),
    or None when there was nothing to declare: a manifest already exists (the
    vault has spoken, never overrule it), the path is not a folder, or the
    content only calls for the safe-mode mirror.

    That last case is the default: safe mode is OFF until asked for, so a prose
    vault files in place and gets no `vault.yaml` at all. Only a source tree —
    which needs a home for its docs either way — is declared here. Turning safe
    mode on writes the line that this function deliberately does not.

    Deliberately creates no directory: an empty `docs/silica` would then read as
    a pre-`write_dir` vault to every back-compat lookup.
    """
    root = Path(vault)
    manifest = root / MANIFEST_REL
    if not root.is_dir() or manifest.exists():
        return None
    declared = write_dir_for(root)
    if not declared or declared == SAFE_WRITE_DIR:
        return None
    try:
        manifest.write_text(f"write_dir: {declared}\n", encoding="utf-8")
    except OSError as exc:
        # A read-only or unwritable vault root is the user's business, not a
        # crash: fall through to the in-place default and say nothing new.
        logger.warning("could not write %s (%s) — write_dir stays the default", manifest, exc)
        return None
    return declared
