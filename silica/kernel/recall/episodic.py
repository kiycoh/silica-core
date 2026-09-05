# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Episodic memory lane — short-term fact store with supersedes chains and TTL.

Captures the personal, ephemeral facts the distiller used to discard ("my dog
is named Tom"), keeps them with fact-level supersedes chains and a wall-clock
TTL, recalls them at answer time next to vault notes, and surfaces nucleation
candidates (facts reinforced across runs) in the run digest.

ADR-0019 boundary, stated explicitly: "writes never route to the memory vault"
governs vault NOTES going through the FSM write channel. `episodic.json` is
index-layer state, sibling of the embed/cooccur indices, not vault content.

Kernel rule: this module never calls ``datetime.now()`` — every date (`seen`,
`now`) is supplied by the caller. The product path passes the run date; the
LongMemEval adapter passes the simulated session date.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from silica.kernel.recall.embed import _cosine  # noqa: F401 — shared helper, re-exported for probes

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

class Fact(BaseModel):
    id: str
    key: str
    text: str
    first_seen: str
    last_seen: str
    runs: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    status: str = "live"
    # Provenance of a fact captured from one of Silica's own sessions: the
    # vault's digest12 and the notes that session touched. Additive and
    # optional — stores written before phase E load unchanged, and facts from
    # the note path (or an eval store) simply carry neither.
    vault: str | None = None
    notes: list[str] = Field(default_factory=list)
    # Set on the chain HEAD by `/promote`: the vault note this chain became.
    # Also the queue's exit condition — a promoted chain stops being suggested.
    promoted: str | None = None
    # Packed as float32 npz by save() — a real store hit this field's old
    # upgrade condition at 1067 facts / 59 MB, 99.5% of it vectors printed as
    # decimal text. In memory it stays a plain list; only the disk format packs.
    vec: list[float] | None = None


class NucleationCandidate(BaseModel):
    key: str
    run_count: int
    since: str
    text: str


class FactHit(BaseModel):
    fact: Fact
    score: float


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(
        ch if ch.isalnum() else " " for ch in text.casefold()
    ).split() if len(t) > 1}


_FACTS_ADAPTER = TypeAdapter(list[Fact])


def _days_between(earlier: str, later: str) -> int:
    from datetime import date

    try:
        return (date.fromisoformat(later[:10]) - date.fromisoformat(earlier[:10])).days
    except ValueError:
        return 0  # unparseable date: never expire on bad input


def _normalize(text: str) -> str:
    """Casefold + strip punctuation/whitespace, for supersede-vs-reinforce."""
    out = []
    for ch in text.casefold():
        cat = unicodedata.category(ch)
        if cat.startswith("P") or ch.isspace():
            continue
        out.append(ch)
    return "".join(out)


# Change-marker WORDS per language: models bake the CHANGE into the key
# ("aspiration_reinforced", "job_update", "trip.new") despite the prompt's
# key-discipline block — LoCoMo smoke 2026-07-18 showed the instruction being
# ignored with the clean key in view. Folding these at lookup time makes the
# variant MATCH the clean head so supersede chains stay whole.
# Words, not stems: _marker_stems derives the match set through the store's
# own stemmer, so one representative word covers its morphological family
# (nuovo/nuova/nuovi/nuove -> "nuov") and the entries stay reviewable by a
# speaker instead of encoding snowball internals by hand.
# Single-token entries only, chosen against noun collisions (over-folding
# buries facts — the key-collision defect, probe 2026-08-02): dutch drops
# `nieuw` (nieuws = news folds into it; `nieuwe` is safe), french drops
# `nouvelle` (nouvelles = news), da/no/sv drop the neuter `nyt`/`nytt`
# (nytte/nytta = benefit, nyttig = useful). Romanian keeps `nou` accepting
# the nouă=nine ambiguity — new-fem decorations vastly outnumber spelled-out
# numerals in keys, the same trade english "new" already makes.
# ponytail: hand-kept lists for the SNOWBALL_TO_ISO languages minus arabic
# (no confident single-token markers — several candidates are ambiguous
# nouns unvowelled, e.g. معدل modified/rate). An uncovered language falls
# back to the english WORDS stemmed with the store's own stemmer, so
# english-decorated keys still fold — today's behavior. Upgrade path: ask
# the worker model once at _freeze_lang time and persist the list in the
# store file.
_CHANGE_MARKER_WORDS: dict[str, tuple[str, ...]] = {
    "english":    ("reinforced", "reaffirmed", "updated", "new", "changed"),
    "danish":     ("forstærket", "genbekræftet", "opdateret", "ny", "ændret"),
    "dutch":      ("versterkt", "herbevestigd", "bijgewerkt", "nieuwe",
                   "veranderd", "gewijzigd"),
    "finnish":    ("päivitetty", "uusi", "muutettu"),
    "french":     ("renforcé", "réaffirmé", "actualisé", "nouveau", "changé",
                   "modifié"),
    "german":     ("verstärkt", "bekräftigt", "aktualisiert", "neu",
                   "geändert", "modifiziert"),
    "hungarian":  ("frissített", "új", "megváltozott", "módosított"),
    "italian":    ("rinforzato", "riaffermato", "aggiornato", "nuovo",
                   "cambiato", "modificato"),
    "norwegian":  ("forsterket", "oppdatert", "ny", "endret"),
    "portuguese": ("reforçado", "reafirmado", "atualizado", "novo", "mudado",
                   "alterado", "modificado"),
    "romanian":   ("reafirmat", "actualizat", "nou", "schimbat", "modificat"),
    "russian":    ("обновлено", "обновлённый", "обновленный", "новый",
                   "изменено", "изменённый", "измененный"),
    "spanish":    ("reforzado", "reafirmado", "actualizado", "nuevo",
                   "cambiado", "modificado"),
    "swedish":    ("förstärkt", "återbekräftad", "uppdaterad", "ny", "ändrad"),
}
_VERSION_TOKEN_RE = re.compile(r"v\d+$")


@lru_cache(maxsize=None)
def _marker_stems(lang: str) -> frozenset[str]:
    """Marker stems for `lang`, derived through the same stemmer that
    normalize_key applies to key tokens — marker and token can only match
    if both pass through the store's own stemmer."""
    from silica.kernel.text.text import stem_word

    words = _CHANGE_MARKER_WORDS.get(lang) or _CHANGE_MARKER_WORDS["english"]
    return frozenset(stem_word(w, lang=lang) for w in words)


def normalize_key(key: str, *, lang: str = "english") -> str:
    """Canonical `entity.attribute` form for MATCHING: casefold, then
    snowball-stem every `_`-token of every `.` segment, dropping change-marker
    tokens (`_reinforced`/`_update`/`.new`/`v2` — the change belongs in the
    text, supersede encodes it). Merges morphological key drift
    (`model_kits.gifts` == `model_kit.gift`); semantic synonyms stay distinct.
    Stored keys are never rewritten — this is lookup identity only.

    `lang` is the store's frozen key language (EpisodicStore._freeze_lang).
    The default is english because the STRUCTURAL callers below
    (`enforce_key_schema`'s prefixes, `key_tokens`) match against the
    `user.`/`assistant.` grammar the prompt mandates in English regardless of
    the vault's prose language. Only capture, which decides which keys merge,
    passes the store's own language: an italian store stemmed as english
    leaves `utente.auto.modello` and `.modelli` distinct, so the supersede
    chain splits and recall returns both values of one attribute.
    """
    from silica.kernel.text.text import stem_word

    segs: list[str] = []
    for seg in key.casefold().split("."):
        toks = [stem_word(t, lang=lang) for t in seg.split("_") if t]
        kept = [t for t in toks
                if t not in _marker_stems(lang) and not _VERSION_TOKEN_RE.fullmatch(t)]
        if kept:
            segs.append("_".join(kept))
    if not segs:  # a key that is nothing but markers: fall back unfiltered
        return ".".join("_".join(stem_word(t, lang=lang) for t in s.split("_") if t)
                        for s in key.casefold().split(".") if s.strip("_"))
    return ".".join(segs)


_ENTITY_PREFIXES = {"user", "assist"}  # canonical forms of user. / assistant.


def enforce_key_schema(key: str, schema) -> str:
    """Structural write-time enforcement of the declared key grammar
    (ADR-0021): unknown first segment folds under `schema.default_prefix`,
    segments beyond `schema.max_depth` fold into the last one. Never rejects.

    Distinct from `normalize_key` (lookup-only matching identity): stored
    keys are shaped here but never stemmed — the spelling survives.
    """
    segs = [s for s in key.split(".") if s]
    if not segs:
        return key
    canonical = {normalize_key(p) for p in schema.prefixes}
    if normalize_key(segs[0]) not in canonical:
        segs.insert(0, schema.default_prefix)
    if len(segs) > schema.max_depth:
        segs = segs[:schema.max_depth - 1] + ["_".join(segs[schema.max_depth - 1:])]
    return ".".join(segs)


def _entity_segments(key: str) -> tuple:
    """Entity namespace of a key, as segments. `user.<name>.*` keys are
    per-person; any other first segment is the entity itself (so assistant.*
    observations belong to one entity across sessions)."""
    segs = [s for s in key.casefold().split(".") if s]
    if not segs:
        return ()
    if segs[0] == "user" and len(segs) > 1:
        return ("user", segs[1])
    return (segs[0],)


def entity_key(key: str) -> str:
    """Dotted entity namespace of a key — the unit a promotion writes.

    `user.dog.name` and `user.dog.breed` are two attributes of one dog: one note
    about the dog beats two notes about its fields, and the fields alone are too
    thin to clear the write gate.
    """
    return ".".join(_entity_segments(key))


def key_tokens(key: str) -> set[str]:
    """Stemmed tokens of a key, entity prefix dropped: the shared alphabet
    of the eval key-drift/clustering probes."""
    segs = normalize_key(key).split(".")
    if len(segs) > 1 and segs[0] in _ENTITY_PREFIXES:
        segs = segs[1:]
    return {t for s in segs for t in s.split("_") if len(t) > 1}


def rare_token_components(keys: list[str], *,
                          max_df: int | None = None) -> dict[str, str]:
    """Connected components over shared key tokens: key -> root key.

    With ``max_df``, a token forms edges only while its document frequency
    over the (deduplicated) key set stays <= max_df; None means no filter
    (the naive blob view kept for diagnostics). Pure function of the key
    set: deterministic and order-independent."""
    keys = sorted(set(keys))
    toks = {k: key_tokens(k) for k in keys}
    if max_df is not None:
        df: dict[str, int] = {}
        for ts in toks.values():
            for t in ts:
                df[t] = df.get(t, 0) + 1
        toks = {k: {t for t in ts if df[t] <= max_df} for k, ts in toks.items()}
    parent = {k: k for k in keys}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    owner: dict[str, str] = {}
    for k in keys:
        for t in toks[k]:
            if t in owner:
                parent[find(k)] = find(owner[t])
            else:
                owner[t] = k
    return {k: find(k) for k in keys}


def key_vocabulary(store: "EpisodicStore", *, cap: int = 60) -> list[str]:
    """Raw keys of live heads, most recently seen first, capped.

    Feeds the distiller's `## Episodic keys` context section so capture snaps
    to the established vocabulary instead of coining synonym keys."""
    heads = sorted(store.live_facts(), key=lambda f: f.last_seen, reverse=True)
    return [f.key for f in heads[:cap]]


def key_vocabulary_section(store: "EpisodicStore") -> str | None:
    """`## Episodic keys` distiller-context section; None on an empty store."""
    keys = key_vocabulary(store)
    if not keys:
        return None
    return (
        "## Episodic keys\n"
        "Live ephemeral keys already in the store. When a fact concerns one "
        "of these attributes, reuse that exact key instead of coining a new "
        "one:\n" + ", ".join(keys)[:600]  # hard token-budget cap
    )


def _pack_store(doc: dict) -> bytes:
    """Serialize the store doc as npz: vectors as one flat float32 block, the
    rest as JSON in the meta entry. Same layout idea as embed._serialize_notes;
    measured on a real store: 59 MB of decimal text -> ~11 MB binary.

    Mutates `doc["facts"]` entries in place (vec -> vlen); callers pass a doc
    freshly built from model_dump, never a shared structure.
    """
    import io

    import numpy as np

    vecs: list = []
    for fact in doc.get("facts", []):
        v = fact.pop("vec", None)
        if v is not None:
            arr = np.asarray(v, dtype=np.float32).ravel()
            vecs.append(arr)
            fact["vlen"] = int(arr.size)

    flat = np.concatenate(vecs) if vecs else np.zeros(0, dtype=np.float32)
    meta_arr = np.frombuffer(json.dumps(doc, ensure_ascii=False).encode("utf-8"),
                             dtype=np.uint8)
    buf = io.BytesIO()
    np.savez(buf, flat=flat, meta=meta_arr)
    return buf.getvalue()


def _unpack_store(raw: bytes) -> dict:
    """Inverse of _pack_store: meta JSON with each fact's vec spliced back in
    (vlen consumed in order). Raises on a malformed archive; _load quarantines."""
    import io

    import numpy as np

    with np.load(io.BytesIO(raw), allow_pickle=False) as z:
        flat = z["flat"]
        doc = json.loads(z["meta"].tobytes().decode("utf-8"))

    off = 0
    for fact in doc.get("facts", []):
        vlen = fact.pop("vlen", None)
        if vlen is not None:
            vlen = int(vlen)
            fact["vec"] = flat[off:off + vlen].tolist()
            off += vlen
    return doc


def read_store_doc(path: Path) -> dict:
    """The raw store doc off disk, whichever format it is written in.

    npz archives start with the zip magic 'PK'; the legacy store is JSON text
    and is recognized forever. Public because the store is not read only by the
    store: the LongMemEval probes read the same file, and a plain `read_text()`
    there raised UnicodeDecodeError on the binary format the product had already
    started writing. One sniff, one place.
    """
    raw = path.read_bytes()
    return _unpack_store(raw) if raw[:2] == b"PK" else json.loads(raw.decode("utf-8"))


class EpisodicStore:
    """JSON-file-backed fact store. Facts are not notes; they nucleate INTO notes."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else store_path()
        self.next_id = 1
        self.facts: list[Fact] = []
        self.lang: str | None = None  # frozen key-stemming language, see _freeze_lang
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            # Legacy files are recognized forever and migrate to npz on the
            # next save(); read_store_doc owns the sniff.
            doc = read_store_doc(self.path)
            self.next_id = int(doc.get("next_id", 1))
            self.lang = doc.get("lang") or None
            self.facts = _FACTS_ADAPTER.validate_python(doc.get("facts", []))
        except Exception:
            from silica.kernel.recall.paths import quarantine

            quarantine(self.path)
            self.next_id, self.facts = 1, []

    def save(self) -> None:
        from silica.kernel.recall.paths import atomic_write_bytes

        doc: dict = {
            "schema_version": SCHEMA_VERSION,
            "next_id": self.next_id,
        }
        if self.lang:  # absent until the first capture pins it
            doc["lang"] = self.lang
        doc["facts"] = [f.model_dump(exclude_none=False) for f in self.facts]
        atomic_write_bytes(self.path, _pack_store(doc))

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, facts: list[dict], *, run_id: str, seen: str,
                embedder=None, schema=None,
                vault: str | None = None,
                notes: list[str] | None = None) -> None:
        """Merge distiller ephemerals into the store. Mechanical, no LLM.

        Same key + same normalized text reinforces (last_seen, runs); same key
        + different text supersedes the live head; a new key starts a chain.
        New/changed facts are embedded when `embedder` is served; embedding
        failure is silent (recall falls back to lexical).

        `schema` (ADR-0021): an `EpisodicKeySchema` shapes stored keys via
        `enforce_key_schema` before merge; None means no enforcement —
        bit-identical to before the schema existed (frozen-store replays and
        A/B baselines depend on this default).
        """
        # Heads keyed by canonical form: keys written before Layer A still
        # match variant arrivals. On a legacy collision (two live heads with
        # the same canonical form) the later chain wins the lookup; TTL
        # retires the other.
        lang = self._freeze_lang([(r.get("text") or "") for r in facts])
        heads = {normalize_key(f.key, lang=lang): f
                 for f in self.facts if f.status == "live"}
        created: list[Fact] = []
        folded = 0
        for raw in facts:
            key = (raw.get("key") or "").strip()
            text = (raw.get("text") or "").strip()
            if not key or not text:
                continue
            if schema is not None:
                shaped = enforce_key_schema(key, schema)
                folded += shaped != key
                key = shaped
            nkey = normalize_key(key, lang=lang)
            head = heads.get(nkey)
            if head is not None and _normalize(head.text) == _normalize(text):
                head.last_seen = seen
                if run_id not in head.runs:
                    head.runs.append(run_id)
                continue
            fid = f"f_{self.next_id:04d}"
            self.next_id += 1
            fact = Fact(id=fid, key=key, text=text, first_seen=seen, last_seen=seen,
                        runs=[run_id], vault=vault, notes=list(notes or []))
            if head is not None:
                fact.supersedes = head.id
                head.status = "superseded"
            self.facts.append(fact)
            heads[nkey] = fact
            created.append(fact)
        pending = [f for f in created if not f.vec]
        if embedder is not None and pending:
            try:
                vecs = embedder.embed([f.text for f in pending])
                for fact, vec in zip(pending, vecs):
                    fact.vec = list(vec)
            except Exception as e:
                logger.debug("episodic capture: embedding skipped (%s)", e)
        if folded:
            logger.debug("episodic capture: %d key(s) schema-folded", folded)
        self.save()

    def _freeze_lang(self, incoming: list[str]) -> str:
        """Pin the store's key-stemming language on first capture, from the
        facts' own prose (`text` is verbatim in the source language, the
        distiller prompt never constrains the KEY to English).

        Frozen rather than re-detected, on the `cooccurrence_lang` precedent:
        the stemmer decides which keys MATCH, so letting detection drift as
        the store grows would silently re-partition live supersede chains
        mid-life. Delete `episodic.json` to re-pin. A store written before
        this existed pins on its next capture; english stores land on
        "english" and keep their exact identity.
        """
        if self.lang:
            return self.lang
        from silica.kernel.text import language

        sample = " ".join([*(f.text for f in self.facts), *incoming])[:4000]
        self.lang = language.detect(sample) if sample.strip() else "english"
        return self.lang

    # ------------------------------------------------------------------
    # TTL sweep
    # ------------------------------------------------------------------

    def sweep(self, now: str, *, ttl_days: int | None = None) -> int:
        """Delete chains whose HEAD's last_seen is older than ttl_days at `now`.

        Superseded ancestors live exactly as long as their head; expired chains
        are deleted, not archived. Returns the number of chains removed.
        ttl_days=0 means never expire. Persists when anything was removed.
        """
        if ttl_days is None:
            from silica.config import CONFIG

            ttl_days = int(getattr(CONFIG, "episodic_ttl_days", 90))
        if ttl_days <= 0:
            return 0
        expired_ids: set[str] = set()
        removed = 0
        for head in self.live_facts():
            if _days_between(head.last_seen, now) <= ttl_days:
                continue
            removed += 1
            expired_ids.update(self._chain_ids(head))
        if expired_ids:
            self.facts = [f for f in self.facts if f.id not in expired_ids]
            self.save()
        return removed

    def chain(self, head: Fact) -> list[Fact]:
        """The supersede chain from `head` back to its oldest ancestor."""
        by_id = {f.id: f for f in self.facts}
        out: list[Fact] = []
        cur: Fact | None = head
        while cur is not None:
            out.append(cur)
            cur = by_id.get(cur.supersedes) if cur.supersedes else None
        return out

    def _chain_ids(self, head: Fact) -> list[str]:
        return [f.id for f in self.chain(head)]

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    def recall(self, query_text: str, query_vec: list[float] | None = None, *,
               k: int = 10, now: str, ttl_days: int | None = None,
               floor: float | None = None) -> list["FactHit"]:
        """Top-k LIVE facts for a query. Never mutates the store.

        A fact is scored by the embed leg (cosine) when both vectors exist,
        else lexically (token overlap with text + key segments). The two never
        fuse. `now` filters chains whose head is past TTL without deleting —
        sweep at digest time is the only deleter.

        `floor` (None = CONFIG.episodic_recall_floor, 0 = off) is the relevance
        cut on the EMBED leg only: cosine over a normalized embedder is
        essentially never negative, so `score > 0` is not a floor and top-k
        degenerates to "the whole store, every query". The lexical leg keeps
        `> 0` because its overlap ratio is a different scale — a two-term query
        matching one term scores 0.5 there, which is a hit, not noise.
        """
        if ttl_days is None:
            from silica.config import CONFIG

            ttl_days = int(getattr(CONFIG, "episodic_ttl_days", 90))
        if floor is None:
            from silica.config import CONFIG

            floor = float(getattr(CONFIG, "episodic_recall_floor", 0.5))
        q_tokens = _tokens(query_text)
        hits: list[FactHit] = []
        for fact in self.live_facts():
            if ttl_days > 0 and _days_between(fact.last_seen, now) > ttl_days:
                continue
            if query_vec is not None and fact.vec:
                score = _cosine(query_vec, fact.vec)
                if score < floor:
                    continue
            else:
                f_tokens = _tokens(fact.text) | _tokens(fact.key.replace(".", " "))
                score = len(q_tokens & f_tokens) / len(q_tokens) if q_tokens else 0.0
                if score <= 0.0:
                    continue
            hits.append(FactHit(fact=fact, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    # ------------------------------------------------------------------
    # Nucleation
    # ------------------------------------------------------------------

    def nucleation_candidates(self, *, min_runs: int | None = None) -> list["NucleationCandidate"]:
        """Keys whose chain accumulated >= min_runs distinct run ids.

        Suggested in the digest, never auto-written: promotion goes through
        the normal write channel when the user or agent acts on it.
        """
        if min_runs is None:
            from silica.config import CONFIG

            min_runs = int(getattr(CONFIG, "episodic_nucleation_runs", 3))
        out: list[NucleationCandidate] = []
        for head in self.live_facts():
            if head.promoted:
                continue  # already a note: the suggestion is spent
            links = self.chain(head)
            runs = {r for f in links for r in f.runs}
            if len(runs) >= min_runs:
                out.append(NucleationCandidate(key=head.key, run_count=len(runs),
                                               since=min(f.first_seen for f in links),
                                               text=head.text))
        return out

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def live_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.status == "live"]


def capture_from_distill(result: dict, *, run_id: str, seen: str,
                         vault: str | None = None,
                         notes: list[str] | None = None) -> None:
    """Route a distiller result's `ephemerals` into the default store.

    Failures here must never fail the ingest: log + continue. The embedder is
    optional — when unavailable, facts are stored unembedded (lexical recall).
    """
    try:
        ephemerals = result.get("ephemerals") or []
        if not ephemerals:
            return
        embedder = None
        try:
            from silica.agent.providers import get_embedder
            from silica.config import CONFIG

            embedder = get_embedder(CONFIG)
        except Exception:
            pass
        # ADR-0021: the key schema is owned by the MEMORY vault (the store's
        # home), never by the vault active at capture. Absent block ⇒ None ⇒
        # no enforcement.
        schema = None
        try:
            from silica.kernel.vault_manifest import load_manifest

            schema = load_manifest(episodic_home()).conventions.episodic_keys
        except Exception:
            pass
        EpisodicStore().capture(ephemerals, run_id=run_id, seen=seen,
                                embedder=embedder, schema=schema,
                                vault=vault, notes=notes)
    except Exception as e:
        logger.warning("episodic capture failed (ingest continues): %s", e)


def render(hits: list[FactHit], *, store: EpisodicStore) -> str:
    """Render recalled facts with their supersede history, dates included —
    knowledge-update and temporal-reasoning questions need the chain."""
    lines: list[str] = []
    for hit in hits:
        links = store.chain(hit.fact)
        lines.append(f"- [since {links[0].first_seen}] {links[0].text}")
        for newer, older in zip(links, links[1:]):
            lines.append(
                f"  (previously: {older.text}, {older.first_seen} to {newer.first_seen})"
            )
    return "\n".join(lines)


def promotion_stub(heads: list[Fact], *, store: EpisodicStore) -> str:
    """Markdown source for `/promote`: one entity, one section per attribute.

    The stub is an ordinary inbox file from there on — same FSM, same dedup,
    same write gate. The frontmatter says where the material came from so the
    resulting note is not mistaken for something the user wrote. Per attribute
    it would be a two-line note the gate rejects as a placeholder; the entity
    is the smallest thing worth a note.
    """
    chains = {h.id: store.chain(h) for h in heads}
    links = [f for c in chains.values() for f in c]
    runs = {r for f in links for r in f.runs}
    # Bold, NOT a `##` heading: the payload builder's heading-section match
    # would window the excerpt down to one attribute's line, and the distiller
    # would never see the entity (measured: empty bodies, every /promote
    # no_ops). A bold line is body text, so the window carries the stub whole.
    body = "\n\n".join(
        f"**{h.key}**\n" + render([FactHit(fact=h, score=1.0)], store=store)
        for h in heads
    )
    return (
        "---\n"
        f"episodic_key: {entity_key(heads[0].key)}\n"
        f"episodic_attributes: {', '.join(h.key for h in heads)}\n"
        f"first_seen: {min(f.first_seen for f in links)}\n"
        f"last_seen: {max(h.last_seen for h in heads)}\n"
        f"sessions: {len(runs)}\n"
        "---\n\n"
        f"# {entity_key(heads[0].key)}\n\n"
        + body
        + "\n"
    )


def episodic_home() -> Path:
    """Home vault for episodic state: CONFIG.memory_vault, default ~/.silica/vault.

    Unlike ``memory_lane.memory_vault()`` there is NO abstain rule — when the
    active vault IS the memory vault, facts still land there.
    """
    from silica.config import CONFIG

    raw = (getattr(CONFIG, "memory_vault", "") or "").strip()
    return (Path(raw).expanduser() if raw else Path.home() / ".silica" / "vault").resolve()


def store_path() -> Path:
    from silica.kernel.recall import paths

    return paths.index_dir_for(str(episodic_home())) / "episodic.json"
