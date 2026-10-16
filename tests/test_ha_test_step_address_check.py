"""The Home Assistant Test step obeys the internet setting.

The Developer Brain page's Test button posts the typed Home Assistant password
to /setup/ha/test, which probed any address with the token in the
Authorization header; only Save (/setup/ha) was checked. Both routes now share
one address check, and a refused address is never contacted. A cloud
provider's instance-data address (169.254.169.254 and kin), which sits in
link-local space, is refused for a configured service under every setting.

No network: every probe and every name lookup is stubbed.
"""
from __future__ import annotations

import os

import pytest

from puffpai import build_manifest, setup_ops
from puffpai.config import get_config
from puffpai.security import lan_posture

TOKEN = "llat-typed-in-the-brain-page"


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: {"posture": {"run_egress": "island"}})

    def _no_dns(host, timeout):
        raise OSError("no network in unit tests")

    monkeypatch.setattr(lan_posture, "_resolve", _no_dns)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


@pytest.fixture
def probes(monkeypatch):
    """Every request the Test step would send, recorded instead of sent."""
    sent: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"version": "2026.9", "location_name": "Home"}

    def _get(url, headers=None, **kwargs):
        sent.append((url, dict(headers or {})))
        return _Resp()

    monkeypatch.setattr(setup_ops.requests, "get", _get)
    return sent


@pytest.fixture
def client():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    from puffpai import api_server
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.mark.parametrize("url", [
    "http://192.0.2.55:8123",        # documentation range, not a home
    "http://example.org",            # a public name
    "http://8.8.8.8:8123",           # a public address
    "http://169.254.169.254",        # a cloud provider's instance data
    "http://[::ffff:169.254.169.254]",
])
def test_the_test_step_never_sends_the_password_outside(client, scratch,
                                                        probes, url):
    resp = client.post("/setup/ha/test", json={"url": url, "token": TOKEN},
                       headers=_auth())
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["ok"] is False and body["refused"] is True
    assert body["message"] and body["message"] == body["error"]
    assert probes == [], "the address was contacted with the password"
    assert TOKEN not in resp.get_data(as_text=True)


@pytest.mark.parametrize("url", ["http://192.168.1.50:8123",
                                 "http://169.254.10.10:8123"])
def test_the_test_step_still_reaches_a_home_address(client, scratch, probes,
                                                    url):
    resp = client.post("/setup/ha/test", json={"url": url, "token": TOKEN},
                       headers=_auth())
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["ok"] is True
    assert [u for u, _ in probes] == [f"{url}/api/config"]
    assert TOKEN not in resp.get_data(as_text=True)


def test_save_refuses_the_cloud_instance_data_address(client, scratch):
    resp = client.post("/setup/ha", json={"url": "http://169.254.169.254",
                                          "token": TOKEN}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert setup_ops.ha_configured() is False


def test_a_name_resolving_to_instance_data_is_refused():
    found = lan_posture.check_url(
        "home_assistant", "http://ha.local:8123", "island", resolve=True,
        resolver=lambda h, t: ["169.254.169.254"])
    assert found is not None
    assert "169.254.169.254" in found.user_message


def test_ordinary_link_local_stays_a_home_address():
    assert lan_posture.classify_host("169.254.169.254") == lan_posture.PRIVATE
    assert lan_posture.check_url("home_assistant", "http://169.254.1.1:8123",
                                 "island") is None


def test_the_brain_page_shows_the_refusal_words():
    """The Brain page's Home Assistant panel is now the shared "Connect my
    Home Assistant" panel: it has no separate Test
    step; the one Connect step shows the server's own words for a refusal."""
    app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
        __file__))), "dashboard", "app")
    with open(os.path.join(app, "dashboard", "developer", "brain", "page.tsx"),
              encoding="utf-8") as fh:
        assert "<ConnectHomeAssistant" in fh.read()
    with open(os.path.join(app, "_components", "ToolServers",
                           "ConnectHomeAssistant.tsx"), encoding="utf-8") as fh:
        src = fh.read()
    step = src[src.index("const connect = async"):src.index("const remove = async")]
    assert "r.data?.message" in step
