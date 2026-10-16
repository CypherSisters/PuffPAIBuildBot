"""Cut long text into overlapping passages of about one paragraph.

The reading helper only looks at the first ~256 words of a passage, and the
chat only quotes 700 characters of one, so a passage must be short for every
part of an article to be findable. Windows of at most ``size`` characters,
cut at the last sentence or line end in the second half of the window, with
``overlap`` characters repeated so a sentence split across two passages is
whole in at least one of them.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

WINDOW = 800
OVERLAP = 100


def chunk_windows(text: str, size: int = WINDOW, overlap: int = OVERLAP) -> list[str]:
    """Overlapping passages of at most *size* characters. ``[]`` for blank text."""
    text = (text or "").strip()
    if not text:
        return []
    size = max(50, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            half = start + size // 2
            cut = max(text.rfind(". ", half, end), text.rfind("\n", half, end))
            if cut > 0:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(start + 1, end - overlap)
    return out


__all__ = ["WINDOW", "OVERLAP", "chunk_windows"]
