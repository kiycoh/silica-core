# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Source files in the index, one unit per symbol: the search ranks units
beside notes and cites the symbol and its lines; the read serves a symbol by
name; an edit invalidates the file's units; the switch off keeps code out."""
from __future__ import annotations

import pytest

from silica.kernel.code.codeunits import units
from silica.kernel.recall.lexical import _tokens

SRC = '''"""The store."""
import os

LIMIT = 5

# what a profile holds
class UserStore:
    """Users on disk."""
    root = "/tmp"

    def get_user_profile(self, uid):
        """Fetch one profile by id."""
        return os.path.join(self.root, uid)

    def purge(self):
        pass

def unrelated():
    return LIMIT

registry.add(UserStore)
'''


def test_units_are_symbols_residue_and_windows():
    got = [(title, part.strip().splitlines()[0]) for _off, part, title in units("a.py", SRC)]
    assert got == [("", '"""The store."""'), ("LIMIT", "LIMIT = 5"),
                   ("UserStore", "# what a profile holds"),  # the comment above the class belongs to it
                   ("UserStore.get_user_profile", "def get_user_profile(self, uid):"),
                   ("UserStore.purge", "def purge(self):"), ("unrelated", "def unrelated():"),
                   ("", "registry.add(UserStore)")]
    long = "def f():\n" + "    x = 1\n" * 200
    parts = units("b.py", long)
    assert [t for _o, _p, t in parts] == ["f", "f", "f"] and parts[1][0] == len("def f():\n" + "    x = 1\n" * 79)
    assert [t for _o, _p, t in units("c.rs", "fn a() {}\n" * 200)] == [""] * 3  # no parser: windows


def test_identifiers_are_indexed_whole_and_in_pieces():
    t = _tokens("getUserProfile best_window_spans HTTPServer fget")
    for term in ("getuserprofile", "get", "user", "profile", "best_window_spans", "best", "window", "spans",
                 "httpserver", "http", "server", "fget"):
        assert term in t, term


@pytest.fixture
def root(tmp_path, monkeypatch):
    from silica.config import CONFIG
    from silica.kernel.recall import paths

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "store.py").write_text(SRC, encoding="utf-8")
    (tmp_path / "pkg" / "big.json").write_text("[" + "1," * 300000 + "1]", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Profiles\n\nA user profile is fetched by id from the store.\n", encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "index_code", True)
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=tmp_path / ".idx": _d)
    import silica.core as core
    core._section_cache.clear()
    return core


def test_search_cites_the_symbol_and_its_lines(root):
    r = root.search("getUserProfile")
    top = r["hits"][0]
    assert top["path"] == "pkg/store.py" and top["section"] == "UserStore.get_user_profile"
    assert top["span"] == [11, 13] and 11 <= top["line"] <= 13
    assert "get_user_profile" in top["window"]
    assert r["documents"][0]["path"] == "pkg/store.py" and r["documents"][0]["line"] == 11
    assert r["scope"]["docs"] == 2  # two files, whatever the number of units
    # the prose query reaches the method through its words, and the note beside it
    paths = {(h["path"], h["section"]) for h in root.search("fetch a user profile by id")["hits"]}
    assert ("pkg/store.py", "UserStore.get_user_profile") in paths and ("notes.md", "Profiles") in paths
    files = root.files()
    by = {f["path"]: f for f in files["files"]}
    assert by["pkg/store.py"]["status"] == "indexed" and by["pkg/big.json"]["status"] == "excluded"


def test_read_serves_a_symbol_by_name(root):
    r = root.read("pkg/store.py", section="UserStore.get_user_profile")
    assert (r["start"], r["end"]) == (11, 13) and r["text"].startswith("    def get_user_profile")
    assert {h["title"] for h in r["outline"]} >= {"UserStore", "UserStore.get_user_profile", "unrelated", "LIMIT"}


def test_an_edit_replaces_the_units(root, tmp_path):
    assert root.search("purge")["hits"][0]["section"] == "UserStore.purge"
    import os
    p = tmp_path / "pkg" / "store.py"
    p.write_text(SRC.replace("def purge(self):\n        pass\n", "def wipe(self):\n        pass\n"), encoding="utf-8")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    r = root.search("purge")
    assert r["hits"] == [] and r["terms_absent"] == ["purge"]
    assert root.search("wipe")["hits"][0]["section"] == "UserStore.wipe"
    from silica.kernel.recall.lexical import get_store
    assert all(not k.startswith("pkg/store.py#") or k in root._load_meta()["units"]["pkg/store.py"]
               for k in get_store(root.INDEX).paths())


def test_the_switch_off_keeps_code_out(root, monkeypatch):
    from silica.config import CONFIG
    monkeypatch.setattr(CONFIG, "index_code", False)
    r = root.search("getUserProfile")  # the note still answers through the words of the name
    assert {h["path"] for h in r["hits"]} == {"notes.md"} and r["scope"]["docs"] == 1
