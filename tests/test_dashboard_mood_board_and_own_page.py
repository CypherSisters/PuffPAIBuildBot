"""Findings from a wedding bot run.

Pins:

* item 7: the Mood board gave no reason when a folder was refused, its tile
  did nothing, and the Apps page led back home;
* item 23: the Use CLI "Mac or Linux" tab showed ``cd 'C:\\Users\\...'``;
* item 20: the bot's own page offered "Remove this app" as if she had added it;
* behind the scenes: the dashboard polled AlarmFlow and Calendar on a build
  without them;
* item 18: "Proof it works" said "5 passed" on the end screen and "Not proven
  yet" on the home page;
* item 22: the home page headline was her whole goal sentence;
* item 24: Amethyst Haze looked like the bot's own dark look at night.

The browser halves run in ``npm run test:gates`` (apps-listing-proof.ts).
No network, no model.

Run:
    pytest tests/test_dashboard_mood_board_and_own_page.py
"""
from __future__ import annotations

import os
import re

import pytest

from puffpai import developer_api as dev
from puffpai import front_page, persona_scaffold, prove
from puffpai import plain_words as pw
from puffpai.looks import colour as C
from puffpai.looks import package as P
from test_plain_words_in_ui_strings import banned_in, tsx_strings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard", "app")


def _read(*parts: str) -> str:
    with open(os.path.join(DASH, *parts), encoding="utf-8") as fh:
        return fh.read()


WEDDING_GOAL = ("I want to build a wedding planner bot. My brother just got engaged "
                "and there is a lot to do and plan.")


# -- item 7: the Mood board ---------------------------------------------------

def _mood_record(**extra) -> dict:
    rec = {"id": "mood-board-gbefw5au", "name": "Mood board", "mode": "folder_palette",
           "status": "ready", "purpose": "Colours and themes from your own pictures.",
           "app_id": None, "list_app_id": None, "pictures_dir": "mood-board-gbefw5au",
           "folder": {"granted": False}}
    rec.update(extra)
    return rec


def test_the_mood_board_tile_opens_its_own_page(monkeypatch):
    from puffpai.integrate import actions, records
    monkeypatch.setattr(records, "all", lambda: [_mood_record()])
    monkeypatch.setattr(actions, "all_actions", lambda: [])
    tile = next(t for t in front_page.app_tiles() if t["id"] == "mood-board-gbefw5au")
    assert tile["href"] == "/dashboard/mood-board/mood-board-gbefw5au"
    assert tile["ask_folder"] is True
    # The dashboard keeps only links that pass its own-page check.
    own_page = re.search(r"const OWN_PAGE = /(.+)/;", _read("_lib", "services", "frontService.ts"))
    assert own_page and re.match(own_page.group(1).replace("\\/", "/"), tile["href"])


def test_a_web_app_still_opens_its_app_page():
    rec = {"id": "bunny", "mode": "static_web", "app_id": "bunny-s-big-day-jghyicu6"}
    assert front_page._app_href(rec) == "/dashboard/apps/bunny-s-big-day-jghyicu6"  # noqa: SLF001


def test_the_mood_board_page_exists_and_reads_the_same_tile():
    page = _read("dashboard", "mood-board", "[id]", "page.tsx")
    assert "getFront" in page and 'a.kind === "folder_palette"' in page
    assert "<MoodBoardFolder" in page


def test_a_refused_folder_shows_the_bots_reason_on_the_page():
    grid = _read("_components", "Front", "BotAppsGrid.tsx")
    chooser = _read("_components", "Front", "MoodBoardFolder.tsx")
    assert "That folder could not be used. Nothing was changed." not in grid + chooser
    assert "<MoodBoardFolder" in grid
    # apiPostRaw keeps the refusal's body; apiPost threw it away.
    assert "apiPostRaw(`/integrations/${appId}/folder`" in chooser
    assert "folderReply(got.ok, got.data)" in chooser
    # The tile with a folder box is not one big link, but its body opens the page.
    assert 'app.href && here && !ownPage' in grid
    assert "ownPage && app.href && here ?" in grid


def test_the_backend_refusal_carries_a_plain_reason(monkeypatch):
    """The route sends the reason as ``message`` (what folderReply reads)."""
    from flask import Flask
    from puffpai.integrate import api as integrate_api, palette
    reason = ("Only folders inside Pictures, Documents, Music, Videos, Desktop or "
              "Downloads can be shared.")

    def refuse(*_a, **_k):
        raise palette.PaletteError(reason, "folder")

    monkeypatch.setattr(integrate_api, "_device", lambda: "device:abc")
    monkeypatch.setattr(palette, "choose_folder", refuse)
    app = Flask(__name__)
    app.register_blueprint(integrate_api.bp)
    reply = app.test_client().post("/integrations/mood-board-gbefw5au/folder",
                                   json={"path": "C:\\Windows"})
    assert reply.status_code == 409
    assert reply.get_json()["message"] == reason


# -- item 23: Use CLI on the other kind of computer ----------------------------

def test_mac_or_linux_never_gets_a_windows_path(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "box_shell", lambda: "windows")
    (tmp_path / "puffpai.cmd").write_text("@echo off\n")
    panel = dev.cli_page("vault", folder=str(tmp_path))
    assert panel["go_to_folder"] == {"windows": f"cd '{tmp_path}'"}
    assert "posix" not in panel["go_to_folder"]
    said = panel["go_to_folder_said"]["posix"]
    assert said == dev.GO_TO_FOLDER_ELSEWHERE
    assert ":\\" not in said and "cd " not in said
    assert not pw.check(said) and not banned_in(said)
    assert all(":\\" not in row["posix"] for row in panel["commands"])


def test_windows_never_gets_a_mac_path(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "box_shell", lambda: "posix")
    (tmp_path / "puffpai").write_text("#!/bin/sh\n")
    panel = dev.cli_page("bridge", folder=str(tmp_path))
    assert set(panel["go_to_folder"]) == {"posix"}
    assert panel["go_to_folder_said"] == {"windows": dev.GO_TO_FOLDER_ELSEWHERE}


def test_the_panel_shows_the_plain_step_when_there_is_no_cd():
    src = _read("dashboard", "developer", "_components", "UseCliPanel.tsx")
    assert "panel.go_to_folder_said?.[shell]" in src


# -- item 20: the bot's own page is not an app she added -----------------------

@pytest.fixture
def owner(tmp_path, monkeypatch):
    from puffpai import app_data as app_data_mod, app_registry
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()
    yield tmp_path
    app_registry.rescan()


def test_the_bots_own_page_is_marked_own(owner, monkeypatch):
    from puffpai import build_manifest
    persona_scaffold.scaffold("weddingbot", name="WeddingBot", goal=WEDDING_GOAL)
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: {"persona": {"scaffolded": True, "app": "weddingbot"}})
    apps = {a["id"]: a for a in persona_scaffold.owner_apps()}
    assert apps["weddingbot"]["own"] is True
    assert apps["gardenbot"]["own"] is False


def test_no_build_record_means_nothing_is_own(owner, monkeypatch):
    from puffpai import build_manifest
    persona_scaffold.scaffold("weddingbot", name="WeddingBot", goal=WEDDING_GOAL)
    monkeypatch.setattr(build_manifest, "read_manifest", lambda *a, **k: None)
    assert [a["own"] for a in persona_scaffold.owner_apps()] == [False]


def test_the_own_page_offers_no_remove_and_says_what_it_is():
    page = _read("dashboard", "apps", "[id]", "page.tsx")
    assert "{persona && !persona.own && (" in page
    remove_at = page.index('<SectionBar title="Remove it" />')
    # The section that holds Remove opens with the own-page guard.
    assert page.rindex("{persona && ", 0, remove_at) == page.rindex(
        "{persona && !persona.own && (", 0, remove_at)
    # (2026-10-13) the bot's own page is its apps: one row per job, Open in a new tab
    assert "persona?.own && <BotApps" in page
    rows = _read("dashboard", "apps", "[id]", "BotApps.tsx")
    assert 'target="_blank"' in rows and "<iframe" not in rows and "Try next app" in rows


# -- polling: only the apps this build has -------------------------------------

def test_app_data_is_read_only_for_apps_on_this_build():
    hook = _read("_lib", "hooks", "useAppData.ts")
    assert "appOnBuild(build, appName)" in hook
    assert "if (present !== true) return undefined;" in hook
    assert "if (present === true) reload();" in hook
    surface = _read("_lib", "surfaceMap.ts")
    assert "export function appOnBuild(" in surface


# -- item 18: one source for "Proof it works" ----------------------------------

RECORD = {"schema": prove.SCHEMA, "checks": [
    {"id": c, "title": c, "result": prove.PASS, "reason": "ok"}
    for c in ("id_sanity", "persona_visible", "sources_honest", "handoff_correct",
              "goal_reaches_model")], "gaps": []}


def test_the_home_page_and_the_end_screen_read_the_same_words():
    from types import SimpleNamespace
    from puffpai import api_server
    from puffpai.wizard.packages.handoff import skill as handoff
    end_screen = handoff._proof_line(SimpleNamespace(shared={"prove_record": RECORD}))  # noqa: SLF001
    payload = api_server._prove_payload(RECORD)  # noqa: SLF001
    assert end_screen == payload["line"] == prove.summary_line(RECORD)
    assert payload["summary"] == "Of 5 checks: 5 passed."
    assert end_screen.startswith("Of 5 checks: 5 passed.")


def test_after_the_live_proof_the_line_stops_asking_for_it():
    record = dict(RECORD, live={"ran": True, "checks": []})
    assert prove.summary_line(record) == "Of 5 checks: 5 passed."


def test_the_strip_shows_the_shared_summary():
    strip = _read("_components", "Widgets", "ProveStrip.tsx")
    assert 'record.summary || "Not proven yet"' in strip
    assert "Once it is running, ask it to prove itself: " in strip
    assert prove.RUN_LINE.startswith("Once it is running, ask it to prove itself: ")


# -- item 22: the headline is the bot's short description ----------------------

def test_the_headline_is_the_short_description(monkeypatch):
    monkeypatch.setattr(front_page, "_goal", lambda: WEDDING_GOAL)
    monkeypatch.setattr(front_page, "_name", lambda: "WeddingBot")
    page = front_page.front()
    assert page["purpose"] == "A wedding planner bot."
    assert "brother" not in page["purpose"]


def test_a_goal_with_no_subject_gives_no_headline_line():
    assert front_page._short_purpose("", "Rex") == ""  # noqa: SLF001
    line = front_page._short_purpose("make it nice", "Rex")  # noqa: SLF001
    assert "made from your goal" not in line.lower()


# -- item 24: a kept look changes the main surfaces ----------------------------

AMETHYST = {
    "schema": "puffpai_theme/v1", "id": "tweakcn:amethyst-haze", "name": "Amethyst Haze",
    "from": {"list": "tweakcn", "item": "amethyst-haze", "license": "Apache-2.0",
             "list_date": "2026-09-24"},
    "day": {"bg": "#f8f7fa", "bg2": "#f2eff6", "card": "#ffffff", "line": "#cec9d9",
            "text": "#3d3c4f", "text2": "#4f4c63", "text3": "#6f6d7e", "accent": "#7b6a9b",
            "accent2": "#945a6c", "danger": "#c84c4e",
            "chart": ["#7b6a9b", "#945a6c", "#3b7a66", "#906b32"]},
    "night": {"bg": "#1a1823", "bg2": "#2a2736", "card": "#232030", "line": "#302c40",
              "text": "#e0ddef", "text2": "#a09aad", "text3": "#807b8c", "accent": "#a995c9",
              "accent2": "#f2b8c6", "danger": "#e57373",
              "chart": ["#a995c9", "#f2b8c6", "#77b8a1", "#f0c88d"]},
    "shape": "soft", "font": "plain", "photo": "keep", "background": "stars",
}


def _lch(hex_value: str):
    return C.srgb_to_oklch(C.rgb_of(hex_value))


def _hue_gap(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def test_amethyst_haze_carries_purple_and_pink_on_its_page_at_night():
    pkg = P.check(P.validate(dict(AMETHYST)))
    night = pkg["night"]
    for key in ("bg", "card"):
        _l, c, h = _lch(night[key])
        assert c >= 0.044, (key, night[key])
        assert _hue_gap(h, _lch(night["accent"])[2]) < 8
    _l, c, h = _lch(night["bg2"])
    assert c >= 0.049 and _hue_gap(h, _lch(night["accent2"])[2]) < 8  # the pink end
    # Lightness kept, every readability check still passes.
    assert abs(_lch(night["bg"])[0] - _lch(AMETHYST["night"]["bg"])[0]) < 0.01
    assert all(row["result"] == "pass" for row in pkg["checks"])
    # Far enough from the bot's own night page to see.
    own = P.OWN["night"]["bg"]
    assert C.contrast(C.rgb_of(night["bg2"]), C.rgb_of(own)) > 1.3
    assert P.css_vars(pkg, "night")["--bg2"] == night["bg2"]


def test_the_day_page_is_tinted_too():
    pkg = P.check(P.validate(dict(AMETHYST)))
    assert pkg["day"]["bg"] != AMETHYST["day"]["bg"]
    assert pkg["day"]["bg2"] != AMETHYST["day"]["bg2"]


def test_tinting_twice_changes_nothing():
    once = P.check(P.validate(dict(AMETHYST)))
    twice = P.check(P.validate({k: v for k, v in once.items()
                                if k not in ("checks", "adjusted")}))
    assert twice["day"] == once["day"] and twice["night"] == once["night"]


def test_a_grey_look_stays_grey():
    grey = {k: (dict(v) if isinstance(v, dict) else v) for k, v in AMETHYST.items()}
    for mode in ("day", "night"):
        grey[mode]["accent"] = "#808080"
        grey[mode]["accent2"] = "#909090"
    for mode in ("day", "night"):
        out = P.tint_surfaces(mode, grey[mode])
        assert all(out[k] == grey[mode][k] for k in ("bg", "bg2", "card", "line"))


def test_a_look_with_coloured_surfaces_is_left_alone():
    m = dict(AMETHYST["night"], bg="#3a1060", bg2="#5a1040", card="#3a1060", line="#6a2050")
    assert P.tint_surfaces("night", m) == m


# -- words on the new and changed pages ----------------------------------------

@pytest.mark.parametrize("parts", [
    ("dashboard", "mood-board", "[id]", "page.tsx"),
    ("_components", "Front", "MoodBoardFolder.tsx"),
    ("_lib", "moodBoard.ts"),
])
def test_the_new_words_are_plain(parts):
    texts = [t for _, t in tsx_strings(os.path.join(DASH, *parts))]
    joined = "\n".join(texts)
    assert "\u2014" not in joined
    assert not banned_in(joined), banned_in(joined)
