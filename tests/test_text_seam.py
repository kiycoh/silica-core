"""kernel/text — the single seam for «note text → tokens/stems» (C1).

One deep module owns stripping (frontmatter, math, images, fences) and
tokenization (language stopwords, optional Snowball stemming); cooccurrence,
keyphrase, cohesion, classify and the MOC writer all cross the same seam, so
a stripping bug is fixed once, everywhere.
"""
from __future__ import annotations


SAMPLE = """---
tags: [math]
---
# Gradiente

La formula $\\nabla f$ guida la discesa e il blocco

$$E = \\frac{a}{b}$$

resta trasparente. ![[grafico.png]] ![alt](media/plot.jpeg)

```python
def fenced_token():
    pass
```

Chiude con \\alpha residuo.
"""


def test_clean_body_strips_frontmatter_math_images():
    from silica.kernel.text.text import clean_body

    out = clean_body(SAMPLE, fences=False)
    assert "tags:" not in out, "frontmatter must be stripped"
    assert "nabla" not in out and "frac" not in out, "math spans must be stripped"
    assert "alpha" not in out, "residual latex commands must be stripped"
    assert "grafico.png" not in out and "plot.jpeg" not in out, "images must be stripped"
    assert "discesa" in out, "prose must survive"


def test_clean_body_fences_are_callers_choice():
    from silica.kernel.text.text import clean_body

    # cooccurrence keeps fences (identifiers are the graph signal of code notes)
    assert "fenced_token" in clean_body(SAMPLE, fences=False)
    # keyphrase strips them (the miner must not rank code identifiers)
    assert "fenced_token" not in clean_body(SAMPLE, fences=True)


def test_tokens_folds_plurals_and_drops_stopwords():
    from silica.kernel.text.text import tokens

    sents = tokens("La rete neurale. Le reti neurali!", lang="italian")
    assert len(sents) == 2, "sentence boundary must be preserved"
    surfaces = [s for sent in sents for (_t, s) in sent]
    assert "la" not in surfaces and "le" not in surfaces, "stopwords dropped"
    stems = [[t for (t, _s) in sent] for sent in sents]
    assert stems[0] == stems[1], "singular and plural must share a stem"


def test_tokens_without_stemming_keeps_surfaces():
    from silica.kernel.text.text import tokens

    sents = tokens("Descrittori compatti", lang="italian", stem=False)
    assert [(t, s) for sent in sents for (t, s) in sent] == [
        ("descrittori", "descrittori"), ("compatti", "compatti"),
    ]










def test_tokens_min_len_is_callers_choice():
    from silica.kernel.text.text import tokens

    # zk/xj: verified absent from the (aggressive) english stopword list —
    # this asserts the length gate alone.
    text = "zk xj gradient"
    flat = lambda sents: [s for sent in sents for (_t, s) in sent]
    assert flat(tokens(text, lang="english", stem=False)) == ["gradient"]
    assert flat(tokens(text, lang="english", stem=False, min_len=2)) == [
        "zk", "xj", "gradient",
    ]
