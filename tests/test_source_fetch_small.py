"""source_fetch.fetch_small: the small GET (searches, READMEs, pictures).

No network: every request goes to a fake transport. Proven here: only the
listed answer headers come back, a request header off the short list refuses
the request before anything is sent, ``allow_truncated`` gives the first bytes,
a sign-up key goes only to the host its row names and only on the first hop,
and there is one line in the outside-traffic log per hop, host only.
"""

from __future__ import annotations

import json
import secrets
import string

import pytest

from puffpai import source_fetch
from puffpai.security import egress_doors, egress_ledger, keys, outside_switches

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
    """A fake transport: answers from a table, remembers what was sent."""

    def __init__(self, answers):
        self.answers = answers          # url -> Resp (or a list of Resp)
        self.calls: list[dict] = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers)})
        got = self.answers.get(url)
        if got is None:
            return Resp(404, b"")
        return got


_MP: list = []


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _MP[:] = [monkeypatch]
    source_fetch.clear_caches()
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_BY_HOST", {})
    outside_switches.forget()
    keys._use_times.clear()  # noqa: SLF001
    yield
    source_fetch.clear_caches()
    keys.set_vault_provider(None)


def _lines():
    return list(reversed(egress_ledger.read(1, limit=100)))


def _call(url, wire, **kw):
    kw.setdefault("allowed_hosts", {"api.github.com"})
    kw.setdefault("max_bytes", 1000)
    kw.setdefault("service", "code_search")
    kw.setdefault("honour_robots", False)
    if kw.get("key_id") is not None:
        # A keyed request never takes a caller's transport: the security
        # package sends the keyed hop on its own pinned connection. The fake
        # wire stands in for that connection and for the plain one.
        _MP[0].setattr(egress_doors, "_keyed_wire", wire)
        _MP[0].setattr(source_fetch, "_default_transport", wire)
        return source_fetch.fetch_small(url, resolver=lambda h: PUBLIC, **kw)
    return source_fetch.fetch_small(url, transport=wire,
                                    resolver=lambda h: PUBLIC, **kw)


# -- answer headers ------------------------------------------------------------

def test_only_the_listed_answer_headers_come_back():
    url = "https://api.github.com/search/repositories?q=garden+planner"
    wire = Wire({url: Resp(200, b"{}", {
        "ETag": '"abc"', "Last-Modified": "Tue, 01 Sep 2026 00:00:00 GMT",
        "Content-Type": "application/json", "X-RateLimit-Limit": "10",
        "X-RateLimit-Remaining": "9", "X-RateLimit-Reset": "1790000000",
        "X-RateLimit-Resource": "search", "Retry-After": "5",
        "Link": '<https://api.github.com/x?page=2>; rel="next"',
        "Set-Cookie": "session=secret", "Content-Security-Policy": "default-src *",
        "Location": "https://evil.example/", "Server": "GitHub.com",
        "Clear-Site-Data": '"*"'})})
    got = _call(url, wire)
    assert got["reason"] == "ok" and got["status"] == 200 and got["body"] == b"{}"
    assert set(got["headers"]) == {
        "etag", "last-modified", "content-type", "x-ratelimit-limit",
        "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-resource",
        "retry-after", "link"}
    assert got["headers"]["x-ratelimit-remaining"] == "9"
    assert got["truncated"] is False


def test_a_not_modified_answer_is_an_answer():
    url = "https://api.github.com/repos/o/r"
    wire = Wire({url: Resp(304, b"", {"ETag": '"abc"'})})
    got = _call(url, wire, headers={"If-None-Match": '"abc"'})
    assert got["reason"] == "ok" and got["status"] == 304 and got["body"] == b""
    assert wire.calls[0]["headers"]["If-None-Match"] == '"abc"'


# -- request headers -----------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"Authorization": "token abc"}, {"Cookie": "a=b"}, {"User-Agent": "me"},
    {"X-Forwarded-For": "1.2.3.4"}, {"Accept": "a\r\nX-Evil: 1"},
    {"accept": 5}, {"Host": "other.example"}])
def test_a_request_header_off_the_list_is_refused_before_sending(extra):
    wire = Wire({})
    got = _call("https://api.github.com/x", wire, headers=extra)
    assert got["reason"] == "header_refused" and got["body"] is None
    assert wire.calls == []
    (line,) = _lines()
    assert (line["service"], line["outcome"], line["code"]) == (
        "code_search", "refused", "header_refused")


def test_the_three_allowed_request_headers_are_sent():
    url = "https://api.github.com/x"
    wire = Wire({url: Resp(200, b"ok")})
    got = _call(url, wire, headers={"accept": "application/vnd.github+json",
                                    "If-None-Match": '"e"',
                                    "If-Modified-Since": "Tue, 01 Sep 2026 00:00:00 GMT"})
    assert got["reason"] == "ok"
    sent = wire.calls[0]["headers"]
    assert sent["Accept"] == "application/vnd.github+json"
    assert sent["If-None-Match"] == '"e"'
    assert "If-Modified-Since" in sent
    assert not any(k.lower() in ("authorization", "cookie") for k in sent)


# -- size ----------------------------------------------------------------------

def test_allow_truncated_returns_the_first_bytes():
    url = "https://github.com/o/r.git/info/refs?service=git-upload-pack"
    body = bytes(range(200))
    wire = Wire({url: Resp(200, body)})
    got = _call(url, wire, allowed_hosts={"github.com"}, max_bytes=64,
                allow_truncated=True)
    assert got["reason"] == "ok" and got["truncated"] is True
    assert got["body"] == body[:64]


def test_without_allow_truncated_a_long_body_is_refused():
    url = "https://api.github.com/x"
    wire = Wire({url: Resp(200, b"x" * 200)})
    got = _call(url, wire, max_bytes=64)
    assert got["reason"] == "too_big" and got["body"] is None
    wire = Wire({url: Resp(200, b"x" * 10, {"Content-Length": "5000"})})
    got = _call(url, wire, max_bytes=64)
    assert got["reason"] == "too_big" and got["body"] is None


# -- guards --------------------------------------------------------------------

@pytest.mark.parametrize("url,code", [
    ("http://api.github.com/x", "not_encrypted"),
    ("https://api.github.com:8443/x", "not_allowed"),
    ("https://user:pw" + AT + "api.github.com/x", "sign_in_refused"),
    ("https://raw.githubusercontent.com/x", "host"),
])
def test_the_usual_guards_refuse_before_sending(url, code):
    wire = Wire({})
    got = _call(url, wire)
    assert got["reason"] == code and wire.calls == []


def test_an_inside_address_is_refused():
    wire = Wire({})
    got = source_fetch.fetch_small(
        "https://api.github.com/x", allowed_hosts={"api.github.com"},
        max_bytes=100, service="code_search", honour_robots=False,
        transport=wire, resolver=lambda h: ["10.0.0.7"])
    assert got["reason"] == "inside_address" and wire.calls == []


def test_an_unknown_service_is_refused():
    wire = Wire({})
    got = _call("https://api.github.com/x", wire, service="made_up_name")
    assert got["reason"] == "not_allowed" and wire.calls == []


@pytest.mark.parametrize("kw,service", [
    ({"service": "made_up_name"}, source_fetch.UNKNOWN_SERVICE_ID),
    ({"service": "Not A Service!"}, source_fetch.UNKNOWN_SERVICE_ID),
    ({"max_bytes": 0}, "code_search"),
    ({"max_bytes": -5}, "code_search"),
    ({"max_bytes": True}, "code_search"),
    ({"allowed_hosts": set()}, "code_search"),
    ({"allowed_hosts": "api.github.com"}, "code_search"),
])
def test_every_early_refusal_is_one_log_line(kw, service):
    wire = Wire({})
    got = _call("https://api.github.com/x?q=secret+words", wire, **kw)
    assert got["reason"] == "not_allowed" and wire.calls == []
    (line,) = _lines()
    assert (line["service"], line["outcome"], line["code"]) == (
        service, "refused", "not_allowed")
    assert line["host"] == "api.github.com" and line["path"] == ""


def test_the_switch_off_refuses_before_a_socket():
    outside_switches.set_switch("code_search", False, by="owner")
    wire = Wire({})
    got = _call("https://api.github.com/x", wire)
    assert got["reason"] == "switched_off" and wire.calls == []
    assert _lines()[-1]["code"] == "switched_off"


def test_a_third_redirect_is_refused_and_each_hop_is_one_line():
    a, b, c, d = (f"https://h{i}.example/p?q=secret+words" for i in range(4))
    wire = Wire({a: Resp(302, b"", {"Location": b}),
                 b: Resp(302, b"", {"Location": c}),
                 c: Resp(302, b"", {"Location": d}),
                 d: Resp(200, b"never")})
    got = _call(a, wire, allowed_hosts={f"h{i}.example" for i in range(4)})
    assert got["reason"] == "redirects"
    assert [c_["url"] for c_ in wire.calls] == [a, b, c]
    lines = _lines()
    assert [(l["host"], l["outcome"]) for l in lines] == [
        ("h0.example", "done"), ("h1.example", "done"), ("h2.example", "done"),
        ("h3.example", "refused")]
    assert lines[-1]["code"] == "redirects"
    text = open(egress_ledger.log_path(), encoding="utf-8").read()
    assert "secret" not in text and "/p" not in text


def test_two_redirects_are_followed_one_line_per_hop():
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(301, b"", {"Location": b}), b: Resp(200, b"{}")})
    got = _call(a, wire)
    assert got["reason"] == "ok" and got["body"] == b"{}"
    lines = _lines()
    assert len(lines) == 2
    assert all(l["service"] == "code_search" and l["what"] == "code_search"
               and l["path"] == "" for l in lines)


def test_robots_is_read_first_and_logged_under_the_service():
    url = "https://api.github.com/x"
    wire = Wire({"https://api.github.com/robots.txt":
                 Resp(200, b"User-agent: *\nDisallow: /x\n"),
                 url: Resp(200, b"no")})
    got = _call(url, wire, honour_robots=True)
    assert got["reason"] == "robots_rule"
    assert [c["url"] for c in wire.calls] == ["https://api.github.com/robots.txt"]
    (line,) = _lines()
    assert (line["service"], line["what"]) == ("code_search", "robots_rules")


def test_an_unlisted_host_is_not_even_asked_for_its_robots_file():
    wire = Wire({})
    got = _call("https://other.example/x", wire, honour_robots=True)
    assert got["reason"] == "host" and wire.calls == []


@pytest.mark.parametrize("url", ["https://[not-closed/x", "https://api.github.com:99999/x",
                                 "", "not a url"])
def test_a_malformed_address_is_refused_not_raised(url):
    wire = Wire({})
    got = _call(url, wire, honour_robots=True)
    assert got["reason"] != "ok" and got["body"] is None and wire.calls == []


# -- a sign-up key ---------------------------------------------------------------

GITHUB_ROW = {
    "id": "github", "name": "GitHub", "for": "Searching GitHub three times faster.",
    "signup_url": "https://github.com/settings/tokens", "door": "github",
    "how_long": "2 minutes", "cost_note": "Free.", "card_required": False,
    "personal_use_only": False, "hosts": ["api.github.com"],
    "placement": {"in": "header", "name": "Authorization",
                  "value_format": "Bearer {key}"},
    "check": {"url": "https://api.github.com/rate_limit", "ok": [200],
              "rejected": [401]},
    "monthly_cap": None, "per_minute_cap": 30, "signup_steps": [],
}


def _canary(n=40):
    alphabet = string.ascii_letters + string.digits
    return "ghp_" + "".join(secrets.choice(alphabet) for _ in range(n))


class FakeVault:
    is_unlocked = True
    is_initialized = True

    def __init__(self, value):
        self.value = value

    def _reserved_get(self, name):
        if name != "__puffpai.key.github":
            return None
        return json.dumps({"schema": keys.RECORD_SCHEMA, "service_id": "github",
                           "value": self.value, "hosts": ["api.github.com"],
                           "card_required": False})


@pytest.fixture
def github_key(monkeypatch):
    value = _canary()
    monkeypatch.setattr(keys, "spec", lambda sid: dict(GITHUB_ROW)
                        if str(sid) == "github" else None)
    monkeypatch.setattr(keys, "_read_index", lambda: {
        "github": {"hosts": ["api.github.com"], "card_required": False,
                   "background": False}})
    keys.set_vault_provider(lambda: FakeVault(value))
    return value


def test_the_key_goes_to_api_github_com_with_its_format(github_key):
    url = "https://api.github.com/search/repositories?q=x"
    wire = Wire({url: Resp(200, b'{"items":[]}')})
    got = _call(url, wire, key_id="github")
    assert got["reason"] == "ok" and got["status"] == 200
    assert wire.calls[0]["headers"]["Authorization"] == f"Bearer {github_key}"
    text = open(egress_ledger.log_path(), encoding="utf-8").read()
    assert github_key not in text


def test_the_key_is_refused_for_raw_githubusercontent(github_key):
    wire = Wire({})
    got = _call("https://raw.githubusercontent.com/o/r/HEAD/README.md", wire,
                allowed_hosts={"raw.githubusercontent.com"}, key_id="github")
    assert got["reason"] == "key_host" and got["body"] is None
    assert wire.calls == []
    assert _lines()[-1]["code"] == "key_host"


def test_the_key_never_rides_a_redirect(github_key):
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(301, b"", {"Location": b}), b: Resp(200, b"{}")})
    got = _call(a, wire, key_id="github")
    assert got["reason"] == "ok"
    assert "Authorization" in wire.calls[0]["headers"]
    assert "Authorization" not in wire.calls[1]["headers"]


def test_an_echoed_key_is_removed_from_the_body(github_key):
    url = "https://api.github.com/x"
    wire = Wire({url: Resp(200, f"you sent {github_key}".encode())})
    got = _call(url, wire, key_id="github")
    assert github_key.encode() not in got["body"]
    assert b"[removed]" in got["body"]


def test_a_missing_key_row_is_refused(monkeypatch):
    monkeypatch.setattr(keys, "spec", lambda sid: None)
    wire = Wire({})
    got = _call("https://api.github.com/x", wire, key_id="github")
    assert got["reason"] == "key_unavailable" and wire.calls == []


def test_a_locked_vault_without_background_refuses_the_key(github_key, monkeypatch):
    class Locked(FakeVault):
        is_unlocked = False

        def read_service_secret_unattended(self, name):
            return None
    keys.set_vault_provider(lambda: Locked("x" * 20))
    wire = Wire({})
    got = _call("https://api.github.com/x", wire, key_id="github")
    assert got["reason"] == "key_unavailable" and wire.calls == []


def test_a_rejected_answer_stops_the_key(github_key, monkeypatch):
    marked = []
    monkeypatch.setattr(keys, "_mark_rejected", lambda sid: marked.append(sid))
    url = "https://api.github.com/x"
    wire = Wire({url: Resp(401, b"bad credentials")})
    got = _call(url, wire, key_id="github")
    assert got["status"] == 401 and marked == ["github"]


# -- the key never leaves the security package ------------------------------------

def test_the_old_public_key_helpers_are_gone():
    assert not hasattr(egress_doors, "fetch_key_credential")
    assert not hasattr(egress_doors, "credential_header_for")
    assert "keyed_first_hop" not in egress_doors.__all__


def test_the_key_helpers_refuse_a_caller_outside_the_security_package(github_key):
    with pytest.raises(PermissionError):
        egress_doors._fetch_key_credential(  # noqa: SLF001
            "github", "api.github.com", caller="anything")
    with pytest.raises(PermissionError):
        egress_doors._credential_header_for(  # noqa: SLF001
            object(), "https://api.github.com/")
    # Nothing was issued, so nothing was counted.
    assert not keys._use_times.get("github")  # noqa: SLF001
    assert keys.uses_this_month("github") in (None, 0)


def test_a_caller_transport_never_receives_the_key(github_key):
    wire = Wire({"https://api.github.com/x": Resp(200, b"{}")})
    got = source_fetch.fetch_small(
        "https://api.github.com/x", allowed_hosts={"api.github.com"},
        max_bytes=100, service="code_search", honour_robots=False,
        key_id="github", transport=wire, resolver=lambda h: PUBLIC)
    assert got["reason"] == "key_unavailable" and got["body"] is None
    assert wire.calls == []
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("refused", "key_unavailable")
    assert not keys._use_times.get("github")  # noqa: SLF001
    assert keys.uses_this_month("github") in (None, 0)


def test_the_keyed_answer_holds_no_key_anywhere(github_key):
    url = "https://api.github.com/x"
    wire = Wire({url: Resp(200, f"a {github_key} b".encode(), {
        "Link": f"<https://api.github.com/x?k={github_key}>; rel=next",
        "ETag": '"e"'})})
    got = _call(url, wire, key_id="github")
    assert got["reason"] == "ok"
    assert github_key not in repr(got)
    assert "[removed]" in got["headers"]["link"]
    assert wire.calls[0]["headers"]["Authorization"] == f"Bearer {github_key}"


def test_a_body_cut_part_way_through_the_key_loses_the_part(github_key):
    url = "https://api.github.com/x"
    body = b"x" * 20 + github_key.encode() + b"y" * 50
    wire = Wire({url: Resp(200, body)})
    got = _call(url, wire, key_id="github", max_bytes=40, allow_truncated=True)
    assert got["reason"] == "ok" and got["truncated"] is True
    assert github_key[:10].encode() not in got["body"]
    assert len(got["body"]) <= 40


def test_a_keyed_long_body_is_still_too_big(github_key):
    url = "https://api.github.com/x"
    # The echo removal makes the body shorter; the size rule counts the wire.
    wire = Wire({url: Resp(200, (github_key * 3).encode())})
    got = _call(url, wire, key_id="github", max_bytes=len(github_key) * 3 - 1)
    assert got["reason"] == "too_big" and got["body"] is None


def test_a_redirected_keyed_answer_is_followed_without_the_key(github_key, monkeypatch):
    marked = []
    monkeypatch.setattr(keys, "_mark_rejected", lambda sid: marked.append(sid))
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(301, b"", {"Location": b}), b: Resp(401, b"no")})
    got = _call(a, wire, key_id="github")
    assert got["status"] == 401
    assert "Authorization" not in wire.calls[1]["headers"]
    # Only the keyed hop's own answer can stop the key.
    assert marked == []


def test_the_keyed_hop_refuses_any_caller_but_fetch_small(github_key):
    with pytest.raises(PermissionError):
        egress_doors.keyed_first_hop("github", "https://api.github.com/x", {},
                                     service="code_search", max_bytes=10,
                                     timeout=1.0)
    assert keys.uses_this_month("github") in (None, 0)


# -- the switch and the log line cannot be skipped ------------------

class KeyWire(Wire):
    """Stands in for the security package's pinned connection."""

    def __init__(self, answers, during=None):
        super().__init__(answers)
        self.during = during

    def __call__(self, url, headers, timeout):
        if self.during is not None:
            self.during()
        return super().__call__(url, headers, timeout)


def _keyed_setup(monkeypatch, wire):
    monkeypatch.setattr(egress_doors, "_keyed_wire", wire)
    monkeypatch.setattr(source_fetch, "_default_transport", wire)


def test_source_fetch_has_no_module_level_keyed_helper():
    # The old source_fetch._keyed_hop let any caller send the key with no
    # switch check and no log line.
    assert not hasattr(source_fetch, "_keyed_hop")
    for name, value in vars(source_fetch).items():
        code = getattr(value, "__code__", None)
        if code is not None and name != "fetch_small":
            assert "keyed_first_hop" not in code.co_names, name


def test_the_chain_with_an_outside_keyed_hop_is_refused(github_key, monkeypatch):
    wire = KeyWire({"https://api.github.com/x": Resp(200, b"{}")})
    _keyed_setup(monkeypatch, wire)

    def outside_hop(url, headers, timeout):
        return egress_doors.keyed_first_hop(
            "github", url, headers, service="code_search", max_bytes=100,
            timeout=timeout)

    # The refusal is a PermissionError, which is an OSError, so the chain
    # reports it like a connection that could not be made (as it must for a
    # firewall's "access denied"): nothing sent, no key use, one line.
    response, _final, code, _sent = source_fetch._r3_chain(  # noqa: SLF001
        "https://api.github.com/x", allowed={"api.github.com"}, headers={},
        transport=wire, resolver=lambda h: PUBLIC, service="code_search",
        what="code_search", max_redirects=2, keyed_hop=outside_hop)
    assert response is None and code == "no_answer"
    assert wire.calls == []
    assert keys.uses_this_month("github") in (None, 0)
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("failed", "no_answer")


def _forged_caller():
    """A function that imitates the one allowed caller: same module name in
    its globals, same qualified name. The caller check reads the frame, so it
    lets this through; the switch and the log line must still hold."""
    src = (
        "def fetch_small(key_id, url, service):\n"
        "    def _send_keyed():\n"
        "        return egress_doors.keyed_first_hop(\n"
        "            key_id, url, {}, service=service, max_bytes=100,\n"
        "            timeout=1.0)\n"
        "    return _send_keyed()\n")
    env = {"__name__": "puffpai.source_fetch", "egress_doors": egress_doors}
    exec(compile(src, "<forged>", "exec"), env)  # noqa: S102 - the attack
    return env["fetch_small"]


def test_a_forged_caller_still_meets_the_owners_switch(github_key, monkeypatch):
    wire = KeyWire({"https://api.github.com/x": Resp(200, b"{}")})
    _keyed_setup(monkeypatch, wire)
    outside_switches.set_switch("code_search", False, by="owner")
    answer, code = _forged_caller()("github", "https://api.github.com/x",
                                    "code_search")
    assert (answer, code) == (None, "switched_off")
    assert wire.calls == []
    assert keys.uses_this_month("github") in (None, 0)
    (line,) = _lines()
    assert (line["service"], line["outcome"], line["code"], line["host"]) == (
        "code_search", "refused", "switched_off", "api.github.com")


def test_a_forged_caller_is_always_logged(github_key, monkeypatch):
    wire = KeyWire({"https://api.github.com/x": Resp(200, b"{}")})
    _keyed_setup(monkeypatch, wire)
    answer, code = _forged_caller()("github", "https://api.github.com/x",
                                    "code_search")
    assert code == "ok" and wire.calls
    (line,) = _lines()
    assert (line["service"], line["what"], line["outcome"], line["host"]) == (
        "code_search", "code_search", "done", "api.github.com")
    text = open(egress_ledger.log_path(), encoding="utf-8").read()
    assert github_key not in text


def test_a_forged_caller_cannot_name_its_own_service(github_key, monkeypatch):
    wire = KeyWire({"https://api.github.com/x": Resp(200, b"{}")})
    _keyed_setup(monkeypatch, wire)
    answer, code = _forged_caller()("github", "https://api.github.com/x",
                                    "made_up")
    assert (answer, code) == (None, "not_allowed") and wire.calls == []
    (line,) = _lines()
    assert (line["service"], line["code"]) == ("fetch_small", "not_allowed")


def test_a_keyed_fetch_with_the_switch_off_sends_nothing(github_key, monkeypatch):
    wire = KeyWire({"https://api.github.com/x": Resp(200, b"{}")})
    _keyed_setup(monkeypatch, wire)
    outside_switches.set_switch("code_search", False, by="owner")
    got = _call("https://api.github.com/x", wire, key_id="github")
    assert got["reason"] == "switched_off" and wire.calls == []
    assert keys.uses_this_month("github") in (None, 0)
    (line,) = _lines()
    assert line["code"] == "switched_off"


def test_keyed_fetches_write_one_line_per_hop(github_key, monkeypatch):
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(301, b"", {"Location": b}), b: Resp(200, b"{}")})
    got = _call(a, wire, key_id="github")
    assert got["reason"] == "ok"
    first, second = _lines()
    assert (first["outcome"], first["status"]) == ("done", "3xx")
    assert (second["outcome"], second["status"]) == ("done", "2xx")

    source_fetch.clear_caches()
    before = len(_lines())
    url = "https://api.github.com/x"
    got = _call(url, Wire({url: Resp(200, b"x" * 50)}), key_id="github",
                max_bytes=10)
    assert got["reason"] == "too_big"
    (line,) = _lines()[before:]
    assert (line["outcome"], line["code"]) == ("refused", "too_big")

    before = len(_lines())
    got = _call(url, Wire({url: Resp(200, b"x" * 50)}), key_id="github",
                max_bytes=10, allow_truncated=True)
    assert got["reason"] == "ok" and got["truncated"] is True
    assert got["body"] == b"x" * 10
    (line,) = _lines()[before:]
    assert (line["outcome"], line["bytes_in"]) == ("done", 10)


def _holds(value, needles, seen, depth=0):
    """True when *value* (or something it holds, a few levels down) contains
    one of *needles*. Walks containers and plain objects' attributes. An
    object that raises when looked at (a web framework's request proxy
    outside a request) is skipped."""
    try:
        return _holds_inner(value, needles, seen, depth)
    except Exception:
        return False


def _holds_inner(value, needles, seen, depth):
    if depth > 4 or id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, (str, bytes, bytearray)):
        data = value.encode("utf-8", "replace") if isinstance(value, str) else bytes(value)
        return any(n in data for n in needles)
    if isinstance(value, type) or callable(value) and not hasattr(value, "__slots__"):
        return False
    if isinstance(value, dict) or hasattr(value, "items") and hasattr(value, "keys"):
        try:
            pairs = list(value.items())
        except Exception:
            return False
        return any(_holds(k, needles, seen, depth + 1)
                   or _holds(v, needles, seen, depth + 1) for k, v in pairs)
    if isinstance(value, (list, tuple, set, frozenset)) or (
            hasattr(value, "__iter__") and type(value).__module__ in (
                "_weakrefset", "weakref", "collections")):
        try:
            items = list(value)
        except Exception:
            return False
        return any(_holds(v, needles, seen, depth + 1) for v in items)
    names = list(getattr(value, "__dict__", {}) or {})
    for klass in type(value).__mro__:
        names += [s for s in getattr(klass, "__slots__", ()) if isinstance(s, str)]
    for name in names:
        try:
            inner = object.__getattribute__(value, name)
        except Exception:
            continue
        if _holds(inner, needles, seen, depth + 1):
            return True
    return False


def test_no_module_holds_the_key_while_a_request_is_in_flight(github_key, monkeypatch):
    import sys
    import types
    needles = (github_key.encode(), f"Bearer {github_key}".encode())
    found: list = []

    def scan():
        seen: set = set()
        for modname, module in list(sys.modules.items()):
            if not modname.startswith("puffpai") or module is None:
                continue
            for name, value in list(vars(module).items()):
                if isinstance(value, types.ModuleType):
                    continue
                if _holds(value, needles, seen):
                    found.append(f"{modname}.{name}")

    url = "https://api.github.com/x"
    wire = KeyWire({url: Resp(200, b"{}")}, during=scan)
    _keyed_setup(monkeypatch, wire)
    got = _call(url, wire, key_id="github")
    assert got["reason"] == "ok"
    assert wire.calls[0]["headers"]["Authorization"] == f"Bearer {github_key}"
    assert found == []


def test_the_scanner_finds_a_key_in_the_old_leak_shapes():
    # Without this, the in-flight test above could pass by looking nowhere.
    import weakref
    secret = _canary()
    needles = (secret.encode(),)
    cred = object.__new__(egress_doors._BoundCredential)  # noqa: SLF001
    object.__setattr__(cred, "_value", f"Bearer {secret}")
    held = weakref.WeakSet([cred])
    assert _holds(held, needles, set())
    echo = weakref.WeakKeyDictionary({cred: (secret.encode(),)})
    assert _holds(echo, needles, set())
    assert not _holds({"a": ("b", b"c")}, needles, set())


def test_the_issued_credential_list_is_gone():
    assert not hasattr(egress_doors, "_ISSUED_CREDENTIALS")
    assert not hasattr(egress_doors, "_KEY_ECHO")


# -- an address the HTTP library would choke on is refused ----------

@pytest.mark.parametrize("url", [
    "https://api.github.com/a b", "https://api.github.com/a\x01b",
    "https://api.github.com/a\r\nX: y", "https://api.github.com/a\x7fb",
    "https://api.github.com/café", " https://api.github.com/x",
    "https://api.github.com/x\t"])
def test_an_address_with_spaces_or_control_characters_is_refused(url):
    wire = Wire({})
    got = _call(url, wire)
    assert got["reason"] == "not_allowed" and got["body"] is None
    assert wire.calls == []
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("refused", "not_allowed")


def test_a_percent_encoded_space_is_fine():
    url = "https://api.github.com/a%20b"
    wire = Wire({url: Resp(200, b"{}")})
    assert _call(url, wire)["reason"] == "ok"


def test_a_redirect_to_an_address_with_a_space_is_refused():
    a = "https://api.github.com/a"
    wire = Wire({a: Resp(302, b"", {"Location": "/b c"})})
    got = _call(a, wire)
    assert got["reason"] == "not_allowed" and len(wire.calls) == 1
    assert [x["outcome"] for x in _lines()] == ["done", "refused"]


def test_a_keyed_address_with_a_space_is_refused(github_key, monkeypatch):
    wire = KeyWire({})
    _keyed_setup(monkeypatch, wire)
    got = _call("https://api.github.com/a b", wire, key_id="github")
    assert got["reason"] == "not_allowed" and wire.calls == []
    assert keys.uses_this_month("github") in (None, 0)


def test_a_garbled_answer_is_a_reason_not_a_crash():
    import http.client

    def broken(url, headers, timeout):
        raise http.client.BadStatusLine("garbage")
    got = source_fetch.fetch_small(
        "https://api.github.com/x", allowed_hosts={"api.github.com"},
        max_bytes=10, service="code_search", honour_robots=False,
        transport=broken, resolver=lambda h: PUBLIC)
    assert got["reason"] == "bad_answer" and got["body"] is None
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("failed", "bad_answer")


def test_a_cut_off_body_is_a_reason_not_a_crash():
    import http.client

    class Cut(Resp):
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"par")
    url = "https://api.github.com/x"
    got = _call(url, Wire({url: Cut(200)}))
    assert got["reason"] == "no_answer" and got["body"] is None


def test_a_keyed_garbled_answer_is_a_reason_not_a_crash(github_key, monkeypatch):
    import http.client

    def broken(url, headers, timeout):
        raise http.client.BadStatusLine("garbage")
    _keyed_setup(monkeypatch, broken)
    got = source_fetch.fetch_small(
        "https://api.github.com/x", allowed_hosts={"api.github.com"},
        max_bytes=10, service="code_search", honour_robots=False,
        key_id="github", resolver=lambda h: PUBLIC)
    assert got["reason"] == "bad_answer"
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("failed", "bad_answer")


# -- the honest word when the robots file cannot be read ----------------------

def _robots_call(url="https://api.github.com/a", **kw):
    kw.setdefault("allowed_hosts", {"api.github.com"})
    kw.setdefault("max_bytes", 1000)
    kw.setdefault("service", "code_search")
    return source_fetch.fetch_small(url, honour_robots=True, **kw)


def test_robots_on_by_default_still_reports_the_switch_off():
    outside_switches.set_switch("code_search", False, by="owner")
    wire = Wire({})
    got = _robots_call(transport=wire, resolver=lambda h: PUBLIC)
    assert got["reason"] == "switched_off" and wire.calls == []
    assert got["body"] is None


def test_robots_on_by_default_still_reports_an_inside_address():
    wire = Wire({})
    got = _robots_call(transport=wire, resolver=lambda h: ["10.0.0.1"])
    assert got["reason"] == "inside_address" and wire.calls == []


def test_robots_on_by_default_still_reports_no_dns_answer():
    wire = Wire({})
    got = _robots_call(transport=wire, resolver=lambda h: [])
    assert got["reason"] == "cannot_look_up" and wire.calls == []


def test_robots_on_by_default_still_reports_offline():
    calls = []

    def offline(url, headers, timeout):
        calls.append(url)
        raise ConnectionRefusedError("offline")

    got = _robots_call(transport=offline, resolver=lambda h: PUBLIC)
    assert got["reason"] == "no_answer"
    assert calls == ["https://api.github.com/robots.txt"]  # never the page itself


def test_a_robots_file_that_breaks_off_is_no_answer_not_a_rule():
    class Broken(Resp):
        def read(self, n=-1):
            raise ConnectionResetError("cut")

    wire = Wire({"https://api.github.com/robots.txt": Broken(200, b"")})
    got = _robots_call(transport=wire, resolver=lambda h: PUBLIC)
    assert got["reason"] == "no_answer"
    (line,) = _lines()
    assert (line["what"], line["outcome"], line["code"]) == (
        "robots_rules", "failed", "no_answer")
    # Not remembered: the next try asks for the robots file again.
    got = _robots_call(transport=wire, resolver=lambda h: PUBLIC)
    assert [c["url"] for c in wire.calls].count(
        "https://api.github.com/robots.txt") == 2


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_an_error_answer_for_the_robots_file_stays_a_robots_rule(status):
    wire = Wire({"https://api.github.com/robots.txt": Resp(status, b"")})
    got = _robots_call(transport=wire, resolver=lambda h: PUBLIC)
    assert got["reason"] == "robots_rule"
    assert [c["url"] for c in wire.calls] == ["https://api.github.com/robots.txt"]


def test_a_missing_robots_file_lets_the_page_through():
    url = "https://api.github.com/a"
    wire = Wire({url: Resp(200, b"page")})   # robots.txt answers 404
    got = _robots_call(url, transport=wire, resolver=lambda h: PUBLIC)
    assert got["reason"] == "ok" and got["body"] == b"page"


def test_a_cached_robots_rule_is_still_a_robots_rule():
    wire = Wire({"https://api.github.com/robots.txt":
                 Resp(200, b"User-agent: *\nDisallow: /a\n")})
    assert _robots_call(transport=wire, resolver=lambda h: PUBLIC)["reason"] == "robots_rule"
    assert _robots_call(transport=wire, resolver=lambda h: PUBLIC)["reason"] == "robots_rule"
    assert len(wire.calls) == 1


# -- header names are case-insensitive on every answer -------------------------

@pytest.mark.parametrize("name", ["Location", "location", "LOCATION", "lOcAtIoN"])
def test_a_keyed_redirect_is_followed_whatever_the_header_case(github_key, name):
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(301, b"", {name: b}), b: Resp(200, b"{}")})
    got = _call(a, wire, key_id="github")
    assert got["reason"] == "ok" and got["body"] == b"{}"
    assert [c["url"] for c in wire.calls] == [a, b]
    assert "Authorization" in wire.calls[0]["headers"]
    assert "Authorization" not in wire.calls[1]["headers"]
    text = open(egress_ledger.log_path(), encoding="utf-8").read()
    assert github_key not in text


@pytest.mark.parametrize("name", ["location", "LOCATION"])
def test_an_unkeyed_redirect_is_followed_whatever_the_header_case(name):
    a = "https://api.github.com/repos/old/name"
    b = "https://api.github.com/repositories/1"
    wire = Wire({a: Resp(302, b"", {name: b}), b: Resp(200, b"{}")})
    got = _call(a, wire)
    assert got["reason"] == "ok" and got["body"] == b"{}"


def test_a_keyed_redirect_off_the_list_is_still_refused_whatever_the_case(github_key):
    a = "https://api.github.com/repos/old/name"
    wire = Wire({a: Resp(301, b"", {"location": "https://evil.example/x"})})
    got = _call(a, wire, key_id="github")
    assert got["reason"] == "host" and got["body"] is None
    assert [c["url"] for c in wire.calls] == [a]


def test_header_of_prefers_the_exact_spelling_and_ignores_non_text_names():
    class R:
        headers = {1: "x", "Content-Length": "5", "content-length": "9"}
    assert source_fetch._header_of(R(), "Content-Length") == "5"  # noqa: SLF001
    assert source_fetch._header_of(R(), "CONTENT-LENGTH") == "5"  # noqa: SLF001
    assert source_fetch._header_of(R(), "Location") is None  # noqa: SLF001


def test_the_module_imports_http_client_once():
    import inspect
    text = inspect.getsource(source_fetch)
    assert [line.strip() for line in text.splitlines()].count("import http.client") == 1
