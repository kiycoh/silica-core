# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Runner tools — whole pipelines and leashed sub-agent batches.

Full FSM runs (injector, organizer), taxonomy generation, run-ledger
inspection, and the dedup/refine/enrich sub-agent passes.
"""
from __future__ import annotations

from typing import Annotated

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silica.kernel.workqueue import WorkItem

from typing import Any

from pydantic import Field

from silica.driver import DRIVER
from silica.tools import tool
from silica.tools.graph import _in_folder


@tool(cls="composed", collapse="eager")
def silica_run_injector(
    inbox_file: Annotated[str, Field(description='Path to a single inbox file (legacy; use inbox_files for multiple files)')] = "",
    inbox_files: Annotated[list[str] | None, Field(description='Paths to one or more inbox files to nucleate in a single run')] = None,
    # Required, and keyword-only so the rest of the signature keeps its order:
    # the guard below rejects an empty target anyway, and a schema that let the
    # model omit it turned that rejection into a wasted turn instead of a
    # missing argument the caller can see before dispatch.
    *,
    target_dir: Annotated[str, Field(description='Destination directory for the extracted concepts')],
    hub: Annotated[str, Field(description='Optional reference hub note')] = "",
    resume_run_id: Annotated[str, Field(description='Run ID to resume (re-processes only failed chunks, skips done ones)')] = "",
    keep_sources: Annotated[bool, Field(description='Write the verbatim source leaf in sources/ beside the notes (default on; pass false to skip)')] = True,
    cancel_token: Any = None,
) -> dict[str, Any]:
    """Nucleate inbox files into the vault — THE tool for "nucleate/inject
    this file": full pipeline, quality gates, rollback. A failed chunk rolls
    back and is marked 'failed' while the rest continue; resume_run_id re-runs
    only the failed chunks. For a single quick note,
    silica_write_note/silica_patch_note are cheaper.
    """
    from silica.router.coordinator import Coordinator

    files: list[str] = list(inbox_files or [])
    if inbox_file and inbox_file not in files:
        files.insert(0, inbox_file)
    if not files:
        return {"error": "No inbox file(s) specified"}

    # A target folder is not optional: the distiller prompt interpolates it into
    # {TARGET} and derives {HUB_NAME} from it, so an empty one renders the two
    # placeholders literally. The model then obeys "hub MUST be exactly
    # {HUB_NAME}" to the letter and every op comes back with hub="{HUB_NAME}"
    # and path="/<title>.md" — all rejected by lint, after a full LLM run, and
    # the hub auto-creator drops a junk `{HUB_NAME}.md` in the vault root. The
    # CLI always resolves a target before dispatching (cli.py:1226); this guard
    # covers the agent-tool path, whose contract already says to pass one.
    # Asked before conversion: a PDF is minutes of work to transcode and it
    # would all be thrown away here.
    if not target_dir.strip():
        return {"error": "No target_dir specified. Pick the vault folder for these notes and pass it as target_dir."}

    # The FSM re-reads inbox files as prose, so a file no adapter claims (PDF,
    # EPUB, other binaries) has to be transcoded first. `/nucleate` has always
    # done that inline; the agent tool used to answer "ask the user to run
    # /convert", which in a REPL means telling the user to type, themselves, the
    # thing they just asked for — and in a library of 74 scanned books that
    # instruction stands between the user and the entire corpus.
    from silica.kernel.vault_manifest import get_active_manifest
    from silica.sources.registry import adapter_for

    enabled = get_active_manifest().sources
    if any(adapter_for(f, enabled=enabled) is None for f in files):
        from silica.sources.convert import convert

        resolved: list[str] = []
        rejected: list[str] = []
        for f in files:
            if adapter_for(f, enabled=enabled) is not None:
                resolved.append(f)
                continue
            try:
                resolved.extend(convert(f, dest_dir=target_dir))
            except (ValueError, RuntimeError) as exc:
                rejected.append(f"{f} ({exc})")
        if rejected:
            return {"error": f"Not ingestible as-is: {'; '.join(rejected)}."}
        files = list(dict.fromkeys(resolved))
        if not files:
            return {"error": "Conversion produced no markdown to nucleate."}

    # Apparatus is not content — the same filter /nucleate applies (cli.py's
    # _prepare_unit). This is THE tool for "nucleate this file", and the path
    # the web drag-drop and every MCP client take, so a filter that lived only
    # in the CLI was no filter at all: a references list or a venue checklist
    # arriving here was distilled into venue/journal/ethics notes. The raw chunk
    # stays in the inbox for lookup, exactly as on the CLI side.
    from silica.sources.convert import is_skippable_chunk

    kept = [f for f in files if not is_skippable_chunk(f)]
    skipped = len(files) - len(kept)
    if not kept:
        return {"error": "Only apparatus sections (references, contents, venue "
                         "checklists) were given — nothing to nucleate. They stay "
                         "in the inbox for lookup."}
    files = kept

    coordinator = Coordinator(
        inbox_files=files,
        target_dir=target_dir,
        hub=hub or None,
        resume_run_id=resume_run_id or None,
        # On by default, matching /nucleate. The leaf lives in `sources/`, which
        # is retrieval-invisible by construction, so it costs disk and nothing
        # else — and it is what makes a note's verbatim source reachable at all
        # (reliability_tier reads exactly that). It was set only on the CLI side
        # while Coordinator defaults it False, so the same file nucleated through
        # this tool — the web drag-drop and every MCP client — silently produced
        # notes whose source could never be checked.
        keep_sources=keep_sources,
        cancel_token=cancel_token,
    )
    result = coordinator.run()

    # Agent-facing projection: outcomes only, never the raw FSM context. The
    # raw context once fed a confabulated success report — planned concepts in
    # payload.chunks read as "created notes", and a last-write-wins error field
    # hid 5 of 6 batch failures.
    failed = result.get("failed_chunks", [])
    projected: dict[str, Any] = {
        "final_status": result.get("final_status", "unknown"),
        "run_id": getattr(coordinator.fsm.progress, "run_id", None),
        "chunks_committed": result.get("committed_chunks", 0),
        "chunks_failed": len(failed),
        "failed_chunks": failed,
        "files_summary": result.get("files_summary", []),
        "subagents": result.get("subagents", {}),
        # What the run actually yielded. Both frontends build their completion
        # line from these; until they were projected here the keys existed only
        # inside fsm.context and every summary read "1 file · 3m12s", never the
        # note and link counts it was written to show.
        "yield_notes": result.get("yield_notes", 0),
        "yield_links": result.get("yield_links", 0),
        "files_total": len(files),
        "apparatus_skipped": skipped,
    }
    if result.get("error"):
        projected["error"] = result["error"]
    return projected


@tool(cls="composed")
def silica_ledger_digest(run_id: Annotated[str, Field(description='Run ID to inspect (latest saved run if empty)')] = "") -> dict[str, Any]:
    """Compact summary of a run's plan and progress (< 500 tokens).

    Use to inspect an nucleate/audit run before advancing it with
    silica_ledger_next. Pass run_id="" for the most recently saved run.
    """
    from silica.kernel.progress import ProgressLedger, latest_run_id

    resolved_id = run_id.strip() or (latest_run_id() or "")
    if not resolved_id:
        return {"error": "No runs found in ~/.silica/runs/"}

    try:
        ledger = ProgressLedger.load(resolved_id)
    except FileNotFoundError:
        return {"error": f"Run '{resolved_id}' not found"}
    except Exception as e:
        return {"error": f"Failed to load ledger: {e}"}

    return {"run_id": resolved_id, "digest": ledger.digest()}


def _scan_dedup_pairs(folder: str = "") -> tuple[list[dict], str | None]:
    """Cosine + title scan for near-duplicate note pairs — vectors only, no body
    reads. Returns (pairs, error); each pair is {source, target, score, full_score,
    title_score}. Shared by silica_dedup (sync) and the /dedup ledger seed.

    A pair is admitted when body similarity is borderline (tau_low < score <
    tau_high) OR titles are strongly similar (title_score >= tau_title) — the
    latter catches "ROS" / "JSON in ROS 2" where bodies diverge but titles are
    clearly related.
    """
    import numpy as np

    from silica.kernel.recall.embed import get_store, _cosine
    from silica.config import CONFIG as _C

    store = get_store()
    if len(store) == 0:
        return [], "Embedding index empty — run /embed first."

    τ_high = getattr(_C, "sim_threshold_high", 0.85)
    τ_low = getattr(_C, "sim_threshold_low", 0.75)
    τ_title = getattr(_C, "sim_title_threshold", 0.80)

    scope = [p for p in store.paths() if _in_folder(p, folder)]
    # One blocked matmul for the whole scope, not one matvec per note: the scan is
    # N searches over an N-row index, so the per-note shape was quadratic in vault
    # size. Same top-k (see cosine_top_k_batch); notes without a usable vector are
    # simply absent from the result, as the `if not vec: continue` did.
    neighbours = store.cosine_top_k_batch(scope, k=_C.dedup_scan_k)
    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[dict] = []

    _title_cache: dict[str, Any] = {}

    def _title_vec(path: str):
        """Title vector as a float64 array, or None when the note has none.

        ponytail: memo, because the title gate below re-converted the same stored
        list on every candidate pair — 10 conversions per distinct vector, measured
        — and the conversion, not the dot product, was the cost (158 us -> 6.6 us
        per pair). float64 specifically: `_cosine` asks for float64, so asarray on
        an already-float64 array is a no-op and the score stays bit-identical; a
        float32 cache would be re-copied on every call and is slower than no cache
        at all. Drop this if the title vectors ever arrive as arrays.
        """
        if path not in _title_cache:
            raw = store.get_title_vec(path)
            _title_cache[path] = np.asarray(raw, dtype=np.float64) if raw else None
        return _title_cache[path]

    for p in scope:
        for match in neighbours.get(p, ()):
            score = match.get("score", 0.0)
            other = match.get("path", "")
            if not other or not _in_folder(other, folder):
                continue

            # Title-level similarity gate: catches pairs whose bodies diverge
            # but whose titles share a strong semantic relationship.
            title_vec_p = _title_vec(p)
            title_vec_o = _title_vec(other)
            title_score = (
                _cosine(title_vec_p, title_vec_o)
                # `is not None`, not truthiness: a numpy array raises on bool().
                if title_vec_p is not None and title_vec_o is not None
                else 0.0
            )

            in_full_window = τ_low < score < τ_high
            in_title_gate = title_score >= τ_title
            # continue (not break): candidates are score-descending; a match above
            # τ_high arrives before borderline ones — break would kill the loop early.
            if not in_full_window and not in_title_gate:
                continue

            key = tuple(sorted((p, other)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            pairs.append({
                "source": p,
                "target": other,
                "score": max(score, title_score),
                "full_score": score,
                "title_score": title_score,
            })

    return pairs, None


def _pairs_to_items(pairs: list[dict]) -> list["WorkItem"]:
    """Build dedup WorkItems from {source, target, score} dicts — the single
    place bodies are read and the larger/smaller split is decided. Optional
    full_score/title_score telemetry is propagated into context when present.
    """
    from silica.kernel.workqueue import WorkItem

    items: list[WorkItem] = []
    for pair in pairs:
        source = pair.get("source")
        target = pair.get("target")
        score = pair.get("score", 0.0)
        if not source or not target:
            continue
        try:
            body_src = DRIVER.read_note(source).content or ""
            body_tgt = DRIVER.read_note(target).content or ""
        except Exception:
            continue

        # The more reliable note is the merge target; the other is the source of
        # new info. Reliability first, length only to break a tie within a tier.
        from silica.kernel.write.contested import merge_rank
        if merge_rank(body_tgt) >= merge_rank(body_src):
            larger, smaller, smaller_body = target, source, body_src
        else:
            larger, smaller, smaller_body = source, target, body_tgt

        context = {
            "concept": smaller.removesuffix(".md").rsplit("/", 1)[-1],
            "excerpt": smaller_body[:4000],
            "candidate": larger.removesuffix(".md").rsplit("/", 1)[-1],
            "score": score,
            "inbox_file": smaller,
            # The loser is a real vault note here (unlike the FSM path, where
            # inbox_file is a source document), so it can be marked on merge.
            "loser_path": smaller,
        }
        reason = f"dedup score={score:.3f}"
        if "full_score" in pair and "title_score" in pair:
            context["full_score"] = pair["full_score"]
            context["title_score"] = pair["title_score"]
            reason += f" (full={pair['full_score']:.3f} title={pair['title_score']:.3f})"

        items.append(WorkItem(kind="dedup", target_path=larger, context=context, reason=reason))
    return items


@tool(cls="composed")
def silica_dedup_pairs(pairs: Annotated[list[dict], Field(description="List of duplicate pairs to merge. Each dict must have 'source' and 'target' keys.")]) -> dict[str, Any]:
    """Merge an ALREADY-KNOWN list of duplicate note pairs (e.g. from a ledger task).

    The smaller note's genuinely-new info is appended to the larger note as a
    single patch. To discover duplicate pairs by scanning, use silica_dedup.
    """
    from silica.agent.subagent import run_subagent_batch

    if not pairs:
        return {"error": "No pairs provided."}

    items = _pairs_to_items(pairs)
    if not items:
        return {"success": False, "message": "No valid pairs to process"}

    res = run_subagent_batch(items)
    res["pairs_found"] = len(items)
    return res


@tool(cls="composed")
def silica_dedup(folder: Annotated[str, Field(description='Vault folder to scan for near-duplicate notes (empty = whole vault)')] = "", cancel_token: Any = None) -> dict[str, Any]:
    """SCAN a folder (or the vault) for near-duplicate pairs and merge each
    smaller note into its larger twin: only genuinely-new info is appended
    (one patch) — never rewrites, deletes, or creates. Requires the embedding
    index (silica_embed_refresh). Known pairs: silica_dedup_pairs. Admission
    is borderline body similarity OR strong title similarity ("ROS" /
    "JSON in ROS 2").
    """
    from silica.agent.subagent import run_subagent_batch

    pairs, err = _scan_dedup_pairs(folder)
    if err:
        return {"error": err}

    items = _pairs_to_items(pairs)
    res = run_subagent_batch(items, cancel_token=cancel_token)
    res["pairs_found"] = len(items)
    res["folder"] = folder or "(vault)"
    return res


@tool(cls="composed")
def silica_refine_batch(note_paths: Annotated[list[str], Field(description='List of vault-relative paths to stylistically refine.')], cancel_token: Any = None) -> dict[str, Any]:
    """Stylistically refine a batch of notes: reformat for clarity and Obsidian
    style WITHOUT adding or losing information.

    To add missing content to thin notes, use silica_enrich_batch instead.
    """
    if not note_paths:
        return {"error": "No note paths provided."}

    from silica.kernel.workqueue import WorkItem
    from silica.agent.subagent import run_subagent_batch

    items = [WorkItem(kind="refine", target_path=p, context={}) for p in note_paths]
    res = run_subagent_batch(items, cancel_token=cancel_token)
    res["notes"] = len(items)
    return res


@tool(cls="composed")
def silica_enrich_batch(note_paths: Annotated[list[str], Field(description='List of vault-relative paths to semantically enrich.')], cancel_token: Any = None) -> dict[str, Any]:
    """Semantically enrich a batch of lean or empty notes: adds substantive
    content. To only fix style/formatting without changing content, use
    silica_refine_batch instead."""
    if not note_paths:
        return {"error": "No note paths provided."}

    from silica.kernel.workqueue import WorkItem
    from silica.agent.subagent import run_subagent_batch

    items = [WorkItem(kind="enrich", target_path=p, context={}) for p in note_paths]
    res = run_subagent_batch(items, cancel_token=cancel_token)
    res["notes"] = len(items)
    return res


@tool(cls="composed")
def silica_generate_taxonomy(
    user_intent: Annotated[str, Field(description='Natural-language description of how the user wants to organize their vault')], scope: Annotated[str, Field(description='Vault-relative subfolder to restrict taxonomy generation and scanning to')] = "", save_path: Annotated[str, Field(description="Vault-relative path where the taxonomy YAML should be written. Defaults to 'taxonomy.yaml' inside the configured vault.")] = "", merge: Annotated[bool, Field(description='If True, feed the existing taxonomy to the LLM as standing rules and update it incrementally instead of regenerating it from scratch.')] = False
) -> dict[str, Any]:
    """Generate a taxonomy YAML from a natural-language organization intent
    and write it to taxonomy.yaml (or save_path). merge=True treats the
    existing taxonomy as standing directives: preserves its rules, only adds
    what the new intent requires. The user should review the output before
    silica_run_organizer.
    """
    from pathlib import Path

    from silica.agent.llm import call_llm
    from silica.config import CONFIG
    from silica.kernel.text.sanitize import parse_json
    from silica.kernel.organize.taxonomy import (
        TAXONOMY_GENERATION_PROMPT,
        TAXONOMY_MERGE_BLOCK,
        Taxonomy,
        default_taxonomy_path,
    )
    from silica.driver import DRIVER

    # Resolve the destination first — merge mode reads the current file from there.
    # save_path is documented vault-relative and is model-supplied, so it goes
    # through the vault choke point: to_yaml creates parent dirs and writes, so
    # an absolute or `..` path would plant a file anywhere on the filesystem.
    if save_path:
        from silica.kernel.recall.paths import contain_in_vault
        try:
            dest = Path(CONFIG.vault_path) / contain_in_vault(save_path, Path(CONFIG.vault_path))
        except ValueError as exc:
            return {"error": f"Refused save_path '{save_path}': {exc}"}
    else:
        dest = default_taxonomy_path()

    note_titles: list[str] = []
    try:
        refs = DRIVER.list_files(scope or "")
        note_titles = [
            Path(ref.path or ref.name).stem
            for ref in refs
            if (ref.path or ref.name).endswith(".md")
        ]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("silica_generate_taxonomy: failed to list files for scope %s: %s", scope, exc)

    # Format the titles clearly (e.g., max 400 titles to prevent prompt bloat)
    titles_summary = "\n".join(f"- {t}" for t in note_titles[:400])
    if len(note_titles) > 400:
        titles_summary += f"\n- ... and {len(note_titles) - 400} more notes."

    system_prompt = "You are an expert knowledge manager. Follow the user instructions exactly."
    user_msg = TAXONOMY_GENERATION_PROMPT.format(
        user_intent=user_intent,
        scope=scope or "Entire Vault",
        note_titles=titles_summary or "(No notes found in scope)",
    )

    if merge and dest.exists():
        try:
            existing = Taxonomy.from_yaml(dest)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "silica_generate_taxonomy: cannot parse existing taxonomy at %s (%s) — generating from scratch",
                dest, exc,
            )
            existing = None
        if existing is not None and existing.rules:
            import yaml as _y
            existing_yaml = _y.dump(
                existing.model_dump(), allow_unicode=True, sort_keys=False, default_flow_style=False
            )
            user_msg += TAXONOMY_MERGE_BLOCK.format(existing_yaml=existing_yaml)

    try:
        response = call_llm(
            model=CONFIG.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
        )
        raw = (response.text or "").strip()
    except Exception as exc:
        return {"error": f"LLM call failed: {exc}"}

    # The LLM is instructed to return raw YAML; try yaml.safe_load first,
    # then fall back to parse_json for robustness.
    import yaml as _yaml

    taxonomy_dict: dict | None = None
    try:
        taxonomy_dict = _yaml.safe_load(raw)
    except Exception:
        pass

    if not isinstance(taxonomy_dict, dict):
        parsed, _ = parse_json(raw, strict=False)
        if isinstance(parsed, dict):
            taxonomy_dict = parsed
        else:
            return {"error": f"LLM returned unparseable output: {raw[:300]}"}

    try:
        taxonomy = Taxonomy.from_dict(taxonomy_dict)
    except Exception as exc:
        return {"error": f"Taxonomy validation failed: {exc}", "raw": taxonomy_dict}

    try:
        taxonomy.to_yaml(dest)
    except Exception as exc:
        return {"error": f"Failed to write taxonomy to {dest}: {exc}"}

    return {
        "success": True,
        "taxonomy_path": str(dest),
        "taxonomy": taxonomy.model_dump(),
        "rules_count": len(taxonomy.rules),
    }


@tool(cls="composed")
def silica_run_organizer(
    taxonomy_path: Annotated[str, Field(description="Path to the taxonomy YAML file. Defaults to 'taxonomy.yaml' in the vault.")] = "",
    scope: Annotated[str, Field(description='Vault-relative subfolder to restrict organization to (empty = vault-wide)')] = "",
    dry_run: Annotated[bool, Field(description='If True (default), compute and return the move plan without executing any moves. Set to False to actually move notes.')] = True,
    llm_arbiter: Annotated[bool, Field(description='If True, use the LLM to classify borderline notes (ambiguous band)')] = True,
    move_uncategorized: Annotated[bool, Field(description='If True, notes matching no taxonomy rule are moved to the uncategorized folder. Default False: unmatched notes stay where they are.')] = False,
) -> dict[str, Any]:
    """Classify notes against the taxonomy (silica_generate_taxonomy first)
    and move them into its folders. dry_run=True (default) returns the plan;
    dry_run=False moves graph-safely (wikilinks updated) with rollback on a
    failed lint gate. Single note: silica_move.
    """
    from silica.kernel.organize.taxonomy import load_taxonomy
    from silica.router.organize_fsm import OrganizerFSM

    taxonomy = load_taxonomy(taxonomy_path or None)
    if not taxonomy.rules:
        return {
            "error": (
                "Taxonomy has no rules. Run silica_generate_taxonomy first or "
                "create taxonomy.yaml manually."
            )
        }

    fsm = OrganizerFSM(
        taxonomy=taxonomy,
        scope=scope,
        dry_run=dry_run,
        llm_arbiter=llm_arbiter,
        move_uncategorized=move_uncategorized,
    )
    return fsm.run()
