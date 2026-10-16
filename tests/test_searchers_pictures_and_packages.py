"""Searchers: pictures, npm, PyPI, Hugging Face, HACS, listed projects.

Recorded answers only, no network. What must hold:

* pictures: only licensed, non-mature JPEG/PNG/GIF/WebP pictures; each
  thumbnail is fetched from the picture host only and re-drawn on this
  computer; SVG, a picture whose type lies, a picture declaring more than 40
  million pixels and a broken file are all refused;
* npm: the trust facts (downloads, the linked project's package.json name and
  command) are read again in ``details``; a package pointing at a project with
  another name (starjacking) fails; a package with no command is never addable;
* PyPI: exact one-word names only; Hugging Face: licence tag required, never addable;
* HACS rows are look-only; a listed GitHub project is pinned from HEAD.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import re
import struct
import sys
import types
import urllib.parse
import zlib

import pytest

from puffpai import source_fetch
from puffpai.catalogs import code_mode, search_budget, trust_floor, where_to_look as w
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import (awesome_selfhosted, commons, github, hacs, huggingface,
                                        npm, openverse, pypi)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "recorded_web")
SHA = "0123456789abcdef0123456789abcdef01234567"


def read(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def fake_outbound(monkeypatch):
    mod = types.ModuleType("puffpai.security.outbound_query")

    @dataclasses.dataclass(frozen=True)
    class Sendable:
        phrase: str
        source_hint: str = ""
        subject: bool = False

    mod.Sendable = Sendable
    mod.is_sendable = lambda o: isinstance(o, Sendable)
    mod.url_quote = lambda s: urllib.parse.quote_plus(s.phrase)
    monkeypatch.setitem(sys.modules, "puffpai.security.outbound_query", mod)
    return mod


class Net:
    def __init__(self, routes=()):
        self.routes = list(routes)
        self.calls: list[dict] = []

    def __call__(self, url, *, allowed_hosts, max_bytes, service, honour_robots=True,
                 allow_truncated=False, headers=None, key_id=None, transport=None, resolver=None):
        self.calls.append({"url": url, "hosts": set(allowed_hosts), "service": service,
                           "robots": honour_robots, "key_id": key_id})
        assert urllib.parse.urlsplit(url).hostname in allowed_hosts, url
        for pattern, body in self.routes:
            if re.search(pattern, url):
                body = body(url) if callable(body) else body
                if body is None:
                    break
                return {"body": body[:max_bytes], "reason": "ok", "status": 200,
                        "truncated": len(body) > max_bytes, "headers": {}}
        return {"body": None, "reason": "ok", "status": 404, "truncated": False, "headers": {}}


def budget(wizard=True):
    return search_budget.SearchBudget(wizard=wizard, clock=lambda: 1_790_000_000.0,
                                      sleeper=lambda s: None)


def picture(kind="PNG", size=(40, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buf, kind)
    return buf.getvalue()


def huge_png_header(width=100_000, height=100_000) -> bytes:
    """A PNG whose header declares 10 billion pixels (a decompression bomb)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return sig + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk)) + \
        b"\x00\x00\x00\x00IEND\xaeB`\x82"


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def test_a_thumbnail_is_redrawn_as_a_plain_jpeg_on_this_computer():
    from PIL import Image
    path = S.save_thumbnail(picture("PNG", (900, 600)))
    assert path and path.startswith(S.thumbs_dir()) and path.endswith(".jpg")
    with Image.open(path) as im:
        assert im.format == "JPEG" and max(im.size) <= S.THUMB_SIDE
        assert not im.info.get("exif")
    for kind in ("JPEG", "GIF", "WEBP"):
        assert S.save_thumbnail(picture(kind)) is not None


@pytest.mark.parametrize("body", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b"GIF89a" + b"\x00" * 10,                                   # says GIF, is not one
    huge_png_header(),
    b"\xff\xd8\xff" + b"\x00" * 100,                            # a broken JPEG
    b"",
    None,
    b"\x89PNG\r\n\x1a\n" + b"\x00" * (S.THUMB_MAX_BYTES + 1),
], ids=["svg", "lying_gif", "bomb", "broken_jpeg", "empty", "none", "too_big"])
def test_unsafe_or_broken_pictures_are_refused(body):
    assert S.save_thumbnail(body) is None


def test_the_first_bytes_decide_the_kind():
    assert S.picture_kind(picture("PNG")) == "PNG"
    assert S.picture_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "WEBP"
    assert S.picture_kind(b"<?xml version='1.0'?><svg") is None


# ---------------------------------------------------------------------------
# Commons and Openverse
# ---------------------------------------------------------------------------

def test_commons_finds_carry_licence_credit_and_a_local_thumbnail(monkeypatch):
    oq = fake_outbound(monkeypatch)
    net = Net([(r"commons\.wikimedia\.org/w/api\.php", read("commons_search_wedding.json")),
               (r"(?:thumb|upload)\.wikimedia\.org/", picture("JPEG"))])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    job = {"id": "j1", "parts": [{"kind": "pictures", "search": ["wedding planning"]}]}
    got = commons.search(job, [oq.Sendable("wedding planning", "j1")], kind="pictures",
                         budget=budget(), row=w.row_by_id("commons"))
    assert got
    for rec in got:
        pic = rec["picture"]
        assert rec["part"] == "pictures" and rec["integrable"] is True
        assert pic["license"] and w.license_words(pic["license"])
        assert os.path.isfile(pic["thumb_local"]) and pic["full_host"] == "upload.wikimedia.org"
        assert rec["trust"]["floor_ok"] is True and rec["trust"]["pretick_ok"] is False
    search, thumbs = net.calls[0], net.calls[1:]
    assert search["service"] == "picture_search" and search["hosts"] == {"commons.wikimedia.org"}
    assert search["robots"] is False and search["key_id"] is None           # on request only
    assert "gsrsearch=wedding+planning" in search["url"]
    assert thumbs and all(c["hosts"] <= set(commons.THUMB_HOSTS) and len(c["hosts"]) == 1
                          for c in thumbs)


def test_a_commons_picture_without_a_safe_thumbnail_is_dropped(monkeypatch):
    net = Net([(r"(?:thumb|upload)\.wikimedia\.org/", b"<svg></svg>")])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    page = json.loads(read("commons_search_wedding.json"))["query"]["pages"][0]
    assert commons.found_from_page(page, budget=budget()) is None
    svg = json.loads(json.dumps(page))
    svg["imageinfo"][0]["mime"] = "image/svg+xml"
    assert commons.found_from_page(svg, budget=budget(), fetch_thumb=False) is None
    offsite = json.loads(json.dumps(page))
    offsite["imageinfo"][0]["thumburl"] = "https://evil.example/x.jpg"
    assert commons.found_from_page(offsite, budget=budget(), fetch_thumb=False) is None


def test_openverse_keeps_only_licensed_pictures_that_are_not_mature(monkeypatch):
    oq = fake_outbound(monkeypatch)
    data = json.loads(read("openverse_search_wedding.json"))
    data["results"][1]["mature"] = True
    data["results"][2]["license"] = "all-rights-reserved"
    net = Net([(r"api\.openverse\.org/v1/images/\?q=", json.dumps(data).encode()),
               (r"api\.openverse\.org/v1/images/[0-9a-f-]+/thumb/", picture("JPEG"))])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    got = openverse.search({"id": "j1"}, [oq.Sendable("wedding", "j1")], kind="pictures",
                           budget=budget(), row=w.row_by_id("openverse"))
    ids = {r["id"] for r in got}
    assert got and f"openverse:{data['results'][1]['id']}" not in ids
    assert f"openverse:{data['results'][2]['id']}" not in ids
    assert "license_type=commercial,modification&mature=false" in net.calls[0]["url"]
    assert all(c["hosts"] == {"api.openverse.org"} for c in net.calls)
    assert openverse.licence_name("by-sa", "2.0") == "CC BY-SA 2.0"
    assert openverse.licence_name("pdm", None) == "Public domain"
    assert w.license_words("Public domain") == "Free to use and change (Public domain)."
    assert openverse.licence_name("proprietary", "1.0") is None


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

def npm_find(name="wed-cli", repo="https://github.com/someone/wed-cli"):
    obj = {"package": {"name": name, "version": "1.2.0", "description": "Plan a day.",
                       "license": "MIT", "date": "2026-05-01T00:00:00Z",
                       "links": {"repository": repo}}}
    return npm.found_from_object(obj, part="cli")


def test_npm_search_shape_and_records(monkeypatch):
    oq = fake_outbound(monkeypatch)
    net = Net([(r"registry\.npmjs\.org/-/v1/search", read("npm_search_wedding.json"))])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    got = npm.search({"id": "j1"}, [oq.Sendable("wedding planner app", "j1")], kind="code-app",
                     budget=budget(), row=w.row_by_id("npm"))
    assert net.calls[0]["url"] == ("https://registry.npmjs.org/-/v1/search?text=wedding+planner"
                                   "&size=20")
    assert got and all(r["id"].startswith("npm:") and r["package"]["bin"] == [] for r in got)
    assert all(r["package"]["downloads_monthly"] is None for r in got)   # read later, not trusted


def test_npm_facts_are_read_again_and_starjacking_fails(monkeypatch):
    pkg_same = json.dumps({"name": "wed-cli", "bin": {"wed": "cli.js"}}).encode()
    pkg_other = json.dumps({"name": "react", "bin": {"wed": "cli.js"}}).encode()
    downloads = json.dumps({"downloads": 5400, "package": "wed-cli"}).encode()
    for body, ok in ((pkg_same, True), (pkg_other, False)):
        net = Net([(r"api\.npmjs\.org/downloads/point/last-month/wed-cli", downloads),
                   (r"raw\.githubusercontent\.com/someone/wed-cli/HEAD/package\.json", body)])
        monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
        forged = npm_find()
        forged["package"].update(downloads_monthly=10**9, bin=["x"], repo_name_matches=True)
        got = w.details(forged, budget=budget())
        assert got["package"]["downloads_monthly"] == 5400
        assert got["package"]["repo_name_matches"] is ok
        assert got["trust"]["floor_ok"] is ok and got["trust"]["pretick_ok"] is False
        assert got["mode_guess"] == "cli_tool" and got["package"]["bin"] == ["wed"]
        S.forget_indexes()
        for n in os.listdir(S.cache_dir()):
            os.remove(os.path.join(S.cache_dir(), n))


def test_an_npm_package_without_a_command_is_not_addable(monkeypatch):
    net = Net([(r"api\.npmjs\.org/", json.dumps({"downloads": 900, "package": "wed-cli"}).encode()),
               (r"raw\.githubusercontent\.com/", json.dumps({"name": "wed-cli"}).encode())])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    got = w.details(npm_find(), budget=budget())
    assert got["mode_guess"] == "node_lib" and got["integrable"] is False


def test_npm_repository_links_are_parsed_strictly():
    assert npm.repo_of("git+https://github.com/a/b.git") == {"owner": "a", "name": "b",
                                                             "branch": "HEAD", "path": ""}
    assert npm.repo_of("https://github.com/a/b/tree/main/packages/c")["path"] == "packages/c"
    for bad in ("https://gitlab.com/a/b", "https://github.com/a/b/tree/main/../../x",
                "https://github.com/-a/b", "file:///etc/passwd"):
        assert npm.repo_of(bad) is None
    assert npm_find(name="../../etc") is None


# ---------------------------------------------------------------------------
# PyPI, Hugging Face, HACS
# ---------------------------------------------------------------------------

def test_pypi_asks_exact_one_word_names_only(monkeypatch):
    oq = fake_outbound(monkeypatch)
    body = json.dumps({"info": {"name": "tomato-timer", "version": "1.0", "summary": "A timer.",
                                "license_expression": "MIT",
                                "project_urls": {"Source": "https://github.com/a/tomato-timer"}}})
    net = Net([(r"pypi\.org/pypi/tomato-timer/json", body.encode())])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    got = pypi.lookup({"id": "j1"}, [oq.Sendable("tomato-timer", "j1"),
                                     oq.Sendable("a study timer", "j1")], kind="cli",
                      budget=budget(), row=w.row_by_id("pypi"))
    assert [r["id"] for r in got] == ["pypi:tomato-timer"] and len(net.calls) == 1
    assert got[0]["repo"]["license_reported"] == "MIT" and got[0]["repo"]["owner"] == "a"
    assert pypi.lookup({"id": "j1"}, [oq.Sendable("x", "j1")], kind="code-app",
                       budget=budget(), row=w.row_by_id("pypi")) == []


def hf_item(**over):
    item = {"id": "someone/party-guests", "author": "someone", "likes": 4, "downloads": 120,
            "lastModified": "2026-05-01T00:00:00.000Z", "private": False, "gated": False,
            "disabled": False, "tags": ["license:cc-by-4.0", "format:csv"],
            "description": "Guest lists for parties."}
    item.update(over)
    return item


def test_hugging_face_needs_a_licence_tag_and_is_never_addable():
    rec = huggingface.found_from_item(hf_item())
    assert rec["repo"]["license_reported"] == "CC-BY-4.0" and rec["integrable"] is False
    assert rec["mode_guess"] == "data_template" and rec["trust"]["floor_ok"] is True
    assert rec["trust"]["pretick_ok"] is False
    for over in ({"tags": []}, {"tags": ["license:other"]}, {"private": True},
                 {"gated": "auto"}, {"disabled": True}, {"id": "../x"},
                 {"tags": ["license:mit", "license:gpl-3.0"]}):
        assert huggingface.found_from_item(hf_item(**over)) is None, over
    nc = huggingface.found_from_item(hf_item(tags=["license:cc-by-nc-4.0"]))
    assert "personal use" in w.license_words(nc["repo"]["license_reported"])
    assert code_mode.integrable(nc) is False


def test_hacs_rows_are_look_only(monkeypatch):
    monkeypatch.setattr(S, "_embed_one", lambda text: None)
    job = {"id": "j1", "name": "Lights dashboard card", "parts": [
        {"kind": "code-app", "search": ["dashboard card"]}]}
    got = hacs.local(job, kind="code-app", row={}, limit=5)
    assert got and all(r["integrable"] is False and r["trust"]["pretick_ok"] is False
                       and r["url"].startswith("https://github.com/") for r in got)
    assert hacs.local(job, kind="data-template", row={}, limit=5) == []


def test_hacs_is_searched_only_when_home_assistant_is_set_up(monkeypatch):
    monkeypatch.setattr(S, "_embed_one", lambda text: None)
    job = {"id": "j1", "name": "Lights dashboard card", "parts": [
        {"kind": "code-app", "search": ["dashboard card"]}]}
    monkeypatch.setattr(w, "_home_assistant_configured", lambda: False)
    assert not [r for r in w.find_local(job, kind="code-app") if r["source"] == "hacs"]
    monkeypatch.setattr(w, "_home_assistant_configured", lambda: True)
    assert [r for r in w.find_local(job, kind="code-app") if r["source"] == "hacs"]


# ---------------------------------------------------------------------------
# a listed project is read at a pin taken from HEAD
# ---------------------------------------------------------------------------

def _pkt(text: str) -> bytes:
    raw = text.encode("ascii")
    return f"{len(raw) + 4:04x}".encode("ascii") + raw


def test_head_names_the_branch_when_it_is_not_known():
    body = (_pkt("# service=git-upload-pack\n") + b"0000"
            + _pkt(f"{SHA} HEAD\x00multi_ack symref=HEAD:refs/heads/trunk agent=git\n")
            + _pkt(f"{SHA} refs/heads/trunk\n") + b"0000")
    assert github.head_from_refs(body) == (SHA, "trunk")
    lying = body.replace(f"{SHA} refs/heads/trunk".encode(),
                         ("f" * 40 + " refs/heads/trunk").encode())
    assert github.head_from_refs(lying) == (None, None)


@pytest.mark.parametrize("listing", [False, True])
def test_a_listed_github_project_is_read_like_a_search_find(monkeypatch, listing):
    row = next(r for r in awesome_selfhosted.rows_raw()
               if str(r.get("source_code_url") or "").startswith("https://github.com/")
               and r.get("licenses") == ["MIT"])
    found = awesome_selfhosted._found(row, "code-app")
    owner, name = found["repo"]["owner"], found["repo"]["name"]
    body = (_pkt("# service=git-upload-pack\n") + b"0000"
            + _pkt(f"{SHA} HEAD\x00symref=HEAD:refs/heads/main\n")
            + _pkt(f"{SHA} refs/heads/main\n") + b"0000")
    raw = rf"raw\.githubusercontent\.com/{re.escape(owner)}/{re.escape(name)}/{SHA}/"
    # The licence names the owner's account itself (a sure holder).
    licence = read("subchibeats_LICENSE").replace(b"Sahib Singh", owner.encode())
    routes = [(rf"github\.com/{re.escape(owner)}/{re.escape(name)}\.git/info/refs", body),
              (raw + "README.md$", read("subchibeats_README.md")),
              (raw + "LICENSE$", licence),
              (raw + "index.html$", read("subchibeats_index.html"))]
    if listing:
        top = [{"path": p, "type": "blob", "mode": "100644", "size": 10, "sha": "b" * 40}
               for p in ("README.md", "LICENSE", "index.html")]
        routes.insert(0, (rf"api\.github\.com/repos/{re.escape(owner)}/{re.escape(name)}"
                          rf"/git/trees/{SHA}$",
                          json.dumps({"sha": SHA, "tree": top, "truncated": False}).encode()))
    net = Net(routes)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    got = w.details(found, budget=budget())
    assert got["repo"]["pinned_commit"] == SHA and got["repo"]["default_branch"] == "main"
    assert got["mode_guess"] == "static_web" and got["integrable"] is True
    assert got["repo"]["in_awesome_selfhosted"] is True
    if listing:
        # Its own licence read at the pin, open: trusted (by its stars first
        # when it has them), and pre-ticked.
        assert got["repo"]["license_read"] == "MIT" and got["repo"]["license_check"] is False
        assert got["trust"]["path"] in ("stars", "listed") and trust_floor.pretick_allowed(got)
    else:
        # Read file by file with no listing, no licence is
        # confirmed (custom), and the list's own "MIT" never makes it free:
        # the licence BuildBot read itself outranks the list's.
        assert got["repo"]["license_read"] == w.CUSTOM_LICENSE
        assert got["trust"]["path"] == "" and not trust_floor.pretick_allowed(got)
