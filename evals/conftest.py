# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Shared fixtures for the benchmark tests moved out of tests/eval/.

These files live outside the `tests/` tree (they are slow, not in the default
`testpaths`), so pytest's upward conftest discovery no longer reaches
tests/conftest.py — re-export the fixtures they still use.
"""
import pytest

from tests.conftest import tmp_vault  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_oracle_cache(monkeypatch, tmp_path):
    """Every eval test gets its own oracle cache dir. Without this, a stub
    reply frozen by one test is served to the next identical request, and a
    call_llm monkeypatch silently never fires."""
    import evals.oracle as oracle

    monkeypatch.setattr(oracle, "_CACHE_DIR", tmp_path / "oracle")


@pytest.fixture(autouse=True)
def _isolate_recall_weights(monkeypatch, tmp_path):
    """Every eval test gets its own recall-weight store.

    These files sit outside `tests/`, so conftest discovery never reaches the
    autouse isolation fixtures there and `index_dir()` resolves through the
    developer's configured vault. Two eval tests that each bump a weight then
    read one another's total, and the totals accrue in the real
    ~/.silica/index across runs — measured: a test asserting 1.0 read 2.0 in a
    full-suite run and passed alone.
    """
    from evals import recall_weights as rw

    monkeypatch.setattr(rw, "_store_path", lambda: tmp_path / "recall_weights.json")


@pytest.fixture(autouse=True)
def _isolate_cooccurrence_index(monkeypatch, tmp_path):
    """Same reason, and the one that still wrote real data: the eval loaders
    build indexes, and the co-occurrence refresh has no embedder gate, so each
    run left a real ~/.silica/index/<tmp-vault-digest>/cooccurrence.json
    behind. Keyed by the tmp path, they never collided — they just accumulated,
    measured at 21 fresh namespaces per eval run. Mirrors the tests/ fixture."""
    import silica.kernel.recall.cooccurrence as cooc_mod

    monkeypatch.setattr(cooc_mod, "_index_path",
                        lambda: tmp_path / "cooccurrence_index.json")


@pytest.fixture(autouse=True)
def _isolate_episodic_store(monkeypatch, tmp_path):
    """The distill state captures ephemerals into the DEFAULT episodic store,
    so an eval driving ingest wrote the developer's real
    ~/.silica/index/<digest>/episodic.json. Tests that need a store pass an
    explicit path; this only redirects the default."""
    import silica.kernel.recall.episodic as ep_mod

    monkeypatch.setattr(ep_mod, "store_path",
                        lambda: tmp_path / "episodic_default.json")


@pytest.fixture(autouse=True)
def _isolate_sync_stamps(monkeypatch, tmp_path):
    """The invocation-time index sweep records mtimes in a real stamp file.
    Only the path is redirected, not `index_sweep` itself: the tests/ twin
    disables the sweep because it would stat stub drivers, but an eval runs the
    real loaders and the sweep is part of what it measures."""
    import silica.kernel.recall.sync as sync_mod

    monkeypatch.setattr(sync_mod, "_stamps_path", lambda: tmp_path / "sync_stamps.json")
    monkeypatch.setattr(sync_mod, "_last_sweep", 0.0)


@pytest.fixture(autouse=True)
def _isolate_distill_cache(monkeypatch, tmp_path):
    """Same reason: an eval test driving the distiller with the cache armed
    would otherwise read and write the developer's real cache, and replay one
    arm's reply into another."""
    import silica.kernel.distill_cache as cache_mod

    monkeypatch.delenv("SILICA_DISTILL_CACHE", raising=False)
    monkeypatch.setattr(cache_mod, "cache_root", lambda: tmp_path / "distill_cache")
