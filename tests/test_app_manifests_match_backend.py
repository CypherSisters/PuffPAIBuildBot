"""The app contract as BuildBot ships it: manifests that do not lie.

WRECK_design items 12 and 13, and the privacy fixes mirrored from HomeBot.

Three things had drifted apart and each of them was a promise to a stranger:

* the manifests named actions (``add_event``, ``list_day``, ``plan_week``,
  ``start_round``) that no method on ``AppData`` answers to, while the action
  API's allowlist named ``cal_*`` / ``alarm_*`` / ``fresh_*``. A phrasebook
  built from the first set teaches an owner sentences the box cannot honour;
* the allowlist refused four methods ``AppData`` actually has, so an app page
  could not snooze, pause or resume a timer or log a set;
* the pages held a free-text model endpoint and dialled it from the BROWSER,
  under a panel that said everything runs locally.

Run:
    pytest tests/test_app_manifests_match_backend.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_bb_apps_mirror_test_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import api_server, app_registry  # noqa: E402
from puffpai.app_data import AppData  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_DIR = os.path.join(REPO_ROOT, "apps")
PACKS_DIR = os.path.join(REPO_ROOT, "packs")
PUBLIC_DIR = os.path.join(REPO_ROOT, "dashboard", "public")


@pytest.fixture(scope="module")
def shipped():
    return app_registry.load_manifests(APPS_DIR)


# ---------------------------------------------------------------------------
# the five privacy fields
# ---------------------------------------------------------------------------

def test_every_shipped_app_answers_the_five_privacy_questions(shipped):
    assert {str(m["id"]) for m in shipped} == {
        "alarmflow", "calendar", "freshtable", "fitfam", "trivia"}
    for manifest in shipped:
        app_id = manifest["id"]
        doors = manifest.get("doors")
        assert isinstance(doors, list) and doors, f"{app_id} declares no doors"
        for door in doors:
            assert isinstance(door.get("hosts"), list), app_id
            assert door.get("purpose"), f"{app_id} has a door with no purpose"
            assert door.get("data_sent_plain"), (
                f"{app_id} does not say what leaves the box")
        assert manifest["credential"]["kind"] in app_registry.CREDENTIAL_KINDS
        assert isinstance(
            manifest["children"]["may_be_used_by_minor"], bool), app_id


def test_the_shipped_five_reach_nowhere(shipped):
    """Every shipped app declares an empty host list. If that ever changes the
    door has to be argued for here first."""
    for manifest in shipped:
        for door in manifest.get("doors") or []:
            assert door["hosts"] == [], (
                f"{manifest['id']} grew a door host; register it with "
                f"security.egress_doors before shipping it")


def test_a_manifest_may_not_ask_for_the_owners_password():
    manifest = _minimal("greedybot")
    manifest["credential"] = {"kind": "password"}
    problem = app_registry.validate_manifest(manifest, {"daily.games"})
    assert problem and "password" in problem


def test_a_manifest_may_not_carry_a_secrets_value():
    manifest = _minimal("leakybot")
    manifest["credential"] = {"kind": "api_key", "value": "not-a-real-key"}
    problem = app_registry.validate_manifest(manifest, {"daily.games"})
    assert problem and "never a secret's value" in problem


def test_a_door_host_is_an_exact_hostname():
    for bad in ("*.example.com", "https://example.com", "example.com:443",
                "example.com/path"):
        manifest = _minimal("wildbot")
        manifest["doors"] = [{"hosts": [bad], "purpose": "why",
                              "data_sent_plain": "words"}]
        problem = app_registry.validate_manifest(manifest, {"daily.games"})
        assert problem, f"{bad} was accepted as a host"


def test_half_a_door_is_refused():
    """A present-but-incomplete block is worse than none: the card would render
    as though the author had answered."""
    manifest = _minimal("vaguebot")
    manifest["doors"] = [{"hosts": ["example.com"]}]
    assert app_registry.validate_manifest(manifest, {"daily.games"})


# ---------------------------------------------------------------------------
# action ids (WRECK item 12)
# ---------------------------------------------------------------------------

def test_every_declared_action_is_a_real_backend_method(shipped):
    for manifest in shipped:
        for action in manifest.get("actions") or []:
            action_id = action["id"]
            assert callable(getattr(AppData, action_id, None)), (
                f"{manifest['id']} declares '{action_id}', which is not a "
                f"method on AppData")


def test_every_declared_action_is_allowed_by_the_action_api(shipped):
    for manifest in shipped:
        for action in manifest.get("actions") or []:
            assert action["id"] in api_server._ALLOWED_APP_ACTIONS, (
                f"{manifest['id']} declares '{action['id']}', which the action "
                f"API would refuse with 403")


def test_no_manifest_promises_a_sentence_the_box_cannot_honour(shipped):
    """The four utterances removed because nothing implements them."""
    gone = ("plan the meals for this week", "start a trivia quiz")
    blob = json.dumps(shipped).lower()
    for sentence in gone:
        assert sentence not in blob


def test_the_router_fallback_lost_the_unimplemented_sentence():
    with open(os.path.join(REPO_ROOT, "config", "rulepacks", "apps.json"),
              encoding="utf-8") as fh:
        baked = json.load(fh)
    assert "start a trivia quiz" not in baked["examples"]


# ---------------------------------------------------------------------------
# the allowlist (WRECK item 13)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    "alarm_snooze", "alarm_pause", "alarm_resume", "fit_log_set"])
def test_the_four_missing_actions_exist_and_are_allowed(action):
    assert callable(getattr(AppData, action, None))
    assert action in api_server._ALLOWED_APP_ACTIONS


def test_the_allowlist_names_only_methods_that_exist():
    for action in api_server._ALLOWED_APP_ACTIONS:
        assert callable(getattr(AppData, action, None)), (
            f"the allowlist names '{action}', which AppData does not have")


def test_snooze_pause_and_resume_move_a_timer(tmp_path, monkeypatch):
    from puffpai import app_data as app_data_module
    monkeypatch.setattr(app_data_module, "_DATA_DIR", str(tmp_path))
    data = AppData()
    data.alarm_create_timer("pasta", minutes=10)
    assert "Paused pasta" in data.alarm_pause("pasta")
    assert "Resumed pasta" in data.alarm_resume("pasta")
    assert "Snoozed pasta for 5 minutes" in data.alarm_snooze("pasta", 5)
    assert "No alarm or timer found" in data.alarm_pause("risotto")


# ---------------------------------------------------------------------------
# requires
# ---------------------------------------------------------------------------

def test_a_model_less_build_marks_the_two_model_apps_degraded(shipped):
    records = {r["id"]: r for r in
               app_registry.ui_records(shipped, {"ollama": False})}
    assert records["freshtable"]["degraded_reason"] == (
        "FreshTable needs a local model, and none is available on this build.")
    assert records["trivia"]["degraded"] is True
    assert records["calendar"]["degraded"] is False


def test_a_probe_that_could_not_answer_says_so_rather_than_nothing(shipped):
    """VERIFY 3d. None used to mean "not unmet", so a box that could not check
    reported five healthy apps. Both False and None are degraded now; only the
    sentence differs."""
    records = {r["id"]: r for r in app_registry.ui_records(
        shipped, {"ollama": None, "home_assistant": None, "network": None})}
    assert records["freshtable"]["degraded"] is True
    assert records["freshtable"]["degraded_reason"] == (
        "FreshTable needs a local model, and this build could not verify that "
        "one is there.")
    # An app that declares no requirement is never degraded by a broken probe.
    assert records["calendar"]["degraded"] is False


def test_nobody_probed_anything_degrades_nothing(shipped):
    """The shape every caller who does not care passes."""
    for available in (None, {}):
        records = app_registry.ui_records(shipped, available)
        assert not any(r["degraded"] for r in records)


def test_a_box_with_no_model_backend_reports_degraded_through_build_self(
        monkeypatch):
    """VERIFY 3d, end to end. config.get_llm_backend RAISES RuntimeError on a
    model-less box - the exact state the badge exists for - and that must reach
    /build/self as a degraded app, not as silence."""
    import puffpai.config as config_module

    def no_backend(*args, **kwargs):
        raise RuntimeError("No LLM backend available.")

    monkeypatch.setattr(config_module, "get_llm_backend", no_backend)
    monkeypatch.setitem(api_server._req_cache, "value", None)

    available = api_server._requirement_availability(force=True)
    assert available["ollama"] is False, (
        "an absent backend is an ANSWER, not a failed probe")

    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    resp = client.get("/build/self",
                      headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert resp.status_code == 200
    apps = {a["id"]: a for a in (resp.get_json().get("apps") or [])}
    assert apps, "/build/self served no apps"
    for app_id in ("freshtable", "trivia"):
        assert apps[app_id]["degraded"] is True, f"{app_id} was not degraded"
        assert "needs a local model" in apps[app_id]["degraded_reason"]
    assert apps["calendar"]["degraded"] is False

    api_server._req_cache["value"] = None


def test_an_unmet_requirement_does_not_uninstall_the_app(shipped):
    records = app_registry.ui_records(shipped, {"ollama": False})
    assert {r["id"] for r in records} == {str(m["id"]) for m in shipped}


def test_an_app_cannot_invent_a_requirement():
    manifest = _minimal("veto")
    manifest["requires"] = {"a_thing_this_build_never_heard_of": True}
    assert app_registry.declared_requirements(manifest) == []


# ---------------------------------------------------------------------------
# drop + rescan (VERIFY 3c)
# ---------------------------------------------------------------------------

def test_rescan_installs_and_uninstalls_by_folder(tmp_path, monkeypatch):
    """Installing IS rescanning: 5 -> 4 -> 5 as a folder leaves and returns.

    The cache in between is stale ON PURPOSE - that is what makes /build/self
    cheap - so this also pins the staleness rather than leaving it to be
    discovered as a bug.
    """
    apps = tmp_path / "apps"
    shutil.copytree(APPS_DIR, apps)
    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(apps))
    monkeypatch.setattr(app_registry, "_cached_manifests", None)

    assert len(app_registry.rescan()) == 5

    shutil.rmtree(apps / "trivia")
    assert len(app_registry.current_manifests()) == 5, (
        "the cache is meant to be reused until rescan says otherwise")
    assert [m["id"] for m in app_registry.rescan()] == [
        "alarmflow", "calendar", "freshtable", "fitfam"]
    assert len(app_registry.current_manifests()) == 4

    shutil.copytree(os.path.join(APPS_DIR, "trivia"), apps / "trivia")
    assert len(app_registry.rescan()) == 5
    assert "trivia" in app_registry.installed_ids(
        app_registry.current_manifests())


def test_build_self_reads_the_cache_rescan_maintains():
    """3c: the cache had no production caller, so the install mechanism was
    dead code. /build/self is that caller now."""
    import inspect
    source = inspect.getsource(api_server._build_self_apps)
    assert "current_manifests()" in source


def test_rescan_is_reachable_and_gated():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    assert client.post("/apps/rescan").status_code == 401
    resp = client.post("/apps/rescan",
                       headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["apps"]) == {"alarmflow", "calendar", "freshtable",
                                 "fitfam", "trivia"}
    assert body["packs"] == ["homebot"]
    assert "apps_rescan" not in api_server._PUBLIC_ENDPOINTS


# ---------------------------------------------------------------------------
# concurrent writes across a rescan (VERIFY appendix)
# ---------------------------------------------------------------------------
# A test drove /apps/rescan between a request resolving its AppData and
# using it, then had both instances write trivia.json: 124 of 131 reads came
# back undecodable, and `_load` turned every one of those into the app's empty
# DEFAULTS. The pages pull, merge and push, so that is the "the whole saved
# collection was gone" shape, arrived at from the other end.
#
# Three things had to be true for that, and each has a test below: the write
# was truncate-in-place, the locks were per-INSTANCE, and a decode failure was
# silently a valid empty document.


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from puffpai import app_data as app_data_module
    monkeypatch.setattr(app_data_module, "_DATA_DIR", str(tmp_path))
    return tmp_path


def test_two_instances_share_one_lock_per_file(data_dir):
    """The locks are keyed on the resolved path, not on the object."""
    first, second = AppData(), AppData()
    assert first is not second
    for app in first.VALID_APPS:
        assert first._locks[app] is second._locks[app], (
            f"{app} has disjoint locks across instances")


def test_a_reader_never_sees_a_half_written_document(data_dir):
    """VERIFY appendix: zero undecodable reads, zero default fallbacks.

    Two instances - exactly what the old singleton swap produced - hammering
    the same document while a rescan runs underneath them. Every read must
    return a document somebody actually wrote.
    """
    from puffpai.app_data import AppDataUnreadable

    writer_side, reader_side = AppData(), AppData()
    # A payload big enough that a truncate-then-write would be visibly torn.
    def payload(n):
        return {"results": [{"n": n, "pad": "x" * 400} for _ in range(40)]}

    writer_side.set_data("trivia", payload(0))

    stop = threading.Event()
    faults = []
    reads = [0]

    def write():
        n = 0
        while not stop.is_set() and n < 200:
            n += 1
            try:
                writer_side.set_data("trivia", payload(n))
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                faults.append(f"write: {exc!r}")

    def read():
        while not stop.is_set():
            try:
                got = reader_side.get_data("trivia")
            except AppDataUnreadable as exc:
                faults.append(f"undecodable: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                faults.append(f"read: {exc!r}")
                continue
            reads[0] += 1
            if not got.get("results"):
                faults.append("read returned the empty DEFAULT document")

    def rescan():
        while not stop.is_set():
            writer_side.reload_manifests()
            reader_side.reload_manifests()

    threads = ([threading.Thread(target=write) for _ in range(3)]
               + [threading.Thread(target=read) for _ in range(3)]
               + [threading.Thread(target=rescan)])
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads[:3]:      # the writers are the bounded ones
        thread.join(timeout=60)
    stop.set()
    for thread in threads[3:]:
        thread.join(timeout=10)

    assert reads[0] > 50, f"the test did not actually read much ({reads[0]})"
    assert not faults, f"{len(faults)} faults in {reads[0]} reads: {faults[:5]}"


def test_a_corrupt_document_is_never_reported_as_an_empty_one(data_dir):
    """`_load` used to swallow the decode error and hand back DEFAULTS."""
    from puffpai.app_data import AppDataUnreadable

    data = AppData()
    data.set_data("trivia", {"results": [{"n": 1}]})
    with open(data._files["trivia"], "w", encoding="utf-8") as fh:
        fh.write('{"results": [{"n": 1}')      # truncated, as a torn read is
    with pytest.raises(AppDataUnreadable):
        data.get_data("trivia")
    with pytest.raises(AppDataUnreadable):
        data.get_updated("trivia")


def test_an_absent_or_empty_file_is_still_just_not_written_yet(data_dir):
    """A missing document, and the zero-byte one an old crash left behind, are
    both "nothing saved yet" - only a non-empty file that will not decode is a
    fault."""
    data = AppData()
    assert data.get_data("trivia")["results"] == []
    open(data._files["trivia"], "w").close()
    assert data.get_data("trivia")["results"] == []


def test_the_write_is_atomic_and_leaves_no_temp_file(data_dir):
    data = AppData()
    data.set_data("trivia", {"results": [{"n": 1}]})
    leftovers = [n for n in os.listdir(data_dir) if not n.endswith(".json")]
    assert not leftovers, f"the writer left {leftovers} behind"
    if os.name == "posix":
        import stat as stat_module
        mode = stat_module.S_IMODE(os.stat(data._files["trivia"]).st_mode)
        assert mode == 0o600, f"app document is {oct(mode)}, not 0600"


def test_rescan_reloads_the_singleton_instead_of_dropping_it():
    """VERIFY appendix: nulling the reference left a window in which a stale
    instance and a fresh one both wrote the same file."""
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    before = api_server._get_app_data()
    resp = client.post("/apps/rescan",
                       headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert resp.status_code == 200
    assert api_server._get_app_data() is before, (
        "the rescan replaced the AppData instance")


def test_a_reader_never_catches_install_between_two_assignments(data_dir,
                                                               monkeypatch):
    """`_install` published VALID_APPS BEFORE the tables it gates, so a reader
    that got past the gate on a newly named app hit KeyError on _locks[app]."""
    from puffpai import app_data as app_data_module

    data = AppData()
    full = app_data_module._manifest_tables()
    valid, files, defaults = full
    # The same build minus one app, so reloads alternate between a wider and a
    # narrower set and both publication directions get exercised.
    narrow = ([a for a in valid if a != "trivia"],
              {a: q for a, q in files.items() if a != "trivia"},
              {a: s for a, s in defaults.items() if a != "trivia"})

    tables = [full, narrow]
    counter = {"n": 0}

    def alternating():
        counter["n"] += 1
        return tables[counter["n"] % 2]

    monkeypatch.setattr(app_data_module, "_manifest_tables", alternating)

    stop = threading.Event()
    faults = []
    looks = [0]

    def reload_loop():
        for _ in range(600):
            if stop.is_set():
                return
            data.reload_manifests()

    def read_loop():
        while not stop.is_set():
            for app in list(data.VALID_APPS):
                try:
                    # Exactly what every public method does: gate on
                    # VALID_APPS, then index the tables.
                    if app not in data.VALID_APPS:
                        continue
                    data._locks[app], data._files[app], data._defaults[app]
                    looks[0] += 1
                except KeyError as exc:
                    faults.append(f"KeyError {exc} on {app}")

    reloader = threading.Thread(target=reload_loop, daemon=True)
    readers = [threading.Thread(target=read_loop, daemon=True)
               for _ in range(3)]
    for thread in [reloader] + readers:
        thread.start()
    reloader.join(timeout=60)
    stop.set()
    for thread in readers:
        thread.join(timeout=10)

    assert looks[0] > 500, f"the readers barely ran ({looks[0]})"
    assert not faults, f"{len(faults)} faults in {looks[0]} lookups: {faults[:5]}"


def test_the_lookup_tables_are_published_before_valid_apps():
    """Deterministic guard on the ordering the race test can only sample.

    Every reader gates on VALID_APPS and then indexes the three tables without
    a lock, so VALID_APPS must be the LAST thing `_install` assigns.
    """
    import inspect
    lines = inspect.getsource(AppData._install).splitlines()
    at = {}
    for number, line in enumerate(lines):
        for field in ("self.VALID_APPS =", "self._files =", "self._locks =",
                      "self._defaults ="):
            if line.strip().startswith(field):
                at[field] = number
    assert len(at) == 4, f"_install no longer assigns all four: {sorted(at)}"
    assert at["self.VALID_APPS ="] == max(at.values()), (
        "VALID_APPS is published before a table it gates")


def test_a_reload_never_strands_an_app_a_reader_is_still_holding(data_dir,
                                                                monkeypatch):
    """The removal direction, deterministically.

    Ordering alone only fixes the app-ADDED case. When an app is removed, the
    narrower tables are published while the older, wider VALID_APPS is still
    what a mid-flight reader is looking at - so the tables are a superset and
    a stale name still resolves.
    """
    from puffpai import app_data as app_data_module

    data = AppData()
    valid, files, defaults = app_data_module._manifest_tables()
    monkeypatch.setattr(app_data_module, "_manifest_tables", lambda: (
        [a for a in valid if a != "trivia"],
        {a: q for a, q in files.items() if a != "trivia"},
        {a: s for a, s in defaults.items() if a != "trivia"}))

    stale_view = list(data.VALID_APPS)
    assert "trivia" in stale_view
    data.reload_manifests()

    assert "trivia" not in data.VALID_APPS, "the app was not uninstalled"
    for app in stale_view:
        # What a reader that got past the gate a moment earlier will do next.
        data._locks[app], data._files[app], data._defaults[app]


def test_a_write_does_not_overwrite_a_document_it_could_not_read(data_dir):
    """The read side was taught that corrupt is not empty; the write side had
    the same hole facing the other way."""
    from puffpai.app_data import AppDataUnreadable

    data = AppData()
    data.set_data("trivia", {"results": [{"n": 1}]})
    path = data._files["trivia"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"results": [{"n": 1}')
    corrupt = io.open(path, encoding="utf-8").read()

    with pytest.raises(AppDataUnreadable):
        data.set_data("trivia", {"results": []})
    assert io.open(path, encoding="utf-8").read() == corrupt, (
        "the unreadable document was overwritten anyway")

    # The deliberate override, which the pages never send.
    assert data.set_data("trivia", {"results": [{"n": 2}]}, recover=True)
    assert data.get_data("trivia")["results"] == [{"n": 2}]


def test_put_refuses_an_unreadable_document_unless_recover_is_asked_for(
        data_dir, monkeypatch):
    """PUT /apps/<id>/data used to answer 200 and replace the file."""
    store = AppData()
    monkeypatch.setattr(api_server, "_get_app_data", lambda: store)
    store.set_data("trivia", {"results": [{"n": 1}]})
    path = store._files["trivia"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"results": [{"n": 1}')
    corrupt = io.open(path, encoding="utf-8").read()

    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}

    resp = client.put("/apps/trivia/data", json={"results": []}, headers=auth)
    assert resp.status_code == 503
    assert "nothing was changed" in resp.get_json()["error"]
    assert io.open(path, encoding="utf-8").read() == corrupt

    resp = client.put("/apps/trivia/data?recover=1",
                      json={"results": [{"n": 2}]}, headers=auth)
    assert resp.status_code == 200
    assert store.get_data("trivia")["results"] == [{"n": 2}]


def test_no_app_page_ever_sends_recover():
    """The override is for a person who has looked at the file."""
    for name in sorted(os.listdir(PUBLIC_DIR)):
        if name.endswith("-app.html"):
            body = io.open(os.path.join(PUBLIC_DIR, name),
                           encoding="utf-8").read()
            for shape in ("recover=1", "recover=true", "?recover", "&recover"):
                assert shape not in body, (
                    f"{name} sends the recover override")


# ---------------------------------------------------------------------------
# packs and the honest hosts label
# ---------------------------------------------------------------------------

def test_the_shipped_pack_loads_with_a_derived_privacy_summary(shipped):
    packs = app_registry.load_packs(PACKS_DIR, manifests=shipped)
    assert [p["id"] for p in packs] == ["homebot"]
    privacy = packs[0]["privacy"]
    assert privacy["child_safe_declared"] is True
    assert privacy["hosts"] == []
    assert privacy["hosts_enforcement"] == "declared_only"


def test_the_pack_never_says_child_safe(shipped):
    """The shorter name would be a judgement the box has not made."""
    packs = app_registry.load_packs(PACKS_DIR, manifests=shipped)
    assert "child_safe" not in packs[0]["privacy"]


def test_a_pack_may_not_list_an_app_that_is_not_installed(shipped):
    pack = {"schema": app_registry.PACK_SCHEMA, "id": "ghost",
            "display_name": "Ghost pack",
            "apps": [{"id": "notinstalled"}]}
    problem = app_registry.validate_pack(pack, {str(m["id"]) for m in shipped})
    assert problem and "notinstalled" in problem


def test_the_box_does_not_claim_to_enforce_declared_hosts():
    assert app_registry.HOSTS_ENFORCEMENT == "declared_only"
    assert "does not yet enforce" in app_registry.HOSTS_ENFORCEMENT_NOTICE


def test_no_app_door_is_in_the_enforced_door_table():
    """The notice above is only honest while this holds. If manifest doors are
    ever registered with egress_doors, this test fails and the notice must be
    rewritten in the same commit."""
    from puffpai.security import egress_doors
    assert not [d for d in egress_doors.door_ids() if str(d).startswith("app:")]


# ---------------------------------------------------------------------------
# the two gated routes, and the pages that use them
# ---------------------------------------------------------------------------

def test_the_model_routes_are_behind_the_app_api_key():
    for endpoint in ("apps_generate", "apps_ocr"):
        assert endpoint not in api_server._PUBLIC_ENDPOINTS


def test_an_unauthenticated_page_cannot_reach_the_model():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    for path in ("/apps/generate", "/apps/ocr"):
        assert client.post(path, json={"prompt": "hi"}).status_code == 401


def test_generate_refuses_an_oversized_prompt():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    body = {"prompt": "x" * (api_server.MAX_APP_PROMPT_CHARS + 1)}
    assert client.post("/apps/generate", json=body,
                       headers=auth).status_code == 413


def test_ocr_refuses_something_that_is_not_an_image():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    resp = client.post("/apps/ocr", json={"image_b64": "not base64!!"},
                       headers=auth)
    assert resp.status_code == 400


def test_no_app_page_holds_an_absolute_url():
    """The panel says everything runs on this box. This is what makes that true
    by construction rather than by the honesty of whoever last edited a page."""
    offenders = []
    for name in sorted(os.listdir(PUBLIC_DIR)):
        if not name.endswith("-app.html"):
            continue
        with open(os.path.join(PUBLIC_DIR, name), encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if "http://" in line or "https://" in line:
                    # An XML namespace is not a fetch.
                    if "www.w3.org" in line:
                        continue
                    offenders.append(f"{name}:{number}")
    assert not offenders, f"absolute URLs in app pages: {offenders}"


# ---------------------------------------------------------------------------

def _minimal(app_id: str) -> dict:
    return {
        "schema": app_registry.SCHEMA,
        "id": app_id,
        "name": app_id.title(),
        "surfaces": ["daily.games"],
    }
