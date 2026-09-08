# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Heading-section lookup over note text.

What survives of the distiller's payload builder: the two functions that
locate a concept's own section in a source note. `kernel/text/media.py` uses
them so a note's images come from the same section its text came from.
"""

import re


def find_heading(content: str, concept: str):
    escaped = re.escape(concept)
    start_b = r'\b' if concept and re.match(r'\w', concept) else ''
    end_b = r'\b' if concept and re.search(r'\w$', concept) else ''
    pattern = re.compile(
        rf'^(#{{1,4}})\s+.*{start_b}{escaped}{end_b}.*$',
        re.IGNORECASE | re.MULTILINE,
    )
    return pattern.search(content)


def extract_section(content: str, heading_match) -> str:
    level = len(heading_match.group(1))
    next_pattern = re.compile(rf'^#{{1,{level}}}(?!#)\s+', re.MULTILINE)
    next_match = next_pattern.search(content, pos=heading_match.end())
    end = next_match.start() if next_match else len(content)
    return content[heading_match.start():end].strip()
