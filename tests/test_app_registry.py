"""Tests for the app contract: manifests, the loader, and the assembled words.

An app is untrusted input that becomes a path component, a data filename and a
list of words the router scans for every message. So the things checked hardest
here are the things whose failure is quiet and expensive:

* **A bad manifest cannot reach a path or the router.** Traversal ids, unknown
  surfaces, keyword floods and collisions are refused, and refused by name.
* **Nothing declarative becomes executable.** A manifest is parsed as JSON and
  used as data. The forbidden-key guard makes that a test, not an intention.
* **The migration changed nothing.** The five shipped apps assemble to exactly
  the vocabulary that used to be typed into ``config/rulepacks/apps.json``, and
  the defaults they declare are exactly the ones that used to be typed into
  ``app_data.py``.

Run:
    pytest tests/test_app_registry.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import app_data, app_registry, build_interview, config_health, rulepacks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED_APPS_DIR = os.path.join(REPO_ROOT, "apps")


# ── The pre-migration defaults, frozen ───────────────────────────────────────
# Copied verbatim from ``AppData.__init__`` as it stood before the per-app data
# shapes moved into apps/<id>/app.json. This is the reference the migration is
# checked against; it is a fixture, not a source of truth, and an owner's
# existing file must keep loading from the same path either way.

OLD_DEFAULTS = {
    "alarmflow": {"items": [], "last_updated": ""},
    "calendar": {
        # One "Me" instead of the sample family Dad, Mom, Alex
        # and Emma (a built bot is not always a family's).
        "family": [
            {"id": "me", "name": "Me", "color": "#38bdf8",
             "emoji": "\U0001f642", "role": "parent"},
        ],
        "events": [], "tasks": [], "meds": [], "bills": [],
        "last_updated": "",
    },
    "freshtable": {
        "pantry": [], "shoppingList": [], "mealPlan": [], "recipes": [],
        "last_updated": "",
    },
    "fitfam": {
        "users": {
            "alex": {"name": "Adult 1", "emoji": "\U0001f9d4",
                     "prefs": {"level": "mid", "type": "full", "duration": 45},
                     "goals": [], "history": [], "streak": 0, "totalWorkouts": 0},
            "jordan": {"name": "Adult 2", "emoji": "\U0001f3c4",
                       "prefs": {"level": "mid", "type": "full", "duration": 30},
                       "goals": [], "history": [], "streak": 0, "totalWorkouts": 0},
        },
        "last_updated": "",
    },
    "trivia": {"results": [], "last_updated": ""},
}

OLD_FILENAMES = {app: f"{app}.json" for app in OLD_DEFAULTS}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def surfaces():
    return set(build_interview.surface_taxonomy())


@pytest.fixture
def shipped():
    """The five manifests this build ships, read from disk."""
    return app_registry.load_manifests(SHIPPED_APPS_DIR)


@pytest.fixture(autouse=True)
def clean_health():
    """Each test judges the health record it caused, not an earlier one's."""
    config_health.clear()
    yield
    config_health.clear()


def write_app(root, folder, manifest):
    """Drop an app folder into ``root``; returns the manifest path."""
    app_folder = root / folder
    app_folder.mkdir(parents=True, exist_ok=True)
    target = app_folder / app_registry.MANIFEST_NAME
    if isinstance(manifest, str):
        target.write_text(manifest, encoding="utf-8")
    else:
        target.write_text(json.dumps(manifest), encoding="utf-8")
    return target


def good_manifest(app_id="widgetbot", **over):
    manifest = {
        "schema": app_registry.SCHEMA,
        "id": app_id,
        "name": "WidgetBot",
        "blurb": "A stranger's app.",
        "surfaces": ["daily.games"],
        "data": {"default": {"items": []}},
        "vocabulary": {
            "keywords": ["widgetbot"],
            "examples": ["show me the widgetbot board"],
        },
    }
    manifest.update(over)
    return manifest


# ── ids and containment ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../../etc", "..", "../trivia", "trivia/../x", "trivia\\..\\x",
    "a/b", "a\\b", "C:evil", "Trivia", "_hidden", "1st", "x", "",
    "trivia.json", "with space", "x" * (app_registry.MAX_ID_LEN + 1),
    None, 12, ["trivia"],
])
def test_an_unsafe_id_is_never_a_valid_id(bad):
    assert app_registry.valid_id(bad) is False


def test_a_traversal_id_is_refused_by_the_manifest_validator(surfaces):
    problem = app_registry.validate_manifest(
        good_manifest("../../etc"), surfaces)
    assert problem is not None
    assert "not usable" in problem


def test_a_traversal_id_never_becomes_a_path(tmp_path):
    """The pattern refuses it, and so does the containment check behind it."""
    for bad in ("../../etc", "..", "a/b"):
        with pytest.raises(ValueError):
            app_registry.app_dir(bad, str(tmp_path))
        with pytest.raises(ValueError):
            app_registry.data_path(bad, str(tmp_path))


def test_resolve_within_refuses_an_escape(tmp_path):
    root = str(tmp_path / "apps")
    os.makedirs(root, exist_ok=True)
    assert app_registry.resolve_within(root, "trivia").startswith(root)
    with pytest.raises(ValueError):
        app_registry.resolve_within(root, os.path.join("..", "escaped"))


def test_a_manifest_with_a_traversal_id_is_recorded_and_skipped(tmp_path):
    write_app(tmp_path, "sneaky", good_manifest("../../etc"))
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    got = app_registry.load_manifests(str(tmp_path))
    assert [m["id"] for m in got] == ["widgetbot"]
    assert any(r["config"] == app_registry.CONFIG_NAME
               for r in config_health.report(degraded_only=True))


# ── collisions ───────────────────────────────────────────────────────────────

def test_two_manifests_with_one_id_is_an_error_naming_both(tmp_path):
    """Neither wins. First-wins and last-wins are both a shadowing hole."""
    write_app(tmp_path, "aaa_shadow", good_manifest("trivia"))
    write_app(tmp_path, "trivia", good_manifest("trivia"))
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    got = app_registry.load_manifests(str(tmp_path))
    assert [m["id"] for m in got] == ["widgetbot"]
    details = " ".join(r["detail"] for r in config_health.report())
    assert "aaa_shadow" in details and "trivia" in details


def test_a_third_manifest_cannot_resurrect_a_collided_id(tmp_path):
    for folder in ("aaa", "bbb", "ccc"):
        write_app(tmp_path, folder, good_manifest("trivia"))
    assert app_registry.load_manifests(str(tmp_path)) == []


# ── bounds ───────────────────────────────────────────────────────────────────

def test_a_keyword_flood_is_refused(surfaces):
    flood = good_manifest(vocabulary={
        "keywords": [f"word{i}" for i in range(app_registry.MAX_KEYWORDS + 1)],
        "examples": [],
    })
    problem = app_registry.validate_manifest(flood, surfaces)
    assert problem is not None and "keywords" in problem


def test_a_one_character_keyword_is_refused(surfaces):
    problem = app_registry.validate_manifest(
        good_manifest(vocabulary={"keywords": ["a"]}), surfaces)
    assert problem is not None and "every message" in problem


def test_an_uppercase_or_non_ascii_keyword_is_refused(surfaces):
    for word in ("Pantry", "café list"):
        assert app_registry.validate_manifest(
            good_manifest(vocabulary={"keywords": [word]}), surfaces) is not None


def test_too_many_examples_or_actions_are_refused(surfaces):
    assert app_registry.validate_manifest(good_manifest(vocabulary={
        "keywords": ["widgetbot"],
        "examples": [f"do the thing number {i}" for i in
                     range(app_registry.MAX_EXAMPLES + 1)],
    }), surfaces) is not None
    assert app_registry.validate_manifest(good_manifest(actions=[
        {"id": f"act_{i}", "says": "do the thing"}
        for i in range(app_registry.MAX_ACTIONS + 1)
    ]), surfaces) is not None


def test_an_oversized_manifest_file_is_refused(tmp_path):
    fat = good_manifest(blurb="ok")
    fat["padding"] = "x" * (app_registry.MAX_MANIFEST_BYTES + 10)
    write_app(tmp_path, "widgetbot", fat)
    assert app_registry.load_manifests(str(tmp_path)) == []
    assert any("ceiling" in r["detail"] for r in config_health.report())


def test_an_oversized_default_data_shape_is_refused(surfaces):
    huge = good_manifest(data={"default": {
        "blob": "x" * (app_registry.MAX_DEFAULT_BYTES + 10)}})
    problem = app_registry.validate_manifest(huge, surfaces)
    assert problem is not None and "not a dataset" in problem


def test_only_the_first_bounded_number_of_apps_install(tmp_path):
    for i in range(app_registry.MAX_INSTALLED_APPS + 3):
        write_app(tmp_path, f"app_{i:03d}", good_manifest(f"app_{i:03d}"))
    got = app_registry.load_manifests(str(tmp_path))
    assert len(got) == app_registry.MAX_INSTALLED_APPS
    assert any("Remove some and rescan" in r["detail"]
               for r in config_health.report())


# ── surfaces ─────────────────────────────────────────────────────────────────

def test_an_unknown_surface_is_refused(surfaces):
    problem = app_registry.validate_manifest(
        good_manifest(surfaces=["house.orbital_laser"]), surfaces)
    assert problem is not None and "cannot invent one" in problem


def test_every_shipped_surface_is_in_the_live_taxonomy(shipped, surfaces):
    for manifest in shipped:
        for surface in manifest["surfaces"]:
            assert surface in surfaces


def test_too_many_surfaces_are_refused(surfaces):
    many = sorted(surfaces)[:app_registry.MAX_SURFACES + 1]
    assert app_registry.validate_manifest(
        good_manifest(surfaces=many), surfaces) is not None


# ── nothing executable ───────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "handler", "module", "import", "entrypoint", "exec", "eval",
    "python", "script", "command", "hook", "plugin", "run",
])
def test_no_manifest_field_may_name_code_to_run(key, surfaces):
    problem = app_registry.validate_manifest(
        good_manifest(**{key: "puffpai.evil:go"}), surfaces)
    assert problem is not None and "declarative only" in problem


def test_an_action_may_not_name_code_either(surfaces):
    problem = app_registry.validate_manifest(good_manifest(actions=[
        {"id": "go", "says": "do it", "handler": "os.system"}]), surfaces)
    assert problem is not None and "never a thing to run" in problem


def test_loading_a_manifest_never_executes_it(tmp_path, monkeypatch):
    """The loader reads JSON. It must not reach an importer, ever."""
    import builtins
    real_import = builtins.__import__
    seen: list[str] = []

    def watched(name, *args, **kwargs):
        seen.append(name)
        return real_import(name, *args, **kwargs)

    write_app(tmp_path, "widgetbot", good_manifest(
        "widgetbot", name="WidgetBot", blurb="puffpai.evil"))
    # Warm every lazy import the loader makes before watching.
    app_registry.load_manifests(str(tmp_path))
    monkeypatch.setattr(builtins, "__import__", watched)
    app_registry.load_manifests(str(tmp_path))
    assert not [n for n in seen if "evil" in n or "widgetbot" in n]


# ── malformed manifests are recorded and skipped ─────────────────────────────

def test_a_malformed_manifest_is_recorded_while_the_others_load(tmp_path):
    write_app(tmp_path, "broken", "{not json")
    write_app(tmp_path, "wrong_schema", good_manifest("wrong", schema="hostile/v9"))
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    write_app(tmp_path, "otherbot", good_manifest("otherbot"))
    got = app_registry.load_manifests(str(tmp_path))
    assert [m["id"] for m in got] == ["otherbot", "widgetbot"]
    report = config_health.report(degraded_only=True)
    assert len(report) == 2
    assert all(r["config"] == app_registry.CONFIG_NAME for r in report)
    assert config_health.is_degraded()


def test_a_folder_without_a_manifest_is_named_not_ignored(tmp_path):
    (tmp_path / "notanapp").mkdir()
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    got = app_registry.load_manifests(str(tmp_path))
    assert [m["id"] for m in got] == ["widgetbot"]
    assert any("notanapp" in r["detail"] for r in config_health.report())


def test_an_absent_apps_directory_is_quiet(tmp_path):
    assert app_registry.load_manifests(str(tmp_path / "nope")) == []
    assert config_health.is_degraded() is False


def test_config_health_summary_stays_ascii(tmp_path):
    write_app(tmp_path, "broken", "{not json")
    app_registry.load_manifests(str(tmp_path))
    for line in config_health.summary_lines():
        line.encode("ascii")


# ── the shipped five ─────────────────────────────────────────────────────────

def test_the_five_shipped_apps_are_installed(shipped):
    assert [m["id"] for m in shipped] == [
        "alarmflow", "calendar", "freshtable", "fitfam", "trivia"]


def test_shipped_manifests_are_all_valid(shipped, surfaces):
    for manifest in shipped:
        assert app_registry.validate_manifest(manifest, surfaces) is None


def test_manifest_defaults_equal_the_old_hardcoded_ones(shipped):
    """The migration moved the data shapes; it must not have changed one."""
    got = {m["id"]: app_registry.default_data(m) for m in shipped}
    assert got == OLD_DEFAULTS


def test_app_data_reads_the_registry_and_keeps_the_old_filenames(tmp_path):
    data = app_data.AppData()
    assert data.VALID_APPS == list(OLD_DEFAULTS)
    for app, filename in OLD_FILENAMES.items():
        assert os.path.basename(data._files[app]) == filename
        assert data._defaults[app]() == OLD_DEFAULTS[app]


def test_a_shipped_app_with_a_document_already_there_keeps_it(monkeypatch, tmp_path):
    """An owner's existing file does not move. That is the whole rule."""
    monkeypatch.setattr(app_data, "_DATA_DIR", str(tmp_path))
    (tmp_path / "calendar.json").write_text("{}", encoding="utf-8")
    assert app_data.data_dir_for("calendar") == str(tmp_path)
    assert os.path.dirname(app_data.AppData()._files["calendar"]) == str(tmp_path)
    # And the app beside it stays with it rather than being split off into the
    # state dir the first time it is opened.
    assert app_data.data_dir_for("freshtable") == str(tmp_path)


def test_a_build_with_no_stores_directory_at_all_uses_the_state_dir(monkeypatch, tmp_path):
    """A fresh build stops writing owner data inside its own install."""
    monkeypatch.setattr(app_data, "_DATA_DIR", str(tmp_path / "checkout"))
    assert app_data.data_dir_for("calendar") == app_data._state_data_dir()
    data = app_data.AppData()
    assert os.path.dirname(data._files["calendar"]) == app_data._state_data_dir()
    assert not os.path.isdir(tmp_path / "checkout"), (
        "constructing AppData created an app_data/ inside the install")


def test_app_data_hands_out_a_fresh_default_every_time():
    data = app_data.AppData()
    first = data._defaults["calendar"]()
    first["events"].append({"id": "x"})
    assert data._defaults["calendar"]()["events"] == []


def test_an_existing_data_file_still_loads_from_the_same_path(tmp_path, monkeypatch):
    monkeypatch.setattr(app_data, "_DATA_DIR", str(tmp_path))
    (tmp_path / "freshtable.json").write_text(
        json.dumps({"pantry": [{"name": "flour"}], "last_updated": "old"}),
        encoding="utf-8")
    data = app_data.AppData()
    assert data.get_data("freshtable")["pantry"] == [{"name": "flour"}]


def test_app_data_falls_back_when_no_manifests_are_readable(monkeypatch):
    monkeypatch.setattr(app_registry, "load_manifests", lambda path=None: [])
    data = app_data.AppData()
    assert data.VALID_APPS == list(OLD_DEFAULTS)
    for app in OLD_DEFAULTS:
        assert data._defaults[app]() == OLD_DEFAULTS[app]


# ── the assembled rulepack ───────────────────────────────────────────────────

def test_the_shipped_five_assemble_to_the_shipped_vocabulary(shipped):
    """Byte-identical to ``config/rulepacks/apps.json``.

    This is what makes the migration provable rather than plausible: the
    hand-written pack on disk and the pack assembled from the five manifests
    must be the same words in the same order, so routing at the light tier is
    unchanged AND the fail-open fallback is not a different router.
    """
    with open(os.path.join(REPO_ROOT, "config", "rulepacks", "apps.json"),
              "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    vocabulary = app_registry.assemble_vocabulary(shipped)
    assert vocabulary["keywords"] == on_disk["keywords"]
    assert vocabulary["examples"] == on_disk["examples"]


def test_the_assembly_matches_the_baked_in_fallback(shipped):
    baked = next(p for p in rulepacks._FALLBACK_PACKS
                 if p["surface"] == app_registry.APPS_SURFACE)
    vocabulary = app_registry.assemble_vocabulary(shipped)
    assert vocabulary["keywords"] == baked["keywords"]
    assert vocabulary["examples"] == baked["examples"]


def _apps_pack(packs):
    return next(p for p in packs if p["surface"] == app_registry.APPS_SURFACE)


def test_removing_an_app_removes_its_words_from_the_router(tmp_path):
    """Remove Trivia and the trivia words go with it."""
    for manifest in app_registry.load_manifests(SHIPPED_APPS_DIR):
        if manifest["id"] != "trivia":
            write_app(tmp_path, str(manifest["id"]), manifest)
    pack = _apps_pack(rulepacks.load_packs(apps_dir=str(tmp_path)))
    assert "trivia" not in pack["keywords"]
    assert "quiz" not in pack["keywords"]
    assert "pantry" in pack["keywords"]
    assert not rulepacks.matches(pack, "start a trivia quiz")


def test_adding_an_app_adds_its_words_to_the_router(tmp_path):
    for manifest in app_registry.load_manifests(SHIPPED_APPS_DIR):
        write_app(tmp_path, str(manifest["id"]), manifest)
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot", order=900))
    pack = _apps_pack(rulepacks.load_packs(apps_dir=str(tmp_path)))
    assert pack["keywords"][-1] == "widgetbot"
    assert rulepacks.matches(pack, "show me the widgetbot board")


def test_a_new_apps_published_example_must_actually_route(tmp_path):
    """The anti-drift test extends to strangers' apps by construction.

    An installed app's examples flow into the phrasebook, so they go through
    the same real matcher the shipped ones do. This is that guarantee, shown
    working for an app the core has never heard of.
    """
    for manifest in app_registry.load_manifests(SHIPPED_APPS_DIR):
        write_app(tmp_path, str(manifest["id"]), manifest)
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot", order=900))
    pack = _apps_pack(rulepacks.load_packs(apps_dir=str(tmp_path)))
    for phrase in rulepacks.examples_for(pack):
        assert rulepacks.matches(pack, phrase.lower()), phrase


def test_no_readable_manifests_falls_back_to_the_shipped_vocabulary(tmp_path):
    """Never an empty router. An absent apps/ directory is not a mute bot."""
    pack = _apps_pack(rulepacks.load_packs(apps_dir=str(tmp_path / "nope")))
    baked = next(p for p in rulepacks._FALLBACK_PACKS
                 if p["surface"] == app_registry.APPS_SURFACE)
    assert pack["keywords"] == baked["keywords"]


def test_assembly_does_not_mutate_the_baked_in_fallback(tmp_path):
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    rulepacks.load_packs(str(tmp_path / "no-rulepacks"), apps_dir=str(tmp_path))
    baked = next(p for p in rulepacks._FALLBACK_PACKS
                 if p["surface"] == app_registry.APPS_SURFACE)
    assert "widgetbot" not in baked["keywords"]


def test_the_apps_pack_shell_stays_core(tmp_path):
    """An app contributes words, not routing. It cannot change where it goes."""
    write_app(tmp_path, "widgetbot", good_manifest(
        "widgetbot", surface="security", route="library", order=1))
    pack = _apps_pack(rulepacks.load_packs(apps_dir=str(tmp_path)))
    assert pack["surface"] == "apps"
    assert pack["route"] == "agent"
    assert pack["category"] == "app"
    assert pack["min_tier"] == "light"


# ---------------------------------------------------------------------------
# An installed app must be reachable, not just registered
# ---------------------------------------------------------------------------

def test_the_api_reads_the_registry_rather_than_a_second_hardcoded_list():
    """The endpoints had their own copy of the app list.

    An owner could install an app, have it validate, have it hold data, and
    then be refused by every /apps/<name>/ route - the app existed and could
    not be reached. Two lists is one list too many.
    """
    import inspect
    from puffpai import api_server

    source = inspect.getsource(api_server)
    assert "VALID_APPS = ['alarmflow'" not in source, (
        "the API is hardcoding its own app list again")

    # And it really resolves through AppData, which reads the registry.
    from puffpai.app_data import AppData
    assert sorted(api_server._valid_apps()) == sorted(AppData.VALID_APPS)


def test_a_broken_registry_serves_no_apps_rather_than_500ing_every_route(monkeypatch):
    """Fail closed on membership, but never take the whole API down with it."""
    from puffpai import api_server

    def boom():
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(api_server, "_get_app_data", boom)
    assert api_server._valid_apps() == []


# ---------------------------------------------------------------------------
# What the dashboard is told, and what it is not
# ---------------------------------------------------------------------------
# The sidebar, the apps index and the surface map each used to carry their own
# typed copy of the app list, which is why removing an app left three places
# still insisting it was there. They now read one answer, served over
# /build/self. These tests hold that answer to the same rule as the manifest
# loader: an app author is not us, so nothing crosses the boundary unnamed and
# nothing crosses it unbounded.

UI_FIELDS = {"id", "name", "blurb", "route", "icon", "surfaces", "order",
             # Derived here, never copied from the manifest: what the app says
             # it needs, whether this build can meet it, and the one honest
             # sentence about what the box does with a declared host.
             "degraded", "degraded_reason", "requires",
             "hosts_enforcement", "hosts_notice",
             # Derived by puffpai.feeds.ui_summary (bounded fields, what leaves
             # the computer written by code); None for an app with no feed.
             "feed"}


def test_ui_records_carry_exactly_the_fields_the_browser_needs(shipped):
    records = app_registry.ui_records(shipped)
    assert [r["id"] for r in records] == [m["id"] for m in shipped]
    for record in records:
        assert set(record) == UI_FIELDS, (
            "a manifest field reached the browser without being named here")


def test_no_manifest_field_rides_out_uninvited():
    """The manifest is copied by name, never echoed."""
    manifest = good_manifest("widgetbot")
    manifest["souvenir"] = "should not travel"
    manifest["data"] = {"default": {"secret": "owner data"}}
    record = app_registry.ui_records([manifest])[0]
    assert "souvenir" not in record
    assert "data" not in record
    assert "souvenir" not in json.dumps(record)
    assert "owner data" not in json.dumps(record)


def test_an_app_authors_strings_are_bounded_on_the_way_out(surfaces):
    """Validated at 200 on the way in; a 200-character nav label is a bug."""
    manifest = good_manifest("widgetbot")
    manifest["name"] = "N" * app_registry.MAX_LABEL_LEN
    manifest["blurb"] = "B" * app_registry.MAX_LABEL_LEN
    manifest["ui"] = {"route": "/dashboard/apps/widgetbot",
                      "icon": "I" * app_registry.MAX_LABEL_LEN}
    assert app_registry.validate_manifest(manifest, surfaces) is None
    record = app_registry.ui_records([manifest])[0]
    assert len(record["name"]) == app_registry.MAX_UI_NAME_LEN
    assert len(record["blurb"]) == app_registry.MAX_UI_BLURB_LEN
    assert len(record["icon"]) == app_registry.MAX_UI_ICON_LEN


def test_a_hostile_name_or_blurb_travels_as_text_not_markup():
    """App-supplied words are data. They are escaped and rendered, never HTML.

    The record carries the string exactly as written - no unescaping, and no
    partial cleaning that would leave something half-safe. The dashboard
    renders it as a React text child, which escapes it, and the files that
    render it hold no raw-HTML escape route at all.
    """
    hostile = good_manifest("widgetbot")
    hostile["name"] = "<img src=x onerror=alert(1)>"
    hostile["blurb"] = "</p><script>steal()</script>"
    record = app_registry.ui_records([hostile])[0]
    assert record["name"] == hostile["name"]
    assert record["blurb"] == hostile["blurb"]

    rendered_in = [
        ("dashboard", "app", "dashboard", "apps", "_components", "AppsIndex.tsx"),
        ("dashboard", "app", "dashboard", "_components", "AsideMenu", "Item.tsx"),
        ("dashboard", "app", "dashboard", "_components", "QuickSearch.tsx"),
    ]
    checked = 0
    for parts in rendered_in:
        path = os.path.join(REPO_ROOT, *parts)
        if not os.path.isfile(path):
            continue
        checked += 1
        with open(path, "r", encoding="utf-8") as fh:
            assert "dangerouslySetInnerHTML" not in fh.read(), (
                f"{parts[-1]} renders an app author's words as markup")
    assert checked, "none of the rendering files were found to check"


def test_an_icon_leaves_as_a_name_never_as_a_drawing():
    """An SVG path is drawing instructions; an app author does not get a pen."""
    manifest = good_manifest("widgetbot")
    manifest["ui"] = {"route": "/dashboard/apps/widgetbot", "icon": "M0,0 L99,99 Z"}
    record = app_registry.ui_records([manifest])[0]
    # It travels as the string it is; the dashboard looks it up in its own
    # icon set and falls back, so an unrecognised name draws nothing.
    assert record["icon"] == "M0,0 L99,99 Z"
    path = os.path.join(REPO_ROOT, "dashboard", "app", "_lib", "surfaceMap.ts")
    with open(path, "r", encoding="utf-8") as fh:
        assert "APP_ICONS[name] ?? mdiViewGridOutline" in fh.read(), (
            "the dashboard stopped looking icon names up in its own set")


@pytest.mark.parametrize("route", [
    "//evil.example/steal",          # protocol-relative: off this box entirely
    "/dashboard/apps/../../evil",    # walks upward
    "/dashboard/apps/a b",           # a space
    "/dashboard/apps/x?q=<script>",  # markup in a query
    "javascript:alert(1)",           # not absolute at all
])
def test_a_route_that_is_not_an_in_app_path_never_becomes_an_href(route):
    manifest = good_manifest("widgetbot")
    manifest["ui"] = {"route": route}
    record = app_registry.ui_records([manifest])[0]
    assert record["route"] == "", f"{route!r} reached the browser as an href"
    # The app still exists - it keeps its words and its data. It just gets no
    # nav entry, because there is nowhere honest to send the owner.
    assert record["id"] == "widgetbot"


def test_an_app_with_no_ui_block_is_listed_without_a_route(surfaces):
    manifest = good_manifest("widgetbot")
    assert app_registry.validate_manifest(manifest, surfaces) is None
    record = app_registry.ui_records([manifest])[0]
    assert record["route"] == ""
    assert record["icon"] == ""


def test_the_shipped_records_are_the_shipped_apps(shipped):
    records = app_registry.ui_records(shipped)
    assert {r["id"] for r in records} == {
        "alarmflow", "calendar", "freshtable", "fitfam", "trivia"}
    assert [r["route"] for r in records] == [
        f"/dashboard/apps/{r['id']}" for r in records]


def test_the_build_self_payload_answers_null_rather_than_nothing(monkeypatch):
    """A scan that could not be done is not the same as an empty build.

    Null means "not known", and the dashboard shows the built-in menu. An
    empty list means "this build carries no apps". Collapsing the two is how a
    stranger with a hiccuping backend ends up with an empty sidebar.
    """
    from puffpai import api_server

    def boom():
        raise RuntimeError("apps directory unreadable")

    monkeypatch.setattr(app_registry, "ui_records", boom)
    assert api_server._build_self_apps() is None


# ---------------------------------------------------------------------------
# The capability catalog stops claiming apps that are not here
# ---------------------------------------------------------------------------

def _app_ids(catalog):
    return sorted(str(i["id"]) for i in catalog["items"]
                  if i.get("kind") == "app")


def test_the_catalog_offers_the_apps_this_build_actually_has(monkeypatch, tmp_path):
    """Remove an app and the wizard stops proposing it."""
    write_app(tmp_path, "calendar",
              good_manifest("calendar", surfaces=["comms.calendar"]))
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(tmp_path))
    catalog = build_interview.installed_catalog()
    assert _app_ids(catalog) == ["app:calendar"]


def test_only_app_rows_are_touched(monkeypatch, tmp_path):
    """Packs and integrations are not folders in apps/. They stay as they are."""
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(tmp_path))
    before = build_interview.load_catalog()
    after = build_interview.installed_catalog()
    others = [i for i in before["items"] if i.get("kind") != "app"]
    assert [i for i in after["items"] if i.get("kind") != "app"] == others
    assert _app_ids(after) == []


def test_an_uninstalled_app_is_never_proposed(monkeypatch, tmp_path):
    """The wizard and the sidebar answer to the same folder."""
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(tmp_path))
    proposal = build_interview.propose("trivia and quiz nights")
    assert [e for e in proposal["flat"] if e["kind"] == "app"] == []


def test_a_strangers_app_gets_a_catalog_row_without_a_core_edit(monkeypatch, tmp_path):
    write_app(tmp_path, "widgetbot", good_manifest("widgetbot"))
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(tmp_path))
    row = next(i for i in build_interview.installed_catalog()["items"]
               if i.get("id") == "app:widgetbot")
    assert row["kind"] == "app"
    assert row["surfaces"] == ["daily.games"]
    # The words it already declares to the router are its tags. Nothing here
    # invents vocabulary the manifest never claimed.
    assert set(row["tags"]) == {"widgetbot"}


def test_a_derived_row_carries_no_dead_vocabulary(monkeypatch, tmp_path):
    """A tag no goal can ever match only makes the scan longer."""
    write_app(tmp_path, "widgetbot", good_manifest(
        "widgetbot",
        vocabulary={"keywords": ["the thing", "puzzles"], "examples": []}))
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(tmp_path))
    row = next(i for i in build_interview.installed_catalog()["items"]
               if i.get("id") == "app:widgetbot")
    assert "the thing" not in row["tags"]
    assert "puzzles" in row["tags"]


def test_the_shipped_catalog_and_the_shipped_apps_agree():
    """On disk, right now: every app row is installed and every app has a row."""
    catalog = build_interview.installed_catalog()
    assert _app_ids(catalog) == sorted(
        f"app:{i}" for i in app_registry.installed_ids())


def test_a_failed_app_scan_leaves_the_catalog_whole(monkeypatch):
    """Offering one app too many beats offering none."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("apps directory unreadable")

    monkeypatch.setattr(app_registry, "load_manifests", boom)
    assert (_app_ids(build_interview.installed_catalog())
            == _app_ids(build_interview.load_catalog()))


# ── Browser apps added at run time: one id rule ────────────
# The issued id shape (``<slug>-<8 base32>``) used to be "not usable" here, so
# every added browser app logged a corrupt manifest and the dashboard showed
# its id's words ("bunny s big day") instead of its name.

WEB_ID = "bunny-s-big-day-vdwsdeh2"


def web_manifest(**over):
    from puffpai.webapps import install
    manifest = install._manifest(WEB_ID, "Bunny’s Big Day", "Plan the wedding",
                                 {"repo": "https://github.com/example/wedding", "license": "MIT"},
                                 [{"host": "fonts.example.org", "for": "lettering"}])
    manifest.update(over)
    return manifest


@pytest.mark.parametrize("good", [WEB_ID, "a-abcdefgh", "9lives-abcdefgh",
                                  "x" * 30 + "-abcdefgh"])
def test_an_issued_id_is_a_valid_id(good):
    assert app_registry.valid_id(good) and app_registry.is_issued_id(good)


@pytest.mark.parametrize("bad", ["-abc-abcdefgh", "abc-abcdefg", "abc-abcdefg1", "ABC-abcdefgh",
                                 "abc.x-abcdefgh", "../x-abcdefgh", "x" * 31 + "-abcdefgh",
                                 "abc-abcdefgh\n", "abc_def"])
def test_near_miss_issued_ids_are_refused(bad):
    assert not app_registry.is_issued_id(bad)
    if bad != "abc_def":
        assert not app_registry.valid_id(bad)


def test_the_web_manifest_toolsmith_writes_is_valid_and_shown_by_its_name(tmp_path, surfaces):
    manifest = web_manifest()
    assert app_registry.validate_manifest(manifest, surfaces) is None
    write_app(tmp_path, WEB_ID, manifest)
    loaded = app_registry.load_manifests(str(tmp_path))
    assert [m["id"] for m in loaded] == [WEB_ID]
    assert config_health.report() == []            # no "not usable" line any more
    rec = app_registry.ui_records(loaded)[0]
    assert rec["name"] == "Bunny’s Big Day"
    assert rec["route"] == f"/dashboard/apps/{WEB_ID}"
    # it brings no words for the router and no data store
    assert app_registry.assemble_vocabulary(loaded) == {"keywords": [], "examples": []}
    with pytest.raises(ValueError):
        app_registry.data_path(WEB_ID, str(tmp_path))


_ZWJ_FAMILY = "Family \U0001F468\u200d\U0001F469\u200d\U0001F467 planner"


@pytest.mark.parametrize("name,title,repo,shown", [
    ("Bunny\u2019s Big Day", "", "o/wedding", "Bunny\u2019s Big Day"),
    (_ZWJ_FAMILY, "", "o/wedding", "Family \U0001F468\U0001F469\U0001F467 planner"),
    ("", "", "o/" + "a" * 80, "a" * 60),                    # a long repo name is cut
    ("Big\u202eDay", "", "o/big-day", "big-day"),           # a direction override is refused
    ("Big\u200bDay", "Big Day", "o/x", "Big Day"),         # a zero-width space too
    ("Big\u00a0Day", "", "o/x", "Big Day"),                 # a no-break space is a space
    ("A" * 70, "", "o/x", "A" * 60),
    ("", "", "o/" + "-" * 3, "Web app"),
])
def test_every_name_toolsmith_picks_is_one_the_app_list_accepts(surfaces, name, title, repo, shown):
    """install.friendly_name never picks a name the manifest check refuses, so
    the app is usable and shows a real name (never its id's words)."""
    from puffpai.webapps import install
    source = {"repo": f"https://github.com/{repo}", "license": "MIT"}
    got = install.friendly_name(name, source, title)
    assert got == shown
    manifest = install._manifest(WEB_ID, got, "Plan the wedding", source, [])
    assert app_registry.validate_manifest(manifest, surfaces) is None


@pytest.mark.parametrize("over", [
    {"vocabulary": {"keywords": ["wedding"]}},
    {"actions": [{"name": "add"}]},
    {"data": {"default": {"items": []}}},
    {"surfaces": ["daily.games"]},
    {"feed": {}},
    {"doors": []},
    {"script": "x.js"},
    {"kind": "declarative"},
    {"id": "wedding_planner"},
    {"name": "Bunny‮ yad gib"},
    {"name": "Two\nlines"},
    {"name": "x" * 65},
    {"ui": {"route": "/dashboard/apps/other-app-abcdefgh"}},
    {"ui": {"route": f"/dashboard/apps/{WEB_ID}", "icon": "x"}},
    {"outside": [{"host": "evil.example/path", "for": "lettering", "learned": "scan"}]},
    {"outside": [{"host": "*.example.org", "for": "lettering", "learned": "scan"}]},
    {"outside": [{"host": "a.example.org", "for": "lettering", "learned": "app"}]},
    {"web": {"entry": "../index.html", "storage": "mirror", "files_root": "x"}},
    {"web": {"entry": "index.html", "storage": "mirror", "files_root": "x", "run": "npm start"}},
])
def test_a_web_manifest_with_anything_extra_or_odd_is_refused(over, surfaces):
    assert app_registry.validate_manifest(web_manifest(**over), surfaces) is not None


def test_app_data_opens_no_store_for_a_browser_app(tmp_path, monkeypatch):
    write_app(tmp_path, WEB_ID, web_manifest())
    monkeypatch.setattr(app_registry, "load_manifests", lambda path=None: [web_manifest()])
    valid, files, _defaults = app_data._manifest_tables()
    assert WEB_ID not in valid and WEB_ID not in files
