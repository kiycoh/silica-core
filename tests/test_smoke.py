"""Smoke test — verify the tool registry and package imports work."""
from silica.tools import TOOLS


def test_tool_registry_loads():
    """Importing atomic tools should register them in the TOOLS dict."""
    import silica.tools.atomic  # noqa: F401
    assert len(TOOLS) > 0, "No tools registered after importing atomic module"




def test_tool_json_schema():
    """Each tool should produce a valid JSON schema."""
    import silica.tools.atomic  # noqa: F401
    for name, t in TOOLS.items():
        schema = t.json_schema()
        assert "function" in schema, f"{name} missing 'function' key"
        assert "name" in schema["function"], f"{name} missing function name"
        assert "parameters" in schema["function"], f"{name} missing parameters"


def test_config_loads():
    """Config singleton should load without errors."""
    from silica.config import CONFIG
    from silica.driver import driver_kind
    # model may legitimately be empty (fail-fast default — see test_config_failfast)
    assert driver_kind() == "fs"
    assert CONFIG.vault_path is not None


def test_driver_base_types():
    """Domain types should be importable."""
    from silica.driver.base import (
        NoteRef, NoteContent
    )
    ref = NoteRef(name="Test", path="test.md")
    assert ref.name == "Test"
    content = NoteContent(ref=ref, content="hello")
    assert content.content == "hello"







def test_inbox_indexing_and_external_reads(tmp_path):
    """Verify that files inside inbox_dir ARE indexed and searchable (staging is
    source material), and that external files can be read."""
    from silica.config import CONFIG
    from silica.driver.fs_backend import ObsidianFSBackend
    
    # Set up directories
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    
    inbox_dir = vault_dir / "Inbox"
    inbox_dir.mkdir()
    
    notes_dir = vault_dir / "notes"
    notes_dir.mkdir()
    
    # Write notes
    (notes_dir / "note1.md").write_text("Hello from Note 1", encoding="utf-8")
    (inbox_dir / "meeting_notes.md").write_text("Hello from Inbox Note", encoding="utf-8")
    
    # Save original config
    orig_inbox = CONFIG.inbox_dir
    orig_vault = CONFIG.vault_path
    
    CONFIG.inbox_dir = "Inbox"
    CONFIG.vault_path = str(vault_dir)
    
    try:
        backend = ObsidianFSBackend(vault_path=str(vault_dir))
        backend._ensure_index()
        
        # 1. Check that notes in Inbox are indexed
        assert any(ref.name == "note1" for ref in backend._notes.values())
        assert any(ref.name == "meeting_notes" for ref in backend._notes.values())

        # 2. Check list_files
        listed = [ref.name for ref in backend.list_files()]
        assert "note1" in listed
        assert "meeting_notes" in listed

        # 3. Check search_names
        searched_names = [ref.name for ref in backend.search_names("notes")]
        assert "meeting_notes" in searched_names

        # 4. Check search_context reaches inbox bodies
        hits = backend.search_context("Hello from Inbox Note")
        assert [h.ref.path for h in hits] == ["Inbox/meeting_notes.md"]

        # 5. But the inbox is still never a legal op target.
        from silica.kernel.recall.paths import is_inbox_path
        assert is_inbox_path("Inbox/meeting_notes.md")

        # 6. Check reading an external file outside the vault
        external_file = tmp_path / "external_inbox.md"
        external_file.write_text("External file content", encoding="utf-8")
        
        nc = backend.read_note(str(external_file))
        assert nc.content == "External file content"
        assert nc.ref.path == str(external_file.resolve())
        
    finally:
        CONFIG.inbox_dir = orig_inbox
        CONFIG.vault_path = orig_vault




def test_list_inbox_files_fs(tmp_path):
    """Verify that list_inbox_files lists notes inside inbox_dir on FS backend."""
    from silica.config import CONFIG
    from silica.driver.fs_backend import ObsidianFSBackend
    
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    
    inbox_dir = vault_dir / "Inbox"
    inbox_dir.mkdir()
    
    (inbox_dir / "lecture_15.md").write_text("Hello from lecture 15", encoding="utf-8")
    (inbox_dir / "subfolder").mkdir()
    (inbox_dir / "subfolder" / "lecture_16.md").write_text("Hello from lecture 16", encoding="utf-8")
    (inbox_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    (inbox_dir / ".DS_Store").write_bytes(b"junk")
    
    orig_inbox = CONFIG.inbox_dir
    orig_vault = CONFIG.vault_path
    
    CONFIG.inbox_dir = "Inbox"
    CONFIG.vault_path = str(vault_dir)
    
    try:
        backend = ObsidianFSBackend(vault_path=str(vault_dir))
        
        inbox_files = backend.list_inbox_files()
        paths = {ref.path for ref in inbox_files}
        names = {ref.name for ref in inbox_files}
        
        assert "Inbox/lecture_15.md" in paths
        assert "Inbox/subfolder/lecture_16.md" in paths
        assert "lecture_15" in names
        assert "lecture_16" in names
        # Non-md files (PDFs awaiting /convert) are listed, name keeps extension.
        assert "Inbox/paper.pdf" in paths
        assert "paper.pdf" in names
        # Dotfiles stay hidden.
        assert not any(".DS_Store" in p for p in paths)
    finally:
        CONFIG.inbox_dir = orig_inbox
        CONFIG.vault_path = orig_vault


