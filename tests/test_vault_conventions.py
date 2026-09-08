"""Per-vault `conventions:` contract (spec-hermes-coherence §2).

Language, max_tags, callout whitelist and size limits become a single-source
contract read from vault.yaml, instead of being hardcoded/duplicated across
the distiller prompt (`{LANGUAGE}`/`{MAX_TAGS}`) and `ofm.LIMITS`/
`ofm.CALLOUT_TYPES`. Absence of a `conventions:` block (or of a manifest at
all) must reproduce today's hardcoded values bit-for-bit.
"""
from __future__ import annotations


from silica.config import CONFIG
from silica.kernel.vault_manifest import (
    VaultConventions,
    load_manifest,
    reset_manifest_cache,
)

# NB: the conftest.py `_reset_manifest_cache` autouse fixture clears the
# module-level manifest cache before every test; `reset_manifest_cache()` is
# still called explicitly after writing a vault.yaml mid-test to force a
# fresh read against the file we just wrote.


# ---------------------------------------------------------------------------
# load_manifest: conventions block parsing
# ---------------------------------------------------------------------------

def test_conventions_default_when_no_manifest(tmp_path):
    m = load_manifest(tmp_path)
    assert m.conventions == VaultConventions(
        language=None, max_tags=3, extra_callouts=(),
    )


def test_conventions_parsed_from_vault_yaml(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n"
        "  language: english\n"
        "  max_tags: 5\n"
        "  extra_callouts: [clinica]\n",
        encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m.conventions.language == "english"
    assert m.conventions.max_tags == 5
    assert m.conventions.extra_callouts == ("clinica",)


def test_conventions_partial_block_defaults_missing_keys(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  language: english\n", encoding="utf-8"
    )
    m = load_manifest(tmp_path)
    assert m.conventions.language == "english"
    assert m.conventions.max_tags == 3          # default, unset
    assert m.conventions.extra_callouts == ()    # default, unset


def test_conventions_whitespace_only_language_folds_to_none(tmp_path):
    """A whitespace-only `language: '   '` is not a concrete language name —
    it must fold to None (follow the source), never leak into {LANGUAGE}."""
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  language: '   '\n", encoding="utf-8"
    )
    m = load_manifest(tmp_path)
    assert m.conventions.language is None


def test_conventions_declared_language_is_stripped(tmp_path):
    """Finding 6 (final multilingua review): `_parse_conventions` checks
    `.strip()` truthiness to accept the field but must STORE the stripped
    value — " Italian " must reach {LANGUAGE} as "Italian", not with
    leading/trailing whitespace baked in."""
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  language: ' Italian '\n", encoding="utf-8"
    )
    m = load_manifest(tmp_path)
    assert m.conventions.language == "Italian"


def test_conventions_non_mapping_block_degrades_to_defaults(tmp_path):
    (tmp_path / "vault.yaml").write_text("conventions: not-a-mapping\n", encoding="utf-8")
    m = load_manifest(tmp_path)
    assert m.conventions == VaultConventions()


def test_conventions_bad_field_types_degrade_to_defaults(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n"
        "  max_tags: not-a-number\n"
        "  extra_callouts: also-not-a-list\n",
        encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m.conventions.max_tags == 3
    assert m.conventions.extra_callouts == ()


def test_conventions_extra_callouts_normalized_lowercase(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  extra_callouts: [Clinica, TRIAGE]\n", encoding="utf-8"
    )
    m = load_manifest(tmp_path)
    assert m.conventions.extra_callouts == ("clinica", "triage")


# ---------------------------------------------------------------------------
# render_prompt: {LANGUAGE} / {MAX_TAGS} placeholder substitution
# ---------------------------------------------------------------------------

























# ---------------------------------------------------------------------------
# ofm_lint: LIMITS (max_tags) + CALLOUT_TYPES resolved from the active manifest
# ---------------------------------------------------------------------------

_NOTE_TMPL = """---
parent note: "[[Hub]]"
tags:
{tags}
last modified: 2026, 07, 02
AI: true
---

# Title

Body text with [[Hub]].
"""


def _note_with_n_tags(n: int) -> str:
    tags = "\n".join(f"  - tag{i}" for i in range(n))
    return _NOTE_TMPL.format(tags=tags)


def test_ofm_lint_default_max_tags_unchanged(monkeypatch):
    monkeypatch.setattr(CONFIG, "vault_path", "")
    from silica.kernel.link.ofm import ofm_lint

    flags = ofm_lint(_note_with_n_tags(4))["flags"]
    assert any("too many tags (4); max 3" in f for f in flags)


def test_ofm_lint_accepts_max_tags_from_manifest(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  max_tags: 5\n", encoding="utf-8"
    )
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    reset_manifest_cache()
    from silica.kernel.link.ofm import ofm_lint

    flags = ofm_lint(_note_with_n_tags(5))["flags"]
    assert not any("too many tags" in f for f in flags)


def test_ofm_lint_literal_newline_ignores_math_commands(monkeypatch):
    """`\\ne`/`\\neq`/`\\nabla` inside math spans contain the two-char `\\n`
    sequence — they are legitimate LaTeX, not an escaping artifact. A note
    carrying them must lint clean or every patch to it fails forever (real
    incident: 2026-07-17, Distribuzioni condizionate.md, `$\\Sigma_k \\ne \\Sigma$`)."""
    monkeypatch.setattr(CONFIG, "vault_path", "")
    from silica.kernel.link.ofm import ofm_lint

    note = _NOTE_TMPL.format(tags="  - tag0") + (
        "\nLDA assume $\\Sigma_k = \\Sigma$, QDA consente $\\Sigma_k \\ne \\Sigma$"
        " e il gradiente $$\\nabla f \\neq 0$$.\n"
    )
    violations = ofm_lint(note)["violations"]
    assert not any("literal" in v for v in violations)


def test_ofm_lint_literal_newline_still_detected_in_prose(monkeypatch):
    monkeypatch.setattr(CONFIG, "vault_path", "")
    from silica.kernel.link.ofm import ofm_lint

    note = _NOTE_TMPL.format(tags="  - tag0") + "\nriga uno\\nriga due\n"
    violations = ofm_lint(note)["violations"]
    assert any("literal" in v for v in violations)


def test_ofm_lint_rejects_unknown_callout_by_default(monkeypatch):
    monkeypatch.setattr(CONFIG, "vault_path", "")
    from silica.kernel.link.ofm import ofm_lint

    note = _note_with_n_tags(1) + "\n> [!clinica] some clinical note\n"
    violations = ofm_lint(note)["violations"]
    assert any("unknown callout type" in v for v in violations)


def test_ofm_lint_extra_callouts_whitelisted_from_manifest(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  extra_callouts: [clinica]\n", encoding="utf-8"
    )
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    reset_manifest_cache()
    from silica.kernel.link.ofm import ofm_lint

    note = _note_with_n_tags(1) + "\n> [!clinica] some clinical note\n"
    violations = ofm_lint(note)["violations"]
    assert not any("unknown callout type" in v for v in violations)


# ---------------------------------------------------------------------------
# template conventions parsing
# ---------------------------------------------------------------------------


def test_template_conventions_parsed_from_vault_yaml(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "conventions:\n  default_template: paper\n  templates_dir: tpl\n",
        encoding="utf-8",
    )
    c = load_manifest(tmp_path).conventions
    assert c.default_template == "paper"
    assert c.templates_dir == "tpl"


def test_template_conventions_defaults(tmp_path):
    c = load_manifest(tmp_path).conventions
    assert c.default_template is None
    assert c.templates_dir == "templates"


def test_templates_dir_traversal_guard(tmp_path):
    """Same trust boundary as wiki_dir: vault.yaml is user-authored and
    templates_dir reaches file reads — traversal/absolute paths fall back."""
    for bad in ("../outside", "/abs/path", "a/../../b", "C:\\evil"):
        (tmp_path / "vault.yaml").write_text(
            f"conventions:\n  templates_dir: {bad}\n", encoding="utf-8",
        )
        reset_manifest_cache()
        assert load_manifest(tmp_path).conventions.templates_dir == "templates"
