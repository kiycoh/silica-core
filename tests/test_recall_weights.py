"""Tests for the recall-outcome weight store (evals/recall_weights.py)."""
from __future__ import annotations

import pytest

from silica.config import CONFIG
from evals import recall_weights
from silica.kernel.recall import paths


def _bind(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_SILICA_HOME", tmp_path)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path), raising=False)


def test_ranking_none_when_store_missing(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    assert recall_weights.ranking() is None


def test_bump_then_ranking_sorted_desc(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    recall_weights.bump(["sessions/s1"])
    recall_weights.bump(["sessions/s2"])
    recall_weights.bump(["sessions/s2"])
    assert recall_weights.ranking() == [("sessions/s2", 2.0), ("sessions/s1", 1.0)]


def test_bump_normalizes_and_dedups_within_one_call(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    recall_weights.bump(["sessions/s3", "sessions/s3.md"])
    assert recall_weights.ranking() == [("sessions/s3", 1.0)]


def test_bump_empty_list_is_noop(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    recall_weights.bump([])
    assert recall_weights.ranking() is None


@pytest.mark.parametrize("bad", ['["a", "b"]', '"garbage"', '42', '{"x": "not-a-number"}'])
def test_ranking_none_on_corrupt_store(tmp_path, monkeypatch, bad):
    _bind(tmp_path, monkeypatch)
    store_path = recall_weights._store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(bad, encoding="utf-8")
    assert recall_weights.ranking() is None
