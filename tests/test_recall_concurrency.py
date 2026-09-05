"""Recall under concurrency — abstention, per-thread stemming, store locking.

The FSM drives sub-agent threads and a residue executor against ONE process-wide
EmbedStore and ONE module-global stemmer cache, so these are the seams where a
silent wrong answer (not an exception) was possible.
"""
from __future__ import annotations

import threading

import pytest

from silica.kernel.recall import relatedness
from silica.kernel.recall.embed import EmbedStore
from silica.kernel.text import text as text_mod


# ---------------------------------------------------------------------------
# neighbours_above abstains instead of raising
# ---------------------------------------------------------------------------

def test_neighbours_above_abstains_when_store_raises(monkeypatch):
    """A broken embed index must degrade AUTOLINK to the full title index.

    The handler logs before returning None; without a module logger it raised
    NameError and killed the whole AUTOLINK step for that chunk.
    """
    import silica.kernel.recall.embed as embed_mod

    def boom():
        raise RuntimeError("corrupt embed index")

    monkeypatch.setattr(embed_mod, "get_store", boom)
    assert relatedness.neighbours_above("Some/Note.md", 0.5) is None


def test_neighbours_above_abstains_when_search_raises(monkeypatch):
    """Same abstention when the failure is inside cosine_top_k, not get_store."""
    import silica.kernel.recall.embed as embed_mod

    class ExplodingStore:
        def __len__(self):
            return 3

        def get_vec(self, key):
            return [1.0, 0.0]

        def cosine_top_k(self, vec, k, exclude=None):
            raise ValueError("mismatched matrix generation")

    monkeypatch.setattr(embed_mod, "get_store", ExplodingStore)
    assert relatedness.neighbours_above("Some/Note.md", 0.5) is None


def test_relatedness_has_a_logger():
    """The abstention path logs; the name must exist at module scope."""
    assert relatedness.logger.name == "silica.kernel.recall.relatedness"


# ---------------------------------------------------------------------------
# Stemmers are per-thread (snowballstemmer.stemWord is not reentrant)
# ---------------------------------------------------------------------------

def test_concurrent_stemming_returns_each_threads_own_stem():
    words = ["running", "connection", "generalisation", "walked", "processes"]
    expected = {w: text_mod.stem_word(w, lang="english") for w in words}
    errors: list[str] = []
    barrier = threading.Barrier(len(words))

    def worker(word: str) -> None:
        barrier.wait()
        for _ in range(300):
            got = text_mod.stem_word(word, lang="english")
            if got != expected[word]:
                errors.append(f"{word} -> {got}, expected {expected[word]}")
                return

    threads = [threading.Thread(target=worker, args=(w,)) for w in words]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert errors == []


def test_each_thread_gets_its_own_stemmer_object():
    # Collect the stemmers themselves, never their id()s: a thread's stemmer is
    # freed when the thread dies, and CPython hands the freed address to the
    # next thread's stemmer — comparing ids alone fails here roughly one run in
    # four with the per-thread cache working perfectly.
    seen: list = []
    lock = threading.Lock()

    def worker() -> None:
        stemmer = text_mod._get_stemmer("english")
        with lock:
            seen.append(stemmer)

    main = text_mod._get_stemmer("english")
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    stemmers = seen + [main]
    assert len({id(s) for s in stemmers}) == 4, "each thread must hold its own stemmer instance"


def test_stemmer_is_still_cached_within_a_thread():
    assert text_mod._get_stemmer("english") is text_mod._get_stemmer("english")


def test_auto_language_still_falls_back_to_english():
    assert text_mod._get_stemmer("auto") is text_mod._get_stemmer("english")


# ---------------------------------------------------------------------------
# EmbedStore: mutation during search must not tear the matrix
# ---------------------------------------------------------------------------

def _store_with_notes(tmp_path, n: int) -> EmbedStore:
    store = EmbedStore(path=tmp_path / "embeddings.npz")
    for i in range(n):
        # Distinct directions so a correct search never scores every candidate 0.0.
        vec = [0.0] * n
        vec[i] = 1.0
        store.upsert(f"note{i}", f"Note {i}", vec)
    return store


def test_search_during_concurrent_upsert_keeps_scoring(tmp_path):
    """A mutation landing mid-search used to leave _mat None / _mat_paths stale,
    so every candidate scored 0.0 and the caller got arbitrary notes with no
    error. Scores must stay real while another thread churns the index."""
    store = _store_with_notes(tmp_path, 24)
    query = [0.0] * 24
    query[7] = 1.0
    stop = threading.Event()
    errors: list[BaseException] = []
    zero_rounds: list[int] = []

    def churn() -> None:
        i = 0
        try:
            while not stop.is_set():
                store.upsert(f"churn{i % 8}", f"Churn {i % 8}", [0.0] * 23 + [1.0])
                store.delete(f"churn{(i + 4) % 8}")
                i += 1
        except BaseException as exc:  # noqa: BLE001 - the assertion is "no raise"
            errors.append(exc)

    writer = threading.Thread(target=churn)
    writer.start()
    try:
        for _ in range(400):
            hits = store.cosine_top_k(query, k=3)
            assert hits, "search returned nothing while the store held notes"
            if max(h["score"] for h in hits) <= 0.0:
                zero_rounds.append(1)
    finally:
        stop.set()
        writer.join(timeout=30)
    assert not writer.is_alive()
    assert errors == []
    assert zero_rounds == [], "search saw a torn matrix and scored everything 0.0"
    assert store.cosine_top_k(query, k=1)[0]["path"] == "note7"


def test_save_during_concurrent_upsert_does_not_raise(tmp_path):
    """_serialize_notes walks the dict in Python; a concurrent upsert raised
    'dictionary changed size during iteration'."""
    store = _store_with_notes(tmp_path, 40)
    stop = threading.Event()
    errors: list[BaseException] = []

    def churn() -> None:
        # Fresh keys, not replacements: only a size change trips
        # "dictionary changed size during iteration". Bounded so the thread
        # always terminates even if save() somehow never returns.
        i = 0
        try:
            while not stop.is_set() and i < 20000:
                store.upsert(f"churn{i}", f"Churn {i}", [0.1] * 40)
                i += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    writer = threading.Thread(target=churn)
    writer.start()
    try:
        for _ in range(20):
            store.save()
    finally:
        stop.set()
        writer.join(timeout=30)
    assert not writer.is_alive()
    assert errors == []
    assert EmbedStore(path=tmp_path / "embeddings.npz").paths()


def test_get_store_is_one_instance_under_concurrent_first_access(tmp_path, monkeypatch):
    """Check-then-set in the singleton let two threads build two stores; every
    upsert against the loser was silently dropped at flush."""
    import silica.kernel.recall.embed as embed_mod

    monkeypatch.setattr(embed_mod, "_index_path", lambda: tmp_path / "embeddings.npz")
    embed_mod.clear()
    barrier = threading.Barrier(8)
    got: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        store = embed_mod.get_store()
        with lock:
            got.append(id(store))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    embed_mod.clear()
    assert not any(t.is_alive() for t in threads)
    assert len(set(got)) == 1


# ---------------------------------------------------------------------------
# perceive(assemble=True) keeps the contested marker
# ---------------------------------------------------------------------------

def test_assembled_block_keeps_contested_of_head_and_periphery(monkeypatch):
    from evals import recall_arms, recall_assembly
    from silica.kernel.recall import perception

    seeds = [
        perception.NoteBlock(path="head", date="d", evidence="e",
                             body="# Head\nbody", excerpt="# Head\nbody",
                             contested="wrong date"),
    ]
    # "head" and "peri" share the hub "H", so squash() folds them into ONE
    # block: the periphery member's own text disappears into the head's block.
    neighbors = {
        "head": recall_assembly.Neighbors("H", ["peri"], [], []),
        "peri": recall_assembly.Neighbors("H", [], [], []),
        "H": recall_assembly.Neighbors(None, [], [], []),
    }
    monkeypatch.setattr(
        recall_arms, "_driver_neighbors",
        lambda p: neighbors.get(p, recall_assembly.Neighbors(None, [], [], [])),
    )
    monkeypatch.setattr(recall_arms, "_assembly_body", lambda p: f"# {p}\n{p} body")
    monkeypatch.setattr(
        recall_arms.product, "_read_dated_body",
        lambda p, origin="vault": (
            ("", "superseded by Peri v2", f"# {p}\n{p} body") if p == "peri"
            else ("", None, f"# {p}\n{p} body")
        ),
    )

    out = recall_arms._assemble_blocks(list(seeds), "q")
    folded = next(b for b in out if b.path == "head")
    assert folded.contested is not None
    assert "wrong date" in folded.contested
    assert "superseded by Peri v2" in folded.contested
    assert "contested: " in perception.Perception(query="q", blocks=[folded]).render()


def test_assembled_block_stays_uncontested_when_nothing_is_flagged(monkeypatch):
    from evals import recall_arms, recall_assembly
    from silica.kernel.recall import perception

    seeds = [perception.NoteBlock(path="a", date="", evidence="",
                                  body="# A\nx", excerpt="# A\nx")]
    monkeypatch.setattr(recall_arms, "_driver_neighbors",
                        lambda p: recall_assembly.Neighbors(None, [], [], []))
    out = recall_arms._assemble_blocks(list(seeds), "q")
    assert len(out) == 1
    assert out[0].contested is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
