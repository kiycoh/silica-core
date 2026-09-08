# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Link cases both web renders must agree on.

An answer is rendered twice: `_linkify` (markdown-it, server, canonical) and
`mdLite` (hand-rolled JS, the live streaming segment). Two languages, one
contract — so the corpus lives here and both halves assert the same list. A
drift where one side gains a behavior the other lacks already has its case
written on both sides.

Only the URL half is shared: note refs resolve server-side against the vault
index and are left to the caller in the live segment, so those assertions stay
in their own test.
"""

# (markdown, must appear, must NOT appear)
URL_CASES: list[tuple[str, list[str], list[str]]] = [
    # A citation line as _sources_block writes it: bare URL, and the URL text
    # survives so a terminal or a reader without link support still reads it.
    (
        "1. Chem - https://en.wikipedia.org/wiki/chemistry",
        ['<a href="https://en.wikipedia.org/wiki/chemistry">'
         "https://en.wikipedia.org/wiki/chemistry</a>"],
        [],
    ),
    # Sentence punctuation belongs to the prose, not to the href.
    ("vedi https://ex.com/a. ok", ['<a href="https://ex.com/a">https://ex.com/a</a>.'], []),
    # …but a URL's own balanced parens do not (Wikipedia disambiguation).
    (
        "(https://en.wikipedia.org/wiki/A_(b))",
        ['href="https://en.wikipedia.org/wiki/A_(b)"'],
        [],
    ),
    # Code is never linkified, in either render.
    ("run `https://x.org/a` inline", ["<code>https://x.org/a</code>"], ["<a href"]),
    # fuzzy_link off: `.md` is a real ccTLD, and a vault path is not a website.
    # fuzzy_email off: prose must not open a mail client.
    (
        "vedi nota.md e www.x.org e foo@bar.com",
        [],
        ["<a href", "http://nota.md", "mailto:"],
    ),
]
