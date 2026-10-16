"""A damaged outside-switch file never lets anything back on by itself.

A damaged ``outside_switches.json`` correctly read as
every service off, but the next ``set_switch`` rewrote the file with one
entry, so the owner's earlier OFF switches went back to "as built" (ON for the
book library and Wikipedia books). Now the damaged file is kept aside, the
owner is told in plain words on "What leaves your computer", and until they
switch a service on again it stays off -- whatever else is written meanwhile.
"""
from __future__ import annotations

import json
import os

import pytest

from livedata_support import jargon_in
from puffpai import api_server, outside_services, plain_words
from puffpai.security import egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    outside_switches.forget()
    egress_doors.reset_cache()
    yield
    outside_switches.forget()
    egress_doors.reset_cache()


def _damage(extra: str = "garbage") -> None:
    with open(outside_switches.path(), "a", encoding="utf-8") as fh:
        fh.write(extra)
    outside_switches.forget()


def _kept_aside() -> list[str]:
    folder = os.path.dirname(outside_switches.path())
    return sorted(n for n in os.listdir(folder)
                  if n.startswith(outside_switches.DAMAGED_PREFIX))


def test_a_later_write_does_not_switch_earlier_offs_back_on():
    """The exact sequence."""
    outside_switches.set_switch("library", False)
    outside_switches.set_switch("wikipedia_books", False)
    _damage()
    assert outside_switches.is_off("library")
    assert outside_switches.is_off("wikipedia_books")
    outside_switches.set_switch("weather", True)       # the owner's next write
    outside_switches.forget()
    assert outside_switches.is_off("library")
    assert outside_switches.is_off("wikipedia_books")
    assert outside_switches.get("weather") is True
    # Services that were never set are off too, not "as built".
    assert outside_switches.is_off("gutenberg_books")
    assert outside_switches.is_off("catalog_refresh")


def test_the_damaged_file_is_kept_aside_not_overwritten():
    outside_switches.set_switch("library", False)
    damaged = open(outside_switches.path(), encoding="utf-8").read() + "garbage"
    _damage()
    outside_switches.get("library")                    # the first read keeps it
    kept = _kept_aside()
    assert len(kept) == 1
    folder = os.path.dirname(outside_switches.path())
    assert open(os.path.join(folder, kept[0]), encoding="utf-8").read() == damaged
    fresh = json.loads(open(outside_switches.path(), encoding="utf-8").read())
    assert fresh["services"] == {}
    assert fresh["reset"]["kept_as"] == kept[0]


def test_hiding_the_note_switches_nothing_on():
    _damage("{not json at all")
    assert outside_services.notice("browser")[1] is True
    assert outside_services.acknowledge_notice() is True
    outside_switches.forget()
    assert outside_services.notice("browser") == ("", False)
    for sid in ("library", "wikipedia_books", "sample_look", "internet_check"):
        assert outside_switches.is_off(sid), sid
    # Clearing one entry after a reset is "off", never "as built".
    outside_switches.set_switch("library", True)
    outside_switches.clear("library")
    outside_switches.forget()
    assert outside_switches.is_off("library")


def test_switching_one_back_on_is_the_owners_choice_one_at_a_time():
    _damage("[]")
    outside_services.switch_on("library", by="owner")   # after a ticket, in the app
    outside_switches.forget()
    assert outside_switches.get("library") is True
    assert outside_switches.is_off("wikipedia_books")
    row = outside_services.row("wikipedia_books")
    assert row["state"] == "off" and row["can_switch"]
    assert row["state_line"] == outside_services.RESET_ROW_LINE


def test_the_owner_is_told_in_plain_words_on_the_page_and_in_the_terminal():
    _damage()
    page = outside_services.page("browser")
    assert page["notice"] and page["notice_can_ok"]
    assert "switched off to be safe" in page["notice"]
    assert _kept_aside()[0] in page["notice"]
    assert not plain_words.check(page["notice"])
    assert not jargon_in(page["notice"])
    term = outside_services.page("terminal")["notice"]
    assert "puffpai outside ok" in term
    lines = {r["state_line"] for r in page["services"]
             if r["id"] in ("library", "wikipedia_books")}
    assert lines == {outside_services.RESET_ROW_LINE}
    assert not plain_words.check(outside_services.RESET_ROW_LINE)


def test_the_page_routes_show_and_hide_the_note(monkeypatch):
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    assert client.post("/outside/notice/ok").status_code == 401   # needs the key
    _damage()
    body = client.get("/outside", headers=AUTH).get_json()
    assert body["notice"] and body["notice_can_ok"] is True
    done = client.post("/outside/notice/ok", headers=DEVICE)
    assert done.status_code == 200 and done.get_json()["hidden"] is True
    body = client.get("/outside", headers=AUTH).get_json()
    assert body["notice"] == ""
    assert all(r["state"] != "on" for r in body["services"]
               if r["id"] in ("library", "wikipedia_books", "gutenberg_books"))


def test_an_unreadable_file_is_never_written_over(monkeypatch):
    outside_switches.set_switch("library", False)
    before = open(outside_switches.path(), encoding="utf-8").read()
    real_open = open

    def _deny(path, *a, **k):
        if os.fspath(path) == outside_switches.path() and "r" in (a[0] if a else k.get("mode", "r")):
            raise PermissionError("denied")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", _deny)
    outside_switches.forget()
    assert outside_switches.is_off("weather") and outside_switches.is_off("library")
    with pytest.raises(outside_switches.SwitchesUnreadable):
        outside_switches.set_switch("weather", True)
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_off("library")
    words, can_ok = outside_services.notice("browser")
    assert "cannot be read" in words and can_ok is False
    monkeypatch.setattr("builtins.open", real_open)
    assert open(outside_switches.path(), encoding="utf-8").read() == before
    assert _kept_aside() == []


def test_a_damaged_reset_record_is_itself_kept_aside():
    _damage()
    outside_switches.get("library")
    data = json.loads(open(outside_switches.path(), encoding="utf-8").read())
    data["reset"] = "nonsense"
    with open(outside_switches.path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    outside_switches.forget()
    assert outside_switches.is_off("library")
    assert len(_kept_aside()) == 2
