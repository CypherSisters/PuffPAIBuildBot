"""The door layer must know when a credential is on the wire — and refuse one.

The gap this file encodes: ``egress_request`` forwarded ``**kwargs`` straight
to ``requests.request``, so any in-process caller could attach an
``Authorization`` header and the door layer would carry it through redirect
hops without ever knowing authority was leaving the box. A door said *where* a
request could go and nothing about *what it could prove on arrival*.

Two properties are tested here:

* **No caller may attach authority.** Credential-shaped headers, ``auth=`` and
  ``cookies=`` are refused before a socket opens, and the refusal never echoes
  the value.
* **The one sanctioned path is narrow.** A ``_BoundCredential`` cannot be
  built outside ``puffpai.security``, goes only to a host it is bound to, only
  over https, never follows a redirect, and never survives in the response's
  record of the request.

As in the other door tests, the absence of a socket is what passes: the
transport stubs record calls, and a refused call that still connected fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai import build_manifest, build_posture
from puffpai.security import egress_doors

SECRET = "Bearer s3cr3t-value-never-logged"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OTHER_HOST_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


# ── harness ──────────────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, config_dir: str):
        self.CONFIG_DIR = config_dir
        self.REPO_ROOT = config_dir
        self.DATA_DIR = config_dir


@pytest.fixture
def build(tmp_path, monkeypatch):
    """Write a manifest + config into a temp CONFIG_DIR and point the doors there."""
    def _make(run_egress: str = "enumerated",
              doors: dict | None = None) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        posture = build_posture.posture_manifest_block(
            build_posture.assess_posture("online", run_egress, []))
        (cfg_dir / "build_manifest.json").write_text(
            json.dumps({"schema": build_manifest.SCHEMA, "posture": posture}),
            encoding="utf-8")
        (cfg_dir / "puffpai_config.json").write_text(
            json.dumps({"egress_doors": doors or {"weather": True,
                                                  "air_quality": True}}),
            encoding="utf-8")
        monkeypatch.setattr("puffpai.config.get_config",
                            lambda **kw: _Cfg(str(cfg_dir)))
        egress_doors.reset_cache()
        return cfg_dir

    yield _make
    egress_doors.reset_cache()


@pytest.fixture(autouse=True)
def _no_stray_env(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)


class _Req:
    """Stand-in for ``response.request``: keeps the headers requests would."""

    def __init__(self, headers: dict):
        self.headers = dict(headers)


class _Resp:
    def __init__(self, headers: dict, status_code: int = 200,
                 location: str = ""):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.request = _Req(headers)

    def json(self):
        return {}

    def raise_for_status(self):
        return None


@pytest.fixture
def wire(monkeypatch):
    """Record every request that reaches the transport. Returns the list.

    ``wire.responses`` can be pre-loaded to script redirects.
    """
    import requests

    class _Wire(list):
        scripted: list

    seen = _Wire()
    scripted: list[_Resp] = []
    seen.scripted = scripted

    def _fake(method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        seen.append({"method": method, "url": url, "headers": headers})
        if scripted:
            resp = scripted.pop(0)
            resp.request = _Req(headers)
            return resp
        return _Resp(headers)

    def _fatal(*args, **kwargs):
        raise AssertionError("an outbound call bypassed egress_request")

    monkeypatch.setattr(egress_doors, "_send", _fake)
    monkeypatch.setattr(requests, "get", _fatal)
    monkeypatch.setattr(requests, "post", _fatal)
    return seen


#: A caller that sits inside the security package, the way the key broker
#: (puffpai.security.keys) does. The factory refuses any other caller, so the
#: tests mint from a frame whose module name is inside the package.
_INSIDE = {"__name__": "puffpai.security._seam_test", "__builtins__": __builtins__}
exec(compile("def mint(bind, header, value, hosts):\n"
             "    return bind(header, value, hosts)\n",
             "<seam-test>", "exec"), _INSIDE)


def _bound(hosts=("api.open-meteo.com",), header="Authorization",
           value=SECRET):
    """Mint a credential the way the broker does: from inside the package."""
    return _INSIDE["mint"](egress_doors._bind_credential, header, value, hosts)


def test_the_factory_is_refused_outside_the_security_package() -> None:
    """Calling the factory from here is not a way round the constructor's
    own guard (the constructor only sees egress_doors)."""
    with pytest.raises(PermissionError) as exc:
        egress_doors._bind_credential("Authorization", SECRET,
                                      ("api.open-meteo.com",))
    assert SECRET not in str(exc.value)


def test_a_key_may_only_travel_in_a_header() -> None:
    exec(compile("def mint_q(bind, value):\n"
                 "    return bind('api_key', value, ('api.open-meteo.com',),"
                 " placement='query')\n", "<seam-test>", "exec"), _INSIDE)
    with pytest.raises(ValueError) as exc:
        _INSIDE["mint_q"](egress_doors._bind_credential, SECRET)
    assert SECRET not in str(exc.value)
    assert egress_doors.CREDENTIAL_PLACEMENTS == ("header",)


# ── no caller may attach authority ───────────────────────────────────────────

BANNED = [
    "Authorization", "authorization", "Proxy-Authorization", "Cookie",
    "X-Api-Key", "X-Auth-Token", "x-session-token", "Api-Key",
    "X-Client-Secret", "X-CSRF-Token", "Set-Cookie", "MyAuthThing",
]


@pytest.mark.parametrize("name", BANNED)
def test_a_caller_supplied_credential_header_is_refused(build, wire, name) -> None:
    build()
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", WEATHER_URL,
                                headers={name: SECRET})
    assert wire == [], "a refused header still opened a socket"
    assert name in exc.value.reason          # the operator learns which header
    assert SECRET not in str(exc.value)      # ...and never its value
    assert SECRET not in exc.value.reason
    assert SECRET not in exc.value.user_message
    assert "attach a sign-in" in exc.value.user_message


def test_the_refusal_covers_post_and_the_generic_wrapper(build, wire) -> None:
    build()
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_request("weather", "POST", WEATHER_URL,
                                    headers={"Authorization": SECRET})
    assert wire == []


def test_an_auth_kwarg_is_refused(build, wire) -> None:
    build()
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", WEATHER_URL,
                                auth=("user", "p4ssw0rd"))
    assert wire == []
    assert "p4ssw0rd" not in str(exc.value)
    assert "auth=" in exc.value.reason


def test_a_cookies_kwarg_is_refused(build, wire) -> None:
    build()
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", WEATHER_URL,
                                cookies={"session": "s3ss10n"})
    assert wire == []
    assert "s3ss10n" not in str(exc.value)


def test_headers_that_cannot_be_screened_are_refused(build, wire) -> None:
    """A sequence of pairs is legal in requests and unscreenable here."""
    build()
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", WEATHER_URL,
                                headers=[("Authorization", SECRET)])
    assert wire == []


def test_ordinary_headers_still_go_through(build, wire) -> None:
    """The screen must not break the widgets that send a User-Agent."""
    build()
    egress_doors.egress_get("weather", WEATHER_URL,
                            headers={"User-Agent": "PuffPAI/1.0",
                                     "Accept": "application/json"})
    assert len(wire) == 1
    assert wire[0]["headers"]["User-Agent"] == "PuffPAI/1.0"
    assert "Authorization" not in wire[0]["headers"]


# ── the seam itself ──────────────────────────────────────────────────────────

def test_nothing_outside_the_security_package_can_build_a_credential() -> None:
    with pytest.raises(PermissionError) as exc:
        egress_doors._BoundCredential("Authorization", SECRET,
                                      ("api.open-meteo.com",))
    assert SECRET not in str(exc.value)


def test_a_forged_credential_object_is_refused(build, wire) -> None:
    """``object.__new__`` skips ``__init__``; it also skips the issue register."""
    build()
    forged = object.__new__(egress_doors._BoundCredential)
    forged._header = "Authorization"
    forged._value = SECRET
    forged._hosts = frozenset({"api.open-meteo.com"})
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", WEATHER_URL, credential=forged)
    assert wire == []
    assert "not issued" in exc.value.reason


def test_a_lookalike_credential_class_is_refused(build, wire) -> None:
    build()

    class Lookalike:
        header_name = "Authorization"
        hosts = frozenset({"api.open-meteo.com"})
        _value = SECRET

    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", WEATHER_URL,
                                credential=Lookalike())
    assert wire == []


def test_a_credential_never_reprs_its_value() -> None:
    cred = _bound()
    assert SECRET not in repr(cred)
    assert SECRET not in str(cred)
    assert cred.header_name == "Authorization"


def test_credential_hosts_are_exact_and_idn_normalised() -> None:
    with pytest.raises(ValueError):
        _bound(hosts=("*.open-meteo.com",))
    with pytest.raises(ValueError):
        _bound(hosts=())
    with pytest.raises(ValueError):
        _bound(hosts="api.open-meteo.com")  # a string is not a host list
    with pytest.raises(ValueError):
        _bound(value="")
    with pytest.raises(ValueError):
        _bound(header="Authorization: x")   # header injection
    with pytest.raises(ValueError):
        _bound(value="tok\r\nX-Evil: 1")

    cred = _bound(hosts=("EXÄMPLE.com.", "api.open-meteo.com"))
    assert "xn--exmple-cua.com" in cred.hosts
    assert "EXÄMPLE.com." not in cred.hosts


# ── what a credential is allowed to do ───────────────────────────────────────

def test_a_bound_credential_reaches_its_bound_host(build, wire) -> None:
    build()
    resp = egress_doors.egress_get("weather", WEATHER_URL,
                                   credential=_bound())
    assert len(wire) == 1
    assert wire[0]["headers"]["Authorization"] == SECRET
    # ...and is gone from what the caller can read back off the response.
    assert "Authorization" not in resp.request.headers


def test_a_credential_is_refused_for_an_unbound_host(build, wire) -> None:
    build()
    cred = _bound(hosts=("api.open-meteo.com",))
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("air_quality", OTHER_HOST_URL,
                                credential=cred)
    assert wire == [], "an unbound host still received a request"
    assert SECRET not in str(exc.value)
    assert "not registered for" in exc.value.user_message


def test_a_credential_is_not_matched_by_suffix(build, wire) -> None:
    """``open-meteo.com`` must not authorise ``api.open-meteo.com``."""
    build()
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", WEATHER_URL,
                                credential=_bound(hosts=("open-meteo.com",)))
    assert wire == []


def test_a_redirect_under_a_credential_is_a_refusal_not_a_hop(build, wire) -> None:
    """The header is offered once, to the host it was bound to, and no further."""
    build()
    wire.scripted.append(_Resp({}, status_code=302,
                               location="https://api.open-meteo.com/v2/moved"))
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", WEATHER_URL, credential=_bound())
    assert len(wire) == 1, "the credential followed a redirect"
    assert wire[0]["url"] == WEATHER_URL
    assert SECRET not in str(exc.value)
    assert "redirected" in exc.value.reason


def test_a_redirect_target_never_sees_the_header(build, wire) -> None:
    """Same-host redirect: even an in-door hop gets nothing under a credential."""
    build()
    wire.scripted.append(_Resp({}, status_code=307,
                               location="https://api.open-meteo.com/v1/other"))
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", WEATHER_URL, credential=_bound())
    assert [c["url"] for c in wire] == [WEATHER_URL]
    assert all(SECRET not in json.dumps(c["headers"]) or c["url"] == WEATHER_URL
               for c in wire)


def test_a_credential_is_refused_over_plain_http(build, wire) -> None:
    build()
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather",
                                "http://api.open-meteo.com/v1/forecast",
                                credential=_bound())
    assert wire == []


def test_the_credential_rule_refuses_http_on_its_own(build, wire, monkeypatch) -> None:
    """Defence in depth: not resting on the door's own https rule.

    The door already refuses http, so that rule is neutralised here to prove
    the credential branch would refuse a downgrade by itself.
    """
    build()
    monkeypatch.setattr(egress_doors, "_check_url",
                        lambda d, url, hop_host="": "api.open-meteo.com")
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather",
                                "http://api.open-meteo.com/v1/forecast",
                                credential=_bound())
    assert wire == []
    assert "not an encrypted connection" in exc.value.user_message


def test_a_shut_door_still_refuses_a_credentialled_call(build, wire) -> None:
    build(run_egress="island")
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", WEATHER_URL, credential=_bound())
    assert wire == []


def test_the_seam_is_not_exported() -> None:
    """The broker does not exist yet, and no public name hands one out."""
    for name in ("_BoundCredential", "_bind_credential"):
        assert name not in egress_doors.__all__
