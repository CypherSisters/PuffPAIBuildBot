"""Make the shipped look-list files from a downloaded ``registry.json``.

A maintainer's tool, run offline on files already downloaded; the running
bot never fetches a look list. It converts every item with
:mod:`puffpai.looks.adapters` (so only colour numbers survive), adds our own
describing words (curated data, many topics, written here -- not text from
the list), and writes ``config/catalog_snapshots/looks_<list>_<date>.json``.

Items named after a company or product are left out (their names are
trademarks, not descriptions) and listed under ``left_out``.

    python -m puffpai.looks.snapshot_build tweakcn <registry.json> 2026-09-24
    python -m puffpai.looks.snapshot_build shadcn <registry.json> 2026-09-24

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import sys

from puffpai.looks import adapters
from puffpai.looks import package as P

LISTS = {
    "tweakcn": {"license": "Apache-2.0",
                "source": "https://tweakcn.com/r/themes/registry.json",
                "project": "https://github.com/jnsahaj/tweakcn"},
    "shadcn": {"license": "MIT",
               "source": "https://ui.shadcn.com/r/styles/new-york-v4/registry.json",
               "project": "https://github.com/shadcn-ui/ui"},
}

#: Items named after a company or product: left out.
BRAND_NAMES = frozenset({"twitter", "t3-chat", "supabase", "vercel", "claude",
                         "doom-64"})

#: Our describing words for each look: how it feels, places and things it
#: suits. Curated data covering many topics; every word is a plain word.
WORDS: dict[str, tuple[str, ...]] = {
    "modern-minimal": ("clean", "simple", "minimal", "modern", "calm", "office", "work"),
    "violet-bloom": ("violet", "flower", "bloom", "bold", "spring"),
    "mocha-mousse": ("coffee", "chocolate", "cozy", "warm", "cafe", "autumn", "earthy", "baking"),
    "bubblegum": ("candy", "sweet", "playful", "fun", "party", "bubblegum"),
    "amethyst-haze": ("lavender", "soft", "dreamy", "calm", "crystal", "gem"),
    "notebook": ("paper", "notebook", "school", "pencil", "sketch", "homework", "study", "writing"),
    "catppuccin": ("pastel", "soft", "cozy", "cat"),
    "graphite": ("dark", "clean", "serious", "charcoal", "simple", "minimal"),
    "perpetuity": ("ocean", "sea", "water", "calm", "cool", "boat", "sailing"),
    "kodama-grove": ("forest", "jungle", "leaves", "nature", "earthy", "woods", "moss", "trees", "garden", "hiking"),
    "cosmic-night": ("space", "cosmic", "galaxy", "stars", "night", "universe", "planet", "rocket", "astronomy", "alien"),
    "tangerine": ("tangerine", "fruit", "sunny", "citrus", "warm", "summer"),
    "quantum-rose": ("rose", "neon", "flower", "bold"),
    "nature": ("nature", "leaves", "garden", "plants", "outdoors", "forest", "jungle", "earthy", "farm", "vegetable"),
    "bold-tech": ("tech", "bold", "robot", "computer", "futuristic", "science", "coding"),
    "elegant-luxury": ("luxury", "elegant", "fancy", "classic", "wine", "royal"),
    "amber-minimal": ("amber", "warm", "minimal", "simple", "honey", "bee"),
    "neo-brutalism": ("bold", "loud", "comic", "poster", "blocky", "superhero"),
    "solar-dusk": ("sunset", "dusk", "warm", "desert", "sun", "autumn", "cowboy", "canyon"),
    "claymorphism": ("clay", "soft", "toy", "playful", "pastel", "crafts"),
    "cyberpunk": ("neon", "cyberpunk", "futuristic", "arcade", "synthwave", "electric", "gaming"),
    "pastel-dreams": ("pastel", "dreamy", "soft", "lavender", "gentle", "baby", "unicorn"),
    "clean-slate": ("clean", "slate", "simple", "calm", "plain", "tidy"),
    "caffeine": ("coffee", "cafe", "cozy", "warm", "espresso", "bakery"),
    "ocean-breeze": ("ocean", "sea", "beach", "water", "fresh", "breeze", "summer", "fish"),
    "retro-arcade": ("retro", "arcade", "game", "games", "video", "eighties", "gaming"),
    "midnight-bloom": ("night", "midnight", "flower", "dark", "mystery"),
    "candyland": ("candy", "sweet", "sugar", "fun", "playful", "rainbow", "pastel", "dessert"),
    "northern-lights": ("aurora", "northern", "lights", "arctic", "winter", "snow", "sky", "polar"),
    "vintage-paper": ("vintage", "paper", "old", "library", "books", "parchment", "antique", "cozy", "history", "reading"),
    "sunset-horizon": ("sunset", "warm", "beach", "evening", "horizon", "summer", "tropical"),
    "starry-night": ("stars", "night", "starry", "sky", "painting", "art", "dreamy", "moon"),
    "darkmatter": ("dark", "space", "matter", "serious", "night", "minimal"),
    "mono": ("mono", "typewriter", "plain", "code", "simple", "minimal", "newspaper"),
    "soft-pop": ("pop", "bright", "fun", "colourful", "colorful", "playful", "soft", "music"),
    "sage-garden": ("sage", "garden", "herbs", "calm", "plants", "soft", "earthy", "tea"),
    "stone": ("stone", "warm", "plain", "simple", "minimal", "neutral"),
    "zinc": ("zinc", "cool", "plain", "simple", "minimal", "metal"),
    "neutral": ("neutral", "plain", "simple", "minimal", "clean"),
    "gray": ("plain", "simple", "cool", "minimal"),
    "slate": ("slate", "plain", "simple", "clean", "minimal"),
}


def build(list_id: str, registry: dict, taken_at: str) -> dict:
    meta = LISTS[list_id]
    looks: list[dict] = []
    left_out: list[dict] = []
    for item in registry.get("items") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind not in ("registry:style", "registry:theme"):
            continue
        slug = str(item.get("name") or "").removeprefix("theme-")
        if slug in BRAND_NAMES:
            left_out.append({"item": slug, "why": "named after a company or product"})
            continue
        try:
            pkg = adapters.from_shadcn_item(
                item, list_id=list_id, license=meta["license"], list_date=taken_at,
                words=WORDS.get(slug, ()))
        except P.LookError as exc:
            left_out.append({"item": slug, "why": str(exc)})
            continue
        looks.append(pkg)
    return {"schema": "looks_snapshot/v1", "list": list_id,
            "license": meta["license"], "project": meta["project"],
            "source": meta["source"], "taken_at": taken_at,
            "note": "Colours converted to numbers by BuildBot; describing "
                    "words written by BuildBot.",
            "left_out": left_out, "looks": looks}


def main(argv: list[str]) -> int:
    list_id, path, taken_at = argv
    with open(path, encoding="utf-8") as fh:
        registry = json.load(fh)
    snap = build(list_id, registry, taken_at)
    from puffpai.catalogs import live
    out = os.path.join(live.shipped_snapshot_dir(), f"looks_{list_id}_{taken_at}.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"{out}: {len(snap['looks'])} looks, {len(snap['left_out'])} left out")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
