# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""EpisodicStore on-disk format: npz-packed vectors, legacy JSON read forever.

The inline-float-list format hit its own upgrade condition (episodic.py's vec
comment): a real store at 1067 facts weighed 59 MB, 99.5% of it vectors printed
as decimal text. Vectors now ride a float32 npz block like the note stores;
everything else stays JSON inside the archive's meta entry.
"""
from __future__ import annotations

import json

import pytest

from silica.kernel.recall.episodic import EpisodicStore, Fact


def _fact(i: int, dim: int = 8, vec: bool = True) -> Fact:
    return Fact(
        id=f"f{i:04d}",
        key=f"topic key {i}",
        text=f"fact number {i}",
        first_seen="2026-08-01",
        last_seen="2026-08-17",
        runs=[f"run{i}"],
        vec=[float(i) + j / 10 for j in range(dim)] if vec else None,
    )


@pytest.fixture
def store(tmp_path):
    return EpisodicStore(path=tmp_path / "episodic.json")


def test_roundtrip_preserves_everything(store, tmp_path):
    store.facts = [_fact(1), _fact(2, vec=False), _fact(3)]
    store.next_id = 4
    store.lang = "italian"
    store.save()

    loaded = EpisodicStore(path=tmp_path / "episodic.json")
    assert loaded.next_id == 4
    assert loaded.lang == "italian"
    # Vectors ride float32 on disk (accepted loss, same as EmbedStore);
    # everything else roundtrips exactly.
    for got, want in zip(loaded.facts, store.facts):
        g, w = got.model_dump(), want.model_dump()
        gv, wv = g.pop("vec"), w.pop("vec")
        assert g == w
        assert gv == pytest.approx(wv, rel=1e-6) if wv is not None else gv is None
    # None vec survives as None, not as an empty list
    assert loaded.facts[1].vec is None


def test_on_disk_format_is_npz(store):
    store.facts = [_fact(1)]
    store.save()
    assert store.path.read_bytes()[:2] == b"PK"


def test_vectors_are_binary_not_decimal_text(store):
    dim = 256
    store.facts = [_fact(i, dim=dim) for i in range(20)]
    # Realistic embedder components print ~19 decimal chars each; the _fact
    # defaults ("1.1") would make the legacy baseline artificially small.
    for i, f in enumerate(store.facts):
        f.vec = [((i * dim + j) * 0.123456789) % 1.0 - 0.5 for j in range(dim)]
    store.save()
    packed = store.path.stat().st_size
    legacy = len(json.dumps(
        {"facts": [f.model_dump() for f in store.facts]}, ensure_ascii=False
    ).encode("utf-8"))
    # float32 payload (4 B/component) vs ~20 chars of decimal text per component
    assert packed < legacy / 3


def test_legacy_json_store_still_loads(tmp_path):
    path = tmp_path / "episodic.json"
    legacy_doc = {
        "schema_version": 1,
        "next_id": 7,
        "lang": "english",
        "facts": [json.loads(_fact(1).model_dump_json())],
    }
    path.write_text(json.dumps(legacy_doc), encoding="utf-8")

    loaded = EpisodicStore(path=path)
    assert loaded.next_id == 7
    assert loaded.lang == "english"
    assert loaded.facts[0].vec == pytest.approx(_fact(1).vec)

    # And the next save migrates it forward to the binary format.
    loaded.save()
    assert path.read_bytes()[:2] == b"PK"


def test_corrupt_file_is_quarantined_and_store_starts_empty(tmp_path):
    path = tmp_path / "episodic.json"
    path.write_bytes(b"PK\x03\x04 not actually a zip")
    loaded = EpisodicStore(path=path)
    assert loaded.facts == []
    assert loaded.next_id == 1
    assert not path.exists()  # quarantined away


def test_float32_rounding_is_the_only_loss(store, tmp_path):
    store.facts = [_fact(1, dim=4)]
    store.facts[0].vec = [0.123456789, 1e-8, -2.5, 1024.75]
    store.save()
    loaded = EpisodicStore(path=tmp_path / "episodic.json")
    assert loaded.facts[0].vec == pytest.approx(store.facts[0].vec, rel=1e-6)
