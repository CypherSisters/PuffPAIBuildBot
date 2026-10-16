"""Live: nothing leaves the owner's app frame by itself.

A hostile app page, framed exactly as
WebAppFrame frames it, made Edge 154 and Chrome 153 send
``GET /prerender?d=SECRET`` to any host through ``<link rel=prerender>``.
This runs that same page, then every hint and frame route one at a time (the
browser runs one prerender per frame, so a combined page can hide one), plus
a service worker, in each browser on this computer, all on 127.0.0.1.

Fails when any listener gets anything at all: a request (page data), a
bare connection with no request (a move of the page or of a
frame to another address opened one; the guard now cancels moves away and
no frame is ever made), or a WebRTC packet (UDP). The same routes, and the
runner secret's scope, are in ``scripts/check_app_frame_leaks.py``.

Run with ``PUFFPAI_R3_LIVE=1`` (and ``PUFFPAI_R3_PROOF_DIR`` to keep the record).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "webapps"))
import frame_leak_probe as probe  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("PUFFPAI_R3_LIVE") != "1",
                       reason="live browser test: set PUFFPAI_R3_LIVE=1"),
    pytest.mark.skipif(sys.platform != "win32", reason="written for Windows"),
]

BROWSERS = {
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}
_RECORD: list[dict] = []


def _browsers():
    return [pytest.param(name, path, id=name) for name, path in BROWSERS.items()]


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(probe._free()))
    monkeypatch.setenv("PUFFPAI_DASHBOARD_PORT", str(probe._free()))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    from puffpai.webapps import host
    host.start(None)
    yield
    host.stop()


def _keep(entry: dict) -> None:
    _RECORD.append(entry)
    out = os.environ.get("PUFFPAI_R3_PROOF_DIR")
    if out and os.path.isdir(out):
        with open(os.path.join(out, "fx-choice_owner_frame_live.json"), "w", encoding="utf-8") as fh:
            json.dump(_RECORD, fh, indent=1)


@pytest.mark.parametrize("name,browser", _browsers())
def test_the_acceptance_page_sends_nothing(scratch, name, browser):
    if not os.path.isfile(browser):
        pytest.skip(f"{name} is not on this computer")
    lis = probe.Listeners(probe.EVIL_TCP, probe.EVIL_UDP)
    try:
        probe.run(browser, probe.evil_page(lis.ports), {}, seconds=13)
    finally:
        lis.close()
    hits = dict(lis.hits)
    _keep({"browser": name, "page": "acceptance evil page", "hits": hits})
    # nothing at all: no request, no bare connection, no WebRTC packet
    assert not hits, hits


@pytest.mark.parametrize("name,browser", _browsers())
@pytest.mark.parametrize("vector", sorted(probe.VECTORS))
def test_each_route_alone_reaches_nothing(scratch, name, browser, vector):
    if not os.path.isfile(browser):
        pytest.skip(f"{name} is not on this computer")
    lis = probe.Listeners([vector])
    try:
        page, files = probe.vector_page([vector], lis.ports)
        probe.run(browser, page, files, seconds=9)
    finally:
        lis.close()
    _keep({"browser": name, "vector": vector, "hits": dict(lis.hits)})
    assert not lis.hits, (vector, lis.hits)
