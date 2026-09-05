# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Read-time assembly (IWE-informed): reconstitute atomic notes into assembled
context at recall time. Pure module — no I/O, no LLM. Callers inject edge and
body readers so the logic is unit-testable without a vault.

Three ported ideas (spec 2026-07-21): directional expansion (1.1), squash by
hub (1.3), breadcrumb (2.3). Ranking is NOT touched here: seeds arrive already
ranked/reranked; neighbours enter as periphery and are never re-ranked.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Unit:
    """One assemblable piece of text: a ranked seed or a periphery neighbour."""

    path: str
    text: str
    is_seed: bool
    rank: int


@dataclass
class Truncation:
    """What the budget kept vs dropped (IWE Truncation style)."""

    kept: int = 0
    dropped: list[str] = field(default_factory=list)


def fill_budget(
    seeds: list[Unit],
    periphery: list[Unit],
    *,
    budget: int,
    cost_of: Callable[[Unit, list[Unit]], int] | None = None,
) -> tuple[list[Unit], Truncation]:
    """Seeds first (never trimmed), then periphery by ascending rank until the
    char ceiling. Metering is chars, no tokenizer (spec Dec. 4).

    `cost_of(unit, kept)` is what admitting `unit` ADDS to the assembled text
    given what is already kept — not len(text): squash() wraps a breadcrumb, a
    "# Hub" header and blank-line separators around a block, relevel_headers
    grows the body itself, and the block wrapper is paid once per hub, so the
    marginal cost of a unit depends on whether a co-hub sibling is already in.
    `assemble` passes a kept-aware closure over `rendered_cost`; the len(text)
    default is for callers that meter raw bodies on purpose.
    """
    cost = cost_of or (lambda u, kept: len(u.text))
    kept: list[Unit] = []
    used = 0
    for u in seeds:  # never trimmed; charged in order so co-hub seeds share a wrapper
        used += cost(u, kept)
        kept.append(u)
    dropped: list[str] = []
    for u in sorted(periphery, key=lambda x: x.rank):
        c = cost(u, kept)
        if used + c <= budget:
            kept.append(u)
            used += c
        else:
            dropped.append(u.path)
    return kept, Truncation(kept=len(kept), dropped=dropped)


_ATX = re.compile(r"^(#{1,6})(\s)", re.MULTILINE)


def rendered_cost(unit: Unit, *, hub: str | None, crumb: str) -> int:
    """Upper bound on the chars `unit` contributes to the assembled text.

    Reserve the wrapper before measuring the payload, never after. Metered as
    len(text) the rendered block always exceeded the ceiling, by an amount that
    grew with member count and heading density: squash() adds a breadcrumb
    line, a "# Hub" header, a blank line between every member, and
    relevel_headers adds one "#" per ATX heading in the body itself.

    The crumb and header are BLOCK wrapper — squash() emits them once per hub
    group, not once per member — so callers admitting a unit into a hub whose
    wrapper is already paid must pass hub=None, crumb="" and add only the
    member separator (see `assemble`). Charging the wrapper per member
    over-reserved by (members-1) wrappers per group, quietly evicting
    periphery that fit.

    Still an upper bound rather than the exact figure: a lone member is never
    releveled and pays no header, but is charged both here. Over-reserving
    keeps the rendered text under the ceiling; under-reserving is the defect
    this closes.
    """
    body = relevel_headers(unit.text, 1)  # >= len(text) on either branch
    wrapper = len(crumb) + 2 if crumb else 0
    if hub:
        wrapper += len(hub) + 6  # "# " + hub + blank lines around the header
    return len(body) + wrapper


@dataclass
class AssembledBlock:
    """One rendered block: a squashed hub group, or a lone (breadcrumbed) seed."""

    hub: str | None
    breadcrumb: str
    text: str
    members: list[str] = field(default_factory=list)


def relevel_headers(body: str, shift: int) -> str:
    """Deepen every ATX heading by `shift` levels (capped at H6)."""
    if shift <= 0:
        return body

    def _bump(m: re.Match) -> str:
        level = min(len(m.group(1)) + shift, 6)
        return "#" * level + m.group(2)

    return _ATX.sub(_bump, body)


def squash(
    units: list[Unit],
    hub_of: dict[str, str | None],
    breadcrumb_of: dict[str, str],
) -> list[AssembledBlock]:
    """Group co-hub units into single ordered blocks; lone units stay separate.

    A hub with >= 2 members: one block, "# Hub" header + each member re-leveled
    one level down, in rank order, breadcrumbed. A lone member (or hub=None): its
    own block, breadcrumb-prefixed, text unchanged (the degenerate case — spec
    1.3 "a single seed means no squash").

    Seeds always order before periphery: the two arrive in separate rank
    spaces (seed rank and prank both start at 0), so comparing raw ranks
    across them would let a periphery neighbour lead a hub block — and the
    lead member is what downstream attribution reads. Separate lanes never
    compete on the same number.
    """
    order_of = {u.path: (not u.is_seed, u.rank) for u in units}
    by_hub: dict[str | None, list[Unit]] = {}
    for u in sorted(units, key=lambda x: order_of[x.path]):
        by_hub.setdefault(hub_of.get(u.path), []).append(u)

    blocks: list[AssembledBlock] = []
    for hub, members in by_hub.items():
        if hub is not None and len(members) >= 2:
            crumb = breadcrumb_of.get(members[0].path, hub)
            parts = [f"# {hub}"]
            for m in members:
                parts.append(relevel_headers(m.text, 1))
            blocks.append(
                AssembledBlock(
                    hub=hub,
                    breadcrumb=crumb,
                    text=(f"{crumb}\n\n" if crumb else "") + "\n\n".join(parts),
                    members=[m.path for m in members],
                )
            )
        else:
            for m in members:
                crumb = breadcrumb_of.get(m.path, "")
                blocks.append(
                    AssembledBlock(
                        hub=None,
                        breadcrumb=crumb,
                        text=(f"{crumb}\n\n" if crumb else "") + m.text,
                        members=[m.path],
                    )
                )
    # Stable order: by the best member — seeds first, then lowest rank — so
    # ranking intent survives and a periphery-only block never outranks a seed.
    blocks.sort(key=lambda b: min(order_of[p] for p in b.members))
    return blocks


# ponytail: never swept — the Arm-1 A/B (conv-26, 2026-07-21) ran at this one
# value and --assemble stayed default-off, so the ceiling is a guess that only
# matters under the flag. Sweep it inside the Arm-1 widening run (more
# conversations + LME) if --assemble is ever considered for promotion.
ASSEMBLY_BUDGET_CHARS = 12000


@dataclass
class Neighbors:
    parent: str | None
    children: list[str]
    related: list[str]
    edges: list[str]


@dataclass
class Caps:
    parent: int = 1
    children: int = 1
    related: int = 1
    edges: int = 1


@dataclass
class AssemblyResult:
    blocks: list[AssembledBlock]
    truncation: Truncation


_MAX_CHAIN = 6  # breadcrumb walk bound (cycle guard)


def _breadcrumb(path: str, neighbors_of: Callable[[str], "Neighbors"]) -> str:
    """spoke -> parent -> hub path, walked up the parent chain (bounded)."""
    chain = [path]
    seen = {path}
    cur = path
    for _ in range(_MAX_CHAIN):
        parent = neighbors_of(cur).parent
        if not parent or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        cur = parent
    return " > ".join(reversed(chain))  # hub > ... > spoke


def assemble(
    seed_paths: list[str],
    *,
    neighbors_of: Callable[[str], Neighbors],
    body_of: Callable[[str], str],
    caps: Caps = Caps(),
    budget: int = ASSEMBLY_BUDGET_CHARS,
) -> AssemblyResult:
    """Directional 1-hop expansion + budget + squash. See module docstring."""
    if not seed_paths:
        return AssemblyResult(blocks=[], truncation=Truncation())

    # The injected reader is expensive (props + links + backlinks + cooccur, none
    # memoized) and each path is queried up to ~8x (seed expansion, hub_of, and
    # _MAX_CHAIN breadcrumb walks). Cache per path for this call. Neighbors are read
    # only (callers slice copies), so sharing one object across reads is safe.
    neighbors_of = functools.cache(neighbors_of)

    seed_set = set(seed_paths)
    seeds = [
        Unit(path=p, text=body_of(p), is_seed=True, rank=i)
        for i, p in enumerate(seed_paths)
    ]

    periphery: list[Unit] = []
    seen: set[str] = set(seed_set)
    prank = 0
    for p in seed_paths:
        n = neighbors_of(p)
        directions = (
            ([n.parent] if n.parent else [])[: caps.parent],
            n.children[: caps.children],
            n.related[: caps.related],
            n.edges[: caps.edges],
        )
        for group in directions:
            for np in group:
                if np in seen:
                    continue
                seen.add(np)
                periphery.append(
                    Unit(path=np, text=body_of(np), is_seed=False, rank=prank)
                )
                prank += 1

    # Hubs and breadcrumbs first: the budget cannot charge for what squash()
    # wraps around a unit until it knows the wrapper. neighbors_of is cached
    # for this call, so covering every candidate costs no extra reads.
    candidates = seeds + periphery
    hub_of = {u.path: neighbors_of(u.path).parent for u in candidates}
    crumb_of = {u.path: _breadcrumb(u.path, neighbors_of) for u in candidates}

    def _marginal_cost(u: Unit, kept: list[Unit]) -> int:
        # The block wrapper (breadcrumb + "# Hub" header) is per HUB GROUP:
        # the first admitted member of a hub pays it, siblings admitted after
        # add their releveled body plus the blank-line separator only.
        hub = hub_of.get(u.path)
        if hub is not None and any(hub_of.get(k.path) == hub for k in kept):
            return len(relevel_headers(u.text, 1)) + 2
        return rendered_cost(u, hub=hub, crumb=crumb_of.get(u.path, ""))

    kept, trunc = fill_budget(seeds, periphery, budget=budget, cost_of=_marginal_cost)
    blocks = squash(kept, hub_of, crumb_of)
    return AssemblyResult(blocks=blocks, truncation=trunc)
