"""Turn one item of a free look list into a look (``puffpai_theme/v1``).

The lists this reads are shadcn-style ``registry.json`` files (tweakcn's
community looks and shadcn's own base looks): each item has ``cssVars.light``
and ``cssVars.dark`` colour sets and a ``cssVars.theme`` block. Only colour
NUMBERS, the corner radius as a number, and whether the main font is a
typewriter font are read. The item's raw ``css`` block, shadows, letter
spacing, spacing and font names are ignored. Every colour goes through
:func:`puffpai.looks.colour.parse` and comes out written by our own formatter,
so no text from the list reaches the page.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from puffpai.looks import colour as C
from puffpai.looks import package as P

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_REM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,4})?)(rem|px)$")


def _get(block: dict, key: str) -> Optional[C.RGB]:
    got = C.parse(block.get(key))
    if got is None:
        return None
    # A see-through source colour is flattened onto the page colour.
    base = C.parse(block.get("background"))
    under = base[:3] if base else (1.0, 1.0, 1.0)
    return C.composite(got, under) if got[3] < 1 else got[:3]


def _saturated(rgb: Optional[C.RGB]) -> bool:
    return rgb is not None and C.srgb_to_oklch(rgb)[1] >= 0.06


def _mode(block: Any) -> Optional[dict]:
    if not isinstance(block, dict):
        return None
    bg = _get(block, "background")
    text = _get(block, "foreground")
    accent = _get(block, "primary")
    if bg is None or text is None or accent is None:
        return None
    card = _get(block, "card") or bg
    secondary = _get(block, "secondary") or card
    line = _get(block, "border") or C.mix(bg, text, 0.15)
    text2 = _get(block, "muted-foreground") or C.mix(text, bg, 0.3)
    list_accent = _get(block, "accent")
    accent2 = list_accent if _saturated(list_accent) else secondary
    if not _saturated(accent2):
        accent2 = _get(block, "chart-2") or accent
    danger = _get(block, "destructive") or (0.85, 0.2, 0.2)
    chart = [_get(block, f"chart-{i}") or accent for i in range(1, 5)]
    return {
        "bg": C.hex_of(bg),
        "bg2": C.hex_of(C.mix(bg, secondary, 0.25)),
        "card": C.hex_of(card),
        "line": C.hex_of(line),
        "text": C.hex_of(text),
        "text2": C.hex_of(text2),
        "text3": C.hex_of(C.mix(text2, bg, 0.35)),
        "accent": C.hex_of(accent),
        "accent2": C.hex_of(accent2),
        "danger": C.hex_of(danger),
        "chart": [C.hex_of(c) for c in chart],
    }


def shape_of(radius: Any) -> str:
    m = _REM_RE.match(str(radius or "").strip().lower())
    if not m:
        return "soft"
    value = float(m.group(1)) / (16.0 if m.group(2) == "px" else 1.0)
    if value <= 0.3:
        return "square"
    if value <= 0.75:
        return "soft"
    return "round"


def font_of(font_sans: Any) -> str:
    """"typewriter" when the list's main font is a monospace one, else "plain".

    "friendly" (rounded headings) is only chosen by the owner or the model.
    """
    first = str(font_sans or "").split(",")[0].strip().strip("'\"").lower()
    return "typewriter" if "mono" in first or first in ("courier", "courier new") else "plain"


def from_shadcn_item(item: Any, *, list_id: str, license: str, list_date: str,
                     name: Optional[str] = None, words: Iterable[str] = ()) -> dict:
    """A checked look from one list item, or :class:`package.LookError`."""
    if not isinstance(item, dict):
        raise P.LookError("A list item is not a look.")
    slug = str(item.get("name") or "").strip().lower()
    if slug.startswith("theme-"):
        slug = slug[len("theme-"):]
    if not _SLUG_RE.match(slug):
        raise P.LookError("A list item has no usable name.")
    css_vars = item.get("cssVars")
    if not isinstance(css_vars, dict):
        raise P.LookError("A list item has no colours.")
    day = _mode(css_vars.get("light"))
    night = _mode(css_vars.get("dark"))
    if day is None and night is None:
        raise P.LookError("A list item has no colours this bot can read.")
    if day is None:
        day, night = night, None
    theme = css_vars.get("theme") if isinstance(css_vars.get("theme"), dict) else {}
    light = css_vars.get("light") if isinstance(css_vars.get("light"), dict) else {}
    radius = theme.get("radius", light.get("radius"))
    from puffpai.catalogs.live import clean_text
    title = name or clean_text(item.get("title") or slug.replace("-", " ").title(), P.MAX_NAME)
    raw = {
        "schema": P.SCHEMA, "id": f"{list_id}:{slug}", "name": title,
        "from": {"list": list_id, "item": slug, "license": license,
                 "list_date": list_date},
        "day": day, "night": night, "shape": shape_of(radius),
        "font": font_of(theme.get("font-sans", light.get("font-sans"))),
        "photo": "keep", "words": sorted(set(words)),
    }
    return P.check(P.validate(raw))


__all__ = ["font_of", "from_shadcn_item", "shape_of"]
