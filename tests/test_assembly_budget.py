# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""The assembly budget charges the wrapper, not only the payload.

`fill_budget` metered `len(unit.text)`, but `squash()` then renders each unit
inside material the budget never saw: a breadcrumb line, a "# Hub" header, a
blank line between every member, and one extra "#" per ATX heading added by
`relevel_headers`. So the assembled text always exceeded ASSEMBLY_BUDGET_CHARS,
by an amount growing with member count and heading density, silently.

This matters beyond the overrun: that ceiling carries a `ponytail:` note saying
it was never swept. Sweeping a budget that is not actually enforced measures
nothing, so the accounting has to be right before the sweep is worth running.
"""
from evals.recall_assembly import (
    Caps, Neighbors, Unit, assemble, fill_budget, rendered_cost,
)


def _fixture(body_chars: int = 300, headings: int = 6):
    """Two spokes under one hub, so the squash wrapper is exercised. Bodies
    carry headings because relevel_headers grows the text per heading."""
    body = "".join(f"# H{i}\n{'x' * (body_chars // headings)}\n" for i in range(headings))
    paths = ["spoke1", "spoke2", "related1", "e1"]
    bodies = {p: f"# {p}\n{body}" for p in paths}
    bodies["hub"] = "# Hub\n" + body
    nbrs = {
        "spoke1": Neighbors(parent="hub", children=[], related=["related1"], edges=["e1"]),
        "spoke2": Neighbors(parent="hub", children=[], related=[], edges=[]),
        "hub": Neighbors(parent=None, children=["spoke1", "spoke2"], related=[], edges=[]),
    }
    return (lambda p: nbrs.get(p, Neighbors(None, [], [], [])),
            lambda p: bodies.get(p, ""))


def _rendered(res) -> int:
    return sum(len(b.text) for b in res.blocks)


# Two seeds under one hub, each with headings, so the per-member separator and
# the relevel growth both land outside the old meter. The seeds alone render to
# 727 chars and are never trimmed by contract, so only budgets above that floor
# say anything about the periphery admission this fixes.
_SEED_FLOOR = 727
_ALL_DIRECTIONS = Caps(parent=1, children=1, related=1, edges=1)


class TestTheRenderedTextRespectsTheCeiling:
    def test_the_periphery_no_longer_pushes_the_block_past_the_ceiling(self):
        """Measured on this fixture: metering len(text), a budget of 1032
        admitted a neighbour whose rendered block came to 1074 chars."""
        neighbors_of, body_of = _fixture()
        budget = 1032

        res = assemble(["spoke1", "spoke2"], neighbors_of=neighbors_of,
                       body_of=body_of, caps=_ALL_DIRECTIONS, budget=budget)

        assert _rendered(res) <= budget

    def test_it_holds_across_the_whole_band_above_the_seed_floor(self):
        """The overrun appeared only where the budget was nearly saturated, so
        a single budget is no evidence; 31 values in this band overran."""
        neighbors_of, body_of = _fixture()
        for budget in range(_SEED_FLOOR, 4000, 37):
            res = assemble(["spoke1", "spoke2"], neighbors_of=neighbors_of,
                           body_of=body_of, caps=_ALL_DIRECTIONS, budget=budget)
            assert _rendered(res) <= budget, budget

    def test_seeds_are_the_floor_and_may_exceed_the_ceiling(self):
        """Stated rather than hidden: seeds are never trimmed, so a budget
        below their rendered size is not honoured and cannot be."""
        neighbors_of, body_of = _fixture()

        res = assemble(["spoke1", "spoke2"], neighbors_of=neighbors_of,
                       body_of=body_of, caps=_ALL_DIRECTIONS, budget=10)

        assert _rendered(res) == _SEED_FLOOR

    def test_charging_more_never_drops_a_seed(self):
        """Charging more per unit must not start trimming seeds: the budget
        bounds the periphery, and a seed the caller ranked is not optional."""
        neighbors_of, body_of = _fixture()

        res = assemble(["spoke1", "spoke2"], neighbors_of=neighbors_of,
                       body_of=body_of, caps=_ALL_DIRECTIONS, budget=10)

        members = {p for b in res.blocks for p in b.members}
        assert {"spoke1", "spoke2"} <= members


class TestTheCostFunction:
    def test_it_is_never_below_the_raw_body(self):
        """An upper bound by construction — the direction that keeps the
        rendered text inside the ceiling."""
        u = Unit(path="p", text="# A\nbody\n## B\nmore", is_seed=True, rank=0)
        assert rendered_cost(u, hub=None, crumb="") >= len(u.text)
        assert rendered_cost(u, hub="Hub", crumb="Hub > p") > len(u.text)

    def test_headings_cost_more_than_flat_text_of_the_same_length(self):
        """relevel_headers grows the body by one char per ATX heading, which
        the old meter could not see."""
        flat = Unit(path="p", text="x" * 20, is_seed=True, rank=0)
        headed = Unit(path="p", text="# a\n# b\n" + "x" * 12, is_seed=True, rank=0)
        assert len(flat.text) == len(headed.text)
        assert (rendered_cost(headed, hub=None, crumb="")
                > rendered_cost(flat, hub=None, crumb=""))

    def test_fill_budget_still_meters_raw_text_by_default(self):
        """Callers that deliberately budget raw bodies keep their semantics."""
        seeds = [Unit(path="s", text="x" * 100, is_seed=True, rank=0)]
        periphery = [Unit(path="p", text="y" * 100, is_seed=False, rank=0)]

        kept, _ = fill_budget(seeds, periphery, budget=200)

        assert [u.path for u in kept] == ["s", "p"]
