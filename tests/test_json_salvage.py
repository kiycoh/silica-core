"""Truncated-array salvage: a max_tokens cut loses the tail, not the batch.

parse_json used to raise on any truncated payload, so a bulk of N ops with the
last one cut mid-string lost all N. Now it raises TruncatedArray carrying the
complete leading objects; only callers that catch it by name ever see partial
data — everyone else still gets an error, just a more informative one.
"""
from __future__ import annotations

import json

import pytest

from silica.kernel.text.sanitize import TruncatedArray, parse_json


OPS = [
    {"op": "write", "path": "A.md", "content": "alpha"},
    {"op": "patch", "path": "B.md", "snippet": "beta"},
    {"op": "write", "path": "C.md", "content": "gamma"},
]


def _truncated(n_chars: int) -> str:
    return json.dumps(OPS)[:-n_chars]


def test_truncated_array_recovers_leading_ops():
    with pytest.raises(TruncatedArray) as exc:
        parse_json(_truncated(20))  # cuts into the third object
    assert exc.value.ops == OPS[:2]
    assert len(exc.value.tail) > 0


def test_cut_inside_a_string_containing_braces():
    ops = [{"op": "write", "content": "code: } ] {"}, {"op": "patch", "content": "x"}]
    raw = json.dumps(ops)[:-5]
    with pytest.raises(TruncatedArray) as exc:
        parse_json(raw)
    assert exc.value.ops == ops[:1]


def test_valid_json_is_untouched():
    parsed, clean = parse_json(json.dumps(OPS))
    assert parsed == OPS and clean is True


def test_unsalvageable_garbage_raises_the_original_error():
    with pytest.raises(json.JSONDecodeError):
        parse_json('[{"op": broken')  # first object itself unparseable


def test_no_array_at_all_raises_the_original_error():
    with pytest.raises(Exception) as exc:
        parse_json("just prose, no json here")
    assert not isinstance(exc.value, TruncatedArray)


def test_truncation_with_open_fence_and_preamble():
    raw = "Here are the ops:\n```json\n" + _truncated(20)
    with pytest.raises(TruncatedArray) as exc:
        parse_json(raw)
    assert exc.value.ops == OPS[:2]


def test_surviving_appendix_bodies_are_injected_into_salvaged_ops():
    raw = (
        '[{"op": "write", "path": "A.md", "content_ref": 1},\n'
        ' {"op": "write", "path": "B.md", "content'  # cut mid-key
        "\n===SILICA-BODY 1===\nthe verbatim body\n"
    )
    # the appendix is split off before parsing, so it survives the cut
    with pytest.raises(TruncatedArray) as exc:
        parse_json(raw)
    assert len(exc.value.ops) == 1
    assert exc.value.ops[0]["content"] == "the verbatim body"


