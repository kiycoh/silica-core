"""frontmatter.split contract: what counts as a usable property block.

The bug that motivated these: a note whose frontmatter is a YAML *sequence*
parses fine, so split() handed the list back as `data`, and the first
`data.get(...)` downstream raised AttributeError. mark_contested, clear_contested,
contested_refs, reliability_tier, mark_superseded_by, resolve_contested,
notetype.derive_type, codedocs and graph_report all took that path.
"""
from __future__ import annotations

import pytest

from silica.kernel.write import frontmatter as fm


def test_mapping_frontmatter_parses_to_a_dict():
    data, raw, body = fm.split("---\ntags: [x]\n---\n\nbody\n")
    assert data == {"tags": ["x"]} and raw == "tags: [x]" and body.strip() == "body"


def test_empty_frontmatter_is_an_empty_mapping_not_a_failure():
    data, raw, _ = fm.split("---\n\n---\n\nbody\n")
    assert data == {} and raw is not None


def test_no_frontmatter_gives_none_data_and_none_raw():
    # the pair (None, None) is how callers tell "no block" from "unusable block"
    assert fm.split("just a body\n") == (None, None, "just a body\n")


@pytest.mark.parametrize("block", ["- a\n- b", "just a bare scalar", "false"])
def test_non_mapping_frontmatter_is_unusable_but_preserved(block):
    data, raw, body = fm.split(f"---\n{block}\n---\n\nbody\n")
    assert data is None            # not a property block
    assert raw == block            # ...but never discarded
    assert body.strip() == "body"


def test_broken_yaml_is_unusable_but_preserved():
    data, raw, _ = fm.split("---\naliases: [\n---\n\nbody\n")
    assert data is None and raw is not None


# ---------------------------------------------------------------------------
# The downstream writers inherit the guard they already had
# ---------------------------------------------------------------------------

SEQ_NOTE = "---\n- a\n- b\n---\n\nbody\n"






def test_add_alias_leaves_a_non_mapping_note_untouched():
    assert fm.add_alias(SEQ_NOTE, "AI") == SEQ_NOTE
    assert fm.aliases_of(SEQ_NOTE) == []


