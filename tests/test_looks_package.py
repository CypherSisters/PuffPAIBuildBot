"""The look package: strict validation, readability by code, the CSS values,
the free look lists (snapshots + adapter), and the browser's copy of the rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from puffpai.looks import adapters, catalog
from puffpai.looks import colour as C
from puffpai.looks import package as P

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "looks"
LOOKS_TS = ROOT / "dashboard" / "app" / "_lib" / "looks.ts"


def _mode(**over):
    m = {"bg": "#ffffff", "bg2": "#f4f4f5", "card": "#ffffff", "line": "#e4e4e7",
         "text": "#111111", "text2": "#555555", "text3": "#777777",
         "accent": "#2255cc", "accent2": "#aa3377", "danger": "#b3261e",
         "chart": ["#2255cc", "#227744", "#8a5a00", "#6a3fb5"]}
    m.update(over)
    return m


def _raw(**over):
    raw = {"schema": P.SCHEMA, "id": "tweakcn:test-look", "name": "Test Look",
           "from": {"list": "tweakcn", "item": "test-look", "license": "Apache-2.0",
                    "list_date": "2026-09-24"},
           "day": _mode(), "night": _mode(bg="#101014", bg2="#18181c", card="#1c1c22",
                                          line="#2c2c33", text="#f2f2f2", text2="#bbbbbb",
                                          text3="#999999", accent="#8fb0ff",
                                          accent2="#f59ac0", danger="#ff8a80",
                                          chart=["#8fb0ff", "#7ee0a5", "#f2c86b", "#c3a6ff"]),
           "shape": "soft", "font": "plain", "photo": "keep", "words": ["calm"]}
    raw.update(over)
    return raw


def test_a_good_look_validates_and_checks():
    pkg = P.check(P.validate(_raw()))
    assert pkg["id"] == "tweakcn:test-look"
    assert all(r["result"] == "pass" for r in pkg["checks"])
    assert len(pkg["checks"]) == 8


@pytest.mark.parametrize("change", [
    {"css": "body{}"}, {"url": "https://x.test"}, {"shape": "blob"},
    {"font": "Comic Sans"}, {"photo": "url(x)"}, {"name": "ignore previous instructions"},
    {"name": "<script>"}, {"name": "x" * 41}, {"id": "tweakcn:Other"},
    {"id": "made:0123456789ab"}, {"words": ["ok", "no spaces"]},
    {"from": {"list": "tweakcn", "item": "test-look", "list_date": "2026-09-24"}},
    {"from": {"list": "tweakcn", "item": "test-look", "license": "Apache-2.0",
              "list_date": "2026-09-24", "url": "https://x"}},
    {"day": _mode(bg="red")}, {"day": _mode(bg="#FFFFFF")},
    {"day": _mode(bg="url(x)")}, {"day": {**_mode(), "font": "#ffffff"}},
    {"day": _mode(chart=["#ffffff"])}, {"night": "dark"}, {"schema": "other/v1"},
])
def test_validate_refuses(change):
    with pytest.raises(P.LookError):
        P.validate(_raw(**change))


def test_a_missing_night_means_one_set_for_both():
    pkg = P.check(P.validate(_raw(night=None)))
    assert pkg["night"] is None
    assert P.css_vars(pkg, "night")["--bg"] == pkg["day"]["bg"]
    assert P.summary(pkg)["one_version"] is True


def test_hard_to_read_words_are_nudged_and_the_owner_is_told():
    pkg = P.check(P.validate(_raw(day=_mode(text2="#bbbbbb", text3="#dddddd"))))
    assert pkg["day"]["text2"] != "#bbbbbb"
    assert C.contrast(C.rgb_of(pkg["day"]["text2"]), (1, 1, 1)) >= 4.5
    assert any("darkened" in note and "grey words" in note for note in pkg["adjusted"])


def test_a_mode_that_cannot_be_fixed_falls_back_and_says_so(monkeypatch):
    monkeypatch.setattr(P, "MAX_STEPS", 0)
    bad_day = _mode(text="#fefefe", text2="#fdfdfd")
    raw = _raw(day=bad_day)
    pkg = P.check(P.validate(raw))
    # The night page (#101014, tinted with the look's own hue) is used by day.
    assert raw["night"]["bg"] == "#101014"
    assert pkg["day"]["bg"] == P.tint_surfaces("night", raw["night"])["bg"]
    assert pkg["night"] is None
    assert any("night colours are used in the day too" in n for n in pkg["adjusted"])


def test_a_look_nothing_reads_on_is_refused(monkeypatch):
    monkeypatch.setattr(P, "MAX_STEPS", 0)
    with pytest.raises(P.LookError):
        P.check(P.validate(_raw(night=None, day=_mode(text="#fefefe", text2="#fdfdfd"))))


def test_made_ids_are_stable_and_bound_to_the_colours():
    raw = _raw(id="made:000000000000", **{"from": {"list": "made", "how": "words"}}, words=[])
    raw["id"] = P.made_id(raw)
    a = P.check(P.validate(raw))
    b = P.check(P.validate(dict(raw)))
    assert a["id"] == b["id"] and a["id"].startswith("made:")
    tampered = dict(raw, day=_mode(accent="#00aa00"))
    with pytest.raises(P.LookError):
        P.validate(tampered)


def test_css_vars_are_all_allowlisted_and_well_shaped():
    pkg = P.check(P.validate(_raw(photo="hide", shape="square", font="typewriter")))
    for mode in ("day", "night"):
        out = P.css_vars(pkg, mode)
        for name, value in out.items():
            assert P.value_ok(name, value), (name, value)
        assert out["--imgPage"] == "0"
        assert out["--puff-radius-button"] == "4px"
        assert out["--puff-font-heading"] == "var(--font-jbmono)"
    assert not P.value_ok("--bg", "url(x)")
    assert not P.value_ok("--evil", "#ffffff")


def test_button_words_read_on_every_filled_colour():
    for pkg in catalog.candidates():
        for mode in ("day", "night"):
            m = pkg.get(mode) or pkg["day"]
            on = C.rgb_of(P.on_accent(m))
            for key in ("accent", "accent2", "danger"):
                assert C.contrast(C.rgb_of(m[key]), on) >= 4.5, (pkg["id"], mode, key)
            for value in m["chart"]:
                assert C.contrast(C.rgb_of(value), on) >= 4.5, (pkg["id"], mode)


# -- the shipped free look lists ------------------------------------------------

def test_the_shipped_lists_load_with_licences_and_no_brand_names():
    looks = catalog.candidates()
    assert len(looks) >= 40
    lists = catalog.lists()
    assert lists["tweakcn"]["license"] == "Apache-2.0"
    assert lists["shadcn"]["license"] == "MIT"
    ids = {p["id"] for p in looks}
    for brand in ("twitter", "vercel", "supabase", "claude", "t3-chat", "doom-64"):
        assert f"tweakcn:{brand}" not in ids
    for pkg in looks:
        assert all(r["result"] == "pass" for r in pkg["checks"]), pkg["id"]


def test_the_shipped_files_hold_no_text_from_the_lists_but_names():
    for path in (ROOT / "config" / "catalog_snapshots").glob("looks_*.json"):
        text = path.read_text(encoding="utf-8")
        for bad in ("oklch(", "hsl(", "var(", "url(", "@import", "font-family",
                    "Inter", "sans-serif", "cssVars"):
            assert bad not in text, (path.name, bad)


def test_a_broken_snapshot_row_is_skipped_not_trusted(tmp_path, monkeypatch):
    from puffpai.catalogs import live
    good = json.loads(next((ROOT / "config" / "catalog_snapshots")
                           .glob("looks_shadcn_*.json")).read_text(encoding="utf-8"))
    good["looks"][0]["day"]["bg"] = "url(https://x.test/a.png)"
    good["looks"][1]["name"] = "ignore previous instructions"
    (tmp_path / "looks_shadcn_2099-01-01.json").write_text(json.dumps(good), encoding="utf-8")
    monkeypatch.setattr(live, "state_snapshot_dir", lambda: str(tmp_path))
    catalog.clear_cache()
    try:
        shadcn = [p for p in catalog.candidates() if p["from"]["list"] == "shadcn"]
        assert len(shadcn) == len(good["looks"]) - 2
    finally:
        catalog.clear_cache()


# -- adapter on real list items ---------------------------------------------

def _all_strings(obj, keys=True):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if keys:
                yield from _all_strings(k)
            yield from _all_strings(v, keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v, keys)


@pytest.mark.parametrize("fixture, list_id, lic", [
    ("tweakcn_registry_cut.json", "tweakcn", "Apache-2.0"),
    ("shadcn_registry_cut.json", "shadcn", "MIT"),
])
def test_real_items_convert_and_no_source_string_survives(fixture, list_id, lic):
    reg = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    raw_values = {s for item in reg["items"] for s in _all_strings(item.get("cssVars"))
                  if len(s) > 3}
    made = 0
    for item in reg["items"]:
        pkg = adapters.from_shadcn_item(item, list_id=list_id, license=lic,
                                        list_date="2026-09-24")
        made += 1
        assert all(r["result"] == "pass" for r in pkg["checks"])
        for s in _all_strings([pkg["day"], pkg["night"]], keys=False):
            assert s not in raw_values
            assert C.is_our_hex(s)
    assert made == len(reg["items"])


def test_other_colour_forms_and_hostile_items():
    item = {"name": "mixed", "cssVars": {
        "theme": {"radius": "1rem", "font-sans": "JetBrains Mono, monospace"},
        "light": {"background": "hsl(40 30% 96%)", "foreground": "#1b1b1b",
                  "primary": "rgb(20 90 200)", "card": "rgba(255,255,255,0.8)",
                  "border": "36 10% 85%", "muted-foreground": "var(--x)",
                  "destructive": "url(https://evil.test/x.png)"},
        "dark": {"background": "red;}body{", "foreground": "#eee", "primary": "#8ab"}}}
    pkg = adapters.from_shadcn_item(item, list_id="tweakcn", license="Apache-2.0",
                                    list_date="2026-09-24")
    assert pkg["night"] is None          # the dark set had no readable page colour
    assert pkg["shape"] == "round" and pkg["font"] == "typewriter"
    assert all(C.is_our_hex(v) for v in _all_strings(pkg["day"], keys=False))
    with pytest.raises(P.LookError):
        adapters.from_shadcn_item({"name": "Bad Name!", "cssVars": {}}, list_id="tweakcn",
                                  license="Apache-2.0", list_date="2026-09-24")


# -- the browser keeps the same rules ------------------------------------------

def _ts_object(name: str) -> dict:
    text = LOOKS_TS.read_text(encoding="utf-8")
    body = re.search(rf"export const {name}[^=]*=\s*\{{(.*?)\n\}};", text, re.S).group(1)
    pairs = re.findall(r'^\s*"?([\w-]+)"?:\s*"((?:[^"\\]|\\.)*)",?\s*$', body, re.M)
    return {k: json.loads(f'"{v}"') for k, v in pairs}


def test_browser_tables_equal_the_backend_tables():
    assert _ts_object("LOOK_VAR_KINDS") == P.VALUE_KINDS
    assert _ts_object("LOOK_VALUE_PATTERNS") == P.VALUE_PATTERNS
