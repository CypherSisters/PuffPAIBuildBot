"""Colours: parsing someone else's colour, our own writing, WCAG contrast."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.looks import colour as C

FIX = Path(__file__).parent / "fixtures" / "looks"


@pytest.mark.parametrize("value, want", [
    ("#fff", "#ffffff"), ("#FFF", "#ffffff"), ("#10b981", "#10b981"),
    ("#10b981ff", "#10b981"), ("#0f08", "#00ff00"),
    ("rgb(16, 185, 129)", "#10b981"), ("rgb(16 185 129 / 0.5)", "#10b981"),
    ("rgba(16,185,129,.5)", "#10b981"), ("rgb(100% 0% 0%)", "#ff0000"),
    ("hsl(0, 100%, 50%)", "#ff0000"), ("hsl(120 100% 25%)", "#008000"),
    ("hsla(240,100%,50%,0.3)", "#0000ff"), ("0 0% 100%", "#ffffff"),
    ("36 39% 88%", "#ece3d4"), ("oklch(1 0 0)", "#ffffff"),
    ("oklch(0 0 0)", "#000000"), ("oklch(62.8% 0.2577 29.23)", "#ff0000"),
])
def test_every_format_parses_to_our_hex(value, want):
    got = C.parse(value)
    assert got is not None, value
    assert C.hex_of(got[:3]) == want


@pytest.mark.parametrize("value", [
    "url(https://x.test/a.png)", "var(--x)", "red;}body{color:red", "red",
    "expression(alert(1))", "calc(1px + 2px)", "rgb(1,2,3);", "rgb(1,2)",
    "hsl(1,2%,3%,4,5)", "oklch(0.5 0.1 20) url(x)", "#12345", "#ggg",
    "rgb(1e999, 2, 3)", "rgb(nan,1,1)", "x" * 10_000, "", None, 5,
    "rgb(1,2,3)/**/", "\\72 gb(1,2,3)", "rgb(1 / 2 / 3)",
])
def test_hostile_or_unknown_strings_are_refused(value):
    assert C.parse(value) is None


def test_hsl_matches_the_shadcn_pairs():
    """shadcn's colour list gives hex and hsl for each colour. Its oklch column
    is rounded to two decimals (too coarse to test against), and one row
    (violet 950) has a hex that belongs to another colour, so it is skipped."""
    data = json.loads((FIX / "shadcn_colors_cut.json").read_text(encoding="utf-8"))
    checked = 0
    for name, rows in data.items():
        for row in rows:
            if (name, row["scale"]) == ("violet", 950):
                continue
            h = C.parse(row["hsl"])
            assert h is not None
            assert C.parse(row["oklch"].replace(",", " ")) is not None
            want = C.rgb_of(row["hex"])
            assert max(abs(a - b) * 255 for a, b in zip(h[:3], want)) <= 2.5, row  # hsl rounded to 0.1
            checked += 1
    assert checked >= 50


@pytest.mark.parametrize("value, want", [
    # Reference values from the OKLab author's own conversion.
    ("oklch(0.627955 0.257683 29.2339)", "#ff0000"),
    ("oklch(0.86644 0.294827 142.4953)", "#00ff00"),
    ("oklch(0.452014 0.313214 264.052)", "#0000ff"),
])
def test_oklch_reference_values(value, want):
    assert C.hex_of(C.parse(value)[:3]) == want


def test_srgb_oklch_round_trip():
    for hex_value in ("#10b981", "#7033ff", "#fdfdfd", "#1a2238", "#e63946"):
        rgb = C.rgb_of(hex_value)
        back = C.oklch_to_srgb(*C.srgb_to_oklch(rgb))
        assert C.hex_of(back) == hex_value


def test_wcag_known_values():
    white, black = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    assert round(C.contrast(black, white), 1) == 21.0
    assert round(C.contrast(C.rgb_of("#777777"), white), 2) == 4.48
    assert round(C.contrast(white, white), 1) == 1.0


def test_nudging_keeps_the_hue_and_moves_the_lightness():
    rgb = C.rgb_of("#7033ff")
    darker = C.nudge_lightness(rgb, "darker")
    assert C.srgb_to_oklch(darker)[0] < C.srgb_to_oklch(rgb)[0]
    assert abs(C.srgb_to_oklch(darker)[2] - C.srgb_to_oklch(rgb)[2]) < 8
    assert C.srgb_to_oklch(C.nudge_lightness(rgb, "lighter"))[0] > C.srgb_to_oklch(rgb)[0]


def test_our_writing_is_strict():
    assert C.hex_of((1.2, -1, 0.5)) == "#ff0080"
    assert C.rgba_css("#102030", 0.72) == "rgba(16,32,48,.72)"
    assert C.rgba_css("#102030", 1) == "rgba(16,32,48,1)"
    with pytest.raises(ValueError):
        C.rgb_of("#FFF")


def test_plain_colour_words():
    assert C.hue_word(C.rgb_of("#2e8b57")) == "green"
    assert C.hue_word(C.rgb_of("#808080")) == "grey"
    assert C.hue_word(C.rgb_of("#7033ff")) == "purple"
    assert C.hue_word(C.rgb_of("#8b4513")) == "brown"
    assert C.named("forestgreen") is not None and C.named("url") is None
