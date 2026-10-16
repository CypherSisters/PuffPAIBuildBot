"""``/__puffpai/*`` accepts only same-origin or runner requests with the page nonce.

Every state-changing call (``/out`` any method, storage PUT, report POST) is
tried cross-site, with a hostile ``Origin``, with ``Origin: null``, and
without (or with a wrong, or another app's) nonce: all refused. No answer
ever carries ``Access-Control-Allow-Origin``.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit as hostkit  # noqa: E402

from puffpai.webapps import host, mirror  # noqa: E402

APP = "shelf-keeper-abcdefgh"
OTHER = "chore-board-bcdefghi"


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(hostkit.PORT))
    hostkit.place_app(APP)
    hostkit.place_app(OTHER, "row_select")
    host.start(None)
    yield APP
    host.stop()


def _h(label=APP):
    return f"{label}.localhost:{hostkit.PORT}"


def _origin(label=APP):
    return f"http://{label}.localhost:{hostkit.PORT}"


STATE_CHANGING = [
    ("GET", "/__puffpai/out?u=https%3A%2F%2Fapi.example.org%2Fx", None),
    ("POST", "/__puffpai/out?u=https%3A%2F%2Fapi.example.org%2Fx", b"{}"),
    ("PUT", "/__puffpai/storage", json.dumps({"base": 0, "data": {"k": "v"}}).encode()),
    ("POST", "/__puffpai/report", json.dumps({"csp-report": {"blocked-uri": "https://x.example.org/"}}).encode()),
]


def _no_acao(raw_headers):
    return all(k.lower() != "access-control-allow-origin" for k, _ in raw_headers)


@pytest.mark.parametrize("method,path,body", STATE_CHANGING)
def test_cross_site_and_hostile_origins_are_refused(served, method, path, body):
    _, _, _, nonce = hostkit.frame_get(APP)
    assert nonce
    attempts = [
        {"Sec-Fetch-Site": "cross-site", "Origin": _origin(), "X-PuffPAI-Nonce": nonce},
        {"Sec-Fetch-Site": "same-site", "Origin": _origin(), "X-PuffPAI-Nonce": nonce},
        {"Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example", "X-PuffPAI-Nonce": nonce},
        {"Sec-Fetch-Site": "same-origin", "Origin": "null", "X-PuffPAI-Nonce": nonce},
        {"Sec-Fetch-Site": "same-origin", "Origin": _origin(OTHER), "X-PuffPAI-Nonce": nonce},
        {"Origin": _origin(), "X-PuffPAI-Nonce": nonce},                     # no Sec-Fetch-Site
        {"Sec-Fetch-Site": "same-origin", "Origin": _origin()},               # no nonce
        {"Sec-Fetch-Site": "same-origin", "Origin": _origin(), "X-PuffPAI-Nonce": "0" * 32},
        {"Sec-Fetch-Site": "same-origin", "Origin": _origin(), "X-PuffPAI-Nonce": nonce + "x"},
    ]
    if method == "GET":
        # a GET needs no Origin, but still the site and the nonce
        attempts = [a for a in attempts if a.get("Origin") in (None, _origin())]
    for hdrs in attempts:
        status, _, _, raw = hostkit.request(method, path, host_header=_h(), headers=hdrs, body=body)
        assert status == 403, (method, path, hdrs)
        assert _no_acao(raw)


def test_another_apps_nonce_does_not_work(served):
    _, _, _, other_nonce = hostkit.frame_get(OTHER)
    body = json.dumps({"base": 0, "data": {"k": "v"}}).encode()
    status, _, _, _ = hostkit.request("PUT", "/__puffpai/storage", host_header=_h(),
                                      headers={"Sec-Fetch-Site": "same-origin", "Origin": _origin(),
                                               "X-PuffPAI-Nonce": other_nonce}, body=body)
    assert status == 403
    assert mirror.get(APP)["v"] == 0


def test_the_pages_own_calls_work_and_never_carry_acao(served):
    _, _, _, nonce = hostkit.frame_get(APP)
    ok = {"Sec-Fetch-Site": "same-origin", "Origin": _origin(), "X-PuffPAI-Nonce": nonce}
    body = json.dumps({"base": 0, "data": {"shelf.v1": "{\"items\":[]}"}}).encode()
    status, _, data, raw = hostkit.request("PUT", "/__puffpai/storage", host_header=_h(), headers=ok, body=body)
    assert status == 200 and json.loads(data) == {"v": 1} and _no_acao(raw)
    status, _, data, raw = hostkit.request("GET", "/__puffpai/storage", host_header=_h(),
                                           headers={"Sec-Fetch-Site": "same-origin"})
    assert status == 200 and json.loads(data)["v"] == 1 and _no_acao(raw)
    status, _, _, raw = hostkit.request("POST", "/__puffpai/report", host_header=_h(), headers=ok,
                                        body=b'{"csp-report":{"blocked-uri":"https://x.example.org/a"}}')
    assert status == 204 and _no_acao(raw)
    # outside calls are refused (switched off), and logged by host only. With
    # the real Bridge, an app that never had its
    # sites put on the Bridge is refused as unknown there.
    status, _, data, raw = hostkit.request("GET", "/__puffpai/out?u=https%3A%2F%2Fx.example.org%2F",
                                           host_header=_h(), headers=ok)
    assert status == 403 and _no_acao(raw)
    assert json.loads(data)["refused"] in ("switched_off", "unknown_app")
    watch = open(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "watch.jsonl"),
                 encoding="utf-8").read()
    assert "x.example.org" in watch and "/a" not in watch


def test_reading_storage_needs_same_origin_or_the_runner(served):
    for hdrs in ({}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                 {"Sec-Fetch-Site": "none"}):
        status, _, _, raw = hostkit.request("GET", "/__puffpai/storage", host_header=_h(), headers=hdrs)
        assert status == 403 and _no_acao(raw)
    status, _, _, _ = hostkit.request("GET", "/__puffpai/storage", host_header=_h(),
                                      headers={host.RUNNER_HEADER: host.runner_secret()})
    assert status == 200


def test_preflight_gets_no_cors_answer(served):
    status, _, _, raw = hostkit.request("OPTIONS", "/__puffpai/storage", host_header=_h(),
                                        headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "PUT"})
    assert status == 403 and _no_acao(raw)


def test_files_answer_only_the_runner(served):
    status, _, _, _ = hostkit.request("GET", "/__puffpai/files/", host_header=_h(),
                                      headers={"Sec-Fetch-Site": "same-origin"})
    assert status == 403
    status, _, _, _ = hostkit.request("GET", "/__puffpai/files/", host_header=_h(),
                                      headers={host.RUNNER_HEADER: host.runner_secret()})
    assert status == 404          # the folder feature is not in this build yet


def test_reports_are_limited_per_minute(served):
    _, _, _, nonce = hostkit.frame_get(APP)
    ok = {"Sec-Fetch-Site": "same-origin", "Origin": _origin(), "X-PuffPAI-Nonce": nonce}
    codes = [hostkit.request("POST", "/__puffpai/report", host_header=_h(), headers=ok, body=b"{}")[0]
             for _ in range(host.REPORTS_PER_MINUTE + 3)]
    assert codes[:host.REPORTS_PER_MINUTE] == [204] * host.REPORTS_PER_MINUTE
    assert set(codes[host.REPORTS_PER_MINUTE:]) == {429}


def test_nonces_expire(served, monkeypatch):
    _, _, _, nonce = hostkit.frame_get(APP)
    assert host.nonce_ok(APP, nonce)
    assert not host.nonce_ok(OTHER, nonce)
    import time as _time
    real = _time.time
    monkeypatch.setattr(host.time, "time", lambda: real() + host.NONCE_TTL_S + 5)
    assert not host.nonce_ok(APP, nonce)
