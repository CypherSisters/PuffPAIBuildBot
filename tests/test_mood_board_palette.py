"""The mood board: colours and themes from her own pictures.

A folder of 6 generated pictures gives 3 to 6 named themes, the same themes
every time; her folder is never written; the folder is given only from a
paired device with a ``folder_give`` ticket for exactly that folder, read
only, and only a folder the owner-folders check allows.
"""
from __future__ import annotations

import io
import os
import sys
import types

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import drawbridge  # noqa: E402
from puffpai.integrate import actions, chat, palette, records  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

DEV = "device:deviceaaaa000001"
COLOURS = [((240, 180, 190), (210, 170, 60)), ((245, 240, 225), (180, 200, 170)),
           ((30, 40, 90), (200, 200, 205)), ((240, 180, 190), (250, 250, 250)),
           ((120, 30, 40), (210, 170, 60)), ((30, 40, 90), (180, 200, 170))]


def picture(a, b, size=(90, 60)) -> bytes:
    img = Image.new("RGB", size, a)
    for x in range(size[0] // 2, size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), b)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "Pictures" / "ideas"
    root.mkdir(parents=True)
    for i, (a, b) in enumerate(COLOURS):
        (root / f"idea{i}.jpg").write_bytes(picture(a, b))
    (root / "notes.txt").write_text("not a picture", encoding="utf-8")
    (root / "drawing.svg").write_text("<svg/>", encoding="utf-8")
    return root


def _snapshot(root):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in root.iterdir())


def test_six_pictures_give_three_to_six_themes_every_time(tmp_path, monkeypatch, folder):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    before = _snapshot(folder)
    first = palette.build(str(folder), "mood-a")
    second = palette.build(str(folder), "mood-b")
    assert 3 <= len(first["themes"]) <= 6
    assert [t["name"] for t in first["themes"]] == [t["name"] for t in second["themes"]]
    assert first["pictures_read"] == 6 and first["skipped"] == 0
    for theme in first["themes"]:
        assert theme["name"] and all(c.startswith("#") and len(c) == 7 for c in theme["colours"])
        words = theme["name"].split(" and ")
        assert all(w in {row[0] for row in palette.COLOUR_WORDS} for w in words)
    assert _snapshot(folder) == before                     # her folder was never written
    thumbs = os.path.join(palette.pictures.shelf_dir("mood-a"), "thumbs")
    assert len(os.listdir(thumbs)) == 6
    for name in os.listdir(thumbs):
        with Image.open(os.path.join(thumbs, name)) as img:
            assert max(img.size) <= palette.THUMB_SIDE


def test_colour_words_are_a_fixed_table():
    import numpy as np
    lab = palette._srgb_to_lab(np.array([[250, 250, 250], [10, 10, 10]], dtype=float))
    assert palette.colour_word(lab[0]) == "white"
    assert palette.colour_word(lab[1]) == "black"


# -- the tile, the folder, the ticket, the action ----------------------------------------------

@pytest.fixture
def board(monkeypatch, tmp_path, folder):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch, register_code_project=False)
    drawbridge.register_checker(palette.KIND, palette.palette_rows,
                                block_codes=palette.BLOCK_CODES)
    consent_tickets.reset()
    allowed_root = str(tmp_path / "Pictures")
    checks = []

    def check(path, mode):
        checks.append((path, mode))
        real = os.path.realpath(path)
        if mode != "ro" or not real.startswith(os.path.realpath(allowed_root)):
            return None, "That folder holds private PuffPAI or system files, so no app can have it."
        return real, ""
    of = types.ModuleType("puffpai.security.owner_folders")
    of.check = check
    monkeypatch.setitem(sys.modules, "puffpai.security.owner_folders", of)
    monkeypatch.setattr(actions, "_SOURCES", {})
    actions.register_source("palette", palette.list_actions, palette.run_action)
    monkeypatch.setattr(chat, "_backend", lambda: None)
    out = palette.apply_palette({"id": "pictures:buildbot:palette", "need": "j2"}, sup.Ctx())
    return {"id": out["integration_id"], "checks": checks, "folder": folder,
            "private": tmp_path / "state"}


def test_the_mood_board_waits_for_a_folder(board):
    rec = records.get(board["id"])
    assert rec["status"] == "ready" and rec["mode"] == "folder_palette"
    assert rec["folder"]["granted"] is False
    assert actions.all_actions() == []                     # nothing to read yet


def test_a_folder_from_a_paired_device_makes_the_palette(board):
    got = palette.choose_folder(board["id"], str(board["folder"]), device_id=DEV)
    assert got["ok"] and 3 <= len(got["palette"]["themes"]) <= 6
    assert board["checks"] == [(str(board["folder"]), "ro")] * 2   # checked, then again
    rec = records.get(board["id"])
    assert rec["folder"]["granted"] and rec["folder"]["mode"] == "ro"
    assert rec["folder"]["realpath_sha"] and str(board["folder"]) not in str(rec)
    listed = actions.all_actions()
    assert [a["effect"] for a in listed] == ["reads"]
    assert listed[0]["provenance"]["by"] == "buildbot"
    reply = chat.try_answer("show the colours and themes from my pictures", surface="chat",
                            device_id=DEV)
    assert reply and "(read from Mood board)" in reply["reply"]


@pytest.mark.parametrize("device", [None, "", "peer:1.2.3.4", "anon:x"])
def test_no_paired_device_no_folder(board, device):
    with pytest.raises(palette.PaletteError) as err:
        palette.choose_folder(board["id"], str(board["folder"]), device_id=device)
    assert err.value.code == "not_paired" and board["checks"] == []


def test_a_private_folder_is_refused(board):
    with pytest.raises(palette.PaletteError) as err:
        palette.choose_folder(board["id"], str(board["private"]), device_id=DEV)
    assert "private" in err.value.line
    assert records.get(board["id"])["folder"]["granted"] is False


def test_the_folder_route_needs_a_paired_browser(board):
    from puffpai import api_server
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    resp = client.post(f"/integrations/{board['id']}/folder", headers=auth,
                       json={"path": str(board["folder"])})
    assert resp.status_code == 403
    resp = client.post(f"/integrations/{board['id']}/folder",
                       headers={**auth, "X-Puff-Device-Id": "deviceaaaa000001"},
                       json={"path": str(board["folder"])})
    assert resp.status_code == 200 and resp.get_json()["themes"]
