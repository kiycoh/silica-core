# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""`silica mcp --retrieval local-hybrid`: potion in-process, the index and its
vectors built in the background at start, lexical search meanwhile."""
import threading

import pytest

NOTE = "# Compaction\n\nLeveled compaction merges sorted runs level by level.\n"


def fake_embed(texts):
    return [[1.0, 0.0] if "compaction" in t.lower() else [0.0, 1.0] for t in texts]


@pytest.fixture
def root(tmp_path, monkeypatch):
    from silica import embeddings
    from silica.config import CONFIG
    from silica.kernel.recall import paths

    (tmp_path / "notes.md").write_text(NOTE, encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "embedding_model", "model2vec/fake")
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setattr(embeddings, "_static_model", lambda: object())  # the warm-up loads it; no Hub here
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=tmp_path / ".idx": _d)
    import silica.core as core
    core._section_cache.clear()
    return core


def test_local_hybrid_binds_potion_and_drops_the_endpoint(monkeypatch):
    from silica import embeddings
    from silica.config import CONFIG
    monkeypatch.setattr(CONFIG, "embedding_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(CONFIG, "embedding_model", "nomic-embed-text")
    embeddings.set_retrieval("local-hybrid")
    assert CONFIG.embedding_model == embeddings.POTION and CONFIG.embedding_base_url == ""
    assert embeddings.enabled() and embeddings.remote_host() is None
    assert embeddings.warmup_state() == {"retrieval": "local-hybrid", "state": "off"}


def test_local_hybrid_keeps_a_users_static_model(monkeypatch):
    from silica import embeddings
    from silica.config import CONFIG
    monkeypatch.setattr(CONFIG, "embedding_model", "model2vec/minishlab/potion-base-8M")
    embeddings.set_retrieval("local-hybrid")
    assert CONFIG.embedding_model == "model2vec/minishlab/potion-base-8M"


def test_search_is_lexical_and_says_warming_while_the_vectors_build(root, monkeypatch):
    from silica import embeddings
    embeddings.set_retrieval("local-hybrid")
    gate, real = threading.Event(), root.build_index

    def gated(**kw):
        if kw.get("embed"):
            gate.wait(5)
        return real(**kw)

    monkeypatch.setattr(root, "build_index", gated)
    t = embeddings.warm_up()
    r = root.silica_search("leveled compaction")
    assert r["dense"]["state"] == "warming" and r["hits"][0]["path"] == "notes.md"
    gate.set()
    t.join(10)
    r = root.silica_search("leveled compaction")
    assert r["dense"]["state"] == "ready" and r["dense"]["docs"] == 1


def test_a_failed_warm_up_is_reported_and_the_search_stays_lexical(root, monkeypatch):
    from silica import embeddings
    embeddings.set_retrieval("local-hybrid")

    def refused(texts):
        raise RuntimeError("no model on this machine")

    monkeypatch.setattr(embeddings, "embed_texts", refused)
    embeddings.warm_up().join(10)
    r = root.silica_search("leveled compaction")
    assert r["dense"]["state"] == "failed" and "no model on this machine" in r["dense"]["hint"]
    assert r["hits"][0]["path"] == "notes.md"


def test_configure_retrieval_starts_the_warm_up_for_the_plugin(root):
    from silica import embeddings
    from silica.ui.mcp import configure_retrieval
    assert configure_retrieval("lexical") is None and embeddings.warmup_state()["retrieval"] == "lexical"
    t = configure_retrieval("local-hybrid")
    t.join(10)
    assert embeddings.warmup_state() == {"retrieval": "local-hybrid", "state": "ready"}


def test_the_cli_takes_the_retrieval_flag():
    from silica.cli import _parser
    assert _parser().parse_args(["mcp"]).retrieval == "lexical"
    assert _parser().parse_args(["mcp", "--retrieval", "local-hybrid"]).retrieval == "local-hybrid"


def test_search_serves_the_committed_index_while_another_process_embeds(root):
    from silica import embeddings
    root.build_index()  # the lexical index another server committed
    embeddings.set_retrieval("local-hybrid")
    with root._paths.index_lock(root._paths.index_dir() / "build"):  # that server, mid-embedding
        t = embeddings.warm_up()
        out: dict = {}
        s = threading.Thread(target=lambda: out.update(root.silica_search("leveled compaction")), daemon=True)
        s.start()
        s.join(3)
        assert not s.is_alive(), "the search waited on the other process's build"
        assert out["dense"]["state"] == "warming" and out["hits"][0]["path"] == "notes.md"
    t.join(10)
    assert embeddings.warmup_state()["state"] == "ready"


def test_ready_means_the_query_model_is_loaded(root, monkeypatch):
    from silica import embeddings
    embeddings.set_retrieval("local-hybrid")
    embeddings.warm_up().join(10)  # the vectors are on disk now
    assert embeddings.warmup_state()["state"] == "ready"
    loads: list[int] = []
    monkeypatch.setattr(embeddings, "_static_model", lambda: loads.append(1))
    embeddings.set_retrieval("local-hybrid")
    embeddings.warm_up().join(10)  # nothing to embed: the model is loaded before `ready` all the same
    assert embeddings.warmup_state()["state"] == "ready" and loads == [1]


def test_a_missing_model_fails_the_warm_up_not_the_first_search(root, monkeypatch):
    from silica import embeddings
    embeddings.set_retrieval("local-hybrid")
    embeddings.warm_up().join(10)

    def missing():
        raise RuntimeError("model2vec models need the [dense] extra")

    monkeypatch.setattr(embeddings, "_static_model", missing)
    embeddings.set_retrieval("local-hybrid")
    embeddings.warm_up().join(10)
    assert embeddings.warmup_state() == {"retrieval": "local-hybrid", "state": "failed",
                                         "hint": "model2vec models need the [dense] extra"}
