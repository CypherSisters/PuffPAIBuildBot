"""The wedding run's home page, Apps page and words.

Findings this pins:

* item 2: the home page showed no app and no Mood board, because the front
  page drew its apps only when it also had live cards. The Apps page never
  listed the Mood board, so "Choose a pictures folder there" pointed nowhere.
* item 18: the empty persona app ("WeddingBot", "Scaffolded from your goal")
  was the only tile in the home page's Apps widget.
* item 7: the floating Puff layer took every click in its square.
* items 15-16: em dashes and jargon in BuildBot's own words.
* item 10: the Developer Vault card said "opened with your PIN" while locked.
* item 17: day-mode stars read as grey dust.

The browser behaviour itself is proven headless (dev-ui_final proof); these
are the unit halves. No network, no model.

Run:
    pytest tests/test_dashboard_home_and_apps_lists.py
"""
from __future__ import annotations

import os
import re

import pytest

from puffpai import app_data as app_data_mod
from puffpai import app_registry, front_page, persona_scaffold
from puffpai.looks import colour as C
from puffpai.looks import package
from puffpai.wizard import first_look

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard", "app")


def _read(*parts: str) -> str:
    with open(os.path.join(DASH, *parts), encoding="utf-8") as fh:
        return fh.read()


# -- item 18: a persona with nothing in it -----------------------------------

@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()
    yield tmp_path
    app_registry.rescan()


def _owner_app(app_id: str) -> dict:
    return next(a for a in persona_scaffold.owner_apps() if a["id"] == app_id)


def test_a_persona_with_no_list_and_no_row_is_empty(owner):
    persona_scaffold.scaffold("weddingbot", name="WeddingBot",
                              goal="I want to build a wedding planner bot.")
    assert _owner_app("weddingbot")["lists"] == []
    assert _owner_app("weddingbot")["empty"] is True


def test_a_persona_with_a_list_is_not_empty_even_before_its_first_row(owner):
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    assert _owner_app("gardenbot")["empty"] is False


def test_a_persona_with_a_saved_row_under_an_old_key_is_not_empty(owner):
    persona_scaffold.scaffold("weddingbot", name="WeddingBot")
    ad = app_data_mod.AppData()
    if "weddingbot" not in ad.VALID_APPS:
        pytest.skip("this build keeps no store for a list-less persona")
    ad.set_data("weddingbot", {"guests": [{"name": "Leo Park"}]})
    assert _owner_app("weddingbot")["empty"] is False


def test_an_unreadable_store_shows_the_app(owner, monkeypatch):
    persona_scaffold.scaffold("weddingbot", name="WeddingBot")

    class Broken:
        def get_data(self, _app):
            raise app_data_mod.AppDataUnreadable("torn")

    monkeypatch.setattr(app_data_mod, "AppData", Broken)
    assert _owner_app("weddingbot")["empty"] is False


# -- item 2: the home page and the Apps page ----------------------------------

def test_the_front_page_draws_its_apps_without_any_live_card():
    src = _read("_components", "Front", "FrontPage.tsx")
    assert "cards.length > 0 && <BotAppsGrid" not in src
    assert re.search(r"^\s*<BotAppsGrid front=\{front\} />", src, re.M)
    grid = _read("_components", "Front", "BotAppsGrid.tsx")
    # The grid itself shows the apps when there are no cards.
    assert "if (cards.length === 0) return <AppsSection front={front} />;" in grid


def test_the_apps_page_lists_what_toolsmith_added_and_hides_the_empty_persona():
    page = _read("dashboard", "apps", "_components", "AppsIndex.tsx")
    assert "appsToList(build, personas, added)" in page
    assert "getFront()" in page and "usePersonaApps()" in page
    lib = _read("dashboard", "apps", "_lib", "appsIndex.ts")
    assert "export function appsToList(" in lib
    assert 'HOME_HREF = "/dashboard"' in lib
    # The home page has no hardcoded app launcher any more: one tile per
    # installed thing (GET /installed), so an empty persona gets no tile.
    home = _read("dashboard", "page.tsx")
    assert "launcherApps" not in home and "getInstalled()" in home


# -- item 7: the floating Puff takes clicks only on herself ---------------------

def test_only_the_visible_puff_takes_pointer_events():
    src = _read("dashboard", "_components", "FloatingPuff.tsx")
    layer = re.search(r'className="([^"]*fixed z-\[70\][^"]*)"', src)
    assert layer and "pointer-events-none" in layer.group(1).split()
    hit = re.search(r'data-puff-hit="1"\s+className="([^"]*)"', src)
    assert hit and {"pointer-events-auto", "rounded-full"} <= set(hit.group(1).split())
    resize = re.search(r'aria-label="Resize Puff"[\s\S]{0,400}?className="([^"]*)"', src)
    assert resize and "pointer-events-auto" in resize.group(1).split()


def test_puff_goes_back_to_her_corner_when_the_window_changes_unless_moved():
    src = _read("dashboard", "_components", "FloatingPuff.tsx")
    assert "function defaultPos(size: number): Pos" in src
    assert "function placedByOwner(): boolean" in src
    on_resize = src[src.index("const onResize = () =>"):]
    on_resize = on_resize[:on_resize.index("window.addEventListener")]
    assert "placedByOwner()" in on_resize and "defaultPos(" in on_resize


# -- items 15-16: her words -------------------------------------------------------

#: Every page the wedding run read an em dash on, and the words it used.
EM_DASH_PAGES = (
    ("dashboard", "apps", "_components", "AppsIndex.tsx"),
    ("dashboard", "chat", "page.tsx"),
    ("dashboard", "page.tsx"),
    ("_components", "Widgets", "JetsonTile.tsx"),
    ("_components", "Front", "FrontPage.tsx"),
    ("dashboard", "_components", "FloatingPuff.tsx"),
)


@pytest.mark.parametrize("parts", EM_DASH_PAGES, ids=lambda p: "/".join(p))
def test_no_em_dash_in_the_words_on_these_pages(parts):
    from test_plain_words_in_ui_strings import tsx_strings
    hits = [t for _, t in tsx_strings(os.path.join(DASH, *parts)) if "—" in t]
    assert hits == []


def test_the_words_the_wedding_run_flagged_are_gone():
    apps = _read("dashboard", "apps", "_components", "AppsIndex.tsx")
    # apps may arrive with example entries,
    # so the page never says they start empty
    assert "Some come with example entries of their own" in apps
    assert "start empty" not in apps
    chat = _read("dashboard", "chat", "page.tsx")
    assert "no conversations yet,<br />" in chat
    tile = _read("_components", "Widgets", "JetsonTile.tsx")
    assert 'title = "This computer"' in tile
    assert "No readings right now" not in tile and "This box" not in tile
    wizard = _read("onboarding", "ui.tsx")
    assert "Connected to: {" not in wizard
    assert "identity.loading || identity.reachable) return null" in wizard
    lib = _read("dashboard", "apps", "_lib", "appsIndex.ts")
    assert "own page, with the lists it keeps." in lib


def test_the_hardware_screen_says_ollama_is_running_without_its_address():
    shared = {"memory_gb": 16, "installed_models": ["qwen2.5:7b-instruct"],
              "ollama_url": "http://127.0.0.1:11435"}
    rows = {r["label"]: r["text"] for r in first_look.found_rows(shared)}
    assert rows["Ollama"] == "is running, with 1 model already: qwen2.5:7b-instruct"
    assert "127.0.0.1" not in rows["Ollama"]


def test_the_sign_up_keys_line_follows_web_search(monkeypatch):
    assert "Wikipedia" not in front_page.keys_line("WeddingBot", False)
    assert front_page.keys_line("WeddingBot", False).startswith(
        "Web search is off for WeddingBot.")
    assert "searches Wikipedia with no sign-up" in front_page.keys_line("WeddingBot", True)

    from puffpai import outside_services
    monkeypatch.setattr(outside_services, "rows",
                        lambda: [{"id": "web_search", "state": "off"}])
    assert front_page._web_search_on() is False
    monkeypatch.setattr(outside_services, "rows",
                        lambda: [{"id": "web_search", "state": "on"}])
    assert front_page._web_search_on() is True

    def boom():
        raise RuntimeError("unreadable")
    monkeypatch.setattr(outside_services, "rows", boom)
    assert front_page._web_search_on() is False


# -- item 10: the Developer Vault card ------------------------------------------

def test_the_vault_card_says_locked_or_open_now():
    words = _read("dashboard", "developer", "overviewWords.ts")
    assert '"Made, locked"' in words and '"Made, open now"' in words
    assert "opened with your PIN" not in words
    page = _read("dashboard", "developer", "page.tsx")
    assert "status: vaultStatus(vault)," in page
    assert "opened with your PIN" not in page


# -- item 17: day stars ------------------------------------------------------------

def test_day_stars_stand_out_from_a_light_page():
    m = {"bg": "#f7f4fb", "text": "#2e2140", "accent": "#b0328f", "bg2": "#efe9f7"}
    star = package.day_star(m)
    assert star == C.rgba_css("#b0328f", 0.9)
    # A pale accent that would vanish falls back to the text colour, nearly solid.
    pale = dict(m, accent="#f3e8f5")
    assert package.day_star(pale) == C.rgba_css("#2e2140", 0.8)
    day = package.background_vars("stars", "day", m)
    assert day["--puff-star"] == star
    assert day["--puff-star"] != C.rgba_css(m["text"], 0.35)
    # Night is unchanged: white stars on a dark sky.
    night = package.background_vars("stars", "night",
                                     dict(m, bg="#0b1026", bg2="#141a3a"))
    assert night["--puff-star"] == C.rgba_css("#ffffff", 0.9)


def test_day_mode_draws_no_stars():
    """PrintingBot check (screens 53z): even faint day dots sat between
    letters and read like commas. Day mode has no stars; "Stars" is the soft
    wash there, and stays a night sky at night."""
    with open(os.path.join(ROOT, "dashboard", "css", "_theme.css"), encoding="utf-8") as fh:
        css = fh.read()
    assert re.search(r'html\[data-puff-theme="day"\] body::before,\s*'
                     r'html\[data-puff-theme="day"\] body::after \{\s*opacity: 0;', css)
    assert "--star-k: 2.2" not in css
    m = {"bg": "#f7f4fb", "text": "#2e2140", "accent": "#b0328f", "bg2": "#efe9f7"}
    day = package.background_vars("stars", "day", m)
    assert day["--puff-stars"] == day["--puff-stars-more"] == "0"
    assert day == package.background_vars("soft", "day", m)
    night = package.background_vars("stars", "night", dict(m, bg="#0b1026", bg2="#141a3a"))
    assert night["--puff-stars"] == night["--puff-stars-more"] == "1"
