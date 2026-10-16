"""Colours: read a colour written by someone else, and write one of our own.

Every colour a look carries is ``#rrggbb`` written by :func:`hex_of`. A value
from a free look list or from the local model is parsed to numbers here, or it
is dropped. Nothing a source wrote is ever copied through, so no CSS text
(``url(``, ``var(``, ``;}``) can ride along inside a colour.

Also here: the WCAG 2 contrast ratio, lightness nudging in OKLCH (so a colour
keeps its hue when it is made darker or lighter), and plain colour words for a
look's one-line description ("deep green", "soft orange").

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import math
import re
from typing import Optional

RGB = tuple[float, float, float]          # 0..1 each, sRGB
RGBA = tuple[float, float, float, float]  # 0..1 each

MAX_VALUE_LEN = 64

_HEX_RE = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_OUR_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
_FUNC_RE = re.compile(r"^(rgba?|hsla?|oklch)\(([0-9.,%/+\-\s]{1,56})\)$")
_BARE_HSL_RE = re.compile(r"^([0-9.+\-]{1,9})\s+([0-9.]{1,9})%\s+([0-9.]{1,9})%$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(%?)$")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _num(token: str) -> Optional[tuple[float, bool]]:
    m = _NUM_RE.match(token)
    if not m:
        return None
    try:
        value = float(token.rstrip("%"))
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value, bool(m.group(1))


def _split_args(body: str) -> Optional[tuple[list[str], Optional[str]]]:
    """``"1 2 3 / .5"`` or ``"1, 2, 3, .5"`` -> (["1","2","3"], ".5")."""
    alpha = None
    if "/" in body:
        if body.count("/") != 1:
            return None
        body, alpha = body.split("/")
        alpha = alpha.strip()
    parts = [p for p in re.split(r"[\s,]+", body.strip()) if p]
    if alpha is None and len(parts) == 4:
        parts, alpha = parts[:3], parts[3]
    if len(parts) != 3:
        return None
    return parts, alpha


def _alpha(token: Optional[str]) -> Optional[float]:
    if token is None:
        return 1.0
    got = _num(token)
    if got is None:
        return None
    value, pct = got
    return _clamp(value / 100.0 if pct else value)


def parse(value: object) -> Optional[RGBA]:
    """A colour someone else wrote, as numbers, or None.

    Accepted: ``#rgb #rgba #rrggbb #rrggbbaa``, ``rgb() rgba() hsl() hsla()
    oklch()`` with numbers only, and bare ``H S% L%`` channels (older shadcn
    lists). Anything else -- a name, ``var(``, ``url(``, ``calc(``, a ``;`` --
    is None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or len(text) > MAX_VALUE_LEN:
        return None
    m = _HEX_RE.match(text)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (r, g, b, a)
    m = _BARE_HSL_RE.match(text)
    if m:
        try:
            h, s, l = (float(x) for x in m.groups())
        except ValueError:
            return None
        if not all(math.isfinite(x) for x in (h, s, l)):
            return None
        r, g, b = hsl_to_srgb(h, s / 100.0, l / 100.0)
        return (r, g, b, 1.0)
    m = _FUNC_RE.match(text)
    if not m:
        return None
    func, body = m.groups()
    split = _split_args(body)
    if split is None:
        return None
    parts, alpha_tok = split
    nums = [_num(p) for p in parts]
    if any(n is None for n in nums):
        return None
    a = _alpha(alpha_tok)
    if a is None:
        return None
    if func in ("rgb", "rgba"):
        chans = [(v / 100.0) if pct else (v / 255.0) for v, pct in nums]  # type: ignore[misc]
        r, g, b = (_clamp(c) for c in chans)
        return (r, g, b, a)
    if func in ("hsl", "hsla"):
        (h, _), (s, _), (l, _) = nums  # type: ignore[misc]
        r, g, b = hsl_to_srgb(h, _clamp(s / 100.0), _clamp(l / 100.0))
        return (r, g, b, a)
    (lv, lpct), (cv, cpct), (hv, _) = nums  # type: ignore[misc]
    lightness = lv / 100.0 if lpct else lv
    chroma = cv * 0.4 / 100.0 if cpct else cv
    r, g, b = oklch_to_srgb(_clamp(lightness), max(0.0, chroma), hv)
    return (r, g, b, a)


# -- conversions -------------------------------------------------------------

def hsl_to_srgb(h: float, s: float, l: float) -> RGB:
    h = (h % 360.0) / 360.0
    s, l = _clamp(s), _clamp(l)
    if s == 0:
        return (l, l, l)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def hue(t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    return (hue(h + 1 / 3), hue(h), hue(h - 1 / 3))


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    c = _clamp(c)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab_to_linear(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def oklab_to_srgb(L: float, a: float, b: float) -> RGB:
    r, g, bl = _oklab_to_linear(L, a, b)
    return (_from_linear(r), _from_linear(g), _from_linear(bl))


def max_chroma(L: float, H: float, cap: float) -> float:
    """The most OKLCH chroma (up to *cap*) a screen can show at lightness *L*
    and hue *H* without clipping a channel."""
    rad = math.radians(H % 360.0)
    lo, hi = 0.0, cap
    for _ in range(24):
        mid = (lo + hi) / 2
        rgb = _oklab_to_linear(L, mid * math.cos(rad), mid * math.sin(rad))
        if all(-1e-9 <= c <= 1 + 1e-9 for c in rgb):
            lo = mid
        else:
            hi = mid
    return lo


def srgb_to_oklab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_to_linear(_clamp(c)) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklch_to_srgb(L: float, C: float, H: float) -> RGB:
    rad = math.radians(H % 360.0)
    return oklab_to_srgb(L, C * math.cos(rad), C * math.sin(rad))


def srgb_to_oklch(rgb: RGB) -> tuple[float, float, float]:
    L, a, b = srgb_to_oklab(rgb)
    C = math.hypot(a, b)
    H = math.degrees(math.atan2(b, a)) % 360.0 if C > 1e-6 else 0.0
    return (L, C, H)


# -- our own writing ---------------------------------------------------------

def hex_of(rgb: RGB) -> str:
    """Our one formatter: ``#rrggbb``, lower case."""
    return "#" + "".join(f"{int(round(_clamp(c) * 255)):02x}" for c in rgb[:3])


def is_our_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_OUR_HEX_RE.match(value))


def rgb_of(hex_value: str) -> RGB:
    """``#rrggbb`` (ours) to numbers. Raises ValueError for anything else."""
    if not is_our_hex(hex_value):
        raise ValueError("not a #rrggbb colour")
    return tuple(int(hex_value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]


def rgba_css(hex_value: str, alpha: float) -> str:
    """``rgba(r,g,b,a)`` from one of our colours. Alpha has 3 decimals at most."""
    r, g, b = (int(round(c * 255)) for c in rgb_of(hex_value))
    a = round(_clamp(alpha), 3)
    a_text = "1" if a >= 1 else "0" if a <= 0 else (f"{a:.3f}".rstrip("0").lstrip("0") or "0")
    return f"rgba({r},{g},{b},{a_text})"


def composite(fg: RGBA, bg: RGB) -> RGB:
    """*fg* with its alpha, painted over the opaque *bg*."""
    a = _clamp(fg[3])
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))  # type: ignore[return-value]


def mix(a: RGB, b: RGB, share_of_b: float) -> RGB:
    t = _clamp(share_of_b)
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3))  # type: ignore[return-value]


def luminance(rgb: RGB) -> float:
    """WCAG 2 relative luminance."""
    r, g, b = (_to_linear(_clamp(c)) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    """WCAG 2 contrast ratio, 1.0 to 21.0."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def is_dark(rgb: RGB) -> bool:
    return luminance(rgb) < 0.18


def nudge_lightness(rgb: RGB, toward: str, step: float = 0.05) -> RGB:
    """*rgb* made a little darker or lighter in OKLCH, keeping its hue."""
    L, C, H = srgb_to_oklch(rgb)
    L = _clamp(L - step if toward == "darker" else L + step)
    # Very dark and very light colours cannot hold much colour.
    C = min(C, 0.37 * min(L, 1 - L) * 2 + 0.01)
    return oklch_to_srgb(L, C, H)


# -- plain colour words ------------------------------------------------------

#: Where each plain colour word ends, in OKLCH hue degrees, so a look is not
#: described with the wrong colours. Measured on the named colours ("red" is
#: 29, "orange" 71, "gold"
#: 95, "blue" 264, "purple" 328, "hotpink" 352), the old edges called red
#: "orange", orange "yellow", blue "purple" and purple "pink".
_HUES = ((15, "pink"), (38, "red"), (75, "orange"), (100, "gold"), (125, "yellow"),
         (175, "green"), (225, "teal"), (280, "blue"), (340, "purple"),
         (360, "pink"))


def hue_word(rgb: RGB) -> str:
    """One plain word for a colour: "green", "grey", "brown"..."""
    L, C, H = srgb_to_oklch(rgb)
    if C < 0.035:
        if L < 0.25:
            return "black"
        if L > 0.93:
            return "white"
        return "grey"
    name = "red"
    for edge, word in _HUES:
        if H < edge:
            name = word
            break
    if name in ("orange", "gold") and L < 0.55 and C < 0.14:
        return "brown"
    # A soft or dusty red is what people call pink ("blush pink" made
    # readable on white is #9c646f), so a look made from "pink" is not
    # described as "red".
    if name == "red" and C < 0.12 and L >= 0.45:
        return "pink"
    if name == "pink" and L < 0.45:
        return "red"            # a dark pink reads as a deep red
    return name


def shade_word(rgb: RGB) -> str:
    """"deep", "soft", "bright" or "" for a colour."""
    L, C, _ = srgb_to_oklch(rgb)
    if C < 0.035:
        return ""
    if L < 0.45:
        return "deep"
    if C > 0.16:
        return "bright"
    if L > 0.78:
        return "soft"
    return ""


def describe(rgb: RGB) -> str:
    shade, hue = shade_word(rgb), hue_word(rgb)
    return f"{shade} {hue}".strip()


#: The 148 CSS colour names, as data (a general table, not topic knowledge),
#: used to make a look from someone's own colour words ("dark green and
#: orange") when no model is running.
NAMED: dict[str, str] = {
    "aliceblue": "f0f8ff", "antiquewhite": "faebd7", "aqua": "00ffff",
    "aquamarine": "7fffd4", "azure": "f0ffff", "beige": "f5f5dc",
    "bisque": "ffe4c4", "black": "000000", "blanchedalmond": "ffebcd",
    "blue": "0000ff", "blueviolet": "8a2be2", "brown": "a52a2a",
    "burlywood": "deb887", "cadetblue": "5f9ea0", "chartreuse": "7fff00",
    "chocolate": "d2691e", "coral": "ff7f50", "cornflowerblue": "6495ed",
    "cornsilk": "fff8dc", "crimson": "dc143c", "cyan": "00ffff",
    "darkblue": "00008b", "darkcyan": "008b8b", "darkgoldenrod": "b8860b",
    "darkgray": "a9a9a9", "darkgreen": "006400", "darkgrey": "a9a9a9",
    "darkkhaki": "bdb76b", "darkmagenta": "8b008b", "darkolivegreen": "556b2f",
    "darkorange": "ff8c00", "darkorchid": "9932cc", "darkred": "8b0000",
    "darksalmon": "e9967a", "darkseagreen": "8fbc8f", "darkslateblue": "483d8b",
    "darkslategray": "2f4f4f", "darkslategrey": "2f4f4f",
    "darkturquoise": "00ced1", "darkviolet": "9400d3", "deeppink": "ff1493",
    "deepskyblue": "00bfff", "dimgray": "696969", "dimgrey": "696969",
    "dodgerblue": "1e90ff", "firebrick": "b22222", "floralwhite": "fffaf0",
    "forestgreen": "228b22", "fuchsia": "ff00ff", "gainsboro": "dcdcdc",
    "ghostwhite": "f8f8ff", "gold": "ffd700", "goldenrod": "daa520",
    "gray": "808080", "green": "008000", "greenyellow": "adff2f",
    "grey": "808080", "honeydew": "f0fff0", "hotpink": "ff69b4",
    "indianred": "cd5c5c", "indigo": "4b0082", "ivory": "fffff0",
    "khaki": "f0e68c", "lavender": "e6e6fa", "lavenderblush": "fff0f5",
    "lawngreen": "7cfc00", "lemonchiffon": "fffacd", "lightblue": "add8e6",
    "lightcoral": "f08080", "lightcyan": "e0ffff",
    "lightgoldenrodyellow": "fafad2", "lightgray": "d3d3d3",
    "lightgreen": "90ee90", "lightgrey": "d3d3d3", "lightpink": "ffb6c1",
    "lightsalmon": "ffa07a", "lightseagreen": "20b2aa",
    "lightskyblue": "87cefa", "lightslategray": "778899",
    "lightslategrey": "778899", "lightsteelblue": "b0c4de",
    "lightyellow": "ffffe0", "lime": "00ff00", "limegreen": "32cd32",
    "linen": "faf0e6", "magenta": "ff00ff", "maroon": "800000",
    "mediumaquamarine": "66cdaa", "mediumblue": "0000cd",
    "mediumorchid": "ba55d3", "mediumpurple": "9370db",
    "mediumseagreen": "3cb371", "mediumslateblue": "7b68ee",
    "mediumspringgreen": "00fa9a", "mediumturquoise": "48d1cc",
    "mediumvioletred": "c71585", "midnightblue": "191970",
    "mintcream": "f5fffa", "mistyrose": "ffe4e1", "moccasin": "ffe4b5",
    "navajowhite": "ffdead", "navy": "000080", "oldlace": "fdf5e6",
    "olive": "808000", "olivedrab": "6b8e23", "orange": "ffa500",
    "orangered": "ff4500", "orchid": "da70d6", "palegoldenrod": "eee8aa",
    "palegreen": "98fb98", "paleturquoise": "afeeee",
    "palevioletred": "db7093", "papayawhip": "ffefd5", "peachpuff": "ffdab9",
    "peru": "cd853f", "pink": "ffc0cb", "plum": "dda0dd",
    "powderblue": "b0e0e6", "purple": "800080", "rebeccapurple": "663399",
    "red": "ff0000", "rosybrown": "bc8f8f", "royalblue": "4169e1",
    "saddlebrown": "8b4513", "salmon": "fa8072", "sandybrown": "f4a460",
    "seagreen": "2e8b57", "seashell": "fff5ee", "sienna": "a0522d",
    "silver": "c0c0c0", "skyblue": "87ceeb", "slateblue": "6a5acd",
    "slategray": "708090", "slategrey": "708090", "snow": "fffafa",
    "springgreen": "00ff7f", "steelblue": "4682b4", "tan": "d2b48c",
    "teal": "008080", "thistle": "d8bfd8", "tomato": "ff6347",
    "turquoise": "40e0d0", "violet": "ee82ee", "wheat": "f5deb3",
    "white": "ffffff", "whitesmoke": "f5f5f5", "yellow": "ffff00",
    "yellowgreen": "9acd32",
}


def named(word: str) -> Optional[RGB]:
    h = NAMED.get(str(word or "").lower())
    if h is None:
        return None
    return rgb_of("#" + h)


__all__ = ["RGB", "RGBA", "NAMED", "composite", "contrast", "describe",
           "hex_of", "hsl_to_srgb", "hue_word", "is_dark", "is_our_hex",
           "luminance", "mix", "named", "nudge_lightness", "oklab_to_srgb",
           "oklch_to_srgb", "parse", "rgb_of", "rgba_css", "shade_word",
           "srgb_to_oklab", "srgb_to_oklch"]
