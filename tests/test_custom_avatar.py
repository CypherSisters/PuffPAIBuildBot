"""Tests for owner-supplied avatars (puffpai.custom_avatar + its API routes).

This is the one path in the dashboard that accepts a file from a person, so the
tests here are mostly adversarial: a renamed archive, a traversal filename, a
file over the cap, and a rejection landing on top of a working avatar.

The data dir is injected per test by monkeypatching the module's own
``get_config``, not by setting ``PUFFPAI_DATA_DIR``. The config singleton is
built once per process, so an env var set here would be ignored whenever
another test module imported the app first - these tests pass in any order.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import io
import json
import os
import struct
from types import SimpleNamespace

import pytest

from puffpai import api_server, custom_avatar


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point custom_avatar at a throwaway data dir for this test only."""
    fake = SimpleNamespace(DATA_DIR=str(tmp_path))
    monkeypatch.setattr(custom_avatar, "get_config", lambda: fake)
    return tmp_path


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


def make_glb(payload: bytes = b'{"asset":{"version":"2.0"}}') -> bytes:
    """Build a minimal but structurally valid .glb.

    Header, then the mandatory JSON chunk padded to a 4-byte boundary - the
    same shape a real exporter emits, which is what the validator checks.
    """
    padded = payload + b" " * ((4 - len(payload) % 4) % 4)
    chunk = struct.pack("<I4s", len(padded), b"JSON") + padded
    total = 12 + len(chunk)
    return struct.pack("<4sII", b"glTF", 2, total) + chunk


def _upload(client, data: bytes, filename: str = "face.glb"):
    return client.post(
        "/avatar/custom",
        headers=_auth(),
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


# ── Content validation: the allowlist is about bytes, not names ─────────────

def test_valid_glb_passes_content_check():
    assert custom_avatar.looks_like_glb(make_glb()) is True


def test_two_chunk_glb_passes_content_check():
    """The shape a real exporter emits: JSON chunk *and* a BIN chunk.

    ``make_glb`` above is JSON-only, which is legal but not what Blender or
    Ready Player Me hands an owner. Every avatar that will ever reach this
    validator has geometry in a second chunk, so the JSON-only case passing is
    not on its own evidence that a real export does.
    """
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    js = b'{"asset":{"version":"2.0"}}'
    js += b" " * ((4 - len(js) % 4) % 4)
    chunks = (struct.pack("<I4s", len(js), b"JSON") + js
              + struct.pack("<I4s", len(positions), b"BIN\x00") + positions)
    glb = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
    assert custom_avatar.looks_like_glb(glb) is True


@pytest.mark.parametrize("payload,why", [
    (b"PK\x03\x04" + b"\x00" * 64, "a zip renamed to .glb"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "a png renamed to .glb"),
    (b"MZ" + b"\x00" * 64, "a windows executable"),
    (b"", "nothing at all"),
    (b"glTF", "the magic bytes and no more"),
])
def test_non_glb_content_is_refused(payload, why):
    assert custom_avatar.looks_like_glb(payload) is False, why


def test_glb_version_1_is_refused():
    """Only glTF 2.0 renders in this stack, so v1 is not quietly accepted."""
    body = make_glb()
    v1 = body[:4] + struct.pack("<I", 1) + body[8:]
    assert custom_avatar.looks_like_glb(v1) is False


def test_glb_claiming_more_bytes_than_it_has_is_refused():
    """The header's length field must describe this file, not a bigger one."""
    body = make_glb()
    lying = body[:8] + struct.pack("<I", len(body) + 4096) + body[12:]
    assert custom_avatar.looks_like_glb(lying) is False


def test_extension_allowlist_refuses_unlisted_types(data_dir):
    for name in ("face.gltf", "face.png", "face.glb.exe", "face", "face.GLB.zip"):
        with pytest.raises(custom_avatar.AvatarRejected):
            custom_avatar.validate_upload(name, make_glb())


def test_extension_check_is_case_insensitive(data_dir):
    assert custom_avatar.validate_upload("FACE.GLB", make_glb())


# ── Traversal ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "../escape.glb",
    "../../escape.glb",
    "..\\escape.glb",
    "sub/escape.glb",
    "..",
    ".",
    "",
])
def test_resolve_within_refuses_escapes(data_dir, name):
    with pytest.raises(custom_avatar.AvatarRejected):
        custom_avatar.resolve_within(str(data_dir), name)


def test_resolve_within_allows_our_own_name(data_dir):
    target = custom_avatar.resolve_within(str(data_dir), custom_avatar.STORED_NAME)
    assert os.path.dirname(target) == os.path.realpath(str(data_dir))


def test_uploaded_filename_never_becomes_the_stored_name(data_dir):
    """A traversal filename cannot escape, because we do not use the name."""
    meta = custom_avatar.save_custom_avatar("../../../evil.glb", make_glb())

    assert meta["stored_name"] == custom_avatar.STORED_NAME
    stored = custom_avatar.custom_avatar_path()
    assert os.path.isfile(stored)
    # The file landed inside the avatar dir and nowhere near the parents.
    assert os.path.realpath(stored).startswith(
        os.path.realpath(custom_avatar.avatar_dir()) + os.sep
    )
    assert not os.path.exists(os.path.join(str(data_dir), "evil.glb"))
    assert not os.path.exists(
        os.path.join(os.path.dirname(str(data_dir)), "evil.glb")
    )


def test_display_name_strips_path_and_markup(data_dir):
    meta = custom_avatar.save_custom_avatar(
        "../<script>alert(1)</script>.glb", make_glb()
    )
    assert "/" not in meta["original_name"]
    assert "<" not in meta["original_name"]
    assert ">" not in meta["original_name"]


# ── Size cap ────────────────────────────────────────────────────────────────

def test_oversize_is_refused_and_states_the_cap(data_dir):
    huge = b"x" * (custom_avatar.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(custom_avatar.AvatarRejected) as exc:
        custom_avatar.validate_upload("big.glb", huge)
    assert str(custom_avatar.MAX_UPLOAD_MB) in str(exc.value)


def test_limits_are_reported_for_the_ui(data_dir):
    limits = custom_avatar.limits()
    assert limits["max_bytes"] == custom_avatar.MAX_UPLOAD_BYTES
    assert limits["allowed_extensions"] == [".glb"]


# ── Persistence ─────────────────────────────────────────────────────────────

def test_upload_persists_and_reads_back(data_dir):
    body = make_glb()
    meta = custom_avatar.save_custom_avatar("My Face.glb", body)

    assert meta["size_bytes"] == len(body)
    assert meta["original_name"] == "My Face.glb"

    with open(custom_avatar.custom_avatar_path(), "rb") as fh:
        assert fh.read() == body

    again = custom_avatar.custom_avatar_meta()
    assert again is not None
    assert again["original_name"] == "My Face.glb"


def test_no_avatar_reports_absent(data_dir):
    assert custom_avatar.custom_avatar_meta() is None


def test_replacing_swaps_the_model(data_dir):
    first = make_glb(b'{"asset":{"version":"2.0"},"n":1}')
    second = make_glb(b'{"asset":{"version":"2.0"},"n":22}')
    custom_avatar.save_custom_avatar("a.glb", first)
    custom_avatar.save_custom_avatar("b.glb", second)

    with open(custom_avatar.custom_avatar_path(), "rb") as fh:
        assert fh.read() == second
    assert custom_avatar.custom_avatar_meta()["original_name"] == "b.glb"


def test_delete_removes_it(data_dir):
    custom_avatar.save_custom_avatar("a.glb", make_glb())
    assert custom_avatar.delete_custom_avatar() is True
    assert custom_avatar.custom_avatar_meta() is None
    assert custom_avatar.delete_custom_avatar() is False


def test_metadata_survives_a_missing_sidecar(data_dir):
    """The model is the source of truth; a lost sidecar costs only the label."""
    custom_avatar.save_custom_avatar("a.glb", make_glb())
    os.unlink(os.path.join(custom_avatar.avatar_dir(), "custom.json"))
    meta = custom_avatar.custom_avatar_meta()
    assert meta is not None and meta["size_bytes"] > 0


def test_sidecar_without_a_model_reports_absent(data_dir):
    custom_avatar.save_custom_avatar("a.glb", make_glb())
    os.unlink(custom_avatar.custom_avatar_path())
    assert custom_avatar.custom_avatar_meta() is None


# ── A rejection must not disturb what is already working ───────────────────

@pytest.mark.parametrize("bad_name,bad_body", [
    ("evil.exe", b"MZ" + b"\x00" * 64),
    ("evil.glb", b"PK\x03\x04" + b"\x00" * 64),
    ("../evil.glb", b"PK\x03\x04" + b"\x00" * 64),
    ("evil.glb", b""),
])
def test_rejected_upload_leaves_the_previous_avatar_working(
    data_dir, bad_name, bad_body
):
    good = make_glb(b'{"asset":{"version":"2.0"},"keep":true}')
    custom_avatar.save_custom_avatar("good.glb", good)

    with pytest.raises(custom_avatar.AvatarRejected):
        custom_avatar.save_custom_avatar(bad_name, bad_body)

    with open(custom_avatar.custom_avatar_path(), "rb") as fh:
        assert fh.read() == good, "the good avatar was disturbed by a rejection"
    assert custom_avatar.custom_avatar_meta()["original_name"] == "good.glb"


def test_oversize_upload_leaves_the_previous_avatar_working(data_dir):
    good = make_glb()
    custom_avatar.save_custom_avatar("good.glb", good)

    with pytest.raises(custom_avatar.AvatarRejected):
        custom_avatar.save_custom_avatar(
            "big.glb", b"x" * (custom_avatar.MAX_UPLOAD_BYTES + 1)
        )

    with open(custom_avatar.custom_avatar_path(), "rb") as fh:
        assert fh.read() == good


def test_no_partial_files_are_left_behind(data_dir):
    custom_avatar.save_custom_avatar("good.glb", make_glb())
    leftovers = [
        n for n in os.listdir(custom_avatar.avatar_dir())
        if n.endswith(".partial")
    ]
    assert leftovers == []


# ── API routes ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/avatar/custom"),
    ("post", "/avatar/custom"),
    ("delete", "/avatar/custom"),
    ("get", "/avatar/custom/model.glb"),
])
def test_avatar_routes_require_the_api_key(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_status_reports_the_cap_before_any_upload(client, data_dir):
    body = client.get("/avatar/custom", headers=_auth()).get_json()
    assert body["success"] is True
    assert body["present"] is False
    assert body["max_mb"] == custom_avatar.MAX_UPLOAD_MB
    assert body["allowed_extensions"] == [".glb"]


def test_upload_then_serve_round_trip(client, data_dir):
    model = make_glb(b'{"asset":{"version":"2.0"},"round":"trip"}')

    resp = _upload(client, model, "Round Trip.glb")
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    status = client.get("/avatar/custom", headers=_auth()).get_json()
    assert status["present"] is True
    assert status["avatar"]["original_name"] == "Round Trip.glb"

    served = client.get("/avatar/custom/model.glb", headers=_auth())
    assert served.status_code == 200
    assert served.data == model
    assert served.mimetype == "model/gltf-binary"


def test_serving_route_404s_when_there_is_no_avatar(client, data_dir):
    resp = client.get("/avatar/custom/model.glb", headers=_auth())
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


@pytest.mark.parametrize("suffix", [
    "/../../../../etc/passwd",
    "/..%2f..%2fetc%2fpasswd",
    "/../custom.json",
    "/other.glb",
    "?path=../../etc/passwd",
])
def test_serving_route_cannot_be_walked_out_of(client, data_dir, suffix):
    """The route takes no filename, so there is nothing to traverse.

    Anything appended is either a route that does not exist (404) or a query
    string the handler ignores - it never selects a different file.
    """
    custom_avatar.save_custom_avatar("good.glb", make_glb())
    resp = client.get("/avatar/custom/model.glb" + suffix, headers=_auth())
    if resp.status_code == 200:
        # Only the query-string case gets this far, and it served OUR file.
        assert resp.data == open(custom_avatar.custom_avatar_path(), "rb").read()
    else:
        assert resp.status_code in (301, 308, 400, 404)
        assert b"root:" not in resp.data


def test_upload_rejects_a_renamed_archive(client, data_dir):
    resp = _upload(client, b"PK\x03\x04" + b"\x00" * 64, "sneaky.glb")
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_upload_rejects_a_disallowed_extension(client, data_dir):
    resp = _upload(client, make_glb(), "face.gltf")
    assert resp.status_code == 400
    assert ".glb" in resp.get_json()["error"]


def test_upload_rejects_oversize_with_413_and_the_cap(client, data_dir):
    resp = _upload(client, b"x" * (custom_avatar.MAX_UPLOAD_BYTES + 1), "big.glb")
    assert resp.status_code == 413
    body = resp.get_json()
    assert body["max_mb"] == custom_avatar.MAX_UPLOAD_MB
    assert str(custom_avatar.MAX_UPLOAD_MB) in body["error"]


def test_upload_with_no_file_is_a_400(client, data_dir):
    resp = client.post("/avatar/custom", headers=_auth(),
                       data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_rejected_upload_over_the_api_keeps_serving_the_old_avatar(
    client, data_dir
):
    good = make_glb(b'{"asset":{"version":"2.0"},"keep":true}')
    assert _upload(client, good, "good.glb").status_code == 200

    assert _upload(client, b"MZ" + b"\x00" * 64, "evil.glb").status_code == 400
    assert _upload(client, make_glb(), "evil.exe").status_code == 400
    assert _upload(client, make_glb(), "../evil.glb").status_code == 200

    served = client.get("/avatar/custom/model.glb", headers=_auth())
    assert served.status_code == 200, "a rejection took the avatar down"
    # The traversal-named upload was accepted on content (it IS a valid glb)
    # and stored under OUR name, so it replaced the model rather than escaping.
    assert served.data == make_glb()


def test_delete_over_the_api(client, data_dir):
    _upload(client, make_glb(), "good.glb")
    assert client.delete("/avatar/custom", headers=_auth()).get_json()["removed"] is True
    assert client.get("/avatar/custom/model.glb", headers=_auth()).status_code == 404


def test_stored_file_lands_under_the_data_dir(client, data_dir):
    _upload(client, make_glb(), "good.glb")
    stored = custom_avatar.custom_avatar_path()
    assert os.path.realpath(stored).startswith(os.path.realpath(str(data_dir)))
    # And explicitly NOT in the source tree.
    repo_src = os.path.dirname(os.path.abspath(custom_avatar.__file__))
    assert not os.path.realpath(stored).startswith(os.path.realpath(repo_src))


def test_metadata_sidecar_is_json(data_dir):
    custom_avatar.save_custom_avatar("a.glb", make_glb())
    path = os.path.join(custom_avatar.avatar_dir(), "custom.json")
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["stored_name"] == custom_avatar.STORED_NAME
