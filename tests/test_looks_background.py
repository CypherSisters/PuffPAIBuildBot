"""A look changes the WHOLE look, background included.

The cloud background was stuck there and did not leave. Every layer the dashboard paints behind the page is under a look value
now; a look carries one background from a fixed set (clouds, stars, plain,
soft); readability is checked against that background; undo brings it back.
"""

from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pytest

from puffpai import api_server, plain_words
from puffpai.looks import catalog
from puffpai.looks import cli as looks_cli
from puffpai.looks import colour as C
from puffpai.looks import package as P
from puffpai.looks import store
from puffpai.security import egress_ledger

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
THEME_CSS = DASH / "css" / "_theme.css"
AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _mode(**over):
    m = {"bg": "#ffffff", "bg2": "#f4f4f5", "card": "#ffffff", "line": "#e4e4e7",
         "text": "#111111", "text2": "#555555", "text3": "#777777",
         "accent": "#2255cc", "accent2": "#aa3377", "danger": "#b3261e",
         "chart": ["#2255cc", "#227744", "#8a5a00", "#6a3fb5"]}
    m.update(over)
    return m


DARK = _mode(bg="#101014", bg2="#18181c", card="#1c1c22", line="#2c2c33",
             text="#f2f2f2", text2="#bbbbbb", text3="#999999", accent="#8fb0ff",
             accent2="#f59ac0", danger="#ff8a80",
             chart=["#8fb0ff", "#7ee0a5", "#f2c86b", "#c3a6ff"])


def _raw(**over):
    raw = {"schema": P.SCHEMA, "id": "tweakcn:test-look", "name": "Test Look",
           "from": {"list": "tweakcn", "item": "test-look", "license": "Apache-2.0",
                    "list_date": "2026-09-24"},
           "day": _mode(), "night": DARK, "shape": "soft", "font": "plain",
           "photo": "keep", "words": []}
    raw.update(over)
    return raw


# -- the package ---------------------------------------------------------------

def test_the_rule_light_gets_soft_dark_gets_stars():
    assert P.check(P.validate(_raw()))["background"] == "soft"
    dark = P.check(P.validate(_raw(day=DARK, night=None)))
    assert dark["background"] == "stars"
    # Every shipped look gets one, and it is never the clouds by default.
    assert {pkg["background"] for pkg in catalog.candidates()} <= {"soft", "stars"}


def test_only_the_fixed_backgrounds_are_taken():
    for bad in ("url(https://evil.test/x.png)", "/design-cloud-1.jpg", "Stars", 3, ""):
        with pytest.raises(P.LookError):
            P.validate(_raw(background=bad))
    for good in P.BACKGROUNDS:
        assert P.check(P.validate(_raw(background=good)))["background"] == good


def test_a_made_look_keeps_its_name_tag_when_its_background_changes():
    raw = _raw(id="made:000000000000", **{"from": {"list": "made", "how": "words"}})
    raw["id"] = P.made_id(raw)
    pkg = P.check(P.validate(raw))
    for bg in ("stars", "plain", "soft"):
        assert P.with_background(pkg, bg)["id"] == pkg["id"]
    # Over the clouds its colours may be made easier to read; then the tag
    # follows the colours, and the stored look still passes its own check.
    over = P.with_background(pkg, "clouds")
    assert over["id"] == P.made_id(over) and P.validate(over)["id"] == over["id"]


def _img(vars_):
    return {k: vars_[k] for k in P.IMG_VARS}


@pytest.mark.parametrize("mode", P.MODES)
def test_each_background_sets_every_layer(mode):
    base = P.check(P.validate(_raw()))
    m = base[mode]
    stars = P.css_vars(P.with_background(base, "stars"), mode)
    plain = P.css_vars(P.with_background(base, "plain"), mode)
    soft = P.css_vars(P.with_background(base, "soft"), mode)
    clouds = P.css_vars(P.with_background(base, "clouds"), mode)
    for got in (stars, plain, soft):
        assert set(_img(got).values()) == {"0"}, "no cloud picture off the clouds"
        assert got["--puff-vignette"] == P.CLEAR
    if mode == "day":
        # "Stars" is a night sky: in day mode it is the soft wash.
        assert stars == soft
    else:
        assert stars["--puff-stars"] == "1" and stars["--puff-stars-more"] == "1"
        assert stars["--puff-scene-glow1"] == P.CLEAR and "--puff-scene-end" not in stars
    assert plain["--puff-stars"] == "0"
    assert plain["--puff-stars-more"] == soft["--puff-stars-more"] == clouds["--puff-stars-more"] == "0"
    assert soft["--puff-stars"] == "0" and soft["--puff-hero-glow"] == "1"
    assert plain["--puff-scene-end"] == m["bg"]
    assert plain["--puff-scene-glow1"] == plain["--puff-scene-glow2"] == P.CLEAR
    assert "--puff-scene-glow1" not in soft          # the look's own colour blobs
    assert any(v != "0" for v in _img(clouds).values())
    assert clouds["--puff-vignette"] == m["bg"]
    for got in (stars, plain, soft, clouds):
        for name, value in got.items():
            assert P.value_ok(name, value), (name, value)
            assert "url" not in value


def test_readability_is_checked_against_the_clouds():
    # Faint grey words that read on white do not read over the clouds: they
    # are darkened, or the pictures are faded, never left hard to read.
    pkg = P.check(P.validate(_raw(day=_mode(text3="#8a8a8a", text2="#6f6f6f"),
                                  night=None, background="plain")))
    assert pkg["day"]["text3"] == "#8a8a8a"      # fine on a plain white page
    over = P.with_background(pkg, "clouds")
    amount = P.cloud_amount("day", over["day"])
    surfaces = P._surfaces("day", over["day"], "clouds", amount)
    assert all(r["result"] == "pass" for r in over["checks"])
    assert P._passes("day", over["day"], surfaces)
    assert over["day"]["text3"] != "#8a8a8a" or amount < 1
    assert over["adjusted"], "the owner is told what changed"
    for note in over["adjusted"]:
        assert not plain_words.check(note), note


def test_a_faded_cloud_amount_reaches_the_page():
    # A look that cannot be fixed by colour alone has its pictures faded.
    pale = _mode(text="#6a6a6a", text2="#6a6a6a", text3="#8f8f8f")
    pkg = P.with_background(P.check(P.validate(_raw(day=pale, night=None))), "clouds")
    amount = P.cloud_amount("day", pkg["day"])
    got = P.css_vars(pkg, "day")
    expect = P._amount(P.IMG_AMOUNTS["day"]["--imgPage"] * amount)
    assert got["--imgPage"] == expect


def test_soft_checks_the_colour_washes_too():
    pkg = P.check(P.validate(_raw()))
    wash = C.composite((*C.rgb_of(pkg["day"]["accent"]), P.ALPHA["day"]["glow1"] * P.WASH_SHARE),
                       C.rgb_of(pkg["day"]["bg"]))
    assert any(abs(sum(a) - sum(wash)) < 1e-9 for a in P._surfaces("day", pkg["day"], "soft"))


# -- the dashboard keeps the same numbers and puts every layer under a look ------

def _theme_block(selector: str) -> str:
    css = THEME_CSS.read_text(encoding="utf-8")
    return css.split(selector, 1)[1].split("}", 1)[0]


def test_cloud_amounts_and_own_colours_equal_the_css():
    for mode in P.MODES:
        block = _theme_block(f'html[data-puff-theme="{mode}"]')
        for name, value in P.IMG_AMOUNTS[mode].items():
            got = re.search(rf"{re.escape(name)}:\s*([0-9.]+);", block).group(1)
            assert float(got) == value, (mode, name)
        for key in ("bg", "text"):
            got = re.search(rf"--{key}:\s*(#[0-9A-Fa-f]{{6}});", block).group(1)
            assert got.lower() == P.OWN[mode][key]


def test_every_background_layer_in_the_css_is_under_a_look_value():
    css = "\n".join(p.read_text(encoding="utf-8")
                    for p in (DASH / "css").glob("*.css"))
    # Each rule painting a cloud picture shows it at a look-controlled amount.
    for rule in re.findall(r"([^{}]+)\{([^{}]*url\(/design-cloud[^{}]*)\}", css):
        selector, body = rule
        if "background-image" in body and "opacity" not in body:
            continue  # a picture swap inside .cyber-card::before (same layer)
        assert re.search(r"opacity:\s*var\(--img", body), selector.strip()
    assert "opacity: var(--puff-stars, 0)" in css
    assert "opacity: var(--puff-stars, 1)" in css
    assert "opacity: var(--puff-stars-more, 0)" in css
    assert "var(--puff-scene-glow1, var(--glow1))" in css
    assert "var(--puff-scene-end, var(--bg2))" in css
    assert "opacity: var(--puff-hero-glow, 1)" in css


#: Cloud pictures used as CONTENT, not as a background (a small avatar and a
#: music cover stand-in). They are pictures inside cards, like book covers.
_CONTENT_PICTURES = {
    ("TranscriptTile.tsx", "/design-cloud-1.jpg"),
    ("WidgetBoard.tsx", "/design-cloud-5.jpg"),
}


def test_every_cloud_picture_in_the_pages_is_under_a_look_value():
    for path in (DASH / "app").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'src="(/design-cloud-\d\.jpg)"', text):
            if (path.name, m.group(1)) in _CONTENT_PICTURES:
                continue
            near = text[m.end():m.end() + 400]
            assert re.search(r'opacity:\s*"var\(--img', near), (path.name, m.group(1))
    tile = (DASH / "app" / "_components" / "Widgets" / "DesignTile.tsx").read_text(
        encoding="utf-8")
    assert 'imgOpacity ?? "var(--imgTile)"' in tile
    talk = (DASH / "app" / "dashboard" / "talk" / "page.tsx").read_text(encoding="utf-8")
    assert "var(--puff-vignette, var(--bg))" in talk


# -- choosing a background, and undo ----------------------------------------------

def test_the_bot_own_look_can_change_its_background_and_undo(client):
    body = client.get("/looks", headers=AUTH).get_json()
    assert body["background"] == "clouds" and body["vars"] is None
    assert [b["label"] for b in body["backgrounds"]] == ["Clouds", "Stars", "Plain",
                                                        "Soft colour", "Petals",
                                                        "Confetti"]
    got = client.post("/looks/background", headers=AUTH,
                      json={"background": "stars"}).get_json()
    assert got["ok"] and got["changed"] and got["background"] == "stars"
    assert got["current"] is None
    assert got["vars"]["night"]["--puff-stars"] == "1"
    assert got["vars"]["day"]["--imgPage"] == "0"
    back = client.post("/looks/undo", headers=AUTH).get_json()
    assert back["background"] == "clouds" and back["vars"] is None
    for text in (got["message"], back["message"], body["background_line"]):
        assert not plain_words.check(text), text


def test_a_look_background_change_is_undone_with_the_look(client):
    client.post("/looks/use", headers=AUTH, json={"id": "tweakcn:cosmic-night"})
    used = client.get("/looks", headers=AUTH).get_json()
    assert used["background"] == "soft"
    assert used["vars"] == used["current"]["vars"]
    assert used["vars"]["day"]["--imgPage"] == "0"
    clouds = client.post("/looks/background", headers=AUTH,
                         json={"background": "Soft colour"}).get_json()
    assert clouds["changed"] is False and "already" in clouds["message"]
    clouds = client.post("/looks/background", headers=AUTH,
                         json={"background": "clouds"}).get_json()
    assert clouds["current"]["id"] == "tweakcn:cosmic-night"
    assert clouds["background"] == "clouds" and clouds["vars"]["day"]["--imgPage"] != "0"
    back = client.post("/looks/undo", headers=AUTH).get_json()
    assert back["background"] == "soft" and back["current"]["id"] == "tweakcn:cosmic-night"
    reset = client.post("/looks/reset", headers=AUTH).get_json()
    assert reset["background"] == "clouds" and reset["vars"] is None
    # ("Keep it" brought the clouds back) the background she
    # picks stays with every look she uses, so picking the same look again
    # keeps "plain"; going back to the bot's own look forgets the choice.
    client.post("/looks/use", headers=AUTH, json={"id": "tweakcn:cosmic-night"})
    client.post("/looks/background", headers=AUTH, json={"background": "plain"})
    again = store.use("tweakcn:cosmic-night")
    assert not again["changed"] and store.current_background() == "plain"
    store.reset()
    assert store.use("tweakcn:cosmic-night")["changed"]
    assert store.current_background() == "soft"


def test_bad_backgrounds_are_refused_in_plain_words(client):
    assert client.post("/looks/background", json={"background": "stars"}).status_code == 401
    for bad in ("url(https://evil.test/x.png)", "", None, 7, "x" * 300):
        r = client.post("/looks/background", headers=AUTH, json={"background": bad})
        assert r.status_code == 400
        assert not plain_words.check(r.get_json()["message"])
        assert "Clouds, Stars, Plain, Soft colour" in r.get_json()["message"]


def test_no_outside_request_while_changing_backgrounds(client):
    before = egress_ledger.read(hours=1)
    for bg in ("stars", "plain", "soft", "clouds"):
        client.post("/looks/background", headers=AUTH, json={"background": bg})
    client.post("/looks/undo", headers=AUTH)
    assert egress_ledger.read(hours=1) == before


def _run(action, *words):
    out = io.StringIO()
    args = argparse.Namespace(looks_action=action, words=list(words), no_brain=True)
    return looks_cli.cmd_looks(args, out=out), out.getvalue()


def test_the_terminal_changes_the_background_too():
    code, shown = _run("show")
    assert code == 0 and "Background: Clouds." in shown
    assert "puffpai looks background" in shown
    code, said = _run("background", "stars")
    assert code == 0 and "stars" in said and "Reload" in said
    code, shown = _run("show")
    assert "Background: Stars." in shown
    code, bad = _run("background", "rainbow")
    assert code == 2 and "Clouds, Stars, Plain, Soft colour" in bad
    code, undone = _run("undo")
    assert code == 0
    assert "Background: Clouds." in _run("show")[1]
    code, found = _run("find", "outer", "space")
    assert "Background: Soft colour." in found
    for text in (shown, said, bad, undone, found):
        assert not plain_words.check(text), text
    from puffpai import cli
    args = cli.build_parser().parse_args(["looks", "background", "plain"])
    assert args.looks_action == "background" and args.words == ["plain"]


def test_checking_again_changes_nothing_on_any_background():
    for pkg in catalog.candidates()[:12]:
        for bg in P.BACKGROUNDS:
            once = P.with_background(pkg, bg)
            twice = P.with_background(once, bg)
            assert (once["day"], once["night"], once["id"]) == \
                (twice["day"], twice["night"], twice["id"]), (pkg["id"], bg)


def test_a_light_day_dark_night_look_never_shows_the_clouds_at_night():
    """Light by day, dark by night: the rule gives it 'soft' (day colours
    decide), and at night no cloud layer is left on for soft, stars or plain."""
    split = [pkg for pkg in catalog.candidates()
             if not C.is_dark(C.rgb_of(pkg["day"]["bg"]))
             and C.is_dark(C.rgb_of(pkg["night"]["bg"]))]
    assert split, "the shipped lists have light-day, dark-night looks"
    for pkg in split:
        assert pkg["background"] == "soft", pkg["id"]
        for background in ("soft", "stars", "plain"):
            worn = pkg if background == "soft" else P.with_background(pkg, background)
            night = P.css_vars(worn, "night")
            assert all(night[name] == "0" for name in P.IMG_VARS), (pkg["id"], background)
            assert night["--puff-vignette"] == P.CLEAR, (pkg["id"], background)
            assert night["--puff-stars-more"] == ("1" if background == "stars" else "0")


def test_petals_and_confetti_are_themed_layers_over_the_soft_wash():
    """Looks for a wedding wanted a background that
    fits the theme. Each new layer is under its own look value."""
    css = THEME_CSS.read_text(encoding="utf-8")
    assert "opacity: var(--puff-petals, 0)" in css
    assert "opacity: var(--puff-confetti, 0)" in css
    base = P.check(P.validate(_raw()))
    for bg, on in (("petals", "--puff-petals"), ("confetti", "--puff-confetti")):
        worn = P.with_background(base, bg)
        for mode in P.MODES:
            got = P.css_vars(worn, mode)
            assert got[on] == "1"
            assert got["--puff-stars-more"] == "0"
        assert P.BACKGROUND_LABELS[bg]
    plain = P.css_vars(P.with_background(base, "plain"), "day")
    assert plain["--puff-petals"] == plain["--puff-confetti"] == "0"


def test_petals_and_confetti_stay_soft_at_the_edges_not_a_tiled_pattern():
    """Tiled petals read as grey specks behind text and tiles."""
    css = THEME_CSS.read_text(encoding="utf-8")
    for layer in ("html::before {", "html::after {"):
        block = css[css.index(layer):]
        block = block[:block.index("}")]
        assert "background-repeat: no-repeat;" in block
        assert "background-size" not in block
        assert "z-index: -1" in block and "pointer-events: none" in block
