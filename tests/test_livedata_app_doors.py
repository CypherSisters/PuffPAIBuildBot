"""Each feed host is a real named outside service: shut until the owner says yes.

The absence of a socket is what passes these, not a nice message:
``no_network`` makes any request or address lookup fail the test.
"""

from __future__ import annotations

import json
import os

import pytest

from livedata_support import (FakeResponse, fake_network, feed_manifest,
                              install, no_network, set_posture)
from puffpai import feeds
from puffpai.security import egress_doors


@pytest.fixture(autouse=True)
def _clean_doors(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    monkeypatch.delenv(egress_doors.BUILD_PROBE_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def test_installing_registers_a_shut_service(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    d = egress_doors.door("app:skyshots:feed")
    assert d is not None and d.default is False
    # Its data site is a full site; its picture site is pictures only (f-sec).
    assert d.hosts == ("api.example.org",)
    assert d.picture_hosts == ("img.example.org",)
    allowed, _ = egress_doors.egress_allowed(d.id)
    assert allowed is False
    assert feeds.service_state("skyshots")["state"] == "not_asked"


def test_a_shut_service_opens_no_socket(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    no_network(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.app_egress_get("skyshots", "https://api.example.org/v1/latest")
    state = feeds.refresh("skyshots")
    assert state["card"] is None and state["status"] == "not_asked"


def test_the_owners_yes_opens_it_and_no_shuts_it(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    state = feeds.set_allowed("skyshots", True)
    assert state["state"] == "open"
    assert egress_doors.egress_allowed("app:skyshots:feed")[0] is True
    state = feeds.set_allowed("skyshots", False)
    assert state["state"] == "said_no"
    assert egress_doors.egress_allowed("app:skyshots:feed")[0] is False


def test_the_yes_is_kept_in_the_state_folder_not_the_checkout():
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    path = egress_doors.app_consent_path()
    from puffpai.config import get_state_dir
    assert path.startswith(get_state_dir())
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["schema"] == egress_doors.APP_CONSENT_SCHEMA


def test_a_yes_does_not_survive_the_app_pointing_somewhere_else():
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    moved = feed_manifest()
    moved["feed"]["service"]["host"] = "api.other-example.org"
    moved["feed"]["url"] = "https://api.other-example.org/v1/latest"
    install(moved)
    assert egress_doors.egress_allowed("app:skyshots:feed")[0] is False
    assert feeds.service_state("skyshots")["state"] == "not_asked"


@pytest.mark.parametrize("posture", ["island", "sealed"])
def test_a_bot_set_to_stay_offline_stays_offline_even_after_a_yes(posture, monkeypatch):
    set_posture(posture)
    install(feed_manifest())
    state = feeds.set_allowed("skyshots", True)
    assert state["state"] == "bot_offline"
    assert "stay off the internet" in state["message"]
    no_network(monkeypatch)
    assert feeds.refresh("skyshots")["card"] is None


def test_the_operator_switch_shuts_every_app_service(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    monkeypatch.setenv(egress_doors.APPS_KILL_ENV, "off")
    assert egress_doors.egress_allowed("app:skyshots:feed")[0] is False
    assert feeds.service_state("skyshots")["state"] == "switched_off"


def test_an_unreadable_consent_file_means_shut():
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    with open(egress_doors.app_consent_path(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    egress_doors.reset_cache()
    assert egress_doors.egress_allowed("app:skyshots:feed")[0] is False


def test_uninstalling_drops_the_service():
    from puffpai import app_registry
    set_posture("enumerated")
    install(feed_manifest())
    assert egress_doors.app_door("skyshots") is not None
    import shutil
    shutil.rmtree(os.path.join(app_registry.user_apps_dir(), "skyshots"))
    app_registry.rescan()
    assert egress_doors.app_door("skyshots") is None


def test_an_open_app_service_refuses_other_hosts_ports_and_lan_answers(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    net = fake_network(monkeypatch)
    for url in ("https://evil.example.com/x", "http://api.example.org/x",
                "https://api.example.org:8443/x"):
        with pytest.raises(egress_doors.EgressBlocked):
            egress_doors.app_egress_get("skyshots", url)
    net.addresses = ["192.168.1.20"]
    with pytest.raises(egress_doors.EgressBlocked) as caught:
        egress_doors.app_egress_get("skyshots", "https://api.example.org/x")
    assert "this network" in caught.value.user_message
    assert net.calls == []


def test_an_open_app_service_rechecks_every_redirect(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/x",
           FakeResponse(302, b"", headers={"Location": "https://evil.example.com/y"}))
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.app_egress_get("skyshots", "https://api.example.org/x")
    assert [c["url"] for c in net.calls] == ["https://api.example.org/x"]
    assert net.calls[0]["allow_redirects"] is False


def test_a_manifest_can_never_register_an_address_or_local_name():
    egress_doors.register_app_doors([
        ("bad1", {"hosts": ["127.0.0.1"], "name": "x"}),
        ("bad2", {"hosts": ["printer.local"], "name": "x"}),
        ("bad3", {"hosts": ["0x7f000001"], "name": "x"}),
        ("bad4", {"hosts": ["a.example.org", "b.example.org", "c.example.org"]}),
        ("Bad_Id", {"hosts": ["a.example.org"]}),
    ])
    assert egress_doors.app_doors() == ()


# ── the build-time sample look ──────────────────────────────────────────────

def test_the_probe_refuses_an_offline_build_and_the_operator_switch(monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=False)
    monkeypatch.setenv(egress_doors.BUILD_PROBE_ENV, "off")
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True)


@pytest.mark.parametrize("url, host", [
    ("http://api.example.org/x", "api.example.org"),
    ("https://api.example.org:8443/x", "api.example.org"),
    ("https://other.example.org/x", "api.example.org"),
    ("https://u:p@api.example.org/x", "api.example.org"),
    ("https://127.0.0.1/x", "127.0.0.1"),
])
def test_the_probe_refuses_anything_but_https_on_the_named_host(url, host, monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get(url, host=host, build_online=True)


def test_the_probe_never_follows_a_redirect_and_caps_the_read(monkeypatch):
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/r",
           FakeResponse(301, b"", headers={"Location": "https://evil.example.com/"}))
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get("https://api.example.org/r",
                                     host="api.example.org", build_online=True)
    net.on("https://api.example.org/big", FakeResponse(200, b"x" * 70000))
    got = egress_doors.build_probe_get("https://api.example.org/big",
                                       host="api.example.org", build_online=True,
                                       max_bytes=50000)
    assert got["truncated"] is True and got["bytes"] == 50000
    assert all(c["allow_redirects"] is False for c in net.calls)
    assert all("User-Agent" in c["headers"] for c in net.calls)
    assert not any(k.lower() in ("authorization", "cookie")
                   for c in net.calls for k in c["headers"])


def test_the_probe_refuses_a_name_that_answers_with_a_lan_address(monkeypatch):
    net = fake_network(monkeypatch)
    net.addresses = ["10.0.0.5"]
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True)
    assert net.calls == []
