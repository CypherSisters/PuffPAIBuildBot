"""A look as data: the ``puffpai_theme/v1`` package.

A look is colours plus three choices from fixed lists. It has no URL, no CSS
text and no font name, so applying one can never fetch anything or run
anything. The shape::

    {"schema": "puffpai_theme/v1",
     "id": "tweakcn:kodama-grove" | "made:3f9a1c20b7e4",
     "name": "Kodama Grove",
     "from": {"list": "tweakcn", "item": "kodama-grove", "license": "Apache-2.0",
              "list_date": "2026-09-24"} | {"list": "made", "how": "model"|"words"},
     "day":   {"bg", "bg2", "card", "line", "text", "text2", "text3",
               "accent", "accent2", "danger": "#rrggbb", "chart": [4 x "#rrggbb"]},
     "night": {same} | None,          # None: the day colours are used at night too
     "shape": "round" | "soft" | "square",
     "font":  "friendly" | "plain" | "typewriter",
     "photo": "keep" | "hide",        # older field: "hide" also hides the clouds
     "background": "clouds" | "stars" | "plain" | "soft",   # optional; missing
                                      # = the rule in default_background()
     "words": ["forest", ...],        # describing words (look lists only)
     "checks": [...], "adjusted": [...]}   # written by check(), never trusted

Rules kept in code:

* every colour is ``#rrggbb`` written by :func:`puffpai.looks.colour.hex_of`;
* glass see-through amounts, glows and shadows come from fixed per-mode
  numbers here, so a look cannot make the page airy or flat;
* the background is one of a fixed set drawn by the dashboard's own CSS (the
  shipped cloud pictures, a starfield made of dots, the look's own colours);
  a look never carries a picture or a URL;
* spacing, letter spacing and sizes are not part of a look at all (the owner's
  taste: dense and editorial);
* readability is checked in code (WCAG 2 contrast): a colour that is hard to
  read is nudged darker or lighter a step at a time and the owner is told;
  a mode that still fails is refused.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from puffpai.looks import colour as C

SCHEMA = "puffpai_theme/v1"

COLOUR_KEYS: tuple[str, ...] = ("bg", "bg2", "card", "line", "text", "text2",
                                "text3", "accent", "accent2", "danger")
CHART_LEN = 4
MODES = ("day", "night")
SHAPES = ("round", "soft", "square")
FONTS = ("friendly", "plain", "typewriter")
PHOTOS = ("keep", "hide")
BACKGROUNDS = ("clouds", "stars", "plain", "soft", "petals", "confetti")
#: What the owner sees for each background (Looks page and terminal).
BACKGROUND_LABELS = {"clouds": "Clouds", "stars": "Stars", "plain": "Plain",
                     "soft": "Soft colour", "petals": "Petals", "confetti": "Confetti"}
#: Backgrounds drawn over the soft colour wash (themed: the welcome tour picks
#: them for a bot's subject, like petals for a wedding).
WASH_BACKGROUNDS = ("soft", "petals", "confetti")
TOP_KEYS = ("schema", "id", "name", "from", "day", "night", "shape", "font",
            "photo", "background", "words", "checks", "adjusted")
MAX_NAME = 40
MAX_WORDS = 24

_ID_RE = re.compile(r"^(?:[a-z]{2,20}:[a-z0-9][a-z0-9-]{0,39}|made:[0-9a-f]{12})$")
_LIST_RE = re.compile(r"^[a-z]{2,20}$")
_ITEM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&.,-]{0,39}$")
_WORD_RE = re.compile(r"^[a-z]{2,20}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LICENSE_RE = re.compile(r"^[A-Za-z0-9.\- ]{2,20}$")

#: Fixed see-through amounts per mode (match today's dashboard/css/_theme.css).
ALPHA = {
    "day": {"card": 0.72, "card2": 0.48, "rail": 0.66, "chip": 0.07,
            "glow1": 0.45, "glow2": 0.45, "hover": 0.86, "deep": 0.66,
            "glow": 0.18, "shadow": 0.16},
    "night": {"card": 0.62, "card2": 0.45, "rail": 0.35, "chip": 0.07,
              "glow1": 0.20, "glow2": 0.14, "hover": 0.78, "deep": 0.92,
              "glow": 0.20, "shadow": 0.45},
}

#: Readability floors (WCAG 2 AA): body words 4.5, faint words and coloured
#: words/icons 3, words on a coloured button 4.5.
FLOOR_TEXT = 4.5
FLOOR_FAINT = 3.0
FLOOR_COLOUR = 3.0
FLOOR_BUTTON = 4.5
MAX_STEPS = 12

RADIUS = {"round": ("28px", "9999px"), "soft": ("14px", "10px"),
          "square": ("4px", "4px")}
FONT_VARS = {
    "friendly": ("var(--font-fredoka)", "var(--font-nunito)"),
    "plain": ("var(--font-nunito)", "var(--font-nunito)"),
    "typewriter": ("var(--font-jbmono)", "var(--font-nunito)"),
}
IMG_VARS = ("--img1", "--img2", "--img3", "--img4", "--imgTile", "--imgPage")
#: How strongly each cloud picture shows, per mode. Equal to the defaults in
#: dashboard/css/_theme.css (a test keeps them the same).
IMG_AMOUNTS = {
    "day": {"--img1": 0.62, "--img2": 0.42, "--img3": 0.46, "--img4": 0.58,
            "--imgTile": 0.34, "--imgPage": 0.55},
    "night": {"--img1": 0.16, "--img2": 0.20, "--img3": 0.22, "--img4": 0.26,
              "--imgTile": 0.26, "--imgPage": 0.42},
}
#: The pictures' light and dark ends (the average of the lightest and darkest
#: few percent of pixels across dashboard/public/design-cloud-1..5.jpg,
#: measured once). Words over the clouds are checked against both.
CLOUD_LIGHT = "#fdedef"
CLOUD_DARK = "#9c76a4"
#: When words are hard to read over the clouds, the pictures are faded, one
#: step at a time (a softer picture is the scrim).
CLOUD_STEPS = (1.0, 0.5, 0.25, 0.0)
#: The bot's own look (dashboard/css/_theme.css), for its background choices.
OWN = {"day": {"bg": "#cfe9f9", "text": "#2e2140"},
       "night": {"bg": "#0b0714", "text": "#f7eef8"}}
CLEAR = "rgba(0,0,0,0)"
#: How much of a soft wash's brightest colour is checked under the words.
WASH_SHARE = 0.5

_KEY_WORDS = {
    "text": "the main words", "text2": "the grey words",
    "text3": "the faint words", "accent": "the main colour",
    "accent2": "the second colour", "danger": "the warning red",
    "chart": "the extra colours",
}


class LookError(ValueError):
    """A look this build will not use, with a plain reason."""


# -- validation --------------------------------------------------------------

def _mode(raw: Any, where: str) -> dict:
    if not isinstance(raw, dict):
        raise LookError(f"The {where} colours are missing.")
    extra = set(raw) - set(COLOUR_KEYS) - {"chart"}
    if extra:
        raise LookError(f"The {where} colours have something that is not a colour.")
    out: dict[str, Any] = {}
    for key in COLOUR_KEYS:
        value = raw.get(key)
        if not C.is_our_hex(value):
            raise LookError(f"A {where} colour is not written the way this bot writes colours.")
        out[key] = value
    chart = raw.get("chart")
    if (not isinstance(chart, list) or len(chart) != CHART_LEN
            or not all(C.is_our_hex(v) for v in chart)):
        raise LookError(f"The {where} extra colours are not right.")
    out["chart"] = list(chart)
    return out


def _from(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise LookError("The look does not say where it came from.")
    lst = raw.get("list")
    if not isinstance(lst, str) or not _LIST_RE.match(lst):
        raise LookError("The look does not say where it came from.")
    if lst == "made":
        how = raw.get("how")
        if how not in ("model", "words"):
            raise LookError("The look does not say how it was made.")
        if set(raw) - {"list", "how"}:
            raise LookError("The look has something extra in it.")
        return {"list": "made", "how": how}
    if set(raw) - {"list", "item", "license", "list_date"}:
        raise LookError("The look has something extra in it.")
    item, lic, date = raw.get("item"), raw.get("license"), raw.get("list_date")
    if not (isinstance(item, str) and _ITEM_RE.match(item)):
        raise LookError("The look's name in its list is not right.")
    if not (isinstance(lic, str) and _LICENSE_RE.match(lic)):
        raise LookError("A look whose licence is not known is not offered.")
    if not (isinstance(date, str) and _DATE_RE.match(date)):
        raise LookError("The look's list has no date.")
    return {"list": lst, "item": item, "license": lic, "list_date": date}


def validate(raw: Any) -> dict:
    """A canonical look, or :class:`LookError`. Extra fields are refused."""
    if not isinstance(raw, dict):
        raise LookError("A look is a set of named colours.")
    if raw.get("schema") != SCHEMA:
        raise LookError("This is not a look this bot can read.")
    extra = set(raw) - set(TOP_KEYS)
    if extra:
        raise LookError("The look has something extra in it.")
    look_id = raw.get("id")
    if not isinstance(look_id, str) or not _ID_RE.match(look_id):
        raise LookError("The look has no proper name tag.")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name.strip()) \
            or len(name.strip()) > MAX_NAME:
        raise LookError("The look's name is not a plain name.")
    from puffpai.artifact_review import reads_like_orders
    if reads_like_orders(name):
        raise LookError("The look's name is not a plain name.")
    src = _from(raw.get("from"))
    if look_id.startswith("made:") != (src["list"] == "made"):
        raise LookError("The look's name tag does not match where it came from.")
    if src["list"] != "made" and look_id != f"{src['list']}:{src['item']}":
        raise LookError("The look's name tag does not match where it came from.")
    day = _mode(raw.get("day"), "day")
    night = None if raw.get("night") is None else _mode(raw.get("night"), "night")
    shape, font, photo = raw.get("shape"), raw.get("font"), raw.get("photo")
    if shape not in SHAPES or font not in FONTS or photo not in PHOTOS:
        raise LookError("The look's shape, letters or pictures choice is not one this bot has.")
    background = raw.get("background")
    if background is not None and background not in BACKGROUNDS:
        raise LookError("The look's background is not one this bot has.")
    words = raw.get("words", [])
    if (not isinstance(words, list) or len(words) > MAX_WORDS
            or not all(isinstance(w, str) and _WORD_RE.match(w) for w in words)):
        raise LookError("The look's describing words are not plain words.")
    pkg = {"schema": SCHEMA, "id": look_id, "name": name.strip(), "from": src,
           "day": day, "night": night, "shape": shape, "font": font,
           "photo": photo, "background": "clouds", "words": list(words),
           "checks": [], "adjusted": []}
    pkg["background"] = background or default_background(pkg)
    if look_id.startswith("made:") and look_id != made_id(pkg):
        raise LookError("The look's name tag does not match its colours.")
    return pkg


def default_background(pkg: dict) -> str:
    """The rule: a dark look gets stars, a light look a soft wash of its own
    colours. (The bot's own look, no package at all, keeps the clouds.)"""
    return "stars" if C.is_dark(C.rgb_of(pkg["day"]["bg"])) else "soft"


def with_background(pkg: dict, background: str) -> dict:
    """*pkg* wearing *background*, checked again (words over clouds need it)."""
    if background not in BACKGROUNDS:
        raise LookError("That background is not one this bot has.")
    raw = {k: v for k, v in pkg.items() if k not in ("checks", "adjusted")}
    raw["background"] = background
    return check(validate(raw))


def made_id(pkg: dict) -> str:
    """``made:`` + 12 hex of sha256 over the canonical values.

    The background is left out on purpose: changing it keeps the look's name tag.
    """
    body = {k: pkg.get(k) for k in ("day", "night", "shape", "font", "photo")}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return "made:" + digest[:12]


# -- readability -------------------------------------------------------------

def shown_background(background: str, mode: str) -> str:
    """The background a mode really paints: "Stars" is a night sky, so in day
    mode it is the soft wash (dots on a light page sat between letters and
    read like commas)."""
    return "soft" if background == "stars" and mode == "day" else background


def _surfaces(mode: str, m: dict, background: str = "plain",
              clouds: float = 1.0) -> list[C.RGB]:
    """Every colour words can sit on: the page, the glass cards on it, and
    what the background paints under both (*clouds*: how much of the cloud
    pictures shows, 1 = the design's own amounts)."""
    background = shown_background(background, mode)
    a = ALPHA[mode]
    bg, bg2, card = (C.rgb_of(m[k]) for k in ("bg", "bg2", "card"))
    pages = [bg, bg2]
    tiles: list[C.RGB] = []
    if background in WASH_BACKGROUNDS:
        # Each coloured wash (see --puff-scene) where words usually sit: half
        # way out from its brightest point, which is under the top bar.
        pages += [C.composite((*C.rgb_of(m["accent"]), a["glow1"] * WASH_SHARE), bg),
                  C.composite((*C.rgb_of(m["chart"][0]), a["glow2"] * WASH_SHARE), bg2)]
    elif background == "clouds" and clouds > 0:
        amounts = IMG_AMOUNTS[mode]
        page_amt = max(amounts[k] for k in ("--img1", "--img2", "--img4", "--imgPage")) * clouds
        tile_amt = max(amounts["--imgTile"], amounts["--img3"]) * clouds
        for base in (bg, bg2):
            for cloud in (CLOUD_LIGHT, CLOUD_DARK):
                under = C.composite((*C.rgb_of(cloud), page_amt), base)
                pages.append(under)
                glass = C.composite((*card, a["card"]), under)
                tiles += [glass, C.composite((*C.rgb_of(cloud), tile_amt), glass)]
    # Stars are single dots, never under a whole word, so they add nothing.
    return pages + [C.composite((*card, a["card"]), p) for p in pages] + tiles


def on_accent(m: dict) -> str:
    """Words on a coloured button: white on a light look, the page colour on a dark one."""
    bg = C.rgb_of(m["bg"])
    return "#ffffff" if not C.is_dark(bg) else m["bg"]


def _worst(value: C.RGB, against: list[C.RGB]) -> float:
    return min(C.contrast(value, s) for s in against)


def _fit(value: str, against: list[C.RGB], floor: float, toward: str) -> Optional[str]:
    """*value* nudged until it reads at *floor* against every colour, or None."""
    rgb = C.rgb_of(value)
    for _ in range(MAX_STEPS + 1):
        # Judge the colour as it will be written (#rrggbb), not before rounding.
        rgb = C.rgb_of(C.hex_of(rgb))
        if _worst(rgb, against) >= floor:
            return C.hex_of(rgb)
        rgb = C.nudge_lightness(rgb, toward)
    return None


def _ratio(x: float) -> str:
    return f"{x:.1f} to 1"


def _check_mode(mode: str, m: dict, background: str = "plain"
                ) -> tuple[Optional[dict], list[dict], list[str]]:
    """(fixed colours or None, check rows, plain notes) for one mode, on its
    background. Over the clouds, when the colours cannot be made readable,
    the pictures are faded a step at a time before the mode is refused."""
    steps = CLOUD_STEPS if background == "clouds" else (1.0,)
    got: tuple[Optional[dict], list[dict], list[str]] = (None, [], [])
    for amount in steps:
        got = _check_on(mode, m, _surfaces(mode, m, background, amount))
        # The soft wash is made from the look's own colours, so a colour that
        # was nudged moves the wash too: check again until nothing moves.
        for _ in range(3):
            if got[0] is None:
                break
            again = _check_on(mode, got[0], _surfaces(mode, got[0], background, amount))
            if again[0] == got[0]:
                break
            got = (again[0], again[1], got[2] or again[2])
        if got[0] is not None:
            break
    fixed, rows, notes = got
    if fixed is not None and background == "clouds" and cloud_amount(mode, fixed) < 1:
        word = "day" if mode == "day" else "night"
        notes.append(f"On the {word} look I faded the cloud pictures so the "
                     f"words stay easy to read.")
    return fixed, rows, notes


def _passes(mode: str, m: dict, surfaces: list[C.RGB]) -> bool:
    button = C.rgb_of(on_accent(m))
    colours = [m["accent"], m["accent2"], m["danger"], *m["chart"]]
    return (min(_worst(C.rgb_of(m[k]), surfaces) for k in ("text", "text2")) >= FLOOR_TEXT
            and _worst(C.rgb_of(m["text3"]), surfaces) >= FLOOR_FAINT
            and min(_worst(C.rgb_of(v), surfaces) for v in colours) >= FLOOR_COLOUR
            and min(C.contrast(C.rgb_of(v), button) for v in colours) >= FLOOR_BUTTON)


def cloud_amount(mode: str, m: dict) -> float:
    """How much of the cloud pictures these (checked) colours can take."""
    for amount in CLOUD_STEPS:
        if _passes(mode, m, _surfaces(mode, m, "clouds", amount)):
            return amount
    return 0.0


def _check_on(mode: str, m: dict, surfaces: list[C.RGB]
              ) -> tuple[Optional[dict], list[dict], list[str]]:
    fixed = {k: (list(v) if isinstance(v, list) else v) for k, v in m.items()}
    dark = C.is_dark(C.rgb_of(fixed["bg"]))
    toward_text = "lighter" if dark else "darker"
    button = C.rgb_of(on_accent(fixed))
    notes: list[str] = []
    rows: list[dict] = []
    failed = False
    moved: list[str] = []

    def fit(key: str, value: str, against: list[C.RGB], floor: float) -> str:
        nonlocal failed
        got = _fit(value, against, floor, toward_text)
        if got is None:
            failed = True
            return value
        if got != value and key not in moved:
            moved.append(key)
        return got

    for key, floor in (("text", FLOOR_TEXT), ("text2", FLOOR_TEXT),
                       ("text3", FLOOR_FAINT)):
        fixed[key] = fit(key, fixed[key], surfaces, floor)
    # Coloured words and icons read on the page; coloured buttons carry words.
    for key in ("accent", "accent2", "danger"):
        fixed[key] = fit(key, fixed[key], surfaces, FLOOR_COLOUR)
        fixed[key] = fit(key, fixed[key], [button], FLOOR_BUTTON)
    for i, value in enumerate(fixed["chart"]):
        value = fit("chart", value, surfaces, FLOOR_COLOUR)
        fixed["chart"][i] = fit("chart", value, [button], FLOOR_BUTTON)

    word = "day" if mode == "day" else "night"
    lowest_text = min(_worst(C.rgb_of(fixed[k]), surfaces) for k in ("text", "text2"))
    lowest_faint = _worst(C.rgb_of(fixed["text3"]), surfaces)
    colours = [fixed["accent"], fixed["accent2"], fixed["danger"], *fixed["chart"]]
    lowest_colour = min(_worst(C.rgb_of(v), surfaces) for v in colours)
    lowest_button = min(C.contrast(C.rgb_of(v), button) for v in colours)
    for label, low, floor in (
            (f"Words are easy to read on the {word} look", lowest_text, FLOOR_TEXT),
            (f"Faint words are readable on the {word} look", lowest_faint, FLOOR_FAINT),
            (f"Coloured words stand out on the {word} look", lowest_colour, FLOOR_COLOUR),
            (f"Button words are easy to read on the {word} look", lowest_button, FLOOR_BUTTON)):
        rows.append({"check": label, "result": "pass" if low >= floor else "fail",
                     "why": f"lowest {_ratio(low)}, needs {_ratio(floor)}"})
    if failed or any(r["result"] != "pass" for r in rows):
        return None, rows, notes
    if moved:
        verb = "lightened" if dark else "darkened"
        names = [_KEY_WORDS[k] for k in moved]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        notes.append(f"On the {word} look I {verb} {listed} a little so "
                     f"everything is easy to read.")
    return fixed, rows, notes


#: How much colour a kept look's big surfaces carry at least (OKLCH chroma),
#: and from which of its colours they take the hue. A look whose page and
#: cards are near grey ("Amethyst Haze": purple and pink accents on
#: grey-violet) read almost like the bot's own dark look at night; its page
#: and cards now carry its own colours. Lightness is kept, so the words keep their contrast; the checks
#: below still run on the tinted colours.
SURFACE_TINT = {
    "day": {"bg": ("accent", 0.022), "bg2": ("accent2", 0.035),
            "card": ("accent", 0.012), "line": ("accent", 0.05)},
    "night": {"bg": ("accent", 0.05), "bg2": ("accent2", 0.055),
              "card": ("accent", 0.05), "line": ("accent2", 0.065)},
}
#: A source colour with less colour than this is grey: a grey look stays grey.
TINT_SOURCE_MIN = 0.04
#: Room for #rrggbb rounding, so tinting twice changes nothing.
TINT_SLACK = 0.006


def tint_surfaces(mode: str, m: dict) -> dict:
    """*m* with near-grey page, card and line colours given the look's own hue.

    Same lightness, at least :data:`SURFACE_TINT` chroma, the hue of the
    named accent. A surface that already has that much colour is left as it
    is, so this is safe to run on a look that was tinted before."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in m.items()}
    for key, (source, chroma) in SURFACE_TINT[mode].items():
        src = m.get(source)
        val = m.get(key)
        if not (C.is_our_hex(src) and C.is_our_hex(val)):
            continue
        _sl, src_c, src_h = C.srgb_to_oklch(C.rgb_of(src))
        lum, cur_c, _h = C.srgb_to_oklch(C.rgb_of(val))
        # Near white or near black a screen cannot show much colour: aim for
        # what it can, so a second run finds nothing left to do.
        reach = C.max_chroma(lum, src_h, chroma)
        if src_c < TINT_SOURCE_MIN or cur_c >= reach - TINT_SLACK:
            continue
        out[key] = C.hex_of(C.oklch_to_srgb(lum, reach, src_h))
    return out


def check(pkg: dict) -> dict:
    """*pkg* with readable colours, ``checks`` and ``adjusted`` filled in.

    Raises :class:`LookError` when neither mode can be made readable.
    """
    out = dict(pkg)
    checks: list[dict] = []
    adjusted: list[str] = []
    background = pkg.get("background") or default_background(pkg)
    out["background"] = background
    day, rows, notes = _check_mode("day", tint_surfaces("day", pkg["day"]), background)
    checks += rows
    night = None
    if pkg.get("night") is not None:
        night, rows_n, notes_n = _check_mode("night", tint_surfaces("night", pkg["night"]),
                                             background)
        checks += rows_n
        if night is None:
            adjusted.append("The night colours were too hard to read, so the "
                            "day colours are used at night too.")
        else:
            notes += notes_n
    if day is None:
        if night is None:
            raise LookError("This look is too hard to read, even after I tried "
                            "to fix its colours, so I left it out.")
        day = night
        adjusted.append("The day colours were too hard to read, so the night "
                        "colours are used in the day too.")
        night = None
    out["day"], out["night"] = day, night
    out["checks"] = checks
    out["adjusted"] = notes + adjusted
    if out["id"].startswith("made:"):
        out["id"] = made_id(out)
    return out


# -- what the dashboard gets -------------------------------------------------

def css_vars(pkg: dict, mode: str) -> dict[str, str]:
    """The allowlisted custom properties for one mode, every value ours."""
    m = pkg.get(mode) or pkg["day"]
    a = ALPHA[mode]
    out = {
        "--bg": m["bg"], "--bg2": m["bg2"],
        "--glow1": C.rgba_css(m["accent"], a["glow1"]),
        "--glow2": C.rgba_css(m["chart"][0], a["glow2"]),
        "--card": C.rgba_css(m["card"], a["card"]),
        "--card2": C.rgba_css(m["card"], a["card2"]),
        "--line": m["line"],
        "--text": m["text"], "--text2": m["text2"], "--text3": m["text3"],
        "--pink": m["accent"], "--magenta": m["accent2"],
        "--sky": m["chart"][0], "--mint": m["chart"][1],
        "--lemon": m["chart"][2], "--lav": m["chart"][3],
        "--railBg": C.rgba_css(m["card"], a["rail"]),
        "--chip": C.rgba_css(m["text"], a["chip"]),
        "--shadow": f"0 18px {'46' if mode == 'day' else '50'}px "
                    f"{C.rgba_css(m['text'] if mode == 'day' else '#000000', a['shadow'])}",
        "--puff-text-strong": m["text"],
        "--puff-card-hover": C.rgba_css(m["card"], a["hover"]),
        "--puff-surface-deep": C.rgba_css(m["card"] if mode == "day" else m["bg2"], a["deep"]),
        "--puff-red": m["danger"],
        "--puff-glow": C.rgba_css(m["accent"], a["glow"]),
        "--puff-on-accent": on_accent(m),
        "--puff-radius-card": RADIUS[pkg["shape"]][0],
        "--puff-radius-button": RADIUS[pkg["shape"]][1],
        "--puff-font-heading": FONT_VARS[pkg["font"]][0],
        "--puff-font-body": FONT_VARS[pkg["font"]][1],
    }
    background = pkg.get("background") or default_background(pkg)
    out.update(background_vars(background, mode, m,
                               cloud_amount(mode, m) if background == "clouds" else 1.0))
    if pkg.get("photo") == "hide":
        out.update({name: "0" for name in IMG_VARS})
    return out


def _amount(x: float) -> str:
    """0..1 written the way the amount pattern takes it: "0", "1", ".42"."""
    text = f"{max(0.0, min(1.0, x)):.3f}".rstrip("0").rstrip(".")
    return text[1:] if text.startswith("0.") else text


#: The least contrast a day star has against the page (below it,
#: grey specks at 35% on near-white read as dust, not stars).
DAY_STAR_CONTRAST = 3.0


def day_star(m: dict) -> str:
    """The star colour on a light page: the look's accent when it stands out
    from the page, otherwise its text colour, drawn nearly solid."""
    bg = C.rgb_of(m["bg"])
    accent = m.get("accent")
    if isinstance(accent, str) and C.is_our_hex(accent) \
            and C.contrast(C.rgb_of(accent), bg) >= DAY_STAR_CONTRAST:
        return C.rgba_css(accent, 0.9)
    return C.rgba_css(m["text"], 0.8)


def background_vars(background: str, mode: str, m: dict,
                    clouds: float = 1.0) -> dict[str, str]:
    """The background layers for one mode, as allowlisted amounts and colours.

    *m* needs ``bg`` and ``text`` (and ``bg2`` for the washes); the bot's own
    look passes :data:`OWN`. Every layer the dashboard paints behind the page
    is named here, so switching looks can switch every one of them off.
    """
    background = shown_background(background, mode)
    dark = C.is_dark(C.rgb_of(m["bg"]))
    star = C.rgba_css("#ffffff", 0.9) if dark else day_star(m)
    shows_clouds = background == "clouds"
    out = {name: _amount(IMG_AMOUNTS[mode][name] * (clouds if shows_clouds else 0.0))
           for name in IMG_VARS}
    out["--puff-stars"] = ("1" if background == "stars"
                           or (shows_clouds and mode == "night" and dark) else "0")
    out["--puff-stars-more"] = "1" if background == "stars" else "0"
    out["--puff-star"] = star
    out["--puff-petals"] = "1" if background == "petals" else "0"
    out["--puff-confetti"] = "1" if background == "confetti" else "0"
    out["--puff-hero-glow"] = "1" if background in ("clouds", *WASH_BACKGROUNDS) else "0"
    # Under the clouds the picture fades into the page colour at the edges.
    out["--puff-vignette"] = m["bg"] if shows_clouds else CLEAR
    if background in ("stars", "plain"):
        out["--puff-scene-glow1"] = CLEAR
        out["--puff-scene-glow2"] = CLEAR
    if background == "plain":
        out["--puff-scene-end"] = m["bg"]
    return out


def own_vars(background: str) -> Optional[dict[str, dict[str, str]]]:
    """The bot's own look wearing *background* (None: its own clouds)."""
    if background not in BACKGROUNDS or background == "clouds":
        return None
    return {mode: background_vars(background, mode, OWN[mode]) for mode in MODES}


def background_line(background: str) -> str:
    return f"Background: {BACKGROUND_LABELS.get(background, 'Clouds')}."


#: The names the dashboard may set, and the shape each value must have. The
#: browser keeps the same table (dashboard/app/_lib/looks.ts); a test keeps
#: the two equal.
VALUE_KINDS: dict[str, str] = {
    **{n: "colour" for n in (
        "--bg", "--bg2", "--glow1", "--glow2", "--card", "--card2", "--line",
        "--text", "--text2", "--text3", "--pink", "--magenta", "--sky", "--mint",
        "--lemon", "--lav", "--railBg", "--chip", "--puff-text-strong",
        "--puff-card-hover", "--puff-surface-deep", "--puff-red", "--puff-glow",
        "--puff-on-accent")},
    "--shadow": "shadow",
    "--puff-radius-card": "px", "--puff-radius-button": "px",
    "--puff-font-heading": "font", "--puff-font-body": "font",
    **{n: "amount" for n in IMG_VARS},
    **{n: "colour" for n in ("--puff-scene-glow1", "--puff-scene-glow2",
                             "--puff-scene-end", "--puff-vignette", "--puff-star")},
    "--puff-stars": "amount", "--puff-stars-more": "amount",
    "--puff-petals": "amount", "--puff-confetti": "amount",
    "--puff-hero-glow": "amount",
}
VALUE_PATTERNS: dict[str, str] = {
    "colour": r"^(?:#[0-9a-f]{6}|rgba\(\d{1,3},\d{1,3},\d{1,3},(?:0|1|0?\.\d{1,3})\))$",
    "shadow": r"^0 18px \d{1,2}px rgba\(\d{1,3},\d{1,3},\d{1,3},(?:0|1|0?\.\d{1,3})\)$",
    "px": r"^\d{1,4}px$",
    "font": r"^var\(--font-(?:fredoka|nunito|jbmono)\)$",
    "amount": r"^(?:0|1|0?\.\d{1,3})$",
}


def value_ok(name: str, value: Any) -> bool:
    kind = VALUE_KINDS.get(name)
    return (kind is not None and isinstance(value, str)
            and re.match(VALUE_PATTERNS[kind], value) is not None)


# -- plain words about a look ------------------------------------------------

def from_line(pkg: dict) -> str:
    src = pkg.get("from") or {}
    if src.get("list") == "made":
        return "I made this one from your words."
    return "From a free look list. Free to use."


def _page_word(hex_value: str) -> str:
    bg = C.rgb_of(hex_value)
    base = C.hue_word(bg)
    if base in ("black", "white"):
        return base
    return f"{'dark' if C.is_dark(bg) else 'light'} {base}"


def colours_line(pkg: dict) -> str:
    """"bright purple and teal on white by day, on dark blue at night"."""
    m = pkg["day"]
    accent = C.describe(C.rgb_of(m["accent"])) or "grey"
    colours = accent
    second = C.hue_word(C.rgb_of(m["accent2"]))
    # A second colour that is only a paler or greyer shade of the first
    # (a look made from one colour) is not named as another colour: a
    # pink look is not described as "red and grey".
    shade_only = second in ("grey", "white", "black") and C.hue_word(
        C.rgb_of(m["accent"])) not in ("grey", "white", "black")
    if second != C.hue_word(C.rgb_of(m["accent"])) and not shade_only:
        colours += f" and {second}"
    night = pkg.get("night")
    if not night:
        return f"{colours} on {_page_word(m['bg'])}"
    return (f"{colours} on {_page_word(m['bg'])} by day, "
            f"on {_page_word(night['bg'])} at night")


def swatch(pkg: dict, mode: str = "day") -> list[str]:
    m = pkg.get(mode) or pkg["day"]
    return [m["bg"], m["card"], m["text"], m["accent"], m["accent2"]]


def summary(pkg: dict) -> dict:
    """What any screen shows about a look (both modes' values included)."""
    return {
        "id": pkg["id"], "name": pkg["name"],
        "from_line": from_line(pkg), "colours_line": colours_line(pkg),
        "one_version": pkg.get("night") is None,
        "background": pkg.get("background") or default_background(pkg),
        "background_line": background_line(pkg.get("background") or default_background(pkg)),
        "swatch": {"day": swatch(pkg, "day"), "night": swatch(pkg, "night")},
        "vars": {"day": css_vars(pkg, "day"), "night": css_vars(pkg, "night")},
        "adjusted": list(pkg.get("adjusted") or []),
        "checks": list(pkg.get("checks") or []),
    }


__all__ = ["ALPHA", "BACKGROUNDS", "BACKGROUND_LABELS", "COLOUR_KEYS", "FONTS", "LookError", "PHOTOS", "SCHEMA",
           "SHAPES", "VALUE_KINDS", "VALUE_PATTERNS", "background_line", "shown_background",
           "background_vars", "check", "cloud_amount", "default_background", "colours_line",
           "css_vars", "from_line", "made_id", "on_accent", "summary", "swatch",
           "own_vars", "tint_surfaces", "validate", "value_ok", "with_background"]
