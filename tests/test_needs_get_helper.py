"""needs.get_helper: the reading helper comes only after a yes from this device,
only from its pinned address, only with its pinned check number."""
from __future__ import annotations

import sys
import types

import pytest

from puffpai.wizard import needs


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(needs, "meaning_ready", lambda: False)
    calls: dict = {"redeem": [], "get": [], "install": []}
    tickets = types.ModuleType("puffpai.security.consent_tickets")

    def redeem(ticket, device_id, action, target):
        calls["redeem"].append((ticket, device_id, action, target))
        return (ticket == "good" and device_id == "dev1", "ok")
    tickets.redeem = redeem
    fetch = types.ModuleType("puffpai.source_fetch")

    def get_archive(url, *, allowed_hosts, max_bytes, dest_path, service, expect_sha256=None,
                    max_redirects=2):
        calls["get"].append((url, set(allowed_hosts), service, expect_sha256))
        with open(dest_path, "wb") as fh:
            fh.write(b"x")
        return {"path": dest_path, "sha256": expect_sha256, "bytes": 1, "final_host": "h"}
    fetch.get_archive = get_archive
    monkeypatch.setitem(sys.modules, "puffpai.security.consent_tickets", tickets)
    monkeypatch.setitem(sys.modules, "puffpai.source_fetch", fetch)
    from puffpai.knowledge import reading_helper

    def install(path):
        calls["install"].append(path)
        return {"ok": True, "reason": "The reading helper is ready."}
    monkeypatch.setattr(reading_helper, "install_from_file", install)
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    return calls


def test_a_yes_from_this_device_gets_the_pinned_archive(world):
    from puffpai.knowledge import reading_helper
    lines: list[str] = []
    got = needs.get_helper(ticket="good", device_id="dev1", progress=lines.append)
    assert got == {"state": "ready", "line": needs.HELPER_READY}
    assert world["redeem"] == [("good", "dev1", "add_on", "reading_helper")]
    url, hosts, service, sha = world["get"][0]
    assert url == reading_helper.ARCHIVE_URL and service == "reading_helper"
    assert sha == reading_helper.ARCHIVE_SHA256 and len(hosts) == 1
    assert lines == [needs.HELPER_GETTING]


def test_no_yes_means_nothing_is_fetched(world):
    got = needs.get_helper(ticket="forged", device_id="dev1", progress=None)
    assert got == {"state": "failed", "line": needs.HELPER_NO_YES}
    assert world["get"] == [] and world["install"] == []


def test_a_bad_archive_is_a_plain_failure(world, monkeypatch):
    from puffpai.knowledge import reading_helper
    monkeypatch.setattr(reading_helper, "install_from_file",
                        lambda path: {"ok": False, "reason": "not it"})
    got = needs.get_helper(ticket="good", device_id="dev1", progress=None)
    assert got == {"state": "failed", "line": needs.HELPER_FAILED}


def test_a_download_that_fails_never_raises(world, monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(sys.modules["puffpai.source_fetch"], "get_archive", boom)
    assert needs.get_helper(ticket="good", device_id="dev1", progress=None)["state"] == "failed"


def test_without_the_download_path_it_is_not_in_this_build(monkeypatch):
    monkeypatch.setattr(needs, "meaning_ready", lambda: False)
    monkeypatch.setitem(sys.modules, "puffpai.source_fetch", types.ModuleType("x"))
    got = needs.get_helper(ticket="good", device_id="dev1", progress=None)
    assert got == {"state": "failed", "line": needs.HELPER_NOT_IN_BUILD}


def test_already_there_needs_nothing(monkeypatch):
    monkeypatch.setattr(needs, "meaning_ready", lambda: True)
    assert needs.get_helper(ticket="", device_id="", progress=None)["state"] == "ready"
