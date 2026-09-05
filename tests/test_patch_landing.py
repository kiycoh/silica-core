# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Where a patch lands (2026-09-04).

A patch used to append one `## Additional notes: <heading> (from <source>)`
block per (heading, source): 136 blocks in one lecture folder, 112 of them a
single typed-relation bullet. Now a relation bullet joins `## Relations` and
prose joins the body, cited once with the source's footnote label. The
header was also the idempotency key; these pin the keys that replace it.
"""
from __future__ import annotations

from silica.kernel.write.bulk import execute_one
from silica.kernel.write.ops import Op, OpType

NOTE = (
    "---\nAI: true\n---\n\n# Gradiente\n\n> claim\n\nprose line one.\n\n"
    "## Relations\n- applies [[Backprop]] (Lezione 3 -> earlier): why\n\n"
    "## Sources\n[[sources/Lezione 3]]\n"
)


def _patch(**kw) -> Op:
    base = dict(op=OpType.patch, heading="Gradiente", source_basename="Lezione 6.md",
                path="ML/Gradiente.md", hub="Hub")
    base.update(kw)
    return Op(**base)


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


# --- typed relations ---------------------------------------------------------

def test_relation_patch_lands_under_relations_without_a_provenance_header(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)
    bullet = "- [[Apprendimento sequenziale]] applies this (Lezione 6): incremental updates\n"

    execute_one(_patch(relation=True, snippet=bullet))

    c = tmp_vault.read(p)
    assert "## Additional notes" not in c
    assert c.count("## Relations") == 1
    rel = _between(c, "## Relations", "## Sources")
    assert "- applies [[Backprop]]" in rel
    assert bullet.strip() in rel


def test_relation_patch_creates_the_section_above_sources_when_absent(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md",
                       "# Gradiente\n\nprose.\n\n## Sources\n[[sources/Lezione 3]]\n")

    execute_one(_patch(relation=True, snippet="- [[X]] applies this (Lezione 6): why\n"))

    c = tmp_vault.read(p)
    assert c.index("prose.") < c.index("## Relations") < c.index("- [[X]] applies this") < c.index("## Sources")


def test_relation_patch_is_idempotent_on_the_pair_not_the_wording(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)
    execute_one(_patch(relation=True, snippet="- [[X]] applies this (Lezione 6): first wording\n"))
    once = tmp_vault.read(p)

    res = execute_one(_patch(relation=True, snippet="- [[X]] generalizes this (Lezione 6): reworded\n"))

    assert res.get("skipped") == "duplicate"
    assert tmp_vault.read(p) == once
    assert once.count("[[X]]") == 1


# --- prose -------------------------------------------------------------------

def test_prose_patch_lands_in_the_body_and_cites_the_source_once(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="Nuova prosa dalla lezione sei.\nSeconda riga."))

    c = tmp_vault.read(p)
    assert "## Additional notes" not in c
    assert "Nuova prosa dalla lezione sei.\nSeconda riga.[^Lezione-6]" in c
    assert c.index("prose line one.") < c.index("Nuova prosa") < c.index("## Relations")


def test_prose_patch_lead_joins_the_top_prose_and_facets_become_h2_sections(tmp_vault):
    p = tmp_vault.note(
        "ML/Percettrone.md",
        "# Percettrone\n\nintro.\n\n## XOR\nxor text.\n\n## Sources\n[[sources/a]]\n",
    )

    execute_one(_patch(path="ML/Percettrone.md", heading="Percettrone",
                       snippet="Lead paragraph.\n\n### Storia\n- 1958\n- connessionista"))

    c = tmp_vault.read(p)
    assert "### Storia" not in c and "## Additional notes" not in c
    assert c.index("intro.") < c.index("Lead paragraph.[^Lezione-6]") < c.index("## XOR")
    assert c.index("xor text.") < c.index("## Storia") < c.index("- connessionista[^Lezione-6]") < c.index("## Sources")


def test_prose_patch_is_idempotent_on_the_snippet_without_a_ledger(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)
    op = _patch(snippet="Nuova prosa dalla lezione sei.")
    execute_one(op)
    once = tmp_vault.read(p)

    res = execute_one(op)

    assert res.get("skipped") == "duplicate"
    assert tmp_vault.read(p) == once


def test_prose_patch_keeps_the_valid_from_stamp_right_above_the_prose(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="Nuova prosa.", valid_from="2023-05-08"))

    c = tmp_vault.read(p)
    assert "<!-- silica: valid_from=2023-05-08 -->\nNuova prosa.[^Lezione-6]" in c


def test_prose_patch_with_an_unmarkable_last_line_cites_on_its_own_line(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="Formula:\n$$\nx = 1\n$$"))

    c = tmp_vault.read(p)
    assert "$$\nx = 1\n$$\n\n[^Lezione-6]\n" in c   # blank line: after a table row the marker would be a row
    assert "$$[^Lezione-6]" not in c


# --- the marker is never left dangling ---------------------------------------

def test_prose_patch_defines_its_marker_at_once_when_the_note_has_no_sources_block(tmp_vault):
    """Live 2026-09-05: a partial run leaves the source in the inbox, CLEANUP
    skips the leaf, and three notes carried `[^Lezione-6]` with no definition
    until a retry. The writer of the marker writes its definition."""
    p = tmp_vault.note("ML/Gradiente.md", "# Gradiente\n\nprose.\n")

    execute_one(_patch(snippet="Nuova prosa."))

    c = tmp_vault.read(p)
    assert "Nuova prosa.[^Lezione-6]" in c
    assert c.count("[^Lezione-6]: Lezione 6.md") == 1
    assert "## Sources" not in c


def test_prose_patch_plain_definition_joins_an_existing_sources_block(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="Nuova prosa."))

    c = tmp_vault.read(p)
    assert "[^Lezione-6]: Lezione 6.md" in _between(c, "## Sources", "\n\n") + c.split("## Sources", 1)[1]
    assert c.index("## Sources") < c.index("[^Lezione-6]: Lezione 6.md")


def test_prose_patch_cites_the_last_prose_line_not_a_trailing_embed(tmp_vault):
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="Testo della lezione.\n\n![[a.jpg]]\n![[b.jpg]]"))

    c = tmp_vault.read(p)
    assert "Testo della lezione.[^Lezione-6]" in c
    assert "]][^Lezione-6]" not in c


def test_prose_patch_drops_a_leading_heading_that_only_repeats_the_concept(tmp_vault):
    """The keyphrase distiller opens a snippet with `## <concept>`; landed in
    the note of that very concept it is a section named after the note
    (live 2026-09-05: `## Regressione lineare` inside Regressione lineare.md).
    The text under it is more prose about the note, so it lands as lead."""
    p = tmp_vault.note("ML/Gradiente.md", NOTE)

    execute_one(_patch(snippet="## Gradiente\n\nProsa sul concetto.\n\n## Un facet\nfacet text"))

    c = tmp_vault.read(p)
    assert c.count("## Gradiente") == 0 and "# Gradiente" in c
    assert c.index("prose line one.") < c.index("Prosa sul concetto.[^Lezione-6]") < c.index("## Relations")
    assert c.index("## Un facet") < c.index("## Relations")


def test_prose_patch_facets_land_above_the_moc_index_of_a_hub(tmp_vault):
    """A hub note carries `## From: <source>` index blocks and no Relations
    or Sources; a facet appended at EOF filed itself under the index (live
    2026-09-05, `## Machine Learning (9 CFU)` after `## From: Lezione 14`)."""
    p = tmp_vault.note("ML/Hub.md", "# Hub\n\nintro.\n\n## From: Lezione 5\n\n- [[A]]\n\n## From: Lezione 6\n\n- [[B]]\n")

    execute_one(_patch(path="ML/Hub.md", heading="Hub", snippet="### Facet\nfacet text"))

    c = tmp_vault.read(p)
    assert c.index("intro.") < c.index("## Facet") < c.index("## From: Lezione 5")
