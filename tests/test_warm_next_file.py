"""Cross-file prefetch warm-up (boundary stall lever, 2026-08-16).

The first distill of every file used to run inline (~40-65s exposed per file
on the library batches). warm_next_file preps the next uncommitted file early
(recon → payload assembly → salience) and attaches its chunks so the distill
prefetch window can cross the file boundary; the per-file states then
fast-path through their warm guards at their normal position in the FSM.
"""
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from silica.router.states import setup as st


def _recon_res(name="Inbox/f1.md"):
    return {"file": name, "collisions": [], "new_concepts": ["warm concept"]}


def _payload_res(name="Inbox/f1.md"):
    return {"chunks": [{"schema_version": 1,
                        "batches": [{"inbox_file": name,
                                     "concepts": [{"name": "warm concept",
                                                   "inbox_excerpt": "text"}]}]}]}


def _warm_stub(n_files=2, cur_fi=0):
    fsm = SimpleNamespace()
    fsm.inbox_files = [f"Inbox/f{i}.md" for i in range(n_files)]
    fsm.inbox_file = fsm.inbox_files[cur_fi]
    fsm._current_file_idx = cur_fi
    fsm._next_uncommitted_file_idx = lambda start: start
    c0 = {"schema_version": 1,
          "batches": [{"inbox_file": fsm.inbox_files[0],
                       "concepts": [{"name": "c0"}]}]}
    fsm._chunks = [c0]
    fsm._chunk_flat_to_fi_ci = {0: (0, 0)}
    fsm._file_chunks = {0: {"source_file": fsm.inbox_files[0], "chunks": [c0]}}
    fsm.context = {"vault_graph_ctx": {}}
    fsm._file_content_hashes = []
    fsm.progress = SimpleNamespace(tasks=[], inputs={}, save=lambda: None)

    def _add_task(cap, task_id=None, depends_on=None, input_ref=None):
        t = SimpleNamespace(id=task_id or cap, capability_name=cap)
        fsm.progress.tasks.append(t)
        return t

    fsm.progress.add_task = _add_task
    fsm._make_tmp = lambda obj: (lambda p: (json.dump(obj, open(p, "w")), p)[1])(
        tempfile.mktemp(suffix=".json"))
    fsm._get_recipe_phase = lambda name: {"partition_if_over": 200}
    fsm._progress_note = MagicMock()
    fsm._transition_success = MagicMock()
    return fsm


def test_warm_next_file_attaches_chunks_and_marks():
    fsm = _warm_stub()
    with patch.object(st.orch, "silica_recon", return_value=_recon_res()) as rec, \
         patch.object(st.orch, "silica_payload", return_value=_payload_res()), \
         patch("silica.agent.providers.get_embedder_or_none", return_value=None):
        assert st.warm_next_file(fsm) is True
    rec.assert_called_once_with("Inbox/f1.md")
    assert len(fsm._chunks) == 2
    assert fsm._chunk_flat_to_fi_ci[1] == (1, 0)
    assert fsm._file_chunks[1]["source_file"] == "Inbox/f1.md"
    assert fsm.context["warm_recon_1"] == _recon_res()
    assert fsm.context.get("file_1_salience_done") is True
    assert any(t.id == "f1_c0_distill" for t in fsm.progress.tasks)
    # the FSM's own chunk cursor is untouched — file 0 is still being processed
    assert not hasattr(fsm, "_current_chunk_idx") or fsm._current_chunk_idx == 0


def test_warm_next_file_bails_on_recon_error():
    fsm = _warm_stub()
    with patch.object(st.orch, "silica_recon", return_value={"error": "boom"}):
        assert st.warm_next_file(fsm) is False
    assert len(fsm._chunks) == 1
    assert 1 not in fsm._file_chunks




def test_warm_next_file_bails_when_no_next_file():
    fsm = _warm_stub(n_files=1)
    with patch.object(st.orch, "silica_recon") as rec:
        assert st.warm_next_file(fsm) is False
    rec.assert_not_called()


def test_handle_recon_consumes_warm_result():
    fsm = _warm_stub(cur_fi=1)
    fsm.context["warm_recon_1"] = _recon_res()
    with patch.object(st.orch, "silica_recon") as rec:
        st.handle_recon(fsm)
    rec.assert_not_called()
    assert fsm.context["recon"] == [_recon_res()]
    fsm._transition_success.assert_called_once()


def test_handle_payload_skips_reassembly_for_warmed_file():
    fsm = _warm_stub(cur_fi=1)
    w0 = {"schema_version": 1,
          "batches": [{"inbox_file": "Inbox/f1.md", "concepts": [{"name": "b"}]}]}
    fsm._chunks.append(w0)
    fsm._chunk_flat_to_fi_ci[1] = (1, 0)
    fsm._file_chunks[1] = {"source_file": "Inbox/f1.md", "chunks": [w0]}
    fsm.context["warm_payload_1"] = {"warm": True}
    with patch.object(st.orch, "silica_payload") as pay:
        st.handle_payload(fsm)
    pay.assert_not_called()
    assert len(fsm._chunks) == 2  # no double append
    assert fsm._current_chunk_idx == 1
    assert fsm.context["payload"] == {"warm": True}
    fsm._transition_success.assert_called_once()


def test_handle_salience_skips_when_warm_marker():
    fsm = _warm_stub(cur_fi=1)
    fsm.context["file_1_salience_done"] = True
    with patch("silica.agent.providers.get_embedder_or_none") as emb:
        st.handle_salience(fsm)
    emb.assert_not_called()
    fsm._transition_success.assert_called_once()


def test_on_pipeline_end_advances_file_when_next_chunk_crosses():
    from silica.router.orchestrator import InjectorFSM
    fsm = SimpleNamespace(
        context={}, _txn=1, _pre_graph=1,
        _get_chunks_from_context_if_empty=lambda: None,
        _chunks=[{}, {}, {}, {}],
        _chunk_flat_to_fi_ci={0: (0, 0), 1: (0, 1), 2: (1, 0), 3: (1, 1)},
        _current_chunk_idx=1,
        _current_file_idx=0,
        _next_uncommitted_chunk_idx=lambda start: start,
        _advance_file_or_done=MagicMock(return_value=True),
        _has_collision_phase=True,
        state=None,
    )
    InjectorFSM._on_pipeline_end(fsm)
    fsm._advance_file_or_done.assert_called_once()
    # cursor untouched: the warmed PAYLOAD guard positions it for the new file
    assert fsm._current_chunk_idx == 1


def test_contain_chunk_failure_advances_file_when_next_chunk_crosses():
    from silica.router.orchestrator import InjectorFSM
    progress = MagicMock()
    progress.tasks = []
    fsm = SimpleNamespace(
        _current_chunk_idx=1,
        _current_file_idx=0,
        _chunk_flat_to_fi_ci={0: (0, 0), 1: (0, 1), 2: (1, 0)},
        _chunk_ctx={},
        progress=progress,
        context={},
        _run_inverses=[],
        _file_chunks={0: {"source_file": "f0", "chunks": [{}, {}]}},
        inbox_file="f0",
        _failed_phase_id=lambda: "",
        _get_chunks_from_context_if_empty=lambda: None,
        _chunks=[{}, {}, {}],
        _next_uncommitted_chunk_idx=lambda start: start,
        _advance_file_or_done=MagicMock(return_value=True),
        _has_collision_phase=True,
        state=None,
    )
    InjectorFSM._contain_chunk_failure(fsm)
    fsm._advance_file_or_done.assert_called_once()
    assert fsm._current_chunk_idx == 1
