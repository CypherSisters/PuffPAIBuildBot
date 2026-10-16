"""Saving the parts an app loads from the internet, once, while adding it.

No network: the audited exit (``source_fetch.fetch_small``) is replaced by a
recorded answer table. Checks: only through that exit with service
``app_parts`` and only the part's own host; saved by SHA-256 inside the app's
folder (so the app fingerprint covers them) with SRI recorded; the app's
files point at the saved copies; connect-ahead hints to other sites are gone;
a style sheet's own lettering is saved too; anything that cannot be saved is
reported and the app is held.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil

import pytest

from puffpai import webapps
from puffpai.webapps import install, scan, vendor, wording

APP = "mood-board-abcdefgh"
LIB = b"window.lib = 1;"
CSS = (b"@font-face{font-family:Inter;src:url(https://fonts.gstatic.com/s/inter/v1/a.woff2) format('woff2')}"
       b".x{background:url(../img/dot.png)}")
FONT = b"wOF2-font-bytes"
DOT = b"\x89PNG-dot"
PIC = b"\x89PNG-pic"

ANSWERS = {
    "https://cdn.jsdelivr.net/npm/lib@latest/lib.min.js": (LIB, "text/javascript"),
    "https://fonts.googleapis.com/css2?family=Inter": (CSS, "text/css; charset=utf-8"),
    "https://fonts.gstatic.com/s/inter/v1/a.woff2": (FONT, "font/woff2"),
    "https://fonts.googleapis.com/img/dot.png": (DOT, "image/png"),
    "https://images.example-pics.org/p/hero.jpg": (PIC, "image/jpeg"),
}

PAGE = """<!doctype html><html><head><title>Mood Board</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">
<link rel="icon" href="favicon.ico">
<script src="https://cdn.jsdelivr.net/npm/lib@latest/lib.min.js"></script>
</head><body><button id="add">Add</button>
<script src="app.js"></script></body></html>"""
APPJS = ('const HERO = "https://images.example-pics.org/p/hero.jpg";\n'
         'localStorage.setItem("board", "[]");\n')


def _write(web, name, text):
    with open(os.path.join(web, name), "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def placed(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    web = webapps.web_dir(APP)
    os.makedirs(web)
    _write(web, "index.html", PAGE)
    _write(web, "app.js", APPJS)
    calls = []

    def fetch(url, host):
        calls.append((url, host))
        if url not in ANSWERS:
            return {"body": None, "reason": "no_answer"}
        body, ctype = ANSWERS[url]
        return {"body": body, "reason": "ok", "status": 200, "headers": {"content-type": ctype}}
    monkeypatch.setattr(vendor, "_fetch", fetch)
    return web, calls


def _read(web, name):
    with open(os.path.join(web, name), encoding="utf-8") as fh:
        return fh.read()


def test_parts_are_saved_by_hash_and_the_app_points_at_the_copies(placed):
    web, calls = placed
    before = scan.scan(web)
    assert set(before["parts_to_save"]) >= {"cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"}
    got = vendor.save_parts(APP, web)
    assert got["ok"] is True and got["failed"] == []
    # every download named only its own host
    assert calls and all(host == url.split("/")[2] for url, host in calls)
    page = _read(web, "index.html")
    assert "https://" not in page and "preconnect" not in page and 'href="favicon.ico"' in page
    assert "/__vendor__/" in page and "https://" not in _read(web, "app.js")
    files = sorted(os.listdir(os.path.join(web, vendor.VENDOR_DIR)))
    for item in got["items"]:
        assert item["file"] in files
        with open(os.path.join(web, vendor.VENDOR_DIR, item["file"]), "rb") as fh:
            body = fh.read()
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        assert item["file"].startswith(item["sha256"][:16])
        assert item["sri"] == "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode()
    # the saved style sheet loads its lettering and picture from the saved copies
    css_item = next(i for i in got["items"] if i["asked"].startswith("https://fonts.googleapis.com/css2"))
    with open(os.path.join(web, vendor.VENDOR_DIR, css_item["file"]), encoding="utf-8") as fh:
        css = fh.read()
    assert "fonts.gstatic.com" not in css and css.count("/__vendor__/") == 2
    # nothing is left to load from outside, so Drawbridge's parts row passes
    after = scan.scan(web)
    assert after["parts_to_save"] == [] and after["needs_internet_while_running"] == []
    with open(os.path.join(webapps.app_state_dir(APP), "vendor.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["app_id"] == APP and len(record["items"]) == len(got["items"]) == 5


def test_an_http_part_or_a_refusal_is_reported_and_nothing_pretends(placed):
    web, _ = placed
    with open(os.path.join(web, "index.html"), "a", encoding="utf-8") as fh:
        fh.write('<script src="http://plain.example-cdn.org/old.js"></script>'
                 '<img src="https://gone.example-pics.org/x.png">')
    got = vendor.save_parts(APP, web)
    whys = {f["url"].split("/")[2]: f["why"] for f in got["failed"]}
    assert got["ok"] is False
    assert whys["plain.example-cdn.org"] == "not_encrypted" and whys["gone.example-pics.org"] == "no_answer"
    assert set(scan.scan(web)["parts_to_save"]) == {"gone.example-pics.org", "plain.example-cdn.org"}


def test_the_real_exit_is_used_with_the_app_parts_service(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    from puffpai import source_fetch
    seen = []
    monkeypatch.setattr(source_fetch, "fetch_small", lambda url, **kw: seen.append((url, kw)) or
                        {"body": None, "reason": "switched_off"})
    web = webapps.web_dir(APP)
    os.makedirs(web)
    _write(web, "index.html", '<script src="https://cdn.jsdelivr.net/npm/a@1.0.0/a.js"></script>')
    got = vendor.save_parts(APP, web)
    assert got["ok"] is False and got["failed"][0]["why"] == "switched_off"
    url, kw = seen[0]
    assert kw["service"] == "app_parts" and kw["allowed_hosts"] == {"cdn.jsdelivr.net"}
    assert kw["max_bytes"] == vendor.MAX_PART_BYTES


def test_only_the_apps_own_folder_is_changed(placed, tmp_path):
    with pytest.raises(ValueError):
        vendor.save_parts(APP, str(tmp_path))
    with pytest.raises(ValueError):
        vendor.save_parts("../x", placed[0])


def test_install_saves_the_parts_then_adds_the_app(tmp_path, monkeypatch, placed):
    from puffpai.webapps import host, runner_host
    web, _ = placed
    src = tmp_path / "download"
    src.mkdir()
    (src / "index.html").write_text(PAGE, encoding="utf-8")
    (src / "app.js").write_text(APPJS, encoding="utf-8")
    shutil.rmtree(os.path.dirname(web))
    monkeypatch.setattr(runner_host, "availability", lambda: (False, wording.NODE_TOO_OLD))
    lines = []
    res = install.install_static(APP, str(src), source={"repo": "example/board"}, name="Mood Board",
                                 purpose="mood board", jobs=[], progress=lines.append)
    assert res["verdict"]["verdict"] == "allow", res["verdict"]["rows"]
    assert "Toolsmith is saving a copy of the parts this app loads from the internet." in lines
    assert APP in host.installed_ids()
    host.refresh()


def test_a_style_sheet_a_script_bundle_imports_is_saved_too(tmp_path, monkeypatch):
    """A bundler puts '@import url(https://fonts...)' inside a script (as the
    recorded todo app does); the scan counts it, so it must be saved as well."""
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    web = webapps.web_dir(APP)
    os.makedirs(web)
    _write(web, "index.html", '<script src="index.bundle.js"></script>')
    _write(web, "index.bundle.js", 'push([1, "@import url(https://fonts.googleapis.com/css2?family=Inter);"]);'
                                   'localStorage.setItem("p", "[]");')
    monkeypatch.setattr(vendor, "_fetch", lambda url, host: (
        {"body": CSS, "reason": "ok", "status": 200, "headers": {"content-type": "text/css"}}
        if url.startswith("https://fonts.googleapis.com/css2") else
        {"body": FONT if url.endswith(".woff2") else DOT, "reason": "ok", "status": 200,
         "headers": {"content-type": "font/woff2" if url.endswith(".woff2") else "image/png"}}))
    assert "fonts.googleapis.com" in scan.scan(web)["parts_to_save"]
    got = vendor.save_parts(APP, web)
    assert got["ok"] is True
    assert "fonts.googleapis.com" not in _read(web, "index.bundle.js")
    assert scan.scan(web)["parts_to_save"] == []
