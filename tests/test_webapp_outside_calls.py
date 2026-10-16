"""An app's outside calls: the forwarding address and the owner's yes.

The Bridge functions for browser apps (``register_web_app_door``,
``app_web_hosts``, ``set_app_web_hosts``, ``app_web_request``) are replaced by
a fake that follows the pinned signatures and the Bridge's rules (the whole list of
sites that are on; switching one on spends an ``app_host_on`` ticket). No
network. Checks: the pinned question words; https/443/no sign-in part; only
three headers go out; the answer carries no cookies, page policy or redirect
address; a site that is off raises the owner's question at most once a day
and never after her No; nothing can be switched on without a paired device
and her ticket; without the Bridge part everything is refused.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import quote

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit as hostkit  # noqa: E402

from puffpai.webapps import forward, headers, host, install, wording  # noqa: E402

APP = "shelf-keeper-abcdefgh"
DEVICE = "device:owner-phone-0001"


class FakeBridge:
    """The pinned Bridge functions, with their consent rule."""

    def __init__(self):
        self.doors = {}
        self.on = {}
        self.requests = []
        self.answer = {"status": 200, "headers": {"content-type": "application/json"}, "body": b'{"ok":1}'}

    def register_web_app_door(self, app_id, hosts):
        assert len(hosts) <= 2
        self.doors[app_id] = list(hosts)
        self.on[app_id] = set()
        return f"app:{app_id}:web"

    def app_web_hosts(self, app_id):
        return {h: h in self.on.get(app_id, set()) for h in self.doors.get(app_id, [])}

    def set_app_web_hosts(self, app_id, hosts_on, *, by, ticket=None):
        from puffpai.security import consent_tickets
        if app_id not in self.doors or any(h not in self.doors[app_id] for h in hosts_on):
            return {"ok": False, "refused": "not_on_list"}
        new = sorted(set(hosts_on) - self.on[app_id])
        if len(new) > 1:
            return {"ok": False, "refused": "one_at_a_time"}
        if new:
            ok, verdict = consent_tickets.redeem(ticket, by, "app_host_on",
                                                 consent_tickets.target_app_host(app_id, new[0]))
            if not ok:
                return {"ok": False, "refused": verdict}
        self.on[app_id] = set(hosts_on)
        return {"ok": True, "hosts": self.app_web_hosts(app_id)}

    def app_web_request(self, app_id, method, url, *, body=None, headers=None, key_handle=None,
                        max_out=1_000_000, max_in=5_000_000):
        self.requests.append({"app": app_id, "method": method, "url": url, "body": body,
                              "headers": dict(headers or {}), "max_out": max_out, "max_in": max_in})
        hostname = url.split("/")[2]
        if hostname not in self.on.get(app_id, set()):
            return {"refused": "switched_off"}
        return dict(self.answer)


@pytest.fixture
def bridge(monkeypatch):
    from puffpai.security import consent_tickets, egress_doors
    fake = FakeBridge()
    for name in ("register_web_app_door", "app_web_hosts", "set_app_web_hosts", "app_web_request"):
        monkeypatch.setattr(egress_doors, name, getattr(fake, name), raising=False)
    consent_tickets.reset()
    return fake


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(hostkit.PORT))
    monkeypatch.setenv("PUFFPAI_DASHBOARD_PORT", "3003")
    hostkit.place_app(APP)
    rec_path = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "web.json")
    with open(rec_path, encoding="utf-8") as fh:
        rec = json.load(fh)
    rec["scan"] = {"hosts": [{"host": "api.example-data.org", "for": "api", "needed_while_running": True},
                             {"host": "login.example-auth.org", "for": "auth", "needed_while_running": True}]}
    with open(rec_path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    host.refresh()
    host.start(None)
    yield APP
    host.stop()


_BRIDGE_APP_PART = ("register_web_app_door", "app_web_hosts", "set_app_web_hosts",
                    "app_web_request")


def _no_bridge_app_part(monkeypatch):
    """A build without the Bridge part for browser apps. A full build
    has the real functions, so they are taken out here."""
    from puffpai.security import egress_doors
    for name in _BRIDGE_APP_PART:
        monkeypatch.delattr(egress_doors, name, raising=False)


def _out(url, *, method="GET", body=None, extra=None):
    _, _, _, nonce = hostkit.frame_get(APP)
    h = {"Sec-Fetch-Site": "same-origin", "Origin": f"http://{APP}.localhost:{hostkit.PORT}",
         "X-PuffPAI-Nonce": nonce, "Content-Type": "application/json", "Accept": "application/json",
         "Cookie": "puff_device=SECRET", "Authorization": "Bearer x", "X-Other": "y"}
    h.update(extra or {})
    return hostkit.request(method, "/__puffpai/out?u=" + quote(url, safe=""),
                           host_header=f"{APP}.localhost:{hostkit.PORT}", headers=h, body=body)


def test_without_the_bridges_app_part_everything_is_refused(served, monkeypatch):
    _no_bridge_app_part(monkeypatch)
    status, _, body, _ = _out("https://api.example-data.org/v1/items")
    assert status == 403 and json.loads(body)["refused"] == "switched_off"
    assert forward.hosts(APP) == []
    assert forward.set_host(APP, "api.example-data.org", True, by=DEVICE)["refused"] == "not_in_this_build"
    assert forward.note_wanted(APP, "api.example-data.org") is False


def test_a_site_that_is_off_is_refused_and_asked_about_once_a_day(served, bridge):
    bridge.register_web_app_door(APP, ["api.example-data.org", "login.example-auth.org"])
    status, hdrs, body, raw = _out("https://api.example-data.org/v1/items")
    assert status == 403 and json.loads(body)["refused"] == "switched_off"
    assert json.loads(body)["message"] == wording.OUTSIDE_NOT_ON
    assert all(k.lower() != "access-control-allow-origin" for k, _ in raw)
    rows = {h["host"]: h for h in forward.hosts(APP)}
    assert rows["api.example-data.org"]["state"] == "asked"
    assert rows["login.example-auth.org"]["state"] == "off"
    # the same day: no second question
    assert forward.note_wanted(APP, "api.example-data.org") is False
    # a site not on the app's door is never asked about (it could never be switched on)
    assert forward.note_wanted(APP, "tracker.example-ads.org") is False
    # her No is remembered: never asked again, even a day later
    forward.remember_no(APP, "login.example-auth.org")
    asks_path = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "asks.json")
    with open(asks_path, encoding="utf-8") as fh:
        asks = json.load(fh)
    asks["api.example-data.org"]["asked_at"] = time.time() - 2 * forward.ASK_EVERY_S
    asks["login.example-auth.org"]["asked_at"] = time.time() - 2 * forward.ASK_EVERY_S
    with open(asks_path, "w", encoding="utf-8") as fh:
        json.dump(asks, fh)
    assert forward.note_wanted(APP, "api.example-data.org") is True
    assert forward.note_wanted(APP, "login.example-auth.org") is False


def test_the_question_uses_the_pinned_words_from_toolsmith_not_the_app(served, bridge, monkeypatch):
    bridge.register_web_app_door(APP, ["api.example-data.org", "login.example-auth.org"])
    assert forward.question_lines(APP, "api.example-data.org") == [
        "Shelf Keeper wants to reach example-data.org.",
        "Toolsmith says it would use it for: fetching or saving data online."]
    assert forward.question_lines(APP, "login.example-auth.org")[1] == \
        "Toolsmith says it would use it for: signing in."
    from puffpai.webapps import files
    monkeypatch.setattr(files, "granted_name", lambda app_id: "Pictures")
    assert forward.question_lines(APP, "api.example-data.org")[2] == (
        "This app can also read Pictures. With an outside service on, it could send those files there.")


def test_a_switched_on_site_is_reached_with_three_headers_and_a_clean_answer(served, bridge):
    bridge.register_web_app_door(APP, ["api.example-data.org"])
    bridge.on[APP] = {"api.example-data.org"}
    bridge.answer = {"status": 201, "body": b'{"id":7}', "headers": {
        "content-type": "application/json", "set-cookie": "a=b", "content-security-policy": "x",
        "clear-site-data": '"*"', "location": "https://evil.example.org/", "etag": '"v1"',
        "access-control-allow-origin": "*", "service-worker-allowed": "/"}}
    status, hdrs, body, raw = _out("https://api.example-data.org/v1/items", method="POST",
                                   body=b'{"name":"Tea"}')
    assert status == 201 and body == b'{"id":7}'
    sent = bridge.requests[-1]
    assert sent["method"] == "POST" and sent["body"] == b'{"name":"Tea"}' and sent["app"] == APP
    assert set(sent["headers"]) <= {"content-type", "accept", "accept-language"}
    assert "cookie" not in {k.lower() for k in sent["headers"]}
    names = {k.lower() for k, _ in raw}
    for gone in ("set-cookie", "clear-site-data", "location", "access-control-allow-origin",
                 "service-worker-allowed"):
        assert gone not in names
    assert hdrs["content-security-policy"] == headers.csp(3003)      # the host's own, not theirs
    assert hdrs["etag"] == '"v1"' and hdrs["cache-control"] == "no-store"


@pytest.mark.parametrize("url,code", [
    ("http://api.example-data.org/x", "not_https"),
    ("https://user:" + "pw" + "@" + "api.example-data.org/x", "userinfo"),
    ("https://api.example-data.org:8443/x", "not_443"),
    ("https://127.0.0.1/x", "not_allowed"),
    ("https://localhost/x", "not_allowed"),
    ("ftp://api.example-data.org/x", "not_https"),
])
def test_only_https_on_443_with_no_sign_in_part(served, bridge, url, code):
    bridge.register_web_app_door(APP, ["api.example-data.org"])
    bridge.on[APP] = {"api.example-data.org"}
    status, _, body, _ = _out(url)
    assert status == 403 and json.loads(body)["refused"] == code
    assert bridge.requests == []


def test_a_csp_report_from_the_page_raises_the_question(served, bridge):
    bridge.register_web_app_door(APP, ["api.example-data.org"])
    _, _, _, nonce = hostkit.frame_get(APP)
    status, _, _, _ = hostkit.request(
        "POST", "/__puffpai/report", host_header=f"{APP}.localhost:{hostkit.PORT}",
        headers={"Sec-Fetch-Site": "same-origin", "Origin": f"http://{APP}.localhost:{hostkit.PORT}",
                 "X-PuffPAI-Nonce": nonce, "Content-Type": "application/json"},
        body=json.dumps({"csp-report": {"blocked-uri": "https://api.example-data.org/v1",
                                        "effective-directive": "connect-src"}}).encode())
    assert status == 204
    assert forward.hosts(APP)[0]["state"] == "asked"


def _client(monkeypatch, device=DEVICE):
    from flask import Flask
    from puffpai.webapps import api
    monkeypatch.setattr(api, "_device", lambda: device)
    app = Flask("t")
    app.register_blueprint(api.bp)
    return app.test_client()


def test_switching_on_needs_a_paired_device_and_her_yes_for_exactly_that_site(served, bridge, monkeypatch):
    bridge.register_web_app_door(APP, ["api.example-data.org", "login.example-auth.org"])
    c = _client(monkeypatch, device="")
    r = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True})
    assert r.status_code == 403 and r.get_json()["refused"] == "not_paired"
    c = _client(monkeypatch)
    first = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True}).get_json()
    assert first["needs_yes"] and first["buttons"] == ["Switch on", "No"]
    assert first["words"][0] == "Shelf Keeper wants to reach example-data.org."
    assert bridge.on[APP] == set()                                   # nothing on yet
    # a ticket for one site does not switch on another
    wrong = c.post(f"/webapps/{APP}/hosts", json={"host": "login.example-auth.org", "on": True,
                                                  "ticket": first["ticket"]}).get_json()
    assert wrong["ok"] is False and bridge.on[APP] == set()
    first = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True}).get_json()
    # another device cannot spend her ticket
    other = _client(monkeypatch, device="device:someone-else-0002")
    assert other.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True,
                                                     "ticket": first["ticket"]}).get_json()["ok"] is False
    first = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True}).get_json()
    done = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True,
                                                 "ticket": first["ticket"]}).get_json()
    assert done["ok"] is True and bridge.on[APP] == {"api.example-data.org"}
    assert {h["host"]: h["state"] for h in c.get(f"/webapps/{APP}/hosts").get_json()} == {
        "api.example-data.org": "on", "login.example-auth.org": "off"}
    # switching off needs no ticket and is remembered as her No
    off = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": False}).get_json()
    assert off["ok"] is True and bridge.on[APP] == set()
    assert forward.note_wanted(APP, "api.example-data.org") is False
    # a site that is not the app's is refused before any ticket exists
    r = c.post(f"/webapps/{APP}/hosts", json={"host": "tracker.example-ads.org", "on": True})
    assert r.status_code == 404 and r.get_json()["refused"] == "not_on_list"


def test_without_the_bridge_part_no_ticket_is_ever_minted(served, monkeypatch):
    from puffpai.security import consent_tickets
    _no_bridge_app_part(monkeypatch)
    minted = []
    monkeypatch.setattr(consent_tickets, "mint", lambda *a, **k: minted.append(a) or "t")
    c = _client(monkeypatch)
    r = c.post(f"/webapps/{APP}/hosts", json={"host": "api.example-data.org", "on": True})
    assert r.status_code == 409 and r.get_json()["refused"] == "not_in_this_build" and minted == []
    assert c.get(f"/webapps/{APP}/hosts").get_json() == []


def test_install_gives_the_app_at_most_two_sites_on_the_bridge_all_off(served, bridge):
    sc = {"hosts": [{"host": "tiles.example-maps.org", "for": "maps", "needed_while_running": True},
                    {"host": "api.example-data.org", "for": "api", "needed_while_running": True},
                    {"host": "x.example-other.org", "for": "", "needed_while_running": True},
                    {"host": "fonts.example-type.org", "for": "fonts", "needed_while_running": False}]}
    assert install._register_door(APP, sc) == ["api.example-data.org", "tiles.example-maps.org"]
    assert bridge.app_web_hosts(APP) == {"api.example-data.org": False, "tiles.example-maps.org": False}
    bridge.on[APP] = {"api.example-data.org"}
    install._close_door(APP)
    assert bridge.on[APP] == set()
