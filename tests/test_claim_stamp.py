"""Tests for the claim stamp (spec-contested-bitemporal §3, Fase A).

Three things must hold: the stamp round-trips and cannot break out of its own
HTML comment; the event clock resolves with the documented precedence and is
shared with the source-leaf writer; and a write with no `valid_from` produces
byte-identical output to the pre-stamp behaviour, so every non-FSM path
(dedup, enrich, refine, interactive tools) and the goldens are untouched.
"""
from __future__ import annotations

from silica.kernel.write import templates
from silica.kernel.write.contested import parse_stamp, stamp
from silica.kernel.write.ops import Op, OpType
from silica.kernel.write.provenance import source_event_date


# --- stamp / parse_stamp (pure) ----------------------------------------------

def test_stamp_round_trip():
    s = stamp(valid_from="2023-05-08", run="b07f126889f04ee5")
    assert s == "<!-- silica: valid_from=2023-05-08 run=b07f126889f04ee5 -->"
    assert parse_stamp(s) == {"valid_from": "2023-05-08", "run": "b07f126889f04ee5"}


def test_stamp_drops_empty_fields_and_is_empty_when_all_empty():
    assert stamp(valid_from="2023-05-08", run="") == "<!-- silica: valid_from=2023-05-08 -->"
    assert stamp(valid_from="", run=None) == ""


def test_stamp_value_cannot_close_the_comment():
    """A hostile value must not end the comment early or inject markdown."""
    s = stamp(valid_from="2023 --> <script>alert(1)</script>")
    assert s.count("-->") == 1
    assert "<script>" not in s
    assert parse_stamp(s)["valid_from"]


def test_parse_stamp_absent():
    assert parse_stamp("# Titolo\n\nSolo prosa.") == {}


# --- source_event_date (pure) ------------------------------------------------

SOURCE = "---\ndate: 2023-05-08\nsource_id: session_1.md\n---\n\nCaroline: ciao!\n"


def test_event_date_prefers_capture_clock():
    assert source_event_date(SOURCE, "2026-07-25") == "2026-07-25"


def test_event_date_falls_back_to_source_date():
    assert source_event_date(SOURCE, None) == "2023-05-08"


def test_event_date_none_without_signal():
    """No today fallback: an undated source must yield NO stamp, not an
    ingest-dated one — the run date is noise on the event axis and feeds
    note_clock a fake freshness that defeats the recency veto."""
    assert source_event_date("# Nessun frontmatter\n", None) is None
    assert source_event_date("", None) is None


def test_event_date_survives_broken_yaml():
    """Unparseable frontmatter must degrade to None, never raise into the FSM."""
    broken = "---\ndate: [unclosed\n---\n\nbody\n"
    assert source_event_date(broken, None) is None


# --- patch_snippet parity ----------------------------------------------------

def test_patch_snippet_byte_identical_without_valid_from():
    """The parity guarantee: no valid_from, no diff."""
    expected = "\nIl dosaggio raccomandato è 5mg/die.[^appunti]\n"
    assert templates.patch_snippet(
        heading="Warfarin", snippet="Il dosaggio raccomandato è 5mg/die.",
        source_basename="appunti.md",
    ) == expected


def test_patch_snippet_stamps_right_above_the_prose():
    out = templates.patch_snippet(
        heading="Warfarin", snippet="Il dosaggio raccomandato è 5mg/die.",
        source_basename="appunti.md", valid_from="2023-05-08",
    )
    assert "<!-- silica: valid_from=2023-05-08 -->\nIl dosaggio raccomandato" in out
    assert parse_stamp(out) == {"valid_from": "2023-05-08"}


# --- write / patch through the executor --------------------------------------

NOTE = """---
parent note: "[[Farmacologia]]"
related:
  - "[[Farmacologia]]"
tags:
  - farmacologia
last modified: 2026-07-02
AI: true
---

# Dosaggio Warfarin

Il dosaggio raccomandato è 5mg/die.
"""


def test_execute_write_stamps_under_the_h1(tmp_vault):
    from silica.driver import DRIVER
    from silica.kernel.write.bulk import execute_one

    execute_one(Op(
        op=OpType.write, heading="Dosaggio Warfarin", source_basename="appunti.md",
        path="Farmacologia/Dosaggio Warfarin.md", hub="Farmacologia",
        snippet="Il dosaggio raccomandato è 5mg/die.", valid_from="2023-05-08",
    ))

    content = DRIVER.read_note("Farmacologia/Dosaggio Warfarin.md").content
    assert parse_stamp(content) == {"valid_from": "2023-05-08"}
    assert content.index("# Dosaggio Warfarin") < content.index("<!-- silica:")
    assert content.index("<!-- silica:") < content.index("Il dosaggio")


def test_execute_write_without_valid_from_emits_no_stamp(tmp_vault):
    from silica.driver import DRIVER
    from silica.kernel.write.bulk import execute_one

    execute_one(Op(
        op=OpType.write, heading="Dosaggio Warfarin", source_basename="appunti.md",
        path="Farmacologia/Dosaggio Warfarin.md", hub="Farmacologia",
        snippet="Il dosaggio raccomandato è 5mg/die.",
    ))
    content = DRIVER.read_note("Farmacologia/Dosaggio Warfarin.md").content
    assert "<!-- silica:" not in content


def test_execute_patch_stamps_the_block(tmp_vault):
    from silica.kernel.write.bulk import execute_one

    path = tmp_vault.note("Farmacologia/Dosaggio Warfarin.md", NOTE)
    execute_one(Op(
        op=OpType.patch, heading="Monitoraggio INR", source_basename="appunti.md",
        path="Farmacologia/Dosaggio Warfarin.md", hub="Farmacologia",
        snippet="INR target 2.0-3.0.", valid_from="2023-05-08",
    ))

    content = tmp_vault.read(path)
    assert parse_stamp(content) == {"valid_from": "2023-05-08"}
    assert "## Additional notes" not in content
    assert content.index("<!-- silica:") < content.index("INR target 2.0-3.0.[^appunti]")
    assert "5mg/die" in content  # the pre-existing claim is untouched


def test_stamp_does_not_leak_into_the_moc_bullet(tmp_vault):
    """hub_desc reads op.snippet, which the stamp must never mutate: a MOC
    bullet reading '<!-- silica: ... -->' would be a visible regression."""
    from silica.kernel.write.bulk import execute_one
    from silica.kernel.write.moc import hub_desc

    op = Op(
        op=OpType.write, heading="Dosaggio Warfarin", source_basename="appunti.md",
        path="Farmacologia/Dosaggio Warfarin.md", hub="Farmacologia",
        snippet="Il dosaggio raccomandato è 5mg/die.", valid_from="2023-05-08",
    )
    execute_one(op)
    assert hub_desc(op.snippet) == "Il dosaggio raccomandato è 5mg/die."


# --- /nucleate --seen: the capture clock's CLI entry point --------------------

def _dispatch_nucleate(monkeypatch, tmp_vault, line):
    """Drive the /nucleate shortcut to the Coordinator seam, capturing kwargs."""
    import silica.router.coordinator as rc

    calls: dict = {}

    class _FakeCoordinator:
        def __init__(self, **kw):
            calls.update(kw)

        def run(self):
            return {"final_status": "done"}

    monkeypatch.setattr(rc, "Coordinator", _FakeCoordinator)
    tmp_vault.note("Inbox/x.md", "parole\n")
    from silica.cli import _expand_workflow_shortcut

    _expand_workflow_shortcut(line)
    return calls


def test_seen_flag_reaches_the_coordinator(tmp_vault, monkeypatch):
    calls = _dispatch_nucleate(
        monkeypatch, tmp_vault, "/nucleate Inbox/x.md --target=Concepts --seen=2023-05-08"
    )
    assert calls.get("seen_override") == "2023-05-08"


def test_garbage_seen_is_refused_not_stamped(tmp_vault, monkeypatch):
    """A typo'd date would ride every claim of the run as valid_from —
    refuse it at the boundary, run without a capture clock."""
    calls = _dispatch_nucleate(
        monkeypatch, tmp_vault, "/nucleate Inbox/x.md --target=Concepts --seen=2023-13-99"
    )
    assert calls.get("seen_override") is None
    assert calls  # the run itself still dispatched


def test_a_citation_after_a_table_is_its_own_paragraph():
    """A marker line straight after a table row is parsed as another row
    (GFM, verified against GitHub's renderer 2026-09-05): the blank line is
    what ends the table."""
    from silica.kernel.write.templates import _cite

    block = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert _cite(block, "src") == block + "\n\n[^src]"


def test_a_citation_after_a_fence_needs_no_blank():
    from silica.kernel.write.templates import _cite

    assert _cite("```py\nx = 1\n```", "src") == "```py\nx = 1\n```\n\n[^src]"
