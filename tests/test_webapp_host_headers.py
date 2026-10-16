"""The app host: Host allowlist, frame only, exact headers, loopback only."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit as hostkit  # noqa: E402

from puffpai.webapps import headers, host, wording  # noqa: E402

APP = "shelf-keeper-abcdefgh"


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(hostkit.PORT))
    monkeypatch.setenv("PUFFPAI_DASHBOARD_PORT", "3003")
    hostkit.place_app(APP, extra={"app.js": "console.log(1);", "sub/page.html": "<html><head></head>x</html>"})
    host.start(None)
    yield APP
    host.stop()


def _h(label=APP):
    return f"{label}.localhost:{hostkit.PORT}"


def test_unknown_host_gets_421(served):
    for bad in ("evil.example:4381", "localhost:4381", "127.0.0.1:4381",
                f"other-app-abcdefgh.localhost:{hostkit.PORT}", f"{APP}.localhost:9999",
                f"{APP}.localhost", f"{APP}.localhost.evil.example:{hostkit.PORT}",
                f"x.{APP}.localhost:{hostkit.PORT}", "chk-aaaaaaaaaaaaaaaa.localhost:4381"):
        status, _, _, _ = hostkit.request("GET", "/", host_header=bad,
                                          headers={"Sec-Fetch-Dest": "iframe"})
        assert status == 421, bad
    status, _, _, _ = hostkit.request("GET", "/", host_header=None)
    assert status == 421
    status, _, _, _ = hostkit.request("GET", "/", host_header=None,
                                      headers={"Host": [_h(), "evil.example:4381"]})
    assert status == 421


def test_absolute_request_targets_are_refused(served):
    status, _, _, _ = hostkit.request("GET", f"http://{_h()}/", host_header=_h(),
                                      headers={"Sec-Fetch-Dest": "iframe"})
    assert status in (400, 404)


def test_top_level_document_is_refused_without_the_runner_secret(served):
    status, hdrs, body, _ = hostkit.request("GET", "/", host_header=_h(),
                                            headers={"Sec-Fetch-Dest": "document",
                                                     "Sec-Fetch-Site": "none"})
    assert status == 403
    assert body.decode().strip() == wording.FRAME_ONLY
    # a wrong secret is no better
    status, _, _, _ = hostkit.request("GET", "/", host_header=_h(),
                                      headers={"Sec-Fetch-Dest": "document",
                                               host.RUNNER_HEADER: "not-the-secret"})
    assert status == 403
    # the runner's own per-boot secret opens it
    status, _, body, _ = hostkit.request("GET", "/", host_header=_h(),
                                         headers={"Sec-Fetch-Dest": "document",
                                                  host.RUNNER_HEADER: host.runner_secret()})
    assert status == 200 and b"/__puffpai/shim.js" in body


def test_html_needs_a_frame_or_the_runner(served):
    # no browser headers at all (a script or a probe): refused too
    status, _, body, _ = hostkit.request("GET", "/", host_header=_h())
    assert status == 403 and body.decode().strip() == wording.FRAME_ONLY
    status, _, _, _ = hostkit.request("GET", "/sub/page.html", host_header=_h())
    assert status == 403
    status, _, text, nonce = hostkit.frame_get(APP)
    assert status == 200 and nonce and len(nonce) == 32
    assert text.index("window.__PUFFPAI__") < text.index("<h1>")
    # the app's own script and files are plain files
    status, hdrs, body, _ = hostkit.request("GET", "/app.js", host_header=_h())
    assert status == 200 and hdrs["content-type"].startswith("text/javascript")


def test_headers_and_csp_are_exact(served):
    status, hdrs, _, raw = hostkit.request("GET", "/app.js", host_header=_h())
    assert status == 200
    want = headers.app_headers(3003)
    for k, v in want.items():
        assert hdrs[k.lower()] == v
    csp = hdrs["content-security-policy"]
    assert "frame-ancestors http://localhost:3003 http://127.0.0.1:3003;" in csp
    assert "webrtc 'block'" in csp
    assert "connect-src 'self';" in csp and "default-src 'self';" in csp
    assert "form-action 'self';" in csp and "object-src 'none';" in csp
    assert csp.startswith("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:;")
    for name, _ in raw:
        assert name.lower() != "access-control-allow-origin"
    # every answer carries them, refusals included
    for path, h in (("/", _h()), ("/nope", _h())):
        _, hd, _, _ = hostkit.request("GET", path, host_header=h, headers={"Sec-Fetch-Dest": "iframe"})
        assert hd["content-security-policy"] == csp
        assert "access-control-allow-origin" not in hd


def test_only_get_and_head_for_app_files(served):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _, _, _ = hostkit.request(method, "/app.js", host_header=_h(), body=b"")
        assert status == 405


def test_binds_loopback_only(served):
    addr = host.bound_address()
    assert addr[0] == "127.0.0.1" and addr[1] == hostkit.PORT
    assert host.BIND_HOST == "127.0.0.1"
    assert host.origin_for(APP) == f"http://{APP}.localhost:{hostkit.PORT}"


def test_check_copies_are_served_until_dropped(served):
    label = host.new_check_copy(APP)
    status, _, _, _ = hostkit.request("GET", "/app.js", host_header=_h(label))
    assert status == 200
    host.drop_check_copy(label)
    status, _, _, _ = hostkit.request("GET", "/app.js", host_header=_h(label))
    assert status == 421


def test_an_app_that_is_not_installed_is_not_served(served, tmp_path):
    hostkit.place_app("held-app-abcdefgh", status="held")
    status, _, _, _ = hostkit.request("GET", "/", host_header=_h("held-app-abcdefgh"),
                                      headers={"Sec-Fetch-Dest": "iframe"})
    assert status == 421


def test_origin_and_abilities_routes(served):
    from flask import Flask
    from puffpai.webapps import api
    app = Flask("t")
    app.register_blueprint(api.bp)
    c = app.test_client()
    r = c.get(f"/webapps/{APP}/origin")
    assert r.status_code == 200 and r.get_json() == {"origin": f"http://{APP}.localhost:{hostkit.PORT}"}
    assert c.get(f"/webapps/{APP}/abilities").get_json() == []
    for bad in ("nope", "held-app-abcdefgh", "UPPER-abcdefgh", "a" * 40 + "-abcdefgh"):
        assert c.get(f"/webapps/{bad}/origin").status_code == 404


# ---------------------------------------------------------------------------
# how the runner finds Node when host_programs is absent
# ---------------------------------------------------------------------------

def test_node_comes_only_from_the_installers_record_never_from_path(tmp_path, monkeypatch):
    """Spec: node from ``<install>/node_path.txt`` (the installer writes it), never
    ``shutil.which``. A dev checkout has no record, so the runner says it cannot
    drive apps; the live tests take ``PUFFPAI_R3_NODE`` instead."""
    import types
    from puffpai import config, webapps
    from puffpai.webapps import runner_host, wording
    real_optional = webapps._optional
    monkeypatch.setattr(webapps, "_optional",
                        lambda m: None if m == "puffpai.security.host_programs" else real_optional(m))
    monkeypatch.setattr(config, "get_config", lambda: types.SimpleNamespace(REPO_ROOT=str(tmp_path)))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_node = bindir / ("node.exe" if sys.platform == "win32" else "node")
    fake_node.write_text("")
    monkeypatch.setenv("PATH", str(bindir))                        # on PATH, never used
    assert runner_host.program("node") is None
    assert runner_host.availability() == (False, wording.NODE_TOO_OLD)
    (tmp_path / "node_path.txt").write_text("node.exe")              # not a full path
    assert runner_host.program("node") is None
    (tmp_path / "node_path.txt").write_text(str(tmp_path / "missing" / "node.exe"))
    assert runner_host.program("node") is None
    (tmp_path / "node_path.txt").write_text(str(fake_node))
    assert runner_host.program("node") == str(fake_node)
    src = open(runner_host.__file__, encoding="utf-8").read()
    assert "shutil.which" not in src and "which(" not in src


# ---------------------------------------------------------------------------
# the runner browser's own background calls are stopped and never
# logged as an app's traffic (the integrator saw about 380 such lines)
# ---------------------------------------------------------------------------

def test_the_wall_flags_keep_the_pinned_ones_and_one_disable_features_list():
    from puffpai.webapps import runner_host
    flags = runner_host.wall_flags(5555, [f"{APP}.localhost:4381", "bad host"])
    for pinned in ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                   "--disable-background-networking", "--no-first-run",
                   "--no-default-browser-check", "--headless=new",
                   "--proxy-server=http://127.0.0.1:5555"):
        assert pinned in flags
    features = [f for f in flags if f.startswith("--disable-features=")]
    # Chromium keeps only the LAST --disable-features: there must be exactly one
    assert len(features) == 1
    names = features[0].split("=", 1)[1].split(",")
    for pinned in ("AutofillServerCommunication", "OptimizationHints", "MediaRouter", "Translate"):
        assert pinned in names
    assert "--disable-component-update" in flags and "--disable-sync" in flags
    assert f"--proxy-bypass-list=<-loopback>;{APP}.localhost:4381" in flags
    assert not any("remote-debugging-port" in f or "remote-allow-origins" in f for f in flags)
    assert not any("bad host" in f for f in flags)


def test_the_refusing_proxy_logs_the_browsers_own_calls_once_a_day_never_as_app_traffic(monkeypatch):
    from puffpai.security import egress_ledger
    from puffpai.webapps import runner_host
    got = []
    monkeypatch.setattr(egress_ledger, "record", lambda service, **kw: got.append((service, kw)) or True)
    monkeypatch.setattr(runner_host, "_logged_today", set())
    proxy = runner_host.RefuseProxy()
    try:
        for _ in range(50):
            proxy.refused("GET", "www.bing.com")
        proxy.refused("CONNECT", "edge.microsoft.com")
        assert len(proxy.seen) == 51 and proxy.counts["www.bing.com"] == 50
        assert [g[1]["host"] for g in got] == ["www.bing.com", "edge.microsoft.com"]
        for service, kw in got:
            assert service == "app_runner" and kw["outcome"] == "refused"
            assert kw["what"] == "request" and kw["what"] != "app_traffic"
        # a later runner (restarted for a new app) does not log them again today
        proxy2 = runner_host.RefuseProxy()
        try:
            proxy2.refused("GET", "www.bing.com")
            assert len(got) == 2
        finally:
            proxy2.close()
    finally:
        proxy.close()


def test_the_refusing_proxy_answers_403_to_anything(monkeypatch):
    import socket as _socket
    from puffpai.security import egress_ledger
    from puffpai.webapps import runner_host
    monkeypatch.setattr(egress_ledger, "record", lambda *a, **k: True)
    proxy = runner_host.RefuseProxy()
    try:
        with _socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"CONNECT www.bing.com:443 HTTP/1.1\r\nHost: www.bing.com:443\r\n\r\n")
            answer = s.recv(200)
        assert answer.startswith(b"HTTP/1.1 403")
        assert proxy.seen[-1]["host"] == "www.bing.com"
    finally:
        proxy.close()


# ---------------------------------------------------------------------------
# a removed app's address answers only its goodbye page, which
# carries Clear-Site-Data (M7 / 2.10)
# ---------------------------------------------------------------------------

def test_a_removed_app_answers_only_the_goodbye_page_with_clear_site_data(served):
    frame = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-site"}
    # while installed there is no goodbye page: nothing outside clears its data
    status, hdrs, _, _ = hostkit.request("GET", "/__puffpai/bye", host_header=_h(), headers=frame)
    assert status in (403, 404) and "clear-site-data" not in hdrs
    host.mark_removed(APP)
    assert APP in host.removed_ids() and APP not in host.installed_ids()
    status, hdrs, body, _ = hostkit.request("GET", "/__puffpai/bye", host_header=_h(), headers=frame)
    assert status == 200
    assert hdrs["clear-site-data"] == '"cache", "cookies", "storage", "executionContexts"'
    assert "access-control-allow-origin" not in hdrs
    assert hdrs["content-security-policy"] == headers.csp(3003)
    # everything else at the address is an unknown site now
    for path, h in (("/", frame), ("/app.js", frame), ("/__puffpai/storage", {"Sec-Fetch-Site": "same-origin"}),
                    ("/__puffpai/bye", {"Sec-Fetch-Dest": "document"}), ("/__puffpai/bye?x=1", {})):
        status, hdrs, _, _ = hostkit.request("GET", path, host_header=_h(), headers=h)
        assert status == 421 and "clear-site-data" not in hdrs, path
    status, _, _, _ = hostkit.request("POST", "/__puffpai/bye", host_header=_h(), headers=frame, body=b"")
    assert status == 421
    # an app that was never installed gets nothing either
    status, hdrs, _, _ = hostkit.request("GET", "/__puffpai/bye", host_header=_h("never-here-abcdefgh"),
                                         headers=frame)
    assert status == 421 and "clear-site-data" not in hdrs


def test_the_removed_list_survives_a_restart_and_is_bounded(served, monkeypatch):
    host.mark_removed(APP)
    host.stop()
    host.start(None)
    assert APP in host.removed_ids() and APP not in host.installed_ids()
    monkeypatch.setattr(host, "MAX_REMOVED", 3)
    for i in range(5):
        host.mark_removed(f"gone-{i}-abcdefgh")
    assert len(host.removed_ids()) == 3 and "gone-4-abcdefgh" in host.removed_ids()
    with pytest.raises(ValueError):
        host.mark_removed("../x")


def test_a_removed_id_is_never_installed_again(served, tmp_path):
    from puffpai.webapps import install
    host.mark_removed(APP)
    src = tmp_path / "again"
    src.mkdir()
    (src / "index.html").write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(ValueError):
        install.install_static(APP, str(src), source={}, name="x", purpose="", jobs=[],
                               progress=lambda _l: None)


# ---------------------------------------------------------------------------
# every sentence it shows the owner is listed and plain
# ---------------------------------------------------------------------------

def test_every_l8_owner_string_is_listed_and_plain():
    import re
    from puffpai import plain_words
    path = os.path.join(os.path.dirname(__file__), "fixtures", "ui_strings", "webapps.txt")
    with open(path, encoding="utf-8") as fh:
        listed = fh.read().splitlines()
    banned = re.compile(r"(?i)\bdoors?\b|—|\bbeta\b|\bwart|sandbox|egress|\brepo |SPDX|manifest|"
                        r"MiniLM|embedding|seccomp|bwrap|WSL2|little app|parts\)|\b0 parts|"
                        r"recipe check|recipe file")
    for name in dir(wording):
        value = getattr(wording, name)
        if name.isupper() and isinstance(value, str) and " " in value:
            shown = re.sub(r"\{a\}", "a", value)
            shown = re.sub(r"\{app\}|\{name\}", "{app name}", shown)
            assert value in listed or shown in listed or any(
                re.sub(r"\{[^}]+\}", "x", value) == re.sub(r"\{[^}]+\}", "x", line) for line in listed), name
    for line in listed:
        filled = re.sub(r"\{[^}]+\}", "x", line)
        assert not banned.search(filled), line
        assert plain_words.is_plain(filled), (line, plain_words.check(filled))


# ---------------------------------------------------------------------------
# a request with no
# readable host is refused but is never a blank row in the outside log
# ---------------------------------------------------------------------------

def test_the_refusing_proxy_never_logs_a_blank_or_unreadable_host(monkeypatch):
    import socket as _socket
    import time
    from puffpai.security import egress_ledger
    from puffpai.webapps import runner_host
    got = []
    monkeypatch.setattr(egress_ledger, "record", lambda service, **kw: got.append((service, kw)) or True)
    monkeypatch.setattr(runner_host, "_logged_today", set())
    proxy = runner_host.RefuseProxy()
    try:
        # a spare connection closed with nothing sent
        with _socket.create_connection(("127.0.0.1", proxy.port), timeout=5):
            pass
        # a request line with no target, and one whose host is not a name
        for raw in (b"GET\r\n\r\n", b"GET http://\x01bad host!/x HTTP/1.1\r\n\r\n",
                    b"GET / HTTP/1.1\r\n\r\n"):
            with _socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
                s.sendall(raw)
                assert s.recv(200).startswith(b"HTTP/1.1 403")
        # an origin-form request names its host in the header
        with _socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET /x HTTP/1.1\r\nUser-Agent: t\r\nHost: Update.Example.COM:80\r\n\r\n")
            assert s.recv(200).startswith(b"HTTP/1.1 403")
        deadline = time.time() + 5
        while time.time() < deadline and not proxy.seen:
            time.sleep(0.05)
        proxy.refused("GET", "")
        proxy.refused("GET", "   ")
        assert [x["host"] for x in proxy.seen] == ["update.example.com"]
        assert "" not in proxy.counts and list(proxy.counts) == ["update.example.com"]
        assert [kw["host"] for _s, kw in got] == ["update.example.com"]
        assert all(kw["host"] for _s, kw in got)
        assert proxy.unreadable >= 5
    finally:
        proxy.close()


@pytest.mark.parametrize("target,host", [
    ("www.bing.com:443", "www.bing.com"),
    ("http://u" + chr(64) + "Edge.Microsoft.com/p", "edge.microsoft.com"),
    ("[::1]:8080", "[::1]"), ("", ""), (":443", ""), ("bad host", ""), ("a..b", ""),
    ("x" * 300, ""), ("<script>", "")])
def test_the_proxy_reads_only_a_host_a_person_could_read(target, host):
    from puffpai.webapps import runner_host
    assert runner_host._host_part(target) == host
