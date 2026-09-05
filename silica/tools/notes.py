# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Single-note tools — fast-path create/patch with /undo checkpoints.

No temp-file + bulk_write round-trip: these are the interactive-edit
counterparts of the batch pipeline in silica.tools.pipeline.
"""
from __future__ import annotations


import logging
from typing import Annotated, Any

from pydantic import Field

from silica.driver import DRIVER
from silica.tools import tool
from silica.kernel.write.ops import Op, OpType

logger = logging.getLogger(__name__)


# One description, two tools: write_note and patch_note bind `documents:` the
# same way, and a model that learns the contract on one must read the same words
# on the other. Kept as a bare string, not a Field: the parameter's default now
# lives in the signature, and pydantic refuses a default declared twice.
_DOCUMENTS_DESC = (
    "Repo-relative paths (file or directory) whose rationale this note "
    "records — the why, not the what: closed directions, measured "
    "ceilings, constraints not derivable from the source. Conventionally "
    "the note lives at <wiki_dir>/<repo-path>.md so the vault mirrors "
    "the code tree. /stale flags the note once a commit moves the "
    "bound path past the recorded code_ref."
)


def _bind_documents(entries: list[str]) -> tuple[list[str], str | None, str | None]:
    """Validate a `documents:` binding against the repo. Returns
    (paths, code_ref, error) — on error the caller must not write."""
    from silica.config import CONFIG
    from silica.kernel.code import codedocs, gitstate
    from silica.kernel.recall.paths import repo_root_for

    root = repo_root_for(getattr(CONFIG, "vault_path", "") or "")
    if root is None:
        return [], None, "no repo for this vault: `documents:` needs codebase mode"
    docs, err = codedocs.validate_documents(entries, root)
    if err:
        return [], None, err
    # code_ref only when a file is bound: a directory binding records a
    # rationale, and a rationale does not expire because some file under the
    # package changed (a file binding keeps staleness — see codedocs).
    ref = gitstate.head_ref(root) if any((root / p).is_file() for p in docs) else None
    return docs, ref, None


@tool(cls="composed", collapse="eager")
def silica_patch_note(
    name: Annotated[str, Field(description='Name or vault-relative path of the note to patch')],
    heading: Annotated[str, Field(description='Concept/section heading the snippet is filed under')],
    snippet: Annotated[str, Field(description='Distilled body text to append to the note')],
    source_basename: Annotated[str, Field(description='Provenance: source filename this snippet derives from')],
    hub: Annotated[str | None, Field(description='Optional [[Hub]] to link in frontmatter if missing')] = None,
    documents: Annotated[list[str] | None, Field(description=_DOCUMENTS_DESC)] = None,
) -> dict[str, Any]:
    """Append a snippet under a heading in a single EXISTING note — the fast path
    for interactive edits.

    To create a new note use silica_write_note; for nucleating whole documents
    into many notes use silica_run_injector. Every successful patch is
    checkpointed and can be reverted with /undo.
    """
    from pathlib import Path

    from silica.config import CONFIG
    from silica.kernel.recall.paths import contain_in_vault
    from silica.kernel.write import templates as tpl
    from silica.kernel.write.bulk import execute_one
    from silica.kernel.write.checkpoints import get_checkpoint_store
    from silica.kernel.workqueue import path_lease

    docs: list[str] = []
    code_ref: str | None = None
    if documents:
        docs, code_ref, err = _bind_documents(documents)
        if err:
            return {"error": err}

    # Resolve the note to its vault-relative path (read is idempotent).
    try:
        path = DRIVER.read_note(name).ref.path or name
    except Exception as e:
        return {"error": f"Failed to read note '{name}': {e}"}

    # read_note falls back to the resolved absolute path for anything it found
    # outside the vault, and the patch below writes back to whatever it returned:
    # confine it before the lease so the patch can never land outside.
    try:
        path = contain_in_vault(path, Path(CONFIG.vault_path))
    except ValueError as e:
        return {"error": f"Refused note '{name}': {e}"}

    op = Op(
        op=OpType.patch,
        heading=heading,
        source_basename=source_basename,
        path=path,
        snippet=snippet,
        hub=hub,
    )

    # Read prior content, patch and checkpoint all under the lease: the
    # read-modify-write must not interleave with another writer on this note.
    with path_lease(path):
        try:
            prior_content = DRIVER.read_note(path).content
        except Exception as e:
            return {"error": f"Failed to read note '{name}': {e}"}

        try:
            result = execute_one(op)
        except Exception as e:
            return {"error": f"Failed to patch '{name}': {e}"}

        if docs:
            try:
                patched = DRIVER.read_note(path).content
                stamped = tpl.stamp_documents(patched, docs, code_ref)
                if stamped != patched:
                    DRIVER.overwrite(path, stamped)
            except Exception as e:
                return {"error": f"Failed to bind documents on '{name}': {e}"}

            try:
                from silica.config import CONFIG
                from silica.kernel.code import codedocs
                codedocs.invalidate_snapshot(CONFIG.vault_path)
            except Exception:
                pass  # cache hygiene must never fail the write

        # Record the resulting on-disk content as a restore point.
        checkpoint_depth = None
        checkpoint_ok = False
        try:
            new_content = DRIVER.read_note(path).content
            checkpoint_depth = get_checkpoint_store().push(path, prior_content, new_content)
            checkpoint_ok = True
        except Exception as e:
            # A patch that succeeded must not be reported as failed just because
            # the undo bookkeeping hiccuped; undo is best-effort. But the MCP
            # surface advertises these writes as non-destructive on the strength
            # of /undo, so a missing restore point has to be visible, not silent.
            logger.warning("checkpoint push failed for '%s': %s — /undo has no "
                           "restore point for this write", path, e)

    return {**result, "note": name, "path": path,
            "checkpoint_depth": checkpoint_depth, "checkpoint_ok": checkpoint_ok}


@tool(cls="composed", collapse="eager")
def silica_flag_note(name: Annotated[str, Field(description='Name or vault-relative path of the note to flag')], reason: Annotated[str, Field(description='Why the note is wrong or stale, in a few words')] = "", clear: Annotated[bool, Field(description='Clear a previously set flag instead of setting one')] = False,
                     ref: Annotated[str, Field(description="With clear: resolve only this entry of the note's `contradictions:` list, verbatim; default resolves every open one")] = "") -> dict[str, Any]:
    """Flag an EXISTING note as wrong or stale, found while USING it: marks
    `contested` in frontmatter (clear with clear=True). Contested notes are
    demoted and marked at recall — never silently dropped — and surfaced in
    the run digest. Content is never edited or deleted; the human decides.
    Revertible with /undo.
    """
    import datetime

    from silica.kernel.write.checkpoints import get_checkpoint_store
    from silica.kernel.write.contested import (
        contested_refs,
        mark_contested,
        resolve_contested,
    )
    from silica.kernel.write.notetype import agent_id

    try:
        nc = DRIVER.read_note(name)
    except Exception as e:
        return {"error": f"Failed to read note '{name}': {e}"}

    path = nc.ref.path or name
    prior_content = nc.content
    # `user` only when no agent identity exists at all: a person at the REPL.
    # An MCP client always has one (the server sets it from the handshake).
    who = agent_id() or "user"
    today = datetime.date.today().isoformat()

    if clear:
        # Not clear_contested: dropping the flag while leaving a body callout
        # that still reads "Unresolved" makes the note lie about its own state.
        # resolve_contested files the callout under `## Superseded` first.
        new_content = resolve_contested(prior_content, resolved_by=who, valid_to=today,
                                        source_ref=ref or None)
    else:
        source_ref = f"flagged: {reason} (by {who}, {today})"
        new_content = mark_contested(prior_content, source_ref)

    # Read the state back off the note instead of assuming `not clear`: with a
    # `ref` the clear is partial, and a note with contradictions still open is
    # contested whatever the caller asked for.
    still_contested = bool(contested_refs(new_content))

    if new_content == prior_content:
        return {"note": name, "path": path, "contested": still_contested, "changed": False}

    try:
        DRIVER.overwrite(path, new_content)
    except Exception as e:
        return {"error": f"Failed to write '{name}': {e}"}

    try:
        from silica.kernel import contested_register
        contested_register.add(path) if still_contested else contested_register.discard(path)
    except Exception:
        pass  # digest index is best-effort; the note's frontmatter is the truth

    checkpoint_depth = None
    checkpoint_ok = False
    try:
        checkpoint_depth = get_checkpoint_store().push(path, prior_content, new_content)
        checkpoint_ok = True
    except Exception as e:
        logger.warning("checkpoint push failed for '%s': %s — /undo has no "
                       "restore point for this write", path, e)

    return {"note": name, "path": path, "contested": still_contested,
            "checkpoint_depth": checkpoint_depth, "checkpoint_ok": checkpoint_ok}


@tool(cls="composed", collapse="eager")
def silica_write_note(
    path: Annotated[str, Field(description="Vault-relative path for the new note (e.g. 'Computer Science/Computer Vision.md')")],
    body: Annotated[str, Field(description='Markdown body only — NO YAML frontmatter; it is applied mechanically from the vault template')],
    title: Annotated[str | None, Field(description='H1 title; defaults to the filename stem')] = None,
    tags: Annotated[list[str] | None, Field(description='Frontmatter tags; normalized automatically')] = None,
    related: Annotated[list[str] | None, Field(description='Related note names, rendered as frontmatter wikilinks')] = None,
    parent: Annotated[str | None, Field(description="Parent note name for the 'parent note' frontmatter key")] = None,
    template: Annotated[str | None, Field(description="Named template from the vault's templates dir; 'none' skips the skeleton (AI/last-modified floor still applied)")] = None,
    props: Annotated[dict[str, str] | None, Field(description="Extra scalar frontmatter keys, e.g. {'type': 'syllabus'}; AI/last modified/verified are reserved")] = None,
    documents: Annotated[list[str] | None, Field(description=_DOCUMENTS_DESC)] = None,
) -> dict[str, Any]:
    """Create a new note — the fast path for single-note creation.

    Pass frontmatter as structured fields (title/tags/related/parent, scalar
    `props`), never raw YAML in `body` (a leading YAML block is stripped).
    Skeleton from the vault template (explicit `template` > vault default >
    built-in); `template="none"` writes the body as-is. Fails if the note
    exists: append with silica_patch_note; multi-note nucleation with gates
    and rollback: silica_run_injector. Checkpointed, revertible with /undo.
    """
    from pathlib import Path, PurePosixPath

    from silica.config import CONFIG
    from silica.kernel.recall.paths import contain_in_vault
    from silica.kernel.write import templates as tpl
    from silica.kernel.write.checkpoints import get_checkpoint_store
    from silica.kernel.workqueue import path_lease

    # `path` is model-supplied and this fast path never runs validate_operations,
    # so the vault boundary is enforced here — before the lease, so a rejected
    # path never takes a lock — and as an error dict rather than the driver's
    # exception, which the model cannot act on.
    try:
        path = contain_in_vault(path, Path(CONFIG.vault_path))
    except ValueError as e:
        return {"error": f"Refused path '{path}': {e}"}

    # `AI:`, `last modified:` and `verified:` are the system floor and the human
    # trust tier — a model writing them would hand the agent a lever on its own
    # authority (OKF §5.2). Reject, never silently drop: the caller must know.
    if props:
        reserved = [k for k in props if k.strip().lower() in ("ai", "last modified", "verified")]
        if reserved:
            return {"error": f"Reserved frontmatter key(s) {reserved}: "
                             "AI, last modified and verified are system-owned."}

    docs: list[str] = []
    code_ref: str | None = None
    if documents:
        docs, code_ref, err = _bind_documents(documents)
        if err:
            return {"error": err}

    if template == "none":
        content = body
    else:
        try:
            source = tpl.resolve_template(template)
        except tpl.TemplateNotFoundError as e:
            return {"error": str(e)}
        fields = tpl.prepare_fields(
            title=title or PurePosixPath(path).stem,
            body=body,
            tags=tags,
            related=related,
            parent=parent,
        )
        content = tpl.render_note(source, fields)
    content = tpl.ensure_system_floor(content)
    if props:
        try:
            content = tpl.upsert_props(content, props)
        except ValueError as e:
            return {"error": str(e)}
    if docs:
        content = tpl.stamp_documents(content, docs, code_ref)

    # The existence check and the create must be atomic: the fs backend's
    # create() now raises on an existing note, but the pre-check under lease
    # stays: it returns a friendly "use silica_patch_note" error instead of a
    # backend exception, and cross-process it still guards other agents.
    with path_lease(path):
        try:
            DRIVER.read_note(path)
        except Exception:
            pass  # missing note — the happy path
        else:
            return {"error": f"Note '{path}' already exists: use silica_patch_note to modify it."}

        try:
            ref = DRIVER.create(path, content)
        except Exception as e:
            return {"error": f"Failed to create note '{path}': {e}"}

        if docs:
            try:
                from silica.config import CONFIG
                from silica.kernel.code import codedocs
                codedocs.invalidate_snapshot(CONFIG.vault_path)
            except Exception:
                pass  # cache hygiene must never fail the write

        checkpoint_depth = None
        checkpoint_ok = False
        try:
            checkpoint_depth = get_checkpoint_store().push(path, "", content)
            checkpoint_ok = True
        except Exception as e:
            logger.warning("checkpoint push failed for '%s': %s — /undo has no "
                           "restore point for this write", path, e)

    return {"op": "write", "success": True, "path": ref.path or path,
            "checkpoint_depth": checkpoint_depth, "checkpoint_ok": checkpoint_ok}
