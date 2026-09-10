# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The embeddings extension: one vector per SECTION, from an OpenAI-compatible
`/v1/embeddings` endpoint or from a static model2vec model (the `[dense]`
extra), for the dense leg of `silica_search`, which runs whenever the
vectors are there (`dense` in the reply says so, or why not).

Off until `SILICA_EMBEDDING_BASE_URL` names an endpoint or
`SILICA_EMBEDDING_MODEL` names `model2vec/<hf id>`. The vectors live beside
the lexical index: `embed.json` says which sections of which document sit
at which row, `embed.f32` holds the rows, unit length, float32. A section
is embedded as `<stem> › <heading>` plus its first `_UNIT_CHARS` characters,
so the dense leg lands on the passage, not on the document's opening —
measured 2026-09-09: one vector per document found the paper and handed
back its introduction. `SILICA_EMBEDDING_DOC_PREFIX` goes in front of every
section and `SILICA_EMBEDDING_QUERY_PREFIX` in front of the query, for a
model that wants them (nomic: `search_document: ` / `search_query: `);
neither is added on its own.

Text leaves the machine only for a non-loopback endpoint, and only after
`silica index --embed --allow-remote` granted that host once (kept in
`~/.silica/embedding_consent.json`).

ponytail: the dot products run in pure Python unless numpy is importable
(the `[dense]` extra brings it): 5k sections × 768 is ~0.2 s without it.
"""
from __future__ import annotations

import importlib
import math
import time
from array import array
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import orjson

from silica.config import CONFIG
from silica.kernel.recall import paths as _paths

_UNIT_CHARS = 6000
_BATCH = 16
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MODEL2VEC_PREFIX = "model2vec/"
_M2V = None
_CACHE: tuple[tuple[int, int], dict, array] | None = None  # (stamp of embed.json, meta, rows)


# ---------------------------------------------------------------------------
# what is configured, and whether text may leave the machine
# ---------------------------------------------------------------------------

def _local_model() -> bool:
    return (CONFIG.embedding_model or "").startswith(MODEL2VEC_PREFIX)


def enabled() -> bool:
    return _local_model() or bool((CONFIG.embedding_base_url or "").strip())


def remote_host() -> str | None:
    """The host the texts would go to, or None when nothing leaves the machine."""
    if _local_model():
        return None
    host = (urlsplit(CONFIG.embedding_base_url.strip()).hostname or "").lower()
    return None if host in _LOCAL_HOSTS else host or None


def _consent_path() -> Path:
    return _paths._SILICA_HOME / "embedding_consent.json"  # the home conftest rebinds per test


def consent_needed() -> str | None:
    """The remote host that has not been granted yet, or None."""
    host = remote_host()
    if not host:
        return None
    try:
        granted = orjson.loads(_consent_path().read_bytes()).get("hosts", [])
    except (OSError, ValueError):
        granted = []
    return None if host in granted else host


def grant(host: str) -> None:
    try:
        hosts = orjson.loads(_consent_path().read_bytes()).get("hosts", [])
    except (OSError, ValueError):
        hosts = []
    if host not in hosts:
        hosts.append(host)
    _consent_path().parent.mkdir(parents=True, exist_ok=True)
    _paths.atomic_write_bytes(_consent_path(), orjson.dumps({"hosts": hosts, "granted_at": time.time()}))


# ---------------------------------------------------------------------------
# the embedder
# ---------------------------------------------------------------------------

def _static_model():
    global _M2V
    if _M2V is None or _M2V[0] != CONFIG.embedding_model:
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError("model2vec models need the [dense] extra: uv tool install 'silica-core[dense]'") from e
        _M2V = (CONFIG.embedding_model, StaticModel.from_pretrained(CONFIG.embedding_model[len(MODEL2VEC_PREFIX):]))
    return _M2V[1]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """One vector per text, in order. Raises on a refused or unreachable endpoint."""
    if _local_model():
        return [[float(x) for x in v] for v in _static_model().encode(texts)]
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


def unit_text(stem: str, title: str, part: str) -> str:
    """What one section is embedded as: where it sits, then what it says."""
    return f"{stem} › {title}\n{part[:_UNIT_CHARS]}" if title else f"{stem}\n{part[:_UNIT_CHARS]}"


def _unit(vec: list[float]) -> array:
    n = math.sqrt(sum(x * x for x in vec))
    return array("f", [x / n for x in vec] if n else vec)


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

def _meta_path() -> Path:
    return _paths.index_dir() / "embed.json"


def _rows_path() -> Path:
    return _paths.index_dir() / "embed.f32"


def load() -> tuple[dict, array] | None:
    """(meta, rows) or None when nothing is embedded yet. Cached across calls
    while `embed.json` is unchanged: the rows are megabytes."""
    global _CACHE
    try:
        st = _meta_path().stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    if _CACHE is not None and _CACHE[0] == stamp:
        return _current(_CACHE[1], _CACHE[2])
    try:
        meta = orjson.loads(_meta_path().read_bytes())
        rows = array("f")
        rows.frombytes(_rows_path().read_bytes())
    except (OSError, ValueError):
        return None
    if meta.get("unit") != "section" or not meta.get("docs") or len(rows) != meta.get("rows", 0) * meta.get("dim", 0):
        return None
    _CACHE = (stamp, meta, rows)
    return _current(meta, rows)


def _current(meta: dict, rows: array) -> tuple[dict, array] | None:
    """The store, or None when it was embedded by another model or under
    another document prefix: those vectors describe other text, and serving
    them beside this query's would rank noise (`silica index --embed` rebuilds)."""
    if meta.get("model") != CONFIG.embedding_model or meta.get("doc_prefix", "") != CONFIG.embedding_doc_prefix:
        return None
    return meta, rows


def build(units: list[tuple[str, float, list[tuple[int, str]]]], *, rebuild: bool = False) -> dict:
    """Embed the sections of every (rel, mtime, [(offset, text)]) document whose
    stamp changed; rows of unchanged documents are kept. Returns {docs,
    sections, embedded, model}."""
    loaded = None if rebuild else load()
    meta: dict = loaded[0] if loaded else {}
    rows: array = loaded[1] if loaded else array("f")
    if not meta:
        meta, rows = {"dim": 0, "docs": {}}, array("f")  # nothing yet, or another model's or prefix's vectors: `load` said None
    dim: int = meta["dim"]
    keep = {rel: d for rel, d in meta["docs"].items()
            if any(rel == u[0] and d["mtime"] == u[1] for u in units)}
    todo = [(rel, mt, secs) for rel, mt, secs in units if rel not in keep]
    texts, owners = [], []
    for rel, _mt, secs in todo:
        for off, text in secs:
            texts.append(CONFIG.embedding_doc_prefix + text)
            owners.append((rel, off))
    vecs = [_unit(v) for v in embed_texts(texts)] if texts else []
    if vecs:
        if dim and len(vecs[0]) != dim:  # the endpoint answers in another width: not the same model
            keep, dim = {}, 0
        dim = dim or len(vecs[0])
    out_rows, docs, row = array("f"), {}, 0
    for rel, d in keep.items():
        n = len(d["offsets"])
        out_rows.extend(rows[d["row"] * dim:(d["row"] + n) * dim])
        docs[rel] = {"mtime": d["mtime"], "offsets": d["offsets"], "row": row}
        row += n
    by_doc: dict[str, list[tuple[int, array]]] = {}
    for (rel, off), vec in zip(owners, vecs):
        by_doc.setdefault(rel, []).append((off, vec))
    for rel, mt, _secs in todo:
        vs = by_doc.get(rel, [])
        if not vs:
            continue
        docs[rel] = {"mtime": mt, "offsets": [off for off, _v in vs], "row": row}
        for _off, vec in vs:
            out_rows.extend(vec)
        row += len(vs)
    meta = {"model": CONFIG.embedding_model, "doc_prefix": CONFIG.embedding_doc_prefix, "unit": "section",
            "dim": dim, "docs": docs, "rows": row, "built_at": time.time()}
    _paths.atomic_write_bytes(_rows_path(), out_rows.tobytes())
    _paths.atomic_write_bytes(_meta_path(), orjson.dumps(meta))
    return {"docs": len(docs), "sections": row, "embedded": len(texts), "model": meta["model"]}


def _scores(rows: array, dim: int, q: array) -> list[float]:
    """One dot product per row; numpy when it is there, plain Python when not.
    Imported by name so the type checker never opens numpy's stubs (they
    are written for 3.12+, this package is checked as 3.11)."""
    try:
        np: Any = importlib.import_module("numpy")
    except ImportError:
        n = len(rows) // dim
        return [sum(map(float.__mul__, rows[i * dim:(i + 1) * dim], q)) for i in range(n)]
    m = np.frombuffer(rows, dtype=np.float32).reshape(-1, dim)
    return list((m @ np.frombuffer(q, dtype=np.float32)).tolist())


def rank(query: str, live: dict[str, float]) -> tuple[list[tuple[str, float]], dict[tuple[str, int], float]]:
    """Documents best first by their best section's cosine, and every section's
    cosine keyed (rel, offset). A document whose file changed since it was
    embedded (`live` mtime differs) is left out: its vectors describe text
    that is no longer there. ([], {}) when nothing is embedded."""
    loaded = load()
    if loaded is None:
        return [], {}
    meta, rows = loaded
    q = _unit(embed_texts([CONFIG.embedding_query_prefix + query])[0])
    if len(q) != meta["dim"]:
        raise RuntimeError(f"the endpoint answered {len(q)} dimensions, the store holds {meta['dim']}: run `silica index --embed --rebuild`")
    scores = _scores(rows, meta["dim"], q)
    best: dict[str, float] = {}
    sections: dict[tuple[str, int], float] = {}
    for rel, d in meta["docs"].items():
        if live.get(rel) != d["mtime"]:
            continue
        for i, off in enumerate(d["offsets"]):
            s = scores[d["row"] + i]
            sections[(rel, off)] = s
            if s > best.get(rel, -2.0):
                best[rel] = s
    return sorted(best.items(), key=lambda kv: (-kv[1], kv[0])), sections
