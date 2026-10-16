"""egress_doors.app_web_request: a browser app's outside sites, on the app door.

No network: the one network call (``egress_doors._web_wire``) and the name
lookup (``egress_doors._WEB_RESOLVER``) are replaced. Proven here: a site is
refused (``switched_off``) until the owner's owner-only yes; https, 443, no
sign-in in the address and public addresses only; the request and answer
header lists both ways; a sign-up key by handle, bound to exactly that site,
never logged and never on a redirect; redirects only to switched-on sites;
the two-site bound; a new site set means a new yes; one outside-traffic line
per request, host only.
"""

from __future__ import annotations

import json
import logging
import secrets
import string

import pytest

from puffpai.security import consent_tickets, egress_doors, egress_ledger, keys

APP = "wedding-planner-abcdefgh"
OTHER_APP = "chess-board-zyxwvuts"
HOST = "api.example-data.org"
HOST2 = "tiles.example-maps.org"
DEVICE = "device:owner-phone-0001"
PUBLIC = ["140.82.112.5"]
AT = "@"  # kept apart so no line reads like an email address


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = dict(headers or {})
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out

    def close(self):
        self.closed = True


class Wire:
    """Stands in for egress_doors._web_wire: answers from a table."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout, pin):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": body, "pinned": dict(pin._pinned)})  # noqa: SLF001
        got = self.answers.get(url)
        if callable(got):
            return got()
        return got if got is not None else Resp(200, b"ok")


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    egress_doors.clear_web_app_doors()
    consent_tickets.reset()
    consent_tickets.set_owner_devices_provider(None)
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    monkeypatch.setattr(egress_doors, "run_posture", lambda: {
        "run_egress": egress_doors.OPEN_POSTURE, "source": "test", "switches": {}})
    monkeypatch.setattr(egress_doors, "_WEB_RESOLVER", lambda h, p: list(PUBLIC))
    yield
    egress_doors.clear_web_app_doors()
    consent_tickets.set_owner_devices_provider(None)
    keys.set_vault_provider(None)


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(egress_doors, "_web_wire", w)
    return w


def _lines():
    return list(reversed(egress_ledger.read(1, limit=200)))


def _log_text():
    try:
        return open(egress_ledger.log_path(), encoding="utf-8").read()
    except FileNotFoundError:
        return ""


def _yes(app, host, device=DEVICE):
    ticket = consent_tickets.mint("app_host_on",
                                  consent_tickets.target_app_host(app, host),
                                  "Let the app use that site.", device)
    return ticket


def _switch_on(app, *hosts):
    on: list[str] = []
    for host in hosts:
        on.append(host)
        got = egress_doors.set_app_web_hosts(app, list(on), by=DEVICE,
                                             ticket=_yes(app, host))
        assert got["ok"], got
    return got


def _ready(*hosts):
    hosts = hosts or (HOST,)
    assert egress_doors.register_web_app_door(APP, list(hosts)) == f"app:{APP}:web"
    _switch_on(APP, *hosts)


# -- switched off until the owner's yes ---------------------------------------

def test_a_site_is_switched_off_before_the_yes(wire):
    egress_doors.register_web_app_door(APP, [HOST])
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/v1/list")
    assert got == {"refused": "switched_off"}
    assert wire.calls == []
    assert egress_doors.app_web_hosts(APP) == {HOST: False}
    (line,) = _lines()
    assert (line["service"], line["what"], line["outcome"], line["code"]) == (
        f"app:{APP}:web", "app_traffic", "refused", "switched_off")
    assert line["host"] == HOST and line["path"] == ""


def test_after_the_yes_it_goes_out_and_is_logged_host_only(wire):
    _ready()
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/v1/guests?secret=x")
    assert got["status"] == 200 and got["body"] == b"ok"
    assert [c["url"] for c in wire.calls] == [f"https://{HOST}/v1/guests?secret=x"]
    assert wire.calls[0]["pinned"] == {HOST: PUBLIC[0]}
    (line,) = _lines()
    assert (line["outcome"], line["what"], line["host"], line["path"]) == (
        "done", "app_traffic", HOST, "")
    assert "secret" not in _log_text() and "guests" not in _log_text()


def test_a_yes_for_one_app_does_not_open_another(wire):
    _ready()
    egress_doors.register_web_app_door(OTHER_APP, [HOST])
    got = egress_doors.app_web_request(OTHER_APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "switched_off"} and wire.calls == []


def test_an_unknown_app_is_refused(wire):
    got = egress_doors.app_web_request("not-here-abcdefgh", "GET", f"https://{HOST}/")
    assert got == {"refused": "unknown_app"} and wire.calls == []
    (line,) = _lines()
    assert line["service"] == "app_web" and line["code"] == "not_on_list"


def test_web_doors_are_not_reachable_through_the_general_door_path(wire):
    _ready()
    assert egress_doors.door(f"app:{APP}:web") is None
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_request(f"app:{APP}:web", "GET", f"https://{HOST}/")
    assert wire.calls == []


# -- the address rules ---------------------------------------------------------

@pytest.mark.parametrize("url,code", [
    (f"http://{HOST}/x", "not_https"),
    (f"https://{HOST}:8443/x", "not_443"),
    (f"https://{HOST}:80/x", "not_443"),
    (f"https://user:pw{AT}{HOST}/x", "userinfo"),
    (f"https://{AT}{HOST}/x", "userinfo"),
    (f"https://{HOST}/a b", "not_allowed"),
    (f"https://{HOST}/a\r\nX-Evil: 1", "not_allowed"),
    ("", "not_allowed"),
    ("https://[not-closed/x", "not_allowed"),
])
def test_the_address_rules_refuse_before_sending(wire, url, code):
    _ready()
    got = egress_doors.app_web_request(APP, "GET", url)
    assert got == {"refused": code} and wire.calls == []


def test_port_443_written_out_is_fine(wire):
    _ready()
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}:443/x")
    assert got["status"] == 200


@pytest.mark.parametrize("answers", [["10.0.0.7"], ["169.254.169.254"],
                                     ["140.82.112.5", "192.168.1.2"],
                                     ["127.0.0.1", "10.0.0.7"]])
def test_an_inside_address_is_refused(wire, monkeypatch, answers):
    _ready()
    monkeypatch.setattr(egress_doors, "_WEB_RESOLVER", lambda h, p: list(answers))
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "inside_address"} and wire.calls == []


@pytest.mark.parametrize("answers", [["127.0.0.1"], ["::1"], ["::ffff:127.0.0.1"],
                                     ["0.0.0.0"]])
def test_a_name_answered_with_this_computer_is_a_filter_block(wire, monkeypatch, answers):
    """A public name answered with 0.0.0.0 or this
    computer's own address is what a home DNS filter gives for a blocked name;
    refused all the same, and named as the filter's block."""
    _ready()
    monkeypatch.setattr(egress_doors, "_WEB_RESOLVER", lambda h, p: list(answers))
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "network_filter"} and wire.calls == []


def test_no_dns_answer_is_cannot_look_up(wire, monkeypatch):
    _ready()
    monkeypatch.setattr(egress_doors, "_WEB_RESOLVER", lambda h, p: [])
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "cannot_look_up"} and wire.calls == []


def test_a_site_the_app_never_asked_for_is_switched_off(wire):
    _ready()
    got = egress_doors.app_web_request(APP, "GET", "https://other.example.org/x")
    assert got == {"refused": "switched_off"} and wire.calls == []


def test_the_bot_set_to_stay_off_the_internet_refuses(wire, monkeypatch):
    _ready()
    monkeypatch.setattr(egress_doors, "run_posture", lambda: {
        "run_egress": "island", "source": "test", "switches": {}})
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "bot_offline"} and wire.calls == []


def test_the_operator_off_switch_wins(wire, monkeypatch):
    _ready()
    monkeypatch.setenv(egress_doors.APPS_KILL_ENV, "off")
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x")
    assert got == {"refused": "switched_off"} and wire.calls == []
    assert egress_doors.app_web_hosts(APP) == {HOST: False}
    assert _lines()[-1]["code"] == "turned_off_here"
    monkeypatch.setenv(egress_doors.APPS_KILL_ENV, "on")  # "on" opens nothing new
    assert egress_doors.app_web_hosts(APP) == {HOST: True}


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "OPTIONS", "", None, "G ET"])
def test_other_methods_are_refused(wire, method):
    _ready()
    got = egress_doors.app_web_request(APP, method, f"https://{HOST}/x")
    assert got == {"refused": "not_allowed"} and wire.calls == []


def test_a_body_on_a_get_is_refused_but_an_empty_one_is_ignored(wire):
    _ready()
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x",
                                        body=b"x") == {"refused": "not_allowed"}
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x",
                                        body="text") == {"refused": "not_allowed"}
    assert wire.calls == []
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x", body=b"")
    assert got["status"] == 200 and wire.calls[0]["body"] is None


# -- sizes ------------------------------------------------------------------------

def test_a_body_over_max_out_is_refused(wire):
    _ready()
    got = egress_doors.app_web_request(APP, "POST", f"https://{HOST}/x",
                                       body=b"x" * 11, max_out=10)
    assert got == {"refused": "too_big"} and wire.calls == []


def test_an_answer_over_max_in_is_refused(wire):
    _ready()
    url = f"https://{HOST}/x"
    wire.answers[url] = Resp(200, b"y" * 11)
    assert egress_doors.app_web_request(APP, "GET", url, max_in=10) == {"refused": "too_big"}
    wire.answers[url] = Resp(200, b"y", {"Content-Length": "999"})
    assert egress_doors.app_web_request(APP, "GET", url, max_in=10) == {"refused": "too_big"}
    assert [l["code"] for l in _lines()] == ["too_big", "too_big"]


@pytest.mark.parametrize("kw", [{"max_out": -1}, {"max_in": True}, {"max_in": "5"}])
def test_bad_size_limits_are_refused(wire, kw):
    _ready()
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x", **kw) \
        == {"refused": "not_allowed"}


# -- header lists, both ways -----------------------------------------------------------

def test_only_three_request_headers_go_out_and_only_names_are_logged(wire, caplog):
    _ready()
    caplog.set_level(logging.INFO, logger="puffpai.security.egress_doors")
    got = egress_doors.app_web_request(APP, "POST", f"https://{HOST}/x", body=b"{}", headers={
        "Content-Type": "application/json", "accept": "application/json",
        "Accept-Language": "en", "Authorization": "Bearer app-secret-value-1",
        "Cookie": "sid=cookie-secret-value-2", "X-Api-Key": "apikey-secret-3",
        "Origin": "http://x.localhost", "Referer": "http://x.localhost/p",
        "Accept-Encoding": "gzip", "Host": "evil.example", "User-Agent": "App"})
    assert got["status"] == 200
    sent = wire.calls[0]["headers"]
    assert sorted(sent) == ["Accept", "Accept-Encoding", "Accept-Language",
                            "Content-Type", "User-Agent"]
    assert sent["Accept-Encoding"] == "identity" and sent["User-Agent"] != "App"
    assert sent["Content-Type"] == "application/json"
    text = caplog.text + _log_text()
    for secret in ("app-secret-value-1", "cookie-secret-value-2", "apikey-secret-3",
                   "evil.example"):
        assert secret not in text
    assert "Authorization" in caplog.text


def test_a_header_value_with_a_line_break_is_dropped(wire):
    _ready()
    egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x",
                                 headers={"Accept": "a\r\nX-Evil: 1"})
    assert "Accept" not in wire.calls[0]["headers"]


def test_only_five_answer_headers_come_back(wire):
    _ready()
    url = f"https://{HOST}/x"
    wire.answers[url] = Resp(200, b"{}", {
        "Content-Type": "application/json", "Content-Length": "2",
        "Cache-Control": "no-store", "ETag": '"e1"', "Last-Modified": "Tue",
        "Set-Cookie": "a=b", "Content-Security-Policy": "default-src *",
        "Clear-Site-Data": '"*"', "Service-Worker-Allowed": "/",
        "Location": "https://evil.example/", "Access-Control-Allow-Origin": "*",
        "Server": "x"})
    got = egress_doors.app_web_request(APP, "GET", url)
    assert got["headers"] == {"content-type": "application/json", "content-length": "2",
                              "cache-control": "no-store", "etag": '"e1"',
                              "last-modified": "Tue"}


# -- redirects ----------------------------------------------------------------------------

def test_a_redirect_to_a_site_that_is_not_on_is_refused(wire):
    _ready(HOST, HOST2)
    egress_doors.set_app_web_hosts(APP, [HOST], by="owner")  # HOST2 off again
    a = f"https://{HOST}/a"
    wire.answers[a] = Resp(302, b"", {"location": f"https://{HOST2}/b"})
    got = egress_doors.app_web_request(APP, "GET", a)
    assert got == {"refused": "redirect_off_list"}
    assert [c["url"] for c in wire.calls] == [a]
    assert [l["code"] for l in _lines()] == ["", "redirected_away"]


def test_a_redirect_to_a_site_off_the_app_list_is_refused(wire):
    _ready()
    a = f"https://{HOST}/a"
    wire.answers[a] = Resp(301, b"", {"Location": "https://evil.example.org/b"})
    assert egress_doors.app_web_request(APP, "GET", a) == {"refused": "redirect_off_list"}
    assert len(wire.calls) == 1


def test_a_redirect_down_to_plain_http_is_refused(wire):
    _ready()
    a = f"https://{HOST}/a"
    wire.answers[a] = Resp(301, b"", {"Location": f"http://{HOST}/b"})
    assert egress_doors.app_web_request(APP, "GET", a) == {"refused": "not_https"}
    assert len(wire.calls) == 1


def test_two_redirects_to_switched_on_sites_are_followed_a_third_is_not(wire):
    _ready(HOST, HOST2)
    a, b, c, d = (f"https://{HOST}/a", f"https://{HOST2}/b", f"https://{HOST}/c",
                  f"https://{HOST2}/d")
    wire.answers.update({a: Resp(302, b"", {"Location": b}),
                         b: Resp(307, b"", {"Location": c}),
                         c: lambda: Resp(200, b"done")})
    got = egress_doors.app_web_request(APP, "GET", a)
    assert got["body"] == b"done"
    assert [x["url"] for x in wire.calls] == [a, b, c]
    wire.calls.clear()
    # A relative address is read against the site that sent it.
    wire.answers.update({a: Resp(302, b"", {"Location": "/c"})})
    got = egress_doors.app_web_request(APP, "GET", a)
    assert got["body"] == b"done" and wire.calls[-1]["url"] == c
    wire.calls.clear()
    wire.answers.update({a: Resp(302, b"", {"Location": b}),
                         b: Resp(302, b"", {"Location": c}),
                         c: Resp(302, b"", {"Location": d})})
    got = egress_doors.app_web_request(APP, "GET", a)
    assert got == {"refused": "redirects"}
    assert [x["url"] for x in wire.calls] == [a, b, c]


def test_a_303_turns_a_post_into_a_get_without_its_body(wire):
    _ready()
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    wire.answers.update({a: Resp(303, b"", {"Location": b}), b: Resp(200, b"ok")})
    egress_doors.app_web_request(APP, "POST", a, body=b"data",
                                 headers={"Content-Type": "text/plain"})
    first, second = wire.calls
    assert (first["method"], first["body"]) == ("POST", b"data")
    assert (second["method"], second["body"]) == ("GET", None)
    assert "Content-Type" not in second["headers"]


def test_a_307_keeps_the_method_and_body(wire):
    _ready()
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    wire.answers.update({a: Resp(307, b"", {"Location": b}), b: Resp(200, b"ok")})
    egress_doors.app_web_request(APP, "PUT", a, body=b"data")
    assert [(c["method"], c["body"]) for c in wire.calls] == [("PUT", b"data"),
                                                              ("PUT", b"data")]


def test_each_hop_is_one_log_line(wire):
    _ready()
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    wire.answers.update({a: Resp(301, b"", {"Location": b}), b: Resp(200, b"ok")})
    egress_doors.app_web_request(APP, "GET", a)
    lines = _lines()
    assert [(l["outcome"], l["status"]) for l in lines] == [("done", "3xx"),
                                                           ("done", "2xx")]


# -- the two-site bound, and a new site set means a new yes ------------------------------

def test_a_third_site_is_refused_by_the_two_site_bound():
    with pytest.raises(ValueError):
        egress_doors.register_web_app_door(APP, [HOST, HOST2, "third.example.org"])
    assert egress_doors.web_app_door(APP) is None


@pytest.mark.parametrize("host", ["localhost", "nas.local", "10.0.0.1", "127.1",
                                  "0x7f000001", "[::1]", "printer", "x.internal"])
def test_only_public_names_can_be_registered(host):
    with pytest.raises(ValueError):
        egress_doors.register_web_app_door(APP, [host])


@pytest.mark.parametrize("app_id", ["Wedding-abcdefgh", "wedding", "a_b-abcdefgh",
                                    "x" * 31 + "-abcdefgh", "../x-abcdefgh", "",
                                    None, "wedding-abcdefg1"])
def test_only_browser_app_ids_can_be_registered(app_id):
    with pytest.raises(ValueError):
        egress_doors.register_web_app_door(app_id, [HOST])


def test_a_39_character_hyphenated_id_is_fine():
    app = "a" * 30 + "-abcdefgh"
    assert len(app) == 39
    assert egress_doors.register_web_app_door(app, [HOST]) == f"app:{app}:web"


def test_a_new_site_set_means_a_new_yes(wire):
    _ready()
    assert egress_doors.app_web_hosts(APP) == {HOST: True}
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: False}
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "switched_off"}
    assert wire.calls == []


def test_setting_a_site_the_app_never_asked_for_is_refused():
    egress_doors.register_web_app_door(APP, [HOST])
    got = egress_doors.set_app_web_hosts(APP, ["other.example.org"], by="owner")
    assert got["ok"] is False and got["refused"] == "not_on_list" and got["message"]


def test_unregistering_forgets_the_yes(wire):
    _ready()
    assert egress_doors.unregister_web_app_door(APP) is True
    assert egress_doors.app_web_hosts(APP) == {}
    egress_doors.register_web_app_door(APP, [HOST])
    assert egress_doors.app_web_hosts(APP) == {HOST: False}
    data = json.load(open(egress_doors.app_consent_path(), encoding="utf-8"))
    assert not any(v.get("app") == APP for v in data["doors"].values())


# -- the owner's yes -------------------------------------------------------------------

def test_switching_on_needs_a_ticket():
    egress_doors.register_web_app_door(APP, [HOST])
    got = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE)
    assert got["ok"] is False and got["refused"] == "absent"
    assert egress_doors.app_web_hosts(APP) == {HOST: False}


def test_a_ticket_for_another_site_does_not_switch_this_one():
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    got = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE,
                                         ticket=_yes(APP, HOST2))
    assert got["refused"] == "mismatch"
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: False}


def test_a_ticket_from_another_device_does_not_switch():
    egress_doors.register_web_app_door(APP, [HOST])
    ticket = _yes(APP, HOST, device="device:someone-else-01")
    got = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE, ticket=ticket)
    assert got["refused"] == "wrong_device"


def test_a_device_that_is_not_the_owners_cannot_switch_on():
    egress_doors.register_web_app_door(APP, [HOST])
    ticket = _yes(APP, HOST)
    consent_tickets.set_owner_devices_provider(lambda: {"device:the-real-owner"})
    got = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE, ticket=ticket)
    assert got["refused"] == "not_owner"
    assert egress_doors.app_web_hosts(APP) == {HOST: False}


def test_a_ticket_is_spent_once():
    egress_doors.register_web_app_door(APP, [HOST])
    ticket = _yes(APP, HOST)
    assert egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE, ticket=ticket)["ok"]
    egress_doors.set_app_web_hosts(APP, [], by="owner")
    again = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE, ticket=ticket)
    assert again["refused"] == "unknown"


def test_two_new_sites_at_once_are_refused():
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    got = egress_doors.set_app_web_hosts(APP, [HOST, HOST2], by=DEVICE,
                                         ticket=_yes(APP, HOST))
    assert got["refused"] == "one_at_a_time"


def test_switching_off_needs_nothing(wire):
    _ready(HOST, HOST2)
    got = egress_doors.set_app_web_hosts(APP, [HOST2], by="owner")
    assert got["ok"] and got["switched_off"] == [HOST] and got["switched_on"] == []
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: True}
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "switched_off"}


# -- a site switched off while another change is in flight stays off -----------

def _redeem_then(monkeypatch, during):
    """Run *during()* while set_app_web_hosts is spending its yes."""
    real = consent_tickets.redeem

    def interleaved(*a, **k):
        during()
        return real(*a, **k)
    monkeypatch.setattr(consent_tickets, "redeem", interleaved)


def test_an_off_made_while_a_yes_is_spent_is_not_undone(monkeypatch):
    """The validator's probe: A is on; a call adding B (with B's yes) read A
    as on; while it spends B's yes the owner switches A off. A stays off."""
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    _switch_on(APP, HOST)
    ticket_b = _yes(APP, HOST2)
    seen = {}
    _redeem_then(monkeypatch, lambda: seen.setdefault(
        "off", egress_doors.set_app_web_hosts(APP, [], by="owner")["hosts"]))
    got = egress_doors.set_app_web_hosts(APP, [HOST, HOST2], by=DEVICE,
                                         ticket=ticket_b)
    assert seen["off"] == {HOST: False, HOST2: False}
    assert got["ok"] and got["switched_on"] == [HOST2]
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: True}


def test_an_off_from_another_thread_waits_for_the_change_in_flight(monkeypatch):
    """A second change waits for the first to finish (one lock across read,
    yes and write), so the owner's later off is the last word."""
    import threading
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    _switch_on(APP, HOST)
    ticket_b = _yes(APP, HOST2)
    other = threading.Thread(
        target=lambda: egress_doors.set_app_web_hosts(APP, [], by="owner"))
    blocked = {}

    def during():
        other.start()
        other.join(timeout=0.5)
        blocked["waiting"] = other.is_alive()
    _redeem_then(monkeypatch, during)
    got = egress_doors.set_app_web_hosts(APP, [HOST, HOST2], by=DEVICE,
                                         ticket=ticket_b)
    other.join(timeout=10)
    assert blocked["waiting"] is True and not other.is_alive()
    assert got["ok"]
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: False}


def test_an_off_written_by_another_program_is_not_undone(monkeypatch):
    """Even a change that skips the lock (another program writing the file)
    is honoured: the write is applied to what is on the disk now."""
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    _switch_on(APP, HOST)
    ticket_b = _yes(APP, HOST2)
    d = egress_doors.web_app_door(APP)
    path = egress_doors.app_consent_path()

    def rewrite():
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": egress_doors.APP_CONSENT_SCHEMA, "doors": {
                d.config_flag: {"open": False, "kind": "web", "app": APP,
                                "hosts": list(d.hosts), "hosts_on": []}}}, fh)
    _redeem_then(monkeypatch, rewrite)
    got = egress_doors.set_app_web_hosts(APP, [HOST, HOST2], by=DEVICE,
                                         ticket=ticket_b)
    assert got["ok"] and got["switched_on"] == [HOST2]
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: True}


def test_a_refused_yes_writes_nothing(monkeypatch):
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    _switch_on(APP, HOST)
    before = open(egress_doors.app_consent_path(), encoding="utf-8").read()
    got = egress_doors.set_app_web_hosts(APP, [HOST, HOST2], by=DEVICE,
                                         ticket="not-a-ticket")
    assert not got["ok"]
    assert open(egress_doors.app_consent_path(), encoding="utf-8").read() == before


def test_switch_off_web_hosts_names_only_what_goes_off():
    """The list's one-tap off: a site switched on since the list was read is
    not switched off with it."""
    egress_doors.register_web_app_door(APP, [HOST, HOST2])
    _switch_on(APP, HOST, HOST2)
    got = egress_doors.switch_off_web_hosts(APP, [HOST], by="owner")
    assert got["ok"] and got["switched_off"] == [HOST] and got["switched_on"] == []
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: True}
    # a site the app never asked for is ignored; nothing is switched on
    again = egress_doors.switch_off_web_hosts(APP, ["other.example.org"], by="owner")
    assert again["ok"] and again["switched_off"] == []
    assert egress_doors.app_web_hosts(APP) == {HOST: False, HOST2: True}
    assert egress_doors.switch_off_web_hosts("no-such-app-abcdefgh", [HOST],
                                             by="owner")["refused"] == "unknown_app"
    assert egress_doors.switch_off_web_hosts(APP, HOST, by="owner")["refused"] \
        == "not_on_list"


def test_the_yes_is_written_to_the_state_file_with_no_device_id():
    _ready()
    text = open(egress_doors.app_consent_path(), encoding="utf-8").read()
    assert "owner-phone-0001" not in text
    entry = next(v for v in json.loads(text)["doors"].values() if v.get("app") == APP)
    assert entry["hosts_on"] == [HOST] and entry["by"] == "device"


def test_a_hand_edited_yes_for_an_unlisted_site_opens_nothing(wire):
    egress_doors.register_web_app_door(APP, [HOST])
    d = egress_doors.web_app_door(APP)
    path = egress_doors.app_consent_path()
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": egress_doors.APP_CONSENT_SCHEMA, "doors": {
            d.config_flag: {"open": True, "kind": "web", "app": APP,
                            "hosts_on": ["evil.example.org"]}}}, fh)
    egress_doors._reset_app_consent_cache()  # noqa: SLF001
    assert egress_doors.app_web_hosts(APP) == {HOST: False}
    assert egress_doors.app_web_request(APP, "GET", "https://evil.example.org/") == \
        {"refused": "switched_off"}
    assert wire.calls == []


# -- a sign-up key by handle ----------------------------------------------------------------

def _canary(n=40):
    alphabet = string.ascii_letters + string.digits
    return "tk_" + "".join(secrets.choice(alphabet) for _ in range(n))


ROW = {
    "id": "tilesvc", "name": "Tiles", "for": "Map tiles.",
    "signup_url": "https://example-maps.org/signup", "door": "tilesvc",
    "how_long": "2 minutes", "cost_note": "Free.", "card_required": False,
    "personal_use_only": False, "hosts": [HOST2, "other.example-maps.org"],
    "placement": {"in": "header", "name": "Authorization",
                  "value_format": "Bearer {key}"},
    "check": {"url": f"https://{HOST2}/check", "ok": [200], "rejected": [401]},
    "monthly_cap": None, "per_minute_cap": 30, "signup_steps": [],
}


class FakeVault:
    is_unlocked = True
    is_initialized = True

    def __init__(self, value):
        self.value = value

    def _reserved_get(self, name):
        if name != "__puffpai.key.tilesvc":
            return None
        return json.dumps({"schema": keys.RECORD_SCHEMA, "service_id": "tilesvc",
                           "value": self.value, "hosts": list(ROW["hosts"]),
                           "card_required": False})


@pytest.fixture
def tile_key(monkeypatch):
    value = _canary()
    monkeypatch.setattr(keys, "spec", lambda sid: dict(ROW) if str(sid) == "tilesvc" else None)
    monkeypatch.setattr(keys, "_read_index", lambda: {
        "tilesvc": {"hosts": list(ROW["hosts"]), "card_required": False,
                    "background": False}})
    keys.set_vault_provider(lambda: FakeVault(value))
    keys._use_times.clear()  # noqa: SLF001
    return value


def test_the_key_is_injected_for_its_site_and_never_logged(wire, tile_key, caplog):
    caplog.set_level(logging.DEBUG)
    _ready(HOST2)
    url = f"https://{HOST2}/tile/1"
    wire.answers[url] = Resp(200, f"echo {tile_key} end".encode(),
                             {"ETag": f"W/{tile_key}", "Content-Type": "image/png"})
    got = egress_doors.app_web_request(APP, "GET", url, key_handle="key:tilesvc")
    assert wire.calls[0]["headers"]["Authorization"] == f"Bearer {tile_key}"
    assert tile_key.encode() not in got["body"] and b"[removed]" in got["body"]
    assert tile_key not in json.dumps(got["headers"])
    assert tile_key not in _log_text() and tile_key not in caplog.text
    assert tile_key not in repr(got)


def test_the_key_is_bound_to_exactly_the_site_asked_for(monkeypatch, tile_key):
    captured = {}
    real = egress_doors._bind_credential  # noqa: SLF001

    def spy(header, value, hosts, **kw):
        captured["hosts"] = sorted(hosts)
        return real(header, value, hosts, **kw)

    w = Wire()
    monkeypatch.setattr(egress_doors, "_bind_credential", spy)
    monkeypatch.setattr(egress_doors, "_web_wire", w)
    _ready(HOST2)
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST2}/t",
                                       key_handle="key:tilesvc")
    assert captured["hosts"] == [HOST2]  # not every host the key row names
    # The spy lives outside puffpai.security, so the real binder refuses it
    # (its caller guard): the request fails closed, nothing is sent.
    assert got == {"refused": "no_answer"} and w.calls == []


def test_the_key_is_refused_for_a_site_its_row_does_not_name(wire, tile_key):
    _ready(HOST)
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x",
                                       key_handle="key:tilesvc")
    assert got == {"refused": "key_host"} and wire.calls == []


def test_the_key_never_rides_a_redirect(wire, tile_key):
    _ready(HOST2, HOST)
    a, b = f"https://{HOST2}/a", f"https://{HOST}/b"
    wire.answers.update({a: Resp(302, b"", {"Location": b}), b: Resp(200, b"ok")})
    got = egress_doors.app_web_request(APP, "GET", a, key_handle="key:tilesvc")
    assert got["status"] == 200
    assert "Authorization" in wire.calls[0]["headers"]
    assert "Authorization" not in wire.calls[1]["headers"]


@pytest.mark.parametrize("handle", ["tilesvc", "key:", "key:Nope!", "key:unknownsvc"])
def test_an_unusable_key_handle_sends_nothing(wire, tile_key, handle):
    _ready(HOST2)
    got = egress_doors.app_web_request(APP, "GET", f"https://{HOST2}/x", key_handle=handle)
    assert got["refused"] in ("key_unavailable", "key_host") and wire.calls == []


def test_a_rejected_answer_stops_the_key(wire, tile_key, monkeypatch):
    marked = []
    monkeypatch.setattr(keys, "_mark_rejected", lambda sid: marked.append(sid))
    _ready(HOST2)
    url = f"https://{HOST2}/x"
    wire.answers[url] = Resp(401, b"no")
    got = egress_doors.app_web_request(APP, "GET", url, key_handle="key:tilesvc")
    assert got["status"] == 401 and marked == ["tilesvc"]


def test_an_app_header_cannot_carry_a_key_of_its_own(wire):
    _ready()
    egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x",
                                 headers={"Authorization": "Bearer mine",
                                          "X-Goog-Api-Key": "k"})
    assert "Authorization" not in wire.calls[0]["headers"]
    assert "X-Goog-Api-Key" not in wire.calls[0]["headers"]


# -- never raises, and the log keeps every line ------------------------------------------------

def test_a_broken_connection_is_no_answer_not_an_exception(monkeypatch):
    _ready()

    def boom(*a, **k):
        raise ConnectionResetError("cut")

    monkeypatch.setattr(egress_doors, "_web_wire", boom)
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "no_answer"}
    assert _lines()[-1]["outcome"] == "failed"


def test_an_unexpected_error_is_no_answer_not_an_exception(monkeypatch):
    _ready()

    def boom(*a, **k):
        raise RuntimeError("odd")

    monkeypatch.setattr(egress_doors, "_web_wire", boom)
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "no_answer"}


def test_the_pinned_connection_refusal_is_inside_address(monkeypatch):
    from puffpai.security import pinned_net
    _ready()

    def moved(*a, **k):
        raise pinned_net.PinRefused("landed elsewhere", "peer_moved")

    monkeypatch.setattr(egress_doors, "_web_wire", moved)
    assert egress_doors.app_web_request(APP, "GET", f"https://{HOST}/x") == \
        {"refused": "peer_moved"}


def test_every_app_id_shape_keeps_its_log_line(wire):
    """The log's key filter drops random-looking service ids; an app's own id
    (random 8-letter end) must never lose its line."""
    alphabet = "abcdefghijklmnopqrstuvwxyz234567"
    for n in range(40):
        app = f"app{n}-" + "".join(secrets.choice(alphabet) for _ in range(8))
        assert egress_ledger.build_record(f"app:{app}:web", host=HOST,
                                          outcome="done") is not None, app


def test_the_real_wire_is_pinned_and_follows_nothing():
    """The one network function builds a pinned opener with no redirects."""
    import inspect
    text = inspect.getsource(egress_doors._web_wire)  # noqa: SLF001
    assert "pinned_net.urllib_opener(pin, _NoFollow)" in text
    assert "return None" in text


def test_the_length_handed_back_is_the_real_one(wire, tile_key):
    _ready(HOST2)
    url = f"https://{HOST2}/x"
    wire.answers[url] = Resp(200, f"a{tile_key}b".encode(), {"Content-Length": "5"})
    got = egress_doors.app_web_request(APP, "GET", url, key_handle="key:tilesvc")
    assert got["headers"]["content-length"] == str(len(got["body"]))


def test_an_unreadable_consent_file_is_never_written_over(monkeypatch):
    import os
    egress_doors.register_web_app_door(APP, [HOST])
    path = egress_doors.app_consent_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    egress_doors._reset_app_consent_cache()  # noqa: SLF001
    got = egress_doors.set_app_web_hosts(APP, [], by="owner")
    assert got["ok"] is False and got["refused"] == "cannot"
    egress_doors.unregister_web_app_door(APP)
    assert open(path, encoding="utf-8").read() == "{ not json"
    got = egress_doors.set_app_web_hosts(APP, [HOST], by=DEVICE, ticket=_yes(APP, HOST))
    assert got["refused"] == "unknown_app"
