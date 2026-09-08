# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""The embeddings extension: one vector per document from any OpenAI-compatible
`/v1/embeddings` endpoint, for the dense leg of `silica_search(hybrid=True)`.

Off until `SILICA_EMBEDDING_BASE_URL` names an endpoint. The vectors live in
`embed.json` beside the lexical index and refresh by mtime. A document is
embedded as its title plus its first `_HEAD_CHARS` characters: what the
model sees is the abstract and the opening, which is what a paraphrased
question most often paraphrases.

ponytail: pure-python cosine over a dict of float lists. Fine to a few
thousand documents; past that, numpy matrices are the upgrade.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import orjson

from silica.config import CONFIG
from silica.kernel.recall import paths as _paths

_HEAD_CHARS = 4000
_BATCH = 16


def enabled() -> bool:
    return bool((CONFIG.embedding_base_url or "").strip())


def _store_path() -> Path:
    return _paths.index_dir() / "embed.json"


def load_store() -> dict:
    try:
        return orjson.loads(_store_path().read_bytes())
    except (OSError, ValueError):
        return {"model": "", "vectors": {}, "stamps": {}, "built_at": None}


def embed_texts(texts: list[str]) -> list[list[float]]:
    """One vector per text, in order. Raises on a refused or unreachable endpoint."""
    import httpx

    base = CONFIG.embedding_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {CONFIG.embedding_api_key}"} if CONFIG.embedding_api_key else {}
    out: list[list[float]] = []
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)) as client:
        for i in range(0, len(texts), _BATCH):
            r = client.post(f"{base}/embeddings", headers=headers,
                            json={"model": CONFIG.embedding_model, "input": texts[i:i + _BATCH]})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def build(notes: list[tuple[str, float, str]], *, rebuild: bool = False) -> dict:
    """Embed the (rel path, mtime, text) documents whose stamp changed.
    Returns {docs, embedded, model}."""
    store = {"model": CONFIG.embedding_model, "vectors": {}, "stamps": {}, "built_at": None} if rebuild else load_store()
    if store.get("model") != CONFIG.embedding_model:
        store = {"model": CONFIG.embedding_model, "vectors": {}, "stamps": {}, "built_at": None}
    todo = [(rel, mt, text) for rel, mt, text in notes if store["stamps"].get(rel) != mt]
    live = {rel for rel, _mt, _t in notes}
    for gone in [p for p in store["stamps"] if p not in live]:
        store["stamps"].pop(gone, None)
        store["vectors"].pop(gone, None)
    if todo:
        texts = [f"{Path(rel).stem}\n{text[:_HEAD_CHARS]}" for rel, _mt, text in todo]
        for (rel, mt, _t), vec in zip(todo, embed_texts(texts)):
            store["vectors"][rel] = vec
            store["stamps"][rel] = mt
    store["built_at"] = time.time()
    _paths.atomic_write_bytes(_store_path(), orjson.dumps(store))
    return {"docs": len(store["vectors"]), "embedded": len(todo), "model": store["model"]}


def dense_candidates(query: str, k: int = 20) -> list[tuple[str, float]]:
    """(rel path, cosine) best first over the stored vectors; [] when none."""
    store = load_store()
    if not store["vectors"]:
        return []
    q = embed_texts([query])[0]
    scored = sorted(((rel, _cosine(q, v)) for rel, v in store["vectors"].items()), key=lambda kv: -kv[1])
    return scored[:k]
