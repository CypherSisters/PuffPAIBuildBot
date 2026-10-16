"""A folder given to a browser app: served to the bot's hidden browser only.

``owner_folders`` decides which folders may be given (here its read
roots are pointed at a scratch "Pictures" folder and its vault at a scratch
"vault"). Checks: no answer without the runner secret; the owner's yes is a
device-bound ticket for this app, this folder and this mode; the vault and
other private places are refused; a link or junction INSIDE the given folder
is never followed; key-looking names are
hidden; the grant is checked again on every request.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit as hostkit  # noqa: E402

from puffpai.webapps import files, host, wording  # noqa: E402

APP = "photo-wall-abcdefgh"
DEVICE = "device:owner-laptop-0001"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    from puffpai.security import consent_tickets, owner_folders
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(hostkit.PORT))
    pics = tmp_path / "Pictures"
    (pics / "wedding").mkdir(parents=True)
    (pics / "wedding" / "cake.jpg").write_bytes(b"\xff\xd8cake")
    (pics / "wedding" / "api_key.txt").write_text("sk-live-SECRET", encoding="utf-8")
    (pics / "wedding" / ".env").write_text("TOKEN=SECRET", encoding="utf-8")
    (pics / "wedding" / "notes.txt").write_text("flowers", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "secret.txt").write_text("VAULT", encoding="utf-8")
    monkeypatch.setattr(owner_folders, "read_roots", lambda: [owner_folders._canon(str(pics))])
    monkeypatch.setattr(owner_folders, "vault_dir", lambda: str(vault))
    consent_tickets.reset()
    hostkit.place_app(APP)
    return pics, vault


def _give(path, mode="ro"):
    first = files.give(APP, str(path), mode, device=DEVICE)
    assert first.get("needs_yes"), first
    return files.give(APP, str(path), mode, device=DEVICE, ticket=first["ticket"])


def test_giving_a_folder_needs_her_yes_on_this_device_for_exactly_this_folder(roots):
    pics, _ = roots
    first = files.give(APP, str(pics / "wedding"), "ro", device=DEVICE)
    assert first["needs_yes"] and first["buttons"] == ["Give it", "No"]
    assert first["words"] == ["Let Shelf Keeper read the files in your folder wedding?",
                              "It never sees your vault, your keys or other apps' data."]
    assert files.granted(APP) is None
    # another device cannot spend it
    other = files.give(APP, str(pics / "wedding"), "ro", device="device:someone-else-0002",
                       ticket=first["ticket"])
    assert other["ok"] is False and files.granted(APP) is None
    first = files.give(APP, str(pics / "wedding"), "ro", device=DEVICE)
    # a ticket for one folder does not give another
    assert files.give(APP, str(pics), "ro", device=DEVICE, ticket=first["ticket"])["ok"] is False
    done = _give(pics / "wedding")
    assert done["ok"] and done["line"] == "Shelf Keeper can now read wedding."
    assert files.granted(APP)["mode"] == "ro" and files.granted_name(APP) == "wedding"
    assert files.take_back(APP)["ok"] and files.granted(APP) is None


def test_the_vault_and_places_outside_the_owners_folders_are_refused(roots, tmp_path):
    _, vault = roots
    for bad in (vault, tmp_path, tmp_path / "apps", "C:\\Windows", "\\\\server\\share", ""):
        got = files.give(APP, str(bad), "ro", device=DEVICE)
        assert got["ok"] is False and not got.get("needs_yes"), bad
    # write access only to a folder BuildBot made for it
    pics, _ = roots
    assert files.give(APP, str(pics / "wedding"), "rw", device=DEVICE)["ok"] is False
    assert files.give(APP, str(pics / "wedding"), "xx", device=DEVICE)["refused"] == "bad_mode"


def test_nothing_is_served_without_the_runner_secret(roots):
    pics, _ = roots
    _give(pics / "wedding")
    host.start(None)
    try:
        for hdrs in ({"Sec-Fetch-Site": "same-origin"},
                     {"Sec-Fetch-Site": "same-origin", host.RUNNER_HEADER: "not-it"},
                     {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-site"}):
            status, _, body, _ = hostkit.request("GET", "/__puffpai/files/cake.jpg",
                                                 host_header=f"{APP}.localhost:{hostkit.PORT}",
                                                 headers=hdrs)
            assert status == 403 and b"cake" not in body
        status, hdrs, body, _ = hostkit.request(
            "GET", "/__puffpai/files/cake.jpg", host_header=f"{APP}.localhost:{hostkit.PORT}",
            headers={host.RUNNER_HEADER: host.runner_secret()})
        assert status == 200 and body == b"\xff\xd8cake" and hdrs["content-type"] == "image/jpeg"
        status, _, body, _ = hostkit.request(
            "PUT", "/__puffpai/files/cake.jpg", host_header=f"{APP}.localhost:{hostkit.PORT}",
            headers={host.RUNNER_HEADER: host.runner_secret(),
                     "Origin": f"http://{APP}.localhost:{hostkit.PORT}"}, body=b"x")
        assert status == 405
    finally:
        host.stop()


def test_key_looking_names_are_hidden_and_never_served(roots):
    pics, _ = roots
    _give(pics / "wedding")
    listed = files.listing(files.resolve(APP, "/")[0])
    names = [f["name"] for f in listed["files"]]
    assert names == ["cake.jpg", "notes.txt"]
    for hidden in ("/api_key.txt", "/.env", "/API_KEY.TXT"):
        assert files.resolve(APP, hidden)[0] is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_a_junction_inside_the_given_folder_is_never_followed(roots):
    import _winapi
    pics, vault = roots
    _winapi.CreateJunction(str(vault), str(pics / "wedding" / "album"))
    # The owner_folders check now refuses a folder with a
    # link inside it that leads to a private place, so it is never given...
    first = files.give(APP, str(pics / "wedding"), "ro", device=DEVICE)
    assert not first.get("needs_yes") and first.get("refused") == "folder", first
    # ...and a junction that appears after her yes is never followed or listed.
    os.rmdir(pics / "wedding" / "album")
    done = _give(pics / "wedding")
    assert done["ok"]
    _winapi.CreateJunction(str(vault), str(pics / "wedding" / "album"))
    assert files.resolve(APP, "/album")[0] is None
    assert files.resolve(APP, "/album/secret.txt")[0] is None
    root = files.resolve(APP, "/")[0]
    assert root is None or "album" not in files.listing(root)["folders"]


def test_the_grant_is_checked_again_on_every_request(roots, monkeypatch):
    from puffpai.security import owner_folders
    pics, _ = roots
    _give(pics / "wedding")
    assert files.resolve(APP, "/cake.jpg")[1] == "file"
    # the folder stops passing the owner_folders rules (for example it moved into a private place)
    monkeypatch.setattr(owner_folders, "read_roots", lambda: [])
    assert files.granted(APP) is None and files.resolve(APP, "/cake.jpg") == (None, "no_folder")


@pytest.mark.parametrize("raw", ["/..%2f..%2fvault/secret.txt", "/%5c..%5cvault", "/../vault/secret.txt",
                                 "/cake.jpg::$DATA", "/PROGRA~1", "/cake.jpg."])
def test_path_tricks_are_refused(roots, raw):
    pics, _ = roots
    _give(pics / "wedding")
    assert files.resolve(APP, raw)[0] is None


def test_without_lane_l7_no_folder_can_be_given(roots, monkeypatch):
    monkeypatch.setattr(files, "_folders", lambda: None)
    pics, _ = roots
    got = files.give(APP, str(pics / "wedding"), "ro", device=DEVICE)
    assert got["ok"] is False and got["message"] == wording.FOLDER_NOT_IN_BUILD


def test_the_folder_route_needs_a_paired_device(roots, monkeypatch):
    from flask import Flask
    from puffpai.webapps import api
    pics, _ = roots
    app = Flask("t")
    app.register_blueprint(api.bp)
    c = app.test_client()
    monkeypatch.setattr(api, "_device", lambda: "")
    r = c.post(f"/webapps/{APP}/folder", json={"path": str(pics / "wedding"), "mode": "ro"})
    assert r.status_code == 403 and r.get_json()["refused"] == "not_paired"
    monkeypatch.setattr(api, "_device", lambda: DEVICE)
    first = c.post(f"/webapps/{APP}/folder", json={"path": str(pics / "wedding"), "mode": "ro"}).get_json()
    assert first["needs_yes"]
    done = c.post(f"/webapps/{APP}/folder", json={"path": str(pics / "wedding"), "mode": "ro",
                                                  "ticket": first["ticket"]}).get_json()
    assert done["ok"] and files.granted(APP) is not None
    assert c.post(f"/webapps/{APP}/folder", json={"path": ""}).get_json()["ok"]
    assert files.granted(APP) is None
    host.refresh()


def test_the_undo_route_needs_a_paired_device_and_refuses_after_a_newer_write(roots, monkeypatch):
    from flask import Flask
    from puffpai.webapps import api, mirror
    app = Flask("t")
    app.register_blueprint(api.bp)
    c = app.test_client()
    mirror.put(APP, 0, {"k": "1"})
    mirror.put(APP, 1, {"k": "2"})
    monkeypatch.setattr(api, "_device", lambda: "")
    assert c.post(f"/webapps/{APP}/undo", json={"version": 2}).status_code == 403
    monkeypatch.setattr(api, "_device", lambda: DEVICE)
    mirror.put(APP, 2, {"k": "3"})
    r = c.post(f"/webapps/{APP}/undo", json={"version": 2}).get_json()
    assert r["ok"] is False and r["line"] == wording.UNDO_NEWER
    r = c.post(f"/webapps/{APP}/undo", json={"version": 3}).get_json()
    assert r["ok"] is True and r["line"] == wording.UNDONE and mirror.get(APP)["data"] == {"k": "2"}
    mirror.begin_bot_run(APP)
    try:
        r = c.post(f"/webapps/{APP}/undo", json={"version": 4}).get_json()
        assert r["ok"] is False and r["line"] == wording.UNDO_BUSY
    finally:
        mirror.end_bot_run(APP)
    assert c.post(f"/webapps/{APP}/undo", json={"version": True}).status_code == 403
    host.refresh()
