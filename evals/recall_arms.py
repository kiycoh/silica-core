"""Benchmark-only recall arms removed from the production perception path.

The arms are ranked legs and post-processing, never a second pipeline: every
one of them calls `perception.perceive` and hands it something, so a LoCoMo
number keeps measuring the function the product runs. An earlier draft of this
module reimplemented the retrieval facade here and silently dropped the memory
lane, which is exactly the drift this shape exists to prevent.
"""

from __future__ import annotations

from silica.kernel.recall import perception as product


def _lexical_ranking(query: str, k: int) -> list[tuple[str, float]] | None:
    """The hand-written BM25/fuzzy leg as an RRF ranking. None (abstain) when
    the index is absent or empty, which is what `related_notes_for_query`
    expects for a leg that has nothing to say."""
    from silica.kernel.recall.lexical import get_lexical_store

    store = get_lexical_store()
    return (store.rank(query, k=k) if store is not None else None) or None


def _name_of(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _assembly_body(path: str) -> str:
    return product._read_dated_body(path)[2] or ""


def _driver_neighbors(path: str):
    from evals import recall_assembly as assembly
    from silica.config import CONFIG
    from silica.driver import DRIVER
    from silica.kernel.recall.cooccurrence import cooccur_key, get_cooccur_store

    try:
        raw = (DRIVER.props_of(path) or {}).get("parent note") or ""
        parent = str(raw).strip().strip("[]").strip() or None
    except Exception:
        parent = None
    try:
        related = [r.path.removesuffix(".md") for r in DRIVER.links(path)]
    except Exception:
        related = []
    children: list[str] = []
    try:
        for backlink in DRIVER.backlinks(path):
            parent_ref = (DRIVER.props_of(backlink.path) or {}).get("parent note") or ""
            if (
                str(parent_ref).strip().strip("[]").strip().lower()
                == _name_of(path).lower()
            ):
                children.append(backlink.path.removesuffix(".md"))
    except Exception:
        pass
    try:
        store = get_cooccur_store(lang=CONFIG.cooccurrence_lang)
        row = store.note_edges_for(cooccur_key(path))
        edges = [
            p for p, _ in sorted(row.items(), key=lambda item: (-item[1], item[0]))
        ]
    except Exception:
        edges = []
    return assembly.Neighbors(
        parent=parent, children=children, related=related, edges=edges
    )


def _assemble_blocks(
    blocks: list[product.NoteBlock], query: str
) -> list[product.NoteBlock]:
    from evals import recall_assembly as assembly

    by_path = {block.path: block for block in blocks}

    def body(path: str) -> str:
        seed = by_path.get(path)
        return seed.body if seed is not None else _assembly_body(path)

    result = assembly.assemble(
        [block.path for block in blocks],
        neighbors_of=_driver_neighbors,
        body_of=body,
    )
    out: list[product.NoteBlock] = []
    for assembled in result.blocks:
        head = by_path.get(assembled.members[0])
        reasons = []
        for member in assembled.members:
            seed = by_path.get(member)
            reason = seed.contested if seed else product._read_dated_body(member)[1]
            if reason and reason not in reasons:
                reasons.append(reason)
        out.append(
            product.NoteBlock(
                path=assembled.members[0],
                date=head.date if head else "",
                evidence=head.evidence if head else "",
                body=assembled.text,
                excerpt=assembled.text,
                contested="; ".join(reasons) or None,
            )
        )
    return out


def _maybe_assemble(
    blocks: list[product.NoteBlock], *, assemble: bool, query: str
) -> list[product.NoteBlock]:
    return _assemble_blocks(blocks, query) if assemble and blocks else blocks


def _study_order(blocks: list[product.NoteBlock]) -> list[product.NoteBlock]:
    from silica.kernel.report.learner import prerequisites_map

    try:
        prerequisites = prerequisites_map() or {}
    except Exception:
        return blocks
    paths = {block.path for block in blocks}

    def topo(group: list[product.NoteBlock]) -> list[product.NoteBlock]:
        needs = {
            block.path: [
                path for path in prerequisites.get(block.path, []) if path in paths
            ]
            for block in group
        }
        out: list[product.NoteBlock] = []
        placed: set[str] = set()
        while len(out) < len(group):
            ready = [
                block
                for block in group
                if block.path not in placed
                and all(path in placed for path in needs[block.path])
            ]
            if not ready:
                out.extend(block for block in group if block.path not in placed)
                break
            out.extend(ready)
            placed.update(block.path for block in ready)
        for block in out:
            if needs[block.path]:
                block.builds_on = ", ".join(
                    _name_of(path) for path in needs[block.path]
                )
        return out

    return topo([b for b in blocks if not b.contested]) + topo(
        [b for b in blocks if b.contested]
    )


def perceive(
    query: str,
    *,
    improve: bool = False,
    assemble: bool = False,
    lexical: bool = False,
    study: bool = False,
    orient: bool = False,
    **kwargs,
) -> product.Perception:
    """Run a production perception, adding only explicitly requested eval arms."""
    kwargs = dict(kwargs)
    if kwargs.get("paths") is None:
        if improve:
            from evals.recall_weights import ranking

            kwargs["recall_rank"] = ranking()
        if lexical:
            kwargs["lexical_rank"] = _lexical_ranking(
                query, kwargs.get("k", product.DEFAULT_K))
    perception = product.perceive(query, **kwargs)
    perception.blocks = _maybe_assemble(
        perception.blocks, assemble=assemble and kwargs.get("paths") is None,
        query=query)
    if study:
        perception.blocks = _study_order(perception.blocks)
    if orient:
        try:
            from silica.kernel.recall.vault_map import build_vault_map

            perception.orientation = build_vault_map() or ""
        except Exception:
            pass
    return perception
