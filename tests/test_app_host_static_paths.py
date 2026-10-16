"""The app host serves only the app's own files, by names Windows cannot bend."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit as hostkit  # noqa: E402

from puffpai.webapps import host  # noqa: E402

APP = "shelf-keeper-abcdefgh"

REFUSED = [
    "/..%5c..%5cwindows%5cwin.ini", "/%5c..%5csecret.txt", "/sub%5cpage.html",
    "/..%2f..%2fsecret.txt", "/sub%2fpage.html", "/%2e%2e/%2e%2e/secret.txt",
    "/index.html::$DATA", "/index.html:stream", "/C:/Windows/win.ini",
    "/PROGRA~1/x.js", "/SUB~1/page.html", "/index.html.", "/index.html ", "/index.html%20",
    "/sub./page.html", "/../secret.txt", "/sub/../../secret.txt", "/./index.html",
    "//index.html", "/index.html%00.js", "/%252e%252e/secret.txt", "/con", "/aux.js",
    "/nul.txt", "/COM1", "/sub\\page.html", "/index.html%3a%3a$DATA",
]


@pytest.mark.parametrize("raw", REFUSED)
def test_bent_names_are_refused_by_the_path_rule(raw):
    assert host.safe_rel_path(raw) is None, raw


@pytest.mark.parametrize("raw,parts", [("/", []), ("/index.html", ["index.html"]),
                                       ("/sub/page.html", ["sub", "page.html"]),
                                       ("/assets/app%20one.js", ["assets", "app one.js"]),
                                       ("/sub/", ["sub"])])
def test_plain_names_pass(raw, parts):
    assert host.safe_rel_path(raw) == parts


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(hostkit.PORT))
    web = hostkit.place_app(APP, extra={"sub/page.html": "<p>ok</p>", "assets/a.js": "1"})
    # something beside the app's folder that must never be served
    with open(os.path.join(os.path.dirname(os.path.dirname(web)), "secret.txt"), "w") as fh:
        fh.write("secret")
    with open(os.path.join(os.path.dirname(web), "secret.txt"), "w") as fh:
        fh.write("secret")
    host.start(None)
    yield web
    host.stop()


@pytest.mark.parametrize("raw", REFUSED)
def test_bent_names_get_nothing_over_http(served, raw):
    if "\\" in raw or " " in raw:
        pytest.skip("not sendable in a request line by this client")
    if raw == "//index.html":
        pytest.skip("the standard library's server folds leading slashes to one (same file)")
    status, _, body, _ = hostkit.request("GET", raw, host_header=f"{APP}.localhost:{hostkit.PORT}",
                                         headers={"Sec-Fetch-Dest": "iframe"})
    assert status in (400, 404), raw
    assert b"secret" not in body


def test_a_link_inside_the_app_folder_is_not_followed(served, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = os.path.join(served, "link.txt")
    try:
        os.symlink(str(outside), link)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make links")
    assert host.resolve_file(APP, "/link.txt") is None
    status, _, body, _ = hostkit.request("GET", "/link.txt", host_header=f"{APP}.localhost:{hostkit.PORT}")
    assert status == 404 and b"secret" not in body


def test_real_files_resolve_inside_the_folder(served):
    full = host.resolve_file(APP, "/sub/page.html")
    assert full and os.path.normcase(full).startswith(os.path.normcase(os.path.realpath(served)))
    assert host.resolve_file(APP, "/") .endswith("index.html")
    assert host.resolve_file("nobody-abcdefgh", "/index.html") is None


def _junction(target: str, link: str) -> None:
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError):
        pytest.skip("no directory junctions here")


def test_a_junction_inside_the_app_folder_is_not_followed(served, tmp_path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    _junction(str(outside), os.path.join(served, "jdir"))
    assert host.resolve_file(APP, "/jdir/secret.txt") is None
    status, _, body, _ = hostkit.request("GET", "/jdir/secret.txt",
                                         host_header=f"{APP}.localhost:{hostkit.PORT}")
    assert status == 404 and b"secret" not in body
