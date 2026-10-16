"""The plan's yes covers only what the plan showed (invariant 7).

A project much bigger than the plan said is held, not added. (Until
recently the hold here was a licence that differs from what GitHub said;
an app she runs for herself is no longer held over its licence:
``integrate.run_licence``.) Only a yes from a paired device (``POST /integrations/<id>/decide``)
lets it continue, and only if the files still say exactly what she was
shown. Other parts are fakes with their pinned signatures.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import add_more, api_server, plain_words  # noqa: E402
from puffpai.integrate import apply, records  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
ITEM = {"id": "apps_code:github:someone/wedding-planner", "label": "Big Day",
        "apply": {"kind": "code_project", "ref": "github:someone/wedding-planner"},
        # Request extras an applier must never read (S7): wrong on purpose.
        "facts": {"license": "Apache-2.0", "mode": "static_web", "size_bytes": 1},
        "requires": ["x"]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)
    consent_tickets.reset()
    tree = tmp_path / "tree"

    def build(*, license_text=sup.MIT_TEXT, found_kw=None, webapps=None, mutate=None):
        sup.static_tree(str(tree), license_text=license_text)
        if mutate is not None:
            mutate(str(tree))
        f = sup.found(**(found_kw or {}))
        archives = {sup.archive_url(f): sup.tar_gz_of(str(tree))}
        fakes = sup.install_fakes(monkeypatch, str(tmp_path / "apps"),
                                  founds={ITEM["id"]: f}, archives=archives,
                                  webapps=webapps)
        return {"found": f, "tree": str(tree), "apps": str(tmp_path / "apps"), **fakes}
    yield build
    consent_tickets.reset()


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _held(world):
    w = world(found_kw={"size_kb": 1})  # the plan said 1 KB
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    return w, ctx, out


def test_a_project_bigger_than_the_plan_said_is_held_not_added(world):
    w, ctx, out = _held(world)
    assert out["status"] == "held"
    assert w["webapps"].calls == []                     # nothing was installed
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held"
    assert rec["held"]["reasons"] == ["size_over_plan"]
    sha = rec["drawbridge"]["rows_sha"]
    assert rec["held"]["ticket_target"] == f"held:{rec['id']}:{sha[:16]}"
    assert any(line.startswith("Drawbridge found something the plan did not show: it is "
                               "much bigger than the plan said") for line in ctx.lines)
    assert "It is waiting for your yes." in ctx.lines
    assert all(plain_words.check(line) == [] for line in ctx.lines)
    assert rec["from"]["license_read"] == "MIT"
    assert rec["plan_facts"]["license"] == "MIT"        # from the find, not the request


def test_a_licence_that_differs_from_github_is_only_a_note_for_her_own_use(world):
    w = world(license_text=sup.APACHE_TEXT)  # GitHub said MIT
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "added", ctx.lines
    assert any(line.startswith("  Drawbridge checked it: free to use (Apache 2.0)")
               for line in ctx.lines)


def test_a_yes_from_a_paired_device_continues(world, client):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.status_code == 200, got.get_json()
    body = got.get_json()
    assert body["status"] == "added"
    assert len(w["webapps"].calls) == 1
    call = w["webapps"].calls[0]
    # The app gets its own freshly issued id (its own never-reused origin).
    assert records.valid_id(call["app_id"]) and call["app_id"] != rid
    assert call["app_id"] in records._issued()  # noqa: SLF001
    assert os.path.realpath(call["files_dir"]).startswith(os.path.realpath(w["apps"]))
    rec = records.get(rid)
    assert rec["status"] == "ready" and rec["app_id"] == call["app_id"]
    assert rec["held"]["ticket_target"] == ""
    # The yes was a real single-use ticket, spent.
    assert consent_tickets._live == []  # noqa: SLF001


@pytest.mark.parametrize("headers", [
    AUTH,                                              # keyed, no device
    {**AUTH, "X-Puff-Anonymous": "1", "X-Puff-Device-Id": DEVICE},
])
def test_a_yes_from_no_device_is_refused(world, client, headers):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    got = client.post(f"/integrations/{rid}/decide", headers=headers, json={"yes": True})
    assert got.status_code == 403
    assert w["webapps"].calls == []
    assert records.get(rid)["status"] == "held"


def test_a_keyless_yes_is_refused(world, client):
    w, _ctx, out = _held(world)
    got = client.post(f"/integrations/{out['integration_id']}/decide",
                      headers={"X-Puff-Device-Id": DEVICE}, json={"yes": True})
    assert got.status_code == 401
    assert w["webapps"].calls == []


def test_a_lan_caller_cannot_name_a_device(world, client):
    w, _ctx, out = _held(world)
    got = client.post(f"/integrations/{out['integration_id']}/decide", headers=PAIRED,
                      json={"yes": True}, environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert got.status_code == 403
    assert w["webapps"].calls == []


def test_leave_it_out(world, client):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": False})
    assert got.status_code == 200 and got.get_json()["status"] == "refused"
    assert records.get(rid)["status"] == "refused"
    assert not os.path.exists(os.path.join(w["apps"], rid))
    again = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert again.status_code == 409
    assert w["webapps"].calls == []


def test_files_that_changed_after_the_question_stay_held(world, client):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    sup.write(os.path.join(w["apps"], rid, "src"), "scripts/start.sh", "ngrok http 80\n")
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.get_json()["status"] == "refused"          # now a block: never added
    assert w["webapps"].calls == []
    w2 = records.get(rid)
    assert w2["status"] == "refused"


def test_a_new_hold_after_the_question_asks_again(world, client):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    src = os.path.join(w["apps"], rid, "src")
    sup.write(src, "README.md", sup.FEATURE_README + "\nThis is AI-generated.\n")
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    body = got.get_json()
    assert body["status"] == "held"
    assert "changed since you were asked" in body["line"]
    assert w["webapps"].calls == []


def test_bad_bodies_and_ids(world, client):
    _w, _ctx, out = _held(world)
    rid = out["integration_id"]
    assert client.post(f"/integrations/{rid}/decide", headers=PAIRED,
                       json={"yes": "true"}).status_code == 400
    assert client.post("/integrations/..%2fstate/decide", headers=PAIRED,
                       json={"yes": True}).status_code in (404, 405)
    assert client.get("/integrations/NOT-AN-ID", headers=AUTH).status_code == 404


def test_the_list_shows_no_local_paths(world, client):
    _w, _ctx, out = _held(world)
    body = client.get("/integrations", headers=AUTH).get_json()
    assert isinstance(body, list)                 # the pinned bare list
    rec = next(r for r in body if r["id"] == out["integration_id"])
    assert "files_root" not in rec and "path" not in rec["lock"]
    assert client.get("/integrations", headers={}).status_code == 401


def test_allow_with_matching_facts_is_added_under_the_plan_yes(world):
    w = world()
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "added", ctx.lines
    assert len(w["webapps"].calls) == 1
    assert ctx.lines[0] == "Adding Big Day"
    assert ctx.lines[1].startswith("  Downloaded the version from June 2026")
    assert ctx.lines[2].startswith("  Drawbridge checked it: free to use (MIT)")
    assert "  Set it up to open inside your bot ... done" in ctx.lines
    assert any(line.startswith("  Taught your bot what it can do there: add a row")
               for line in ctx.lines)
    assert all(plain_words.check(line) == [] for line in ctx.lines)


def test_a_new_outside_host_after_install_is_held_and_taken_back_out(world):
    web = sup.fake_webapps_install({"hosts": [{"host": "api.example.org", "for": "sync",
                                               "state": "off"}]})
    world(webapps=web)
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "held"
    rec = records.get(out["integration_id"])
    assert rec["held"]["reasons"] == ["runtime_hosts_new"]
    assert web.uninstalled == [web.calls[0]["app_id"]]
    assert rec["app_id"] is None
    assert any("it wants to reach api.example.org, which the plan did not show" in line
               for line in ctx.lines)


def test_a_yes_to_a_new_host_adds_it_with_the_host_still_off(world, client):
    web = sup.fake_webapps_install({"hosts": [{"host": "api.example.org", "for": "sync",
                                               "state": "off"}]})
    world(webapps=web)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rid = out["integration_id"]
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.get_json()["status"] == "added"
    assert records.get(rid)["outside"] == [{"host": "api.example.org", "for": "sync",
                                            "state": "off"}]


def test_a_block_is_refused_and_nothing_is_kept(world):
    w = world(mutate=lambda tree: sup.write(tree, "scripts/start.sh", "ngrok http 80\n"))
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "refused"
    assert any(line.startswith("Drawbridge stopped this one: ") for line in ctx.lines)
    assert not os.path.exists(os.path.join(w["apps"], out["integration_id"]))
    assert w["webapps"].calls == []
    assert records.get(out["integration_id"])["status"] == "refused"


def test_offline_fetches_nothing(world):
    w = world()
    out = apply.apply_code_project(ITEM, sup.Ctx(online=False))
    assert out["status"] == "failed" and "build is offline" in out["line"]
    assert w["fetch_calls"] == []


def test_without_the_finder_nothing_is_added(world, monkeypatch):
    w = world()
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", None)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "failed" and "not in this build yet" in out["line"]
    assert w["fetch_calls"] == []


def test_without_the_app_host_a_clean_app_is_held(world, monkeypatch):
    world()
    monkeypatch.setitem(sys.modules, "puffpai.webapps.install", None)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "held" and "not in this build yet" in out["line"]


def test_without_held_tickets_a_held_item_cannot_be_said_yes_to(world, client, monkeypatch):
    _w, _ctx, out = _held(world)
    monkeypatch.delattr(consent_tickets, "target_held", raising=False)
    got = client.post(f"/integrations/{out['integration_id']}/decide", headers=PAIRED,
                      json={"yes": True})
    assert got.status_code == 409


def test_the_add_page_uses_the_same_applier(world, monkeypatch):
    world(found_kw={"size_kb": 1})
    monkeypatch.setattr(add_more, "_APPLIERS", {})
    sup.fresh_drawbridge(monkeypatch, register_code_project=False)
    done = apply.connect()
    assert "drawbridge:code_project" in done and "add_more:code_project" in done
    ctx = add_more.ApplyContext(via="add_page", by="owner", surface="browser", words="x")
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    out = add_more._APPLIERS["code_project"](ITEM, ctx)  # noqa: SLF001
    assert out["status"] == "held"
    assert records.get(out["integration_id"])["by"] == "add_page"


def test_a_tampered_folder_in_the_record_is_not_followed(world, client, tmp_path):
    w, _ctx, out = _held(world)
    rid = out["integration_id"]
    elsewhere = tmp_path / "elsewhere"
    sup.static_tree(str(elsewhere / "src"))                 # a clean MIT tree
    rec = records.get(rid)
    rec["files_root"] = str(elsewhere)
    records.save(rec)
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.get_json()["status"] == "added"
    # What was checked and installed is the unpacked download, not the path
    # written in the record.
    call = w["webapps"].calls[0]
    assert os.path.realpath(call["files_dir"]).startswith(
        os.path.realpath(os.path.join(w["apps"], rid)))
    assert records.get(rid)["from"]["license_read"] == "MIT"


# -- a project held twice: once for its files, once for its app ------------------------

def _two_stage(world, hosts_per_call=None):
    """Much bigger than the plan said (a hold on the files) AND the app wants
    a host the plan did not show (a hold on the app)."""
    web = sup.fake_webapps_install({"hosts": [{"host": "api.example.org", "for": "sync",
                                               "state": "off"}]})
    if hosts_per_call is not None:
        inner = web.install_static

        def install_static(app_id, files_dir, **kw):
            out = inner(app_id, files_dir, **kw)
            n = min(len(web.calls), len(hosts_per_call)) - 1
            out["hosts"] = [{"host": h, "for": "sync", "state": "off"}
                            for h in hosts_per_call[n]]
            return out
        web.install_static = install_static
    w = world(found_kw={"size_kb": 1}, webapps=web)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "held"
    assert records.get(out["integration_id"])["held"]["reasons"] == ["size_over_plan"]
    return w, web, out["integration_id"]


def _yes(client, rid):
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": True})
    assert got.status_code == 200, got.get_json()
    return got.get_json()


def test_a_project_held_twice_is_added_after_two_yeses(world, client):
    _w, web, rid = _two_stage(world)
    first = _yes(client, rid)
    assert first["status"] == "held"
    assert records.get(rid)["held"]["reasons"] == ["runtime_hosts_new"]
    # The next question is not a change: nothing changed since she was asked.
    assert "changed since you were asked" not in first["line"]
    assert "it wants to reach api.example.org, which the plan did not show" in first["line"]
    second = _yes(client, rid)
    assert second["status"] == "added", second
    rec = records.get(rid)
    assert rec["status"] == "ready"
    assert rec["outside"] == [{"host": "api.example.org", "for": "sync", "state": "off"}]
    # Two install attempts, two different never-reused ids; the held one was
    # taken back out and the added one is the one recorded.
    ids = [c["app_id"] for c in web.calls]
    assert len(ids) == 2 and len(set(ids)) == 2 and rid not in ids
    assert web.uninstalled == [ids[0]]
    assert rec["app_id"] == ids[1]
    assert consent_tickets._live == []  # noqa: SLF001 - both yeses were spent


def test_a_yes_to_the_app_does_not_cover_files_that_changed(world, client):
    w, web, rid = _two_stage(world)
    _yes(client, rid)                                     # files: yes -> app held
    src = os.path.join(w["apps"], rid, "src")
    sup.write(src, "README.md", sup.FEATURE_README + "\nThis is AI-generated.\n")
    body = _yes(client, rid)                              # app: yes, but files changed
    assert body["status"] == "held"
    assert "changed since you were asked" in body["line"]
    assert records.get(rid)["status"] == "held"
    assert len(web.calls) == 1                            # never installed again


def test_a_new_host_at_the_second_install_asks_again(world, client):
    _w, web, rid = _two_stage(world, hosts_per_call=[["api.example.org"],
                                                     ["api.example.org", "evil.example.net"]])
    _yes(client, rid)                                     # files: yes -> app held
    body = _yes(client, rid)                              # app: yes, but a new host now
    assert body["status"] == "held"
    assert "changed since you were asked" in body["line"]
    assert records.get(rid)["status"] == "held"
    assert web.uninstalled == [c["app_id"] for c in web.calls]   # both taken back out
    third = _yes(client, rid)                             # yes to the new finding
    assert third["status"] == "added"
    assert {o["host"] for o in records.get(rid)["outside"]} == {"api.example.org",
                                                                  "evil.example.net"}


def test_a_leave_it_out_at_the_second_question(world, client):
    _w, web, rid = _two_stage(world)
    _yes(client, rid)
    got = client.post(f"/integrations/{rid}/decide", headers=PAIRED, json={"yes": False})
    assert got.get_json()["status"] == "refused"
    assert records.get(rid)["status"] == "refused"
    assert web.uninstalled == [web.calls[0]["app_id"]] and len(web.calls) == 1


def test_an_app_host_that_changes_the_id_is_not_trusted(world):
    web = sup.fake_webapps_install({"app_id": "someone-else-abcdefgh"})
    world(webapps=web)
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "failed"
    assert set(web.uninstalled) == {web.calls[0]["app_id"], "someone-else-abcdefgh"}
    assert records.get(out["integration_id"])["app_id"] is None
