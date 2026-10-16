"""Things that have their own screen, and what they made.

* a command that prints a local address (127.0.0.1 or localhost with a port)
  gets an Open button in chat and on its skill's page; never an outside one;
* pictures, sound and video a skill made in its own folder are listed and
  served read-only, only by a listed name, never outside that folder.
"""
from __future__ import annotations

import os

import pytest

from puffpai import api_server, brain_commands, skills, thing_media, things


def test_only_local_addresses_are_offered():
    out = brain_commands.local_addresses(
        "Serving on http://localhost:8765/app and http://127.0.0.1:9000 "
        "and https://example.com:443/x and http://192.168.1.4:80/ and "
        "http://localhost:8765/app again, http://127.0.0.1:99999/")
    assert out == ["http://127.0.0.1:8765/app", "http://127.0.0.1:9000/"]
    assert brain_commands.local_addresses("http://localhost:80" + chr(64) + "evil.com/") == []


def _skill(tmp_path_factory):
    root = skills.skills_dir(create=True)
    folder = os.path.join(root, "drawer")
    os.makedirs(os.path.join(folder, "out"), exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: drawer\ndescription: Draws things.\n---\n# Drawer\n")
    return folder


def test_a_screen_is_remembered_for_its_skill(tmp_path_factory):
    folder = _skill(tmp_path_factory)
    brain_commands._remember_screen(folder, ["http://127.0.0.1:8765/"])  # noqa: SLF001
    page = things.page("skill-drawer")
    assert page["screen"] == "http://127.0.0.1:8765/"
    things.remember_screen("skill-drawer", "https://example.com/")
    assert things.page("skill-drawer")["screen"] == "http://127.0.0.1:8765/"


def test_media_it_made_is_listed_and_served_inside_its_folder(tmp_path_factory, tmp_path):
    folder = _skill(tmp_path_factory)
    with open(os.path.join(folder, "out", "cat.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("not media")
    with open(os.path.join(folder, "pic.svg"), "w", encoding="utf-8") as fh:
        fh.write("<svg/>")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    thing = things.get("skill-drawer")
    names = [row["name"] for row in thing_media.listing(thing)]
    assert names == ["out/cat.png"]
    path, mime = thing_media.file_for(thing, "out/cat.png")
    assert mime == "image/png" and path.endswith("cat.png")
    for bad in ("../secret.png", "out/../../secret.png", str(outside), "out\\cat.png",
                "notes.txt", "pic.svg", "", "out//cat.png"):
        assert thing_media.file_for(thing, bad) is None, bad


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def test_the_media_routes_need_a_paired_device(client, tmp_path_factory):
    _skill(tmp_path_factory)
    r = client.get("/things/skill-drawer/media",
                   headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert r.status_code == 403


def test_a_paired_page_gets_the_file_and_nothing_else(client, tmp_path_factory):
    folder = _skill(tmp_path_factory)
    with open(os.path.join(folder, "out", "cat.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    paired = {"Authorization": f"Bearer {api_server._API_KEY}",
              "X-Puff-Device-Id": "deviceaaaa000001"}
    listed = client.get("/things/skill-drawer/media", headers=paired).get_json()
    assert [f["name"] for f in listed["files"]] == ["out/cat.png"]
    got = client.get("/things/skill-drawer/media/file?name=out/cat.png", headers=paired)
    assert got.status_code == 200 and got.mimetype == "image/png"
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    got.close()
    bad = client.get("/things/skill-drawer/media/file?name=../SKILL.md", headers=paired)
    assert bad.status_code == 404


def test_a_media_file_answers_a_byte_range_so_a_video_can_seek(client, tmp_path_factory):
    folder = _skill(tmp_path_factory)
    with open(os.path.join(folder, "out", "cat.png"), "wb") as fh:
        fh.write(b"PNGHEADR" + bytes(range(200)))
    paired = {"Authorization": f"Bearer {api_server._API_KEY}",
              "X-Puff-Device-Id": "deviceaaaa000001"}
    got = client.get("/things/skill-drawer/media/file?name=out/cat.png",
                     headers={**paired, "Range": "bytes=8-17"})
    assert got.status_code == 206
    assert got.headers["Content-Range"] == "bytes 8-17/208"
    assert got.headers["Accept-Ranges"] == "bytes"
    assert got.data == bytes(range(10))
    got.close()
    # The same path checks hold with a range.
    bad = client.get("/things/skill-drawer/media/file?name=../SKILL.md",
                     headers={**paired, "Range": "bytes=0-9"})
    assert bad.status_code == 404
