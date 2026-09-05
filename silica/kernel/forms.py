# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Per-file source-form resolution (docs/specs/nucleation-forms.md).

The unit of theming is the file's source form, not the vault. The ladder is
mechanical first: an ingress stamp is known by construction and wins; only
text that arrived unstamped AND unconverted pays one small classification
call; everything else falls back to the vault's declared profile. The verdict
always carries its origin so the run header can print it — the distiller never
self-selects its lens (that is the experiment that failed 3/3 in the creator
audit).
"""
from __future__ import annotations

import logging
from typing import NamedTuple

from silica.agent.llm import call_llm

logger = logging.getLogger(__name__)

FORMS = ("study", "transcript", "clip", "draft")

# draft maps to no lens on purpose: it is the filing path, not a distillation.
_PROFILES = {"study": "default", "transcript": "transcript", "clip": "clip", "draft": ""}

_SNIFF_PROMPT = (
    "Classify the FORM of this file for a note-taking pipeline. Reply with "
    "exactly one word from: study, transcript, clip, draft, unsure.\n"
    "- study: reference or learning material to distill into concepts\n"
    "- transcript: a record of an event (a spoken recording, typed call or "
    "meeting notes). Terminal: it exists to be distilled into durable facts\n"
    "- clip: content saved from elsewhere (an article, a web clipping), "
    "possibly with the owner's own commentary around it\n"
    "- draft: the owner's own in-progress artifact (a script, an essay, a "
    "voiceover pass) that they are still authoring and will edit further\n"
    "- unsure: none of the above fits clearly\n\n"
    "File content:\n"
)


class Form(NamedTuple):
    form: str      # one of FORMS, or "" when nothing decided
    profile: str   # distill profile to use ("" only for draft)
    origin: str    # stamp | sniff | fallback | default


def read_source_text(rel: str) -> str:
    """Read a nucleation source by vault-relative path, .txt included.

    DRIVER.read_note only reads notes; a `.txt` inbox source (the ep180 case)
    needs the filesystem fallback or it can never be classified.
    """
    from silica.driver import DRIVER

    try:
        return DRIVER.read_note(rel).content or ""
    except Exception:
        from pathlib import Path

        from silica.config import CONFIG

        return (Path(CONFIG.vault_path) / rel).read_text(
            encoding="utf-8", errors="replace"
        )


def profile_for(form: str) -> str:
    """Lens profile for a form; "" for draft (filing path, no distiller)."""
    return _PROFILES.get(form, "default")


def stamped_form(text: str) -> str:
    """The ingress `form:` stamp out of the frontmatter; "" if absent/unknown."""
    from silica.kernel.write import frontmatter

    data, _, _ = frontmatter.split(text)
    if not isinstance(data, dict):
        return ""
    val = str(data.get("form", "") or "").strip().lower()
    return val if val in FORMS else ""


# Dispatch (draft filing) and PAYLOAD (profile pin) both resolve the same
# file in one /nucleate run; the memo makes the second resolution free.
_sniff_memo: dict[str, str] = {}


def pin_sniff(text: str, form: str) -> None:
    """Make every later sniff of `text` answer `form` (the folder vote): the
    memo is what RECON's resolve() reads, so one write keeps dispatch and the
    FSM in agreement without stamping the source."""
    import hashlib
    _sniff_memo[hashlib.sha256(text[:2000].encode("utf-8", "replace")).hexdigest()[:16]] = form


def sniff_form(text: str) -> str:
    """One small classification call; "" on unsure or any failure.

    Degrading to "" is deliberate: a failed sniff means the vault fallback
    profile, never an error — same posture as active_distill_profile().
    """
    import hashlib

    from silica.config import CONFIG

    key = hashlib.sha256(text[:2000].encode("utf-8", "replace")).hexdigest()[:16]
    if key in _sniff_memo:
        return _sniff_memo[key]
    try:
        # temperature=0 + reasoning=False: this is a one-word classifier, and
        # a hybrid model bills its thinking against max_tokens. Measured on
        # 'Lezione 13.md' (2026-08-21 run): ~1900 chars of trace against a 512
        # budget, and the two files whose sniff thought came back `transcript`
        # while the twelve identical ones came back `study`. A trace that
        # overruns the budget returns "" and the lens silently falls back.
        resp = call_llm(
            CONFIG.model,
            [{"role": "user", "content": _SNIFF_PROMPT + text[:2000]}],
            max_tokens=512,
            temperature=0,
            reasoning=False,
        )
        word = (resp.text or "").strip().split()[0].strip(".,:;\"'`").lower()
    except Exception as exc:
        logger.debug("form sniff failed (non-fatal): %s", exc)
        return ""  # transient failure: not memoized, the next resolve retries
    _sniff_memo[key] = word if word in FORMS else ""
    return _sniff_memo[key]


def resolve(text: str, *, allow_sniff: bool = True) -> Form:
    """The ladder: stamp > sniff > vault fallback > default.

    The explicit --profile override and the run-level arg short-circuit at
    the caller; they name a profile, not a form, and never reach here.
    """
    import silica.kernel.forms as _self  # late-bound so tests can stub sniff_form
    from silica.kernel import prep_delegation

    # Conversion provenance vetoes the draft lane: a file carrying
    # `source_file:` was produced by convert() from the user's own document —
    # by construction not "the owner's working material", however draft-like
    # its OCR reads. The veto outranks the stamp too, because a draft stamp on
    # a converted segment is Silica's own earlier sniff misfire persisted to
    # disk, and honoring it re-files the same segment on every later run.
    # Draft-only: media conversions legitimately carry source_file AND
    # form: transcript.
    from silica.kernel.write import frontmatter

    data, _, _ = frontmatter.split(text)
    converted = bool(isinstance(data, dict) and data.get("source_file"))

    form = stamped_form(text)
    if form and not (converted and form == "draft"):
        return Form(form, profile_for(form), "stamp")
    # An unstamped converted file came from a document: the ingress lane
    # stamps both forms it knows (media -> transcript, /fetch -> clip), so
    # what is left has no form to discover, only one to invent. It invents
    # badly — 4 of 8 OCR'd book segments came back transcript or draft, and
    # one file answered clip, clip, transcript across three identical calls
    # (measured 2026-08-16). The vault fallback is the honest answer, and it
    # costs one LLM call per file less.
    if allow_sniff and not form and not converted:
        sniffed = _self.sniff_form(text)
        if sniffed and not (converted and sniffed == "draft"):
            return Form(sniffed, profile_for(sniffed), "sniff")
    fallback = prep_delegation.active_distill_profile()
    return Form("", fallback, "default" if fallback == "default" else "fallback")
