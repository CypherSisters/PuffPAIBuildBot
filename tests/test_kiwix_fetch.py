"""Downloading a Kiwix book: newest issue, mirrors from its .meta4, SHA-256 checked.

Every Kiwix row in the registry used to be a fixed address that
404ed, and the fetcher refused Kiwix's redirect to its mirrors anyway. A row
now names the BOOK; the fetcher asks the library for the newest issue, reads
that issue's .meta4 (exact size, SHA-256, mirror list), follows a redirect only
to a mirror that .meta4 names, and refuses bytes that do not match.

Every older protection still holds and is tested here on this path: https
only, public addresses on every hop, robots, the size the owner saw, the
posture gate, resumable partial files, provenance. No network: a scripted
transport stands in for the library and its mirrors.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from puffpai import source_fetch, source_registry
from puffpai.build_posture import assess_posture
from puffpai.catalogs import kiwix_opds

ONLINE = assess_posture("online", "island")
OFFLINE = assess_posture("offline", "island")

BODY = b"ZIM-" + bytes(range(256)) * 40
SHA = hashlib.sha256(BODY).hexdigest()
FILE = "vikidia_en_all_nopic_2026-09a.zim"
META4_URL = f"https://lb.download.kiwix.org/zim/vikidia/{FILE}.meta4"
PRIMARY = f"https://lb.download.kiwix.org/zim/vikidia/{FILE}"
MIRROR_A = f"https://ftp.nluug.nl/pub/kiwix/zim/vikidia/{FILE}"
MIRROR_B = f"https://mirror.download.kiwix.org/zim/vikidia/{FILE}"
OPDS = kiwix_opds.search_url(name="vikidia_en_all", lang="", count=20)


def _feed(size=len(BODY)):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Vikidia</title><updated>2026-09-11T00:00:00Z</updated>
    <summary>The encyclopedia for Children</summary><language>eng</language>
    <name>vikidia_en_all</name><flavour>nopic</flavour><category>vikidia</category>
    <author><name>Vikidia</name></author>
    <link rel="http://opds-spec.org/acquisition/open-access" href="{META4_URL}" length="{size}"/>
  </entry>
  <entry><title>Vikidia older</title><updated>2025-01-01T00:00:00Z</updated>
    <name>vikidia_en_all</name><flavour>nopic</flavour><category>vikidia</category>
    <link rel="http://opds-spec.org/acquisition/open-access"
          href="https://lb.download.kiwix.org/zim/vikidia/vikidia_en_all_nopic_2025-01.zim.meta4" length="5"/>
  </entry>
</feed>""".encode()


def _meta4(sha=SHA, size=len(BODY), mirrors=(MIRROR_A, MIRROR_B)):
    urls = "".join(f'<url priority="{i + 1}">{u}</url>' for i, u in enumerate(mirrors))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <file name="{FILE}"><size>{size}</size>
    <hash type="sha-256">{sha}</hash>{urls}
  </file>
</metalink>""".encode()


class Resp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status, self.headers, self._body, self._pos = status, dict(headers or {}), body, 0

    def read(self, amt=-1):
        if amt is None or amt < 0:
            amt = len(self._body) - self._pos
        chunk = self._body[self._pos:self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


def _bytes(body):
    def make(headers):
        rng = headers.get("Range")
        if rng:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            rest = body[start:]
            return Resp(206, {"Content-Length": str(len(rest))}, rest)
        return Resp(200, {"Content-Length": str(len(body))}, body)
    return make


def _redirect(to):
    return lambda headers: Resp(302, {"Location": to})


class Net:
    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        make = self.routes.get(url)
        return make(headers) if make else Resp(404)


def _routes(**over):
    routes = {
        OPDS: lambda h: Resp(200, {}, _feed()),
        META4_URL: lambda h: Resp(200, {}, _meta4()),
        "https://lb.download.kiwix.org/robots.txt": lambda h: Resp(404),
        "https://ftp.nluug.nl/robots.txt": lambda h: Resp(404),
        "https://mirror.download.kiwix.org/robots.txt": lambda h: Resp(404),
        PRIMARY: _redirect(MIRROR_A),
        MIRROR_A: _bytes(BODY),
        MIRROR_B: _bytes(BODY),
    }
    routes.update(over)
    return routes


def _registry(**over):
    row = {"id": "vik", "label": "Vikidia", "kind": "zim", "domain": "general",
           "tags": ["encyclopedia"], "license": "CC-BY-SA-3.0",
           "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
           "commercial_use": True, "kiwix_book": "vikidia_en_all",
           "kiwix_flavour": "nopic", "size_mb": 0.02, "pin": "kiwix_meta4",
           "what_it_is": "A children's encyclopedia."}
    row.update(over)
    return {"schema": source_registry.SCHEMA, "sources": [row]}


def PUBLIC(host):
    return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def _clean():
    source_fetch.clear_caches()
    yield
    source_fetch.clear_caches()


def _fetch(tmp_path, net, registry=None, posture=ONLINE, resolver=PUBLIC, **kw):
    kw.setdefault("size_acknowledged", True)
    return source_fetch.fetch("vik", posture, dest_dir=str(tmp_path),
                              registry=registry or _registry(), transport=net,
                              resolver=resolver, sleeper=lambda s: None, **kw)


# -- the good path -----------------------------------------------------------

def test_the_newest_issue_is_fetched_through_a_listed_mirror_and_checked(tmp_path):
    net = Net(_routes())
    result = _fetch(tmp_path, net)

    assert result["ok"], result["reason"]
    assert result["path"].endswith(FILE)
    assert (tmp_path / FILE).read_bytes() == BODY
    prov = result["provenance"]
    assert prov["sha256"] == SHA and prov["sha256_pinned"] is True
    assert prov["pin_source"] == "kiwix_meta4"
    assert prov["kiwix_book"] == "vikidia_en_all"
    assert prov["source_url"] == PRIMARY
    assert prov["final_url"] == MIRROR_A
    assert source_fetch.chunk_metadata(prov)["source_sha256_pinned"] is True
    # the older issue in the listing was never touched
    assert not any("2025-01" in url for url in net.calls)


def test_asking_again_for_the_same_issue_downloads_nothing(tmp_path):
    _fetch(tmp_path, Net(_routes()))
    net = Net(_routes())
    again = _fetch(tmp_path, net)
    assert again["ok"] and again["status"] == "already_here"
    assert MIRROR_A not in net.calls


# -- redirects only to mirrors the .meta4 names -------------------------------

def test_a_redirect_to_a_host_the_meta4_does_not_name_is_refused_then_a_named_mirror_is_used(tmp_path):
    net = Net(_routes(**{PRIMARY: _redirect("https://sneaky.example/zim/" + FILE)}))
    result = _fetch(tmp_path, net)
    assert result["ok"], result["reason"]
    assert "https://sneaky.example/zim/" + FILE not in net.calls
    assert result["provenance"]["final_url"] == MIRROR_A


def test_when_no_named_mirror_answers_nothing_lands(tmp_path):
    net = Net(_routes(**{PRIMARY: _redirect("https://sneaky.example/x.zim"),
                         MIRROR_A: lambda h: Resp(503), MIRROR_B: lambda h: Resp(404)}))
    result = _fetch(tmp_path, net)
    assert not result["ok"]
    assert not (tmp_path / FILE).exists()


def test_a_mirror_that_resolves_inside_the_house_is_refused(tmp_path):
    def resolver(host):
        return ["192.168.1.20"] if host == "ftp.nluug.nl" else ["93.184.216.34"]

    net = Net(_routes())
    result = _fetch(tmp_path, net, resolver=resolver)
    # The redirect to the private-resolving mirror is refused on the hop; the
    # other named mirror is public and serves the file.
    assert result["ok"], result["reason"]
    assert result["provenance"]["final_url"] == MIRROR_B
    assert MIRROR_A not in net.calls


def test_a_mirror_whose_robots_forbid_it_is_skipped(tmp_path):
    net = Net(_routes(**{
        PRIMARY: lambda h: Resp(503),
        "https://ftp.nluug.nl/robots.txt": lambda h: Resp(200, {}, b"User-agent: *\nDisallow: /\n"),
    }))
    result = _fetch(tmp_path, net)
    assert result["ok"] and result["provenance"]["final_url"] == MIRROR_B


# -- the fingerprint and the size ---------------------------------------------

def test_bytes_that_do_not_match_the_published_sha256_are_set_aside(tmp_path):
    net = Net(_routes(**{MIRROR_A: _bytes(b"X" * len(BODY))}))
    result = _fetch(tmp_path, net)
    assert result["status"] == "checksum_mismatch"
    assert not (tmp_path / FILE).exists()
    assert (tmp_path / (FILE + ".rejected")).exists()
    assert "fingerprint" in result["reason"]


def test_a_meta4_without_a_sha256_means_no_download(tmp_path):
    body = _meta4().replace(b'type="sha-256"', b'type="md5"')
    net = Net(_routes(**{META4_URL: lambda h: Resp(200, {}, body)}))
    result = _fetch(tmp_path, net)
    assert not result["ok"] and "SHA-256" in result["reason"]
    assert MIRROR_A not in net.calls


def test_an_issue_that_grew_past_what_the_owner_saw_is_refused(tmp_path):
    big = 50 * 1024 * 1024
    net = Net(_routes(**{META4_URL: lambda h: Resp(200, {}, _meta4(size=big))}))
    result = _fetch(tmp_path, net)
    assert not result["ok"] and "you were shown" in result["reason"]
    assert MIRROR_A not in net.calls


def test_a_server_that_sends_more_than_the_meta4_size_is_stopped(tmp_path):
    net = Net(_routes(**{MIRROR_A: _bytes(BODY + b"extra"), MIRROR_B: _bytes(BODY + b"extra")}))
    result = _fetch(tmp_path, net)
    assert not result["ok"]
    assert not (tmp_path / FILE).exists()


def test_an_interrupted_download_resumes_and_is_still_checked(tmp_path):
    part = tmp_path / (FILE + ".part")
    part.write_bytes(BODY[:1000])
    net = Net(_routes())
    result = _fetch(tmp_path, net)
    assert result["ok"] and result["resumed"] is True
    assert (tmp_path / FILE).read_bytes() == BODY


# -- the gates that were always there ------------------------------------------

def test_nothing_moves_without_the_size_acknowledged(tmp_path):
    net = Net(_routes())
    result = _fetch(tmp_path, net, size_acknowledged=False)
    assert result["status"] == "needs_confirmation"
    assert net.calls == []


def test_an_offline_build_does_not_even_ask_the_library(tmp_path):
    net = Net(_routes())
    result = _fetch(tmp_path, net, posture=OFFLINE)
    assert not result["ok"] and net.calls == []


def test_when_the_library_is_down_the_saved_meta4_is_tried(tmp_path):
    net = Net(_routes(**{OPDS: lambda h: Resp(503)}))
    result = _fetch(tmp_path, net, registry=_registry(meta4_url=META4_URL))
    assert result["ok"], result["reason"]


def test_the_preview_names_the_library_and_opens_no_socket(tmp_path):
    preview = source_fetch.preview("vik", ONLINE, _registry(), str(tmp_path))
    text = "\n".join(preview["lines"])
    assert preview["ok"] and "Kiwix library" in text and "fingerprint" in text


# -- the owner overlay: it can add books, never widen where we connect -------

def _cand(ident="kiwix:vikidia_en_all:nopic", **over):
    cand = {"id": ident, "name": "Vikidia (text only)", "fetchable": True,
            "license": "CC-BY-SA-3.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
            "commercial_ok": True, "size_bytes": 10556416,
            "meta4_url": META4_URL, "match_terms": ["children"],
            "what_it_is": "An encyclopedia for children."}
    cand.update(over)
    return cand


def test_an_owner_row_is_written_only_for_a_downloadable_book(tmp_path):
    path = str(tmp_path / "overlay.json")
    row = source_registry.row_from_candidate(_cand(), added_via="cli")
    source_registry.add_owner_row(row, path)
    rows = source_registry.load_overlay(path)
    assert [r["id"] for r in rows] == ["kiwix:vikidia_en_all:nopic"]
    assert rows[0]["provenance"] == "owner_added" and rows[0]["added_at"]
    with pytest.raises(ValueError):
        source_registry.row_from_candidate(_cand(fetchable=False), added_via="cli")


@pytest.mark.parametrize("tamper", [
    {"source_url": "https://evil.example/pack.zim"},
    {"redirect_hosts": ["evil.example"]},
    {"id": "kiwix_astronomy_stackexchange"},
    {"kiwix_book": "something_else"},
    {"provenance": "shipped"},
    {"license": ""},
    {"kind": "docs"},
])
def test_a_hand_edited_overlay_row_that_could_widen_reach_is_ignored(tmp_path, tamper):
    path = tmp_path / "overlay.json"
    row = source_registry.row_from_candidate(_cand(), added_via="cli")
    row.update(tamper)
    path.write_text(json.dumps({"sources": [row]}), encoding="utf-8")
    assert source_registry.load_overlay(str(path)) == []
    with pytest.raises(ValueError):
        source_registry.add_owner_row(row, str(tmp_path / "other.json"))


def test_the_overlay_joins_the_default_registry_and_never_shadows_a_shipped_row(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    source_registry.add_owner_row(
        source_registry.row_from_candidate(_cand(), added_via="cli"))
    ids = [r["id"] for r in source_registry.load_registry()["sources"]]
    assert "kiwix:vikidia_en_all:nopic" in ids
    assert "kiwix_astronomy_stackexchange" in ids
    # a fixture registry never sees the overlay
    assert source_registry.load_registry(source_registry.registry_path()) is not None
    fixture_ids = [r["id"] for r in _registry()["sources"]]
    assert fixture_ids == ["vik"]
    entry = source_registry.get_source("kiwix:vikidia_en_all:nopic")
    assert entry and entry["kiwix_book"] == "vikidia_en_all"


# -- free disk space: checked before a byte moves, fails closed ---------------

def test_a_disk_without_room_for_the_book_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(source_fetch, "_disk_free",
                        lambda path: source_fetch.DISK_HEADROOM_BYTES + len(BODY) - 1)
    net = Net(_routes())
    result = _fetch(tmp_path, net)
    assert not result["ok"] and result["status"] == "no_room"
    assert "Not enough free space" in result["reason"]
    assert MIRROR_A not in net.calls and PRIMARY not in net.calls
    assert not (tmp_path / FILE).exists()


def test_an_unreadable_disk_size_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(source_fetch, "_disk_free", lambda path: None)
    result = _fetch(tmp_path, Net(_routes()))
    assert not result["ok"] and result["status"] == "no_room"
    assert "free space" in result["reason"]


def test_just_enough_room_is_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(source_fetch, "_disk_free",
                        lambda path: source_fetch.DISK_HEADROOM_BYTES + len(BODY))
    assert _fetch(tmp_path, Net(_routes()))["ok"]


def test_a_resumed_download_only_needs_room_for_the_rest(tmp_path, monkeypatch):
    (tmp_path / (FILE + ".part")).write_bytes(BODY[:100])
    monkeypatch.setattr(source_fetch, "_disk_free",
                        lambda path: source_fetch.DISK_HEADROOM_BYTES + len(BODY) - 100)
    assert source_fetch._room_refusal("x", str(tmp_path), len(BODY),
                                      str(tmp_path / (FILE + ".part"))) is None
