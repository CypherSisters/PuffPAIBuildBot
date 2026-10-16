"""Rows on "What leaves your computer", and the switch ids behind them.

Proven here: the five as-built rows (looking for apps and code, downloading
it, looking for pictures, downloading them, saving an app's parts) with
their exact words and a one-tap off that ``source_fetch`` obeys; rows other
parts of BuildBot register (``register_rows``) show, are checked against the
real switch instead of taken on trust, and switch off through the existing
``/outside/<id>/off`` route; the switch-id formulas; and outside-traffic log
lines filed under those ids are kept (the key filter used to drop them).
"""

from __future__ import annotations

import hashlib
import re

import pytest

from puffpai import api_server, outside_services, source_fetch
from puffpai.security import (consent_tickets, egress_doors, egress_ledger,
                              outside_switches)

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
APP = "wedding-planner-abcdefgh"
HOST = "api.example-data.org"
HOST2 = "tiles.example-data.org"
DEVICE = "device:owner-phone-0001"

R3_WORDS = {
    "code_search": ("Looking for apps and code",
                    "Finding apps and code for the jobs you ticked, when you "
                    "press Start looking.",
                    "Only the short search words shown on screen, and your "
                    "internet address."),
    "code_download": ("Downloading an app or code you picked", None,
                      "The name of the project and your internet address."),
    "picture_search": ("Looking for pictures", None,
                       "Only the short search words shown on screen, and your "
                       "internet address."),
    "picture_download": ("Downloading pictures you picked", None,
                         "Which pictures, and your internet address."),
    "app_parts": ("Saving the parts an app loads", None,
                  "Which parts, once, and your internet address."),
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    outside_switches.forget()
    egress_doors.clear_web_app_doors()
    consent_tickets.reset()
    consent_tickets.set_owner_devices_provider(None)
    for name in ("webapps", "boxes", "test_rows", "broken", "junk"):
        outside_services.unregister_rows(name)
    yield
    for name in ("webapps", "boxes", "test_rows", "broken", "junk"):
        outside_services.unregister_rows(name)
    egress_doors.clear_web_app_doors()
    outside_switches.forget()


def _by_id():
    return {r["id"]: r for r in outside_services.rows()}


# -- the switch ids ------------------------------------------------------------

def test_switch_id_app_is_the_pinned_formula():
    want = "app_" + hashlib.sha256(f"{APP}|{HOST}".encode()).hexdigest()[:24]
    assert outside_switches.switch_id_app(APP, HOST) == want
    assert len(want) == 28
    assert outside_switches.switch_id_app(APP, "example-data.org") != want


def test_switch_id_box_is_the_pinned_formula():
    want = "box_" + hashlib.sha256(b"my-project").hexdigest()[:24]
    assert outside_switches.switch_id_box("my-project") == want


def test_switch_ids_fit_the_switch_file_and_the_routes():
    sid_re = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
    for sid in (outside_switches.switch_id_app("a" * 30 + "-abcdefgh", "x" * 253),
                outside_switches.switch_id_box("any name at all, even spaces")):
        assert sid_re.match(sid)
        outside_switches.set_switch(sid, False)
        assert outside_switches.is_off(sid)


@pytest.mark.parametrize("args", [(None, HOST), (APP, None), ("", HOST), (APP, ""),
                                  (1, HOST)])
def test_switch_id_app_refuses_what_is_not_text(args):
    with pytest.raises(ValueError):
        outside_switches.switch_id_app(*args)


@pytest.mark.parametrize("project", [None, "", 5])
def test_switch_id_box_refuses_what_is_not_text(project):
    with pytest.raises(ValueError):
        outside_switches.switch_id_box(project)


# -- the log keeps lines filed under those ids ------------------------------------

def test_box_and_app_log_lines_are_kept():
    box = outside_switches.switch_id_box("some project")
    app_row = outside_switches.switch_id_app(APP, HOST)
    for sid in (box, app_row, f"app:{APP}:web"):
        assert egress_ledger.record(sid, host="registry.npmjs.org", outcome="done",
                                    what="box_install"), sid
    services = [l["service"] for l in egress_ledger.read(1, limit=10)]
    assert set(services) == {box, app_row, f"app:{APP}:web"}


def test_a_random_looking_service_id_is_still_dropped():
    token = "q8Zr2mXv9TcLp4Ws7Yb1Nf6Hd3Kj5Ga0"
    assert egress_ledger.build_record(token.lower(), host="x.org", outcome="done") is None
    # Only the exact made shapes pass: 24 lower-case hex, nothing more.
    assert egress_ledger._MADE_SERVICE_RE.match("box_" + "a" * 24)  # noqa: SLF001
    assert not egress_ledger._MADE_SERVICE_RE.match("box_" + "a" * 25)  # noqa: SLF001
    assert not egress_ledger._MADE_SERVICE_RE.match("box_" + "A" * 24)  # noqa: SLF001
    assert not egress_ledger._MADE_SERVICE_RE.match("app:x:web")  # noqa: SLF001


# -- the five as-built rows --------------------------------------------------------

def test_the_five_round_three_rows_and_their_words():
    rows = _by_id()
    for sid, (name, purpose, leaves) in R3_WORDS.items():
        row = rows[sid]
        assert row["name"] == name
        if purpose:
            assert row["for"] == purpose
        assert row["for"]
        assert row["what_leaves"] == leaves
        assert (row["state"], row["can_switch"], row["kind"]) == ("on", True, "as_built")
        assert row["switch_to"] == "off" and row["websites"]


def test_the_rows_name_the_sites_the_where_to_look_list_uses():
    rows = _by_id()
    assert "api.github.com" in rows["code_search"]["websites"]
    assert "codeload.github.com" in rows["code_download"]["websites"]
    assert "api.openverse.org" in rows["picture_search"]["websites"]


def test_one_tap_off_is_obeyed_before_any_socket():
    outside_services.switch_off("code_search")
    assert _by_id()["code_search"]["state"] == "off"
    sent = []
    got = source_fetch.fetch_small(
        "https://api.github.com/search/repositories?q=x",
        allowed_hosts={"api.github.com"}, max_bytes=100, service="code_search",
        honour_robots=False, transport=lambda *a: sent.append(a),
        resolver=lambda h: ["140.82.112.5"])
    assert got["reason"] == "switched_off" and sent == []


def test_switching_back_on_needs_the_usual_ticket():
    outside_services.switch_off("app_parts")
    sentence = outside_services.proposal("app_parts")
    assert "Saving the parts an app loads" in sentence
    outside_services.switch_on("app_parts")
    assert _by_id()["app_parts"]["state"] == "on"


# -- rows other parts of BuildBot register -------------------------------------------

def _app_row(sid, state="on", **extra):
    return {"id": sid, "name": f"Wedding Planner uses example-data.org",
            "for": "fetching or saving data online",
            "what_leaves": "What the app sends there, and your internet address.",
            "websites": [HOST, HOST2], "state": state, "state_line": "",
            "can_switch": False, "kind": "app_web", **extra}


def _web_app_on(*hosts):
    egress_doors.register_web_app_door(APP, list(hosts))
    on = []
    for host in hosts:
        on.append(host)
        ticket = consent_tickets.mint(
            "app_host_on", consent_tickets.target_app_host(APP, host),
            "Let the app use that site.", DEVICE)
        assert egress_doors.set_app_web_hosts(APP, list(on), by=DEVICE,
                                              ticket=ticket)["ok"]


def test_a_registered_app_row_shows_its_real_state():
    _web_app_on(HOST, HOST2)
    sid = outside_switches.switch_id_app(APP, "example-data.org")
    outside_services.register_rows("webapps", lambda: [_app_row(sid, state="off")])
    row = _by_id()[sid]
    # The provider said "off"; the real switch says a site is on.
    assert (row["state"], row["can_switch"], row["switch_to"]) == ("on", True, "off")
    assert row["kind"] == "app_web" and row["websites"] == [HOST, HOST2]


def test_an_app_row_claiming_on_with_no_such_app_shows_off():
    sid = outside_switches.switch_id_app(APP, "example-data.org")
    outside_services.register_rows("webapps", lambda: [_app_row(sid, state="on")])
    row = _by_id()[sid]
    assert (row["state"], row["can_switch"]) == ("off", False)
    assert row["why_cannot_switch"] == outside_services.APP_ON_ELSEWHERE


def test_an_app_row_switches_off_through_the_outside_route(monkeypatch):
    _web_app_on(HOST, HOST2)
    sid = outside_switches.switch_id_app(APP, "example-data.org")
    outside_services.register_rows("webapps", lambda: [_app_row(sid)])
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    listed = client.get("/outside", headers=AUTH).get_json()
    assert any(s["id"] == sid and s["how_to_switch"] == "Switch off"
               for s in listed["services"])
    got = client.post(f"/outside/{sid}/off", headers=AUTH)
    assert got.status_code == 200, got.get_json()
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: False}
    row = _by_id()[sid]
    assert (row["state"], row["can_switch"]) == ("off", False)
    # And nothing can go there any more.
    monkeypatch.setattr(egress_doors, "run_posture", lambda: {
        "run_egress": egress_doors.OPEN_POSTURE, "source": "t", "switches": {}})
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "switched_off"}


def test_an_app_row_for_one_site_switches_only_that_site_off():
    _web_app_on(HOST, HOST2)
    sid = outside_switches.switch_id_app(APP, HOST2)
    outside_services.register_rows("webapps", lambda: [_app_row(sid)])
    outside_services.switch_off(sid)
    assert egress_doors.app_web_hosts(APP) == {HOST: True, HOST2: False}


def test_an_app_row_is_never_switched_on_from_the_list():
    _web_app_on(HOST)
    egress_doors.set_app_web_hosts(APP, [], by="owner")
    sid = outside_switches.switch_id_app(APP, HOST)
    outside_services.register_rows("webapps", lambda: [_app_row(sid)])
    with pytest.raises(outside_services.OutsideError):
        outside_services.proposal(sid)
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_on(sid)
    assert egress_doors.app_web_hosts(APP) == {HOST: False}


def _box_row(sid, *, can_switch=True, state="on"):
    return {"id": sid, "name": "Wedding site: parts while building in the locked box",
            "for": "Building the project you picked.",
            "what_leaves": "Requests for code parts from registry.npmjs.org, and "
                           "your internet address.",
            "websites": ["registry.npmjs.org"], "state": state,
            "can_switch": can_switch, "kind": "box"}


def test_a_box_row_that_can_switch_switches_off():
    sid = outside_switches.switch_id_box("wedding-site")
    outside_services.register_rows("boxes", lambda: [_box_row(sid)])
    assert _by_id()[sid]["state"] == "on"
    outside_services.switch_off(sid)
    assert outside_switches.is_off(sid)
    row = _by_id()[sid]
    assert (row["state"], row["can_switch"]) == ("off", False)
    assert row["why_cannot_switch"] == outside_services.BOX_ON_ELSEWHERE
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_on(sid)


def test_a_box_row_that_cannot_switch_is_not_switched():
    sid = outside_switches.switch_id_box("wedding-site")
    outside_services.register_rows("boxes", lambda: [_box_row(sid, can_switch=False)])
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_off(sid)
    assert outside_switches.get(sid) is None


@pytest.mark.parametrize("raw", [
    None, "row", {"id": "weather", "name": "Mine now"},
    {"id": "Bad Id!", "name": "x"}, {"id": "app_1234", "name": ""},
    {"id": "x" * 70, "name": "x"},
])
def test_unusable_or_taken_rows_are_left_out(raw):
    before = _by_id()
    outside_services.register_rows("junk", lambda: [raw])
    after = _by_id()
    assert set(after) == set(before)
    assert after["weather"]["name"] == before["weather"]["name"]


@pytest.mark.parametrize("raw", [
    # the validator's probe: a kind its id does not fit, claiming "on"
    {"id": "liar-row", "name": "Liar", "state": "on", "kind": "app_web",
     "can_switch": True},
    {"id": "box_" + "0" * 24, "name": "Wrong kind", "state": "on",
     "kind": "app_web", "can_switch": True},
    {"id": "app_" + "0" * 24, "name": "Wrong kind", "state": "on",
     "kind": "box", "can_switch": True},
    {"id": "made-up-row", "name": "No kind", "state": "on", "can_switch": True},
    {"id": "other-row", "name": "Other kind", "state": "on", "kind": "dynamic"},
])
def test_a_row_whose_state_cannot_be_checked_is_left_out(raw):
    """A row whose id does not fit an app or box kind has no real switch to
    check its state against, so it is never shown (never 'on' on trust)."""
    outside_services.register_rows("junk", lambda: [dict(raw)])
    assert raw["id"] not in _by_id()
    assert outside_services.row(raw["id"]) is None
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_off(raw["id"])
    page = api_server.app.test_client().get("/outside", headers=AUTH)
    assert page.status_code == 200
    ids = {s.get("id") for s in page.get_json()["services"]}
    assert raw["id"] not in ids


def test_a_provider_that_breaks_does_not_break_the_page():
    def broken():
        raise RuntimeError("nope")

    outside_services.register_rows("broken", broken)
    outside_services.register_rows("junk", lambda: "not a list")
    assert "weather" in _by_id()


def test_register_rows_checks_its_inputs_and_replaces_by_name():
    with pytest.raises(ValueError):
        outside_services.register_rows("Bad Name", lambda: [])
    with pytest.raises(ValueError):
        outside_services.register_rows("test_rows", "not callable")
    one, two = outside_switches.switch_id_box("one"), outside_switches.switch_id_box("two")
    outside_services.register_rows("test_rows", lambda: [
        {"id": one, "name": "One", "kind": "box"}])
    outside_services.register_rows("test_rows", lambda: [
        {"id": two, "name": "Two", "kind": "box"}])
    rows = _by_id()
    assert two in rows and one not in rows
    assert outside_services.unregister_rows("test_rows") is True
    assert two not in _by_id()


def test_long_words_are_cut_and_the_row_count_is_capped():
    ids = {outside_switches.switch_id_box(f"p{n}") for n in range(400)}
    outside_services.register_rows("test_rows", lambda: [
        {"id": sid, "name": "N" * 500, "for": "F" * 900, "kind": "box"}
        for sid in sorted(ids)])
    mine = [r for r in outside_services.rows() if r["id"] in ids]
    assert len(mine) == 200
    assert len(mine[0]["name"]) == 120 and len(mine[0]["for"]) == 300


# -- the log in plain words ---------------------------------------------------------

def test_an_apps_log_lines_are_named_and_counted_on_its_row(monkeypatch):
    _web_app_on(HOST)
    sid = outside_switches.switch_id_app(APP, "example-data.org")
    outside_services.register_rows("webapps", lambda: [_app_row(sid)])
    egress_ledger.record(f"app:{APP}:web", host=HOST, outcome="done",
                         what="app_traffic", status=200)
    egress_ledger.record(f"app:{APP}:web", host=HOST, outcome="refused",
                         code="switched_off", what="app_traffic")
    row = _by_id()[sid]
    assert row["today"] == {"sent": 1, "stopped": 1} and row["last_used"]
    lines = outside_services.log_rows(1)
    assert {l["service"] for l in lines} == {"Wedding Planner uses example-data.org"}
    assert lines[0]["what"] == "what an added app sends"


def test_lines_from_an_app_or_box_with_no_row_have_plain_names():
    egress_ledger.record("app:gone-app-abcdefgh:web", host=HOST, outcome="done",
                         what="app_traffic")
    egress_ledger.record("app_web", host=HOST, outcome="refused", code="not_on_list",
                         what="app_traffic")
    egress_ledger.record(outside_switches.switch_id_box("old"), host="pypi.org",
                         outcome="done", what="box_install")
    names = [l["service"] for l in outside_services.log_rows(1)]
    assert names.count(outside_services.APP_LOG_NAME) == 2
    assert outside_services.BOX_LOG_NAME in names


def test_the_new_words_are_plain():
    words = [outside_services.APP_ON_ELSEWHERE, outside_services.BOX_ON_ELSEWHERE,
             outside_services.NOT_SWITCHABLE_HERE, outside_services.APP_LOG_NAME,
             outside_services.BOX_LOG_NAME]
    for sid in R3_WORDS:
        row = _by_id()[sid]
        words += [row["name"], row["for"], row["what_leaves"], row["state_line"]]
    banned = re.compile(r"(?i)\b(doors?|beta|warts|sandbox|egress|repo|api|manifest)\b"
                        "|—")
    for text in words:
        assert not banned.search(text), text


def test_a_key_pasted_into_the_switch_address_goes_nowhere(capsys):
    """The address's switch id is read as an id (it is a hash), so a key
    typed there is not refused by the pasted-key check; it is refused as an
    unknown service, switches nothing and is never printed."""
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    pasted = "sk-" + "q8Zr2mXv9TcLp4Ws7Yb1Nf6Hd3Kj5Ga0"
    for sid in (pasted.lower().replace("-", "_"), "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"):
        got = client.post(f"/outside/{sid}/off", headers=AUTH)
        assert got.status_code == 404
        assert got.get_json()["message"] == "There is no outside service by that name."
    out = capsys.readouterr()
    assert pasted.lower() not in out.out + out.err


def test_every_l3_owner_string_is_plain():
    """Every line listed in tests/fixtures/ui_strings/outside_requests.txt, and
    every row's words are in it."""
    from pathlib import Path

    from puffpai import plain_words
    root = Path(__file__).resolve().parent
    lines = (root / "fixtures" / "ui_strings" / "outside_requests.txt").read_text(
        encoding="utf-8").splitlines()
    banned = re.compile(r"(?i)\bdoors?\b|\u2014|\bbeta\b|\bwart|sandbox|egress|\brepo |"
                        r"SPDX|manifest|MiniLM|embedding|seccomp|bwrap|WSL2|little app|"
                        r"parts\)|\b0 parts|recipe file|" + "Beth" + "any")
    api = re.compile(r"(?<![\w.])API(?![\w.])")
    for line in lines:
        assert plain_words.is_plain(line), plain_words.check(line)
        assert not banned.search(line) and not api.search(line), line
    for sid, name, purpose, leaves, on_line, fallback in outside_services._R3_ROWS:
        for text in (name, purpose, leaves, on_line, fallback):
            assert text in lines, text
    for text in (outside_services.APP_ON_ELSEWHERE, outside_services.BOX_ON_ELSEWHERE,
                 outside_services.NOT_SWITCHABLE_HERE, outside_services.APP_LOG_NAME,
                 outside_services.BOX_LOG_NAME,
                 *egress_doors._WEB_SET_WORDS.values()):  # noqa: SLF001
        assert text in lines, text
