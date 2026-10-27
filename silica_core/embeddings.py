# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The embeddings extension: one vector per SECTION, from an OpenAI-compatible
`/v1/embeddings` endpoint or from a static model2vec model (the `[dense]`
extra), for the dense leg of `silica_search`, which runs whenever the
vectors are there (`dense` in the reply says so, or why not).

Off until `SILICA_EMBEDDING_BASE_URL` names an endpoint or
`SILICA_EMBEDDING_MODEL` names `model2vec/<hf id>`. The vectors live beside
the lexical index: `embed.json` says which sections of which document sit
at which row, `embed.q8` holds the rows as int8 codes (see `_quantize`),
mapped rather than read where the platform allows (see `_map`). A section
is embedded as `<stem> › <heading>` plus its first `_UNIT_CHARS` characters,
so the dense leg lands on the passage, not on the document's opening —
measured 2026-09-09: one vector per document found the paper and handed
back its introduction. `SILICA_EMBEDDING_DOC_PREFIX` goes in front of every
section and `SILICA_EMBEDDING_QUERY_PREFIX` in front of the query, for a
model that wants them (nomic: `search_document: ` / `search_query: `);
neither is added on its own.

Text leaves the machine only for a non-loopback endpoint, and only after
`silica-core index --embed --allow-remote` granted that host once (kept in
`~/.silica/embedding_consent.json`).

ponytail: the dot products run in pure Python unless numpy is importable
(the `[dense]` extra brings it): 5k sections × 768 is ~0.2 s without it.
"""
from __future__ import annotations

import importlib
import math
import mmap
import os
import threading
import time
from array import array
from pathlib import Path
from typing import Any, NamedTuple, Sequence
from urllib.parse import urlsplit

import orjson

from silica_core.config import CONFIG
from silica_core.kernel.recall import paths as _paths

_UNIT_CHARS = 6000
_BATCH = 16
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MODEL2VEC_PREFIX = "model2vec/"
_M2V = None
# Scoring converts the int8 rows to float32 one block of this many bytes at a
# time, into one reused buffer: int8 has no BLAS, and converting the whole
# matrix per query cost 10x the float32 product where a cache-sized block
# costs 2.5x (55,576 x 512: float32 3.0 ms, whole 30 ms, 512 KiB blocks
# 7.9 ms; 389,529 rows: 15.8 / 148 / 46 ms; measured 2026-10-27).
_BLOCK_BYTES = 1 << 19


class _Rows(NamedTuple):
    """The section vectors: row i is `scales[i] * codes[i*dim:(i+1)*dim]`, a
    unit vector. Views over the mapped file where there is one."""
    scales: memoryview[float]  # float32, one per row
    codes: memoryview  # int8, rows x dim
    dim: int


_EMPTY = _Rows(memoryview(b"").cast("f"), memoryview(b"").cast("b"), 0)
_CACHE: tuple[tuple[int, int], dict, _Rows] | None = None  # (stamp of embed.json, meta, rows)


# ---------------------------------------------------------------------------
# what is configured, and whether text may leave the machine
# ---------------------------------------------------------------------------

def _local_model() -> bool:
    return (CONFIG.embedding_model or "").startswith(MODEL2VEC_PREFIX)


def enabled() -> bool:
    return _local_model() or bool((CONFIG.embedding_base_url or "").strip())


# ---------------------------------------------------------------------------
# the retrieval mode: `silica-core mcp --retrieval local-hybrid` is the plugin's
# ---------------------------------------------------------------------------

POTION = "model2vec/minishlab/potion-retrieval-32M"
# `state` is the warm-up: off (never started), warming, ready, failed (`hint`)
_MODE: dict[str, Any] = {"retrieval": "lexical", "state": "off"}
_LEXICAL = threading.Event()  # the warm-up's lexical phase is on disk


def set_retrieval(mode: str) -> None:
    """`lexical` (the default: whatever SILICA_EMBEDDING_* names, or nothing)
    or `local-hybrid`: a static model in this process, potion unless the
    environment names another model2vec model, and no endpoint, so no text
    ever leaves the machine. Resets the warm-up."""
    if mode not in ("lexical", "local-hybrid"):
        raise ValueError(f"retrieval must be lexical or local-hybrid, not {mode!r}")
    _MODE.clear()
    _MODE.update(retrieval=mode, state="off")
    _LEXICAL.clear()
    if mode == "local-hybrid":
        CONFIG.embedding_base_url = ""
        if not _local_model():
            CONFIG.embedding_model = POTION


def warmup_state() -> dict[str, Any]:
    return dict(_MODE)


def lazy_embed() -> bool:
    """Whether a refresh a search makes should embed too: local-hybrid once
    the warm-up succeeded, so the model is loaded and nothing leaves."""
    return _MODE["retrieval"] == "local-hybrid" and _MODE["state"] == "ready"


def wait_lexical(timeout: float | None = None) -> None:
    """Block until the warm-up's lexical phase is on disk, or `timeout`; at
    once when no warm-up runs."""
    if _MODE["state"] == "warming":
        _LEXICAL.wait(timeout)


def warm_up() -> threading.Thread:
    """Build the index, then its vectors, in a background thread: lexical
    search serves as soon as the first phase lands, `dense` says `warming`
    until the second does, `failed` with the reason if either breaks."""
    import silica_core.core as core

    def run() -> None:
        try:
            core.build_index()
            _LEXICAL.set()
            if _local_model():
                _static_model()  # loaded, or fetched, now: `ready` promises the first query no wait and no surprise
            out = core.build_index(embed=True)
        except Exception as e:
            _MODE.update(state="failed", hint=str(e))
        else:
            if "error" in out:
                _MODE.update(state="failed", hint=out["error"]["hint"])
            else:
                _MODE.update(state="ready")
        finally:
            _LEXICAL.set()

    _MODE.pop("hint", None)
    _MODE["state"] = "warming"
    t = threading.Thread(target=run, name="silica-warm-up", daemon=True)
    t.start()
    return t


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
    """One float32 scale per row, then every row's int8 codes."""
    return _paths.index_dir() / "embed.q8"


def _legacy_rows_path() -> Path:
    """float32 rows, the format until 2026-10-27: still read, and written
    back as `embed.q8` by the next build without re-embedding anything.
    Until then each process converts them on load: 0.55 s with numpy, 7.8 s
    without, at 55,576 x 512 (measured 2026-10-27)."""
    return _paths.index_dir() / "embed.f32"


def _map(path: Path) -> memoryview:
    """The file's bytes, mapped on POSIX: every server on this root shares one
    copy in the page cache instead of holding its own (two servers per
    session, see tests/test_root_gate.py), and a build's os.replace leaves a
    mapping of the old file valid. Read on Windows, where a mapped file
    cannot be replaced and the next build's atomic write would fail."""
    with open(path, "rb") as f:
        if os.name == "nt" or os.fstat(f.fileno()).st_size == 0:  # mmap refuses an empty file
            return memoryview(f.read())
        return memoryview(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ))


def _quantize(vecs: Sequence[Sequence[float]]) -> tuple[array, bytes]:
    """One float32 scale per vector and its int8 codes: each component scaled
    so the largest is ±127, and the scale is 1/|codes|, so scale × codes is
    the unit vector and its dot product with a unit query is the cosine.
    4x smaller than float32; measured 2026-10-27 on 55,576 potion sections,
    0.9905 of the float32 top-10 (fp16 kept 0.998 at half the size, half the
    dimensions 0.86). numpy when it is there, the same arithmetic in doubles
    when not: both round half to even, and the sum of squared integer codes
    is exact in any order, so the two produce the same bytes."""
    if not vecs:
        return array("f"), b""
    try:
        np: Any = importlib.import_module("numpy")
    except ImportError:
        scales, codes = array("f"), array("b")
        for v in vecs:
            peak = max(map(abs, v))
            c = [round(x * 127 / peak) for x in v] if peak else [0] * len(v)
            n = math.sqrt(sum(x * x for x in c))
            scales.append(1 / n if n else 0.0)  # a zero vector stays zero: cosine 0 with everything
            codes.extend(c)
        return scales, codes.tobytes()
    m = np.asarray(vecs, dtype=np.float64)
    peak = np.abs(m).max(axis=1, keepdims=True)
    c = np.round(m * 127 / np.where(peak == 0, 1.0, peak))
    n = np.sqrt((c * c).sum(axis=1))
    s = np.divide(1.0, n, out=np.zeros_like(n), where=n != 0)
    return array("f", s.astype(np.float32).tobytes()), c.astype(np.int8).tobytes()


def _from_float32(raw: memoryview, n: int, dim: int) -> _Rows:
    floats = raw.cast("f")
    scales, codes = _quantize([floats[i * dim:(i + 1) * dim] for i in range(n)])
    return _Rows(memoryview(scales), memoryview(codes).cast("b"), dim)


def load() -> tuple[dict, _Rows] | None:
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
        n, dim = int(meta.get("rows", 0)), int(meta.get("dim", 0))
        rows: _Rows | None = None
        if meta.get("codes") == "int8":
            raw = _map(_rows_path())
            if len(raw) == n * (4 + dim):
                rows = _Rows(raw[:4 * n].cast("f"), raw[4 * n:].cast("b"), dim)
        else:
            raw = _map(_legacy_rows_path())
            if len(raw) == 4 * n * dim:
                rows = _from_float32(raw, n, dim)
    except (OSError, ValueError, TypeError):
        return None
    if meta.get("unit") != "section" or not meta.get("docs") or rows is None:
        return None
    _CACHE = (stamp, meta, rows)
    return _current(meta, rows)


def _current(meta: dict, rows: _Rows) -> tuple[dict, _Rows] | None:
    """The store, or None when it was embedded by another model or under
    another document prefix: those vectors describe other text, and serving
    them beside this query's would rank noise (`silica-core index --embed` rebuilds)."""
    if meta.get("model") != CONFIG.embedding_model or meta.get("doc_prefix", "") != CONFIG.embedding_doc_prefix:
        return None
    return meta, rows


def build(units: list[tuple[str, float, list[tuple[int, str]]]], *, rebuild: bool = False) -> dict:
    """Embed the sections of every (rel, mtime, [(offset, text)]) document whose
    stamp changed; rows of unchanged documents are kept. Returns {docs,
    sections, embedded, model}."""
    loaded = None if rebuild else load()
    meta: dict
    if loaded:
        meta, rows = loaded
    else:  # nothing yet, or another model's or prefix's vectors: `load` said None
        meta, rows = {"dim": 0, "docs": {}}, _EMPTY
    dim: int = meta["dim"]
    # One dict lookup per document, not a scan of `units` for each: that was
    # O(docs²), 1.4 s at 8k documents and ~90 s at the 65k of ~/Documents
    # (measured 2026-10-27).
    stamp_of = {rel: mt for rel, mt, _secs in units}
    keep = {rel: d for rel, d in meta["docs"].items() if stamp_of.get(rel) == d["mtime"]}
    todo = [(rel, mt, secs) for rel, mt, secs in units if rel not in keep]
    texts, owners = [], []
    for rel, _mt, secs in todo:
        for off, text in secs:
            texts.append(CONFIG.embedding_doc_prefix + text)
            owners.append((rel, off))
    if loaded and not texts and len(keep) == len(meta["docs"]) and meta.get("codes") == "int8":
        # Nothing to embed and nothing gone: the store on disk is the one this
        # build would write. Every refresh a search makes lands here, and each
        # one rewrote the whole file (108 MB of float32 for silica-internal).
        return {"docs": len(keep), "sections": meta["rows"], "embedded": 0, "model": meta["model"]}
    vecs = embed_texts(texts) if texts else []
    if vecs:
        if dim and len(vecs[0]) != dim:  # the endpoint answers in another width: not the same model
            keep, dim = {}, 0
        dim = dim or len(vecs[0])
    new_scales, new_codes = _quantize(vecs)
    scales, codes, docs, row = array("f"), bytearray(), {}, 0
    for rel, d in keep.items():
        n = len(d["offsets"])
        scales.frombytes(rows.scales[d["row"]:d["row"] + n].cast("B"))
        codes += rows.codes[d["row"] * dim:(d["row"] + n) * dim].cast("B")
        docs[rel] = {"mtime": d["mtime"], "offsets": d["offsets"], "row": row}
        row += n
    by_doc: dict[str, list[tuple[int, int]]] = {}
    for i, (rel, off) in enumerate(owners):
        by_doc.setdefault(rel, []).append((off, i))
    for rel, mt, _secs in todo:
        vs = by_doc.get(rel, [])
        if not vs:
            continue
        docs[rel] = {"mtime": mt, "offsets": [off for off, _i in vs], "row": row}
        for _off, i in vs:
            scales.append(new_scales[i])
            codes += new_codes[i * dim:(i + 1) * dim]
        row += len(vs)
    meta = {"model": CONFIG.embedding_model, "doc_prefix": CONFIG.embedding_doc_prefix, "unit": "section",
            "codes": "int8", "dim": dim, "docs": docs, "rows": row, "built_at": time.time()}
    _paths.atomic_write_bytes(_rows_path(), scales.tobytes() + bytes(codes))
    _paths.atomic_write_bytes(_meta_path(), orjson.dumps(meta))
    _legacy_rows_path().unlink(missing_ok=True)
    return {"docs": len(docs), "sections": row, "embedded": len(texts), "model": meta["model"]}


def _scores(rows: _Rows, q: array) -> list[float]:
    """One cosine per row; numpy when it is there, plain Python when not.
    Imported by name so the type checker never opens numpy's stubs (they
    are written for 3.12+, this package is checked as 3.11)."""
    dim = rows.dim
    try:
        np: Any = importlib.import_module("numpy")
    except ImportError:
        # q first: float.__mul__ takes the int code as its argument, not as self
        return [s * sum(map(float.__mul__, q, rows.codes[i * dim:(i + 1) * dim])) for i, s in enumerate(rows.scales)]
    codes = np.frombuffer(rows.codes, dtype=np.int8).reshape(-1, dim)
    qv = np.frombuffer(q, dtype=np.float32)
    out = np.empty(len(codes), dtype=np.float32)
    step = max(1, _BLOCK_BYTES // (4 * dim))
    buf = np.empty((step, dim), dtype=np.float32)
    for i in range(0, len(codes), step):
        block = codes[i:i + step]
        b = buf[:len(block)]
        b[...] = block
        np.dot(b, qv, out=out[i:i + len(block)])
    return list((out * np.frombuffer(rows.scales, dtype=np.float32)).tolist())


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
        raise RuntimeError(f"the endpoint answered {len(q)} dimensions, the store holds {meta['dim']}: run `silica-core index --embed --rebuild`")
    scores = _scores(rows, q)
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
