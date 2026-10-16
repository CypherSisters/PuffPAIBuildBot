"""Pictures are kept only as clean re-encoded copies.

SVG and anything that is not an ordinary raster picture is refused; the pixel
cap stops a decompression bomb; the kept copy has no metadata and nothing
that followed the image data; stock pictures come only from the host the
plan named, with their credit kept.
"""
from __future__ import annotations

import io
import json
import os
import sys
import types

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import drawbridge  # noqa: E402
from puffpai.integrate import pictures, records  # noqa: E402


def jpeg_with_exif(size=(64, 48), colour=(200, 40, 90)) -> bytes:
    img = Image.new("RGB", size, colour)
    exif = Image.Exif()
    exif[0x010E] = "SECRET DESCRIPTION"        # ImageDescription
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}        # GPS
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif.tobytes(), comment=b"hidden comment")
    return out.getvalue() + b"PAYLOAD-AFTER-THE-IMAGE"


def png_bytes(size=(40, 40), mode="RGBA", colour=(0, 120, 200, 128)) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, format="PNG")
    return out.getvalue()


def test_svg_is_refused():
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(pictures.PictureRefused) as err:
        pictures.reencode(svg, max_bytes=10_000)
    assert err.value.code == "not_raster"
    assert pictures.sniff(svg) == "SVG"


@pytest.mark.parametrize("data", [b"not a picture at all", b"%PDF-1.7 ...", b"MZ\x90\x00"])
def test_other_bytes_are_refused(data):
    with pytest.raises(pictures.PictureRefused):
        pictures.reencode(data, max_bytes=10_000)


def test_a_file_name_does_not_decide_the_kind():
    """PNG bytes are a PNG whatever they are called; SVG bytes never pass."""
    assert pictures.sniff(png_bytes()) == "PNG"


def test_the_pixel_cap_refuses_a_bomb(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)  # put back after
    monkeypatch.setattr(pictures, "MAX_PIXELS", 100 * 100)
    big = io.BytesIO()
    Image.new("RGB", (400, 400), (1, 2, 3)).save(big, format="PNG")
    with pytest.raises(pictures.PictureRefused) as err:
        pictures.reencode(big.getvalue(), max_bytes=10_000_000)
    assert err.value.code == "too_big"


def test_the_byte_cap():
    with pytest.raises(pictures.PictureRefused) as err:
        pictures.reencode(jpeg_with_exif(), max_bytes=100)
    assert err.value.code == "too_big"


def test_reencode_strips_metadata_and_trailing_payload():
    clean, ext = pictures.reencode(jpeg_with_exif(), max_bytes=1_000_000)
    assert ext == ".jpg"
    assert b"SECRET DESCRIPTION" not in clean and b"hidden comment" not in clean
    assert b"PAYLOAD-AFTER-THE-IMAGE" not in clean
    with Image.open(io.BytesIO(clean)) as img:
        assert not img.getexif() and img.size == (64, 48)


def test_transparency_is_kept_as_png():
    clean, ext = pictures.reencode(png_bytes(), max_bytes=1_000_000)
    assert ext == ".png"
    with Image.open(io.BytesIO(clean)) as img:
        assert img.mode == "RGBA"


def test_a_gif_keeps_only_its_first_frame():
    frames = [Image.new("P", (10, 10), i) for i in (1, 2, 3)]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:])
    clean, _ext = pictures.reencode(out.getvalue(), max_bytes=1_000_000)
    with Image.open(io.BytesIO(clean)) as img:
        assert getattr(img, "n_frames", 1) == 1


# -- the stock picture applier -------------------------------------------------------------

@pytest.fixture
def stock(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch, register_code_project=False)
    drawbridge.register_checker(pictures.KIND, pictures.picture_rows,
                                block_codes=pictures.BLOCK_CODES,
                                hold_codes=pictures.HOLD_CODES)
    calls = []
    body = {"data": jpeg_with_exif()}

    def fetch_small(url, *, allowed_hosts, max_bytes, service, **kw):
        calls.append({"url": url, "hosts": set(allowed_hosts), "service": service,
                      "max_bytes": max_bytes})
        return {"body": body["data"], "reason": "ok", "status": 200, "truncated": False,
                "headers": {}}
    from puffpai import source_fetch
    monkeypatch.setattr(source_fetch, "fetch_small", fetch_small)
    found = {"schema": "found/v1", "id": "commons:Cake.jpg", "source": "commons",
             "part": "pictures", "name": "Cake.jpg", "friendly": "A layered cake",
             "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Cake.jpg",
             "picture": {"license": "CC0", "credit": "Someone", "full_host": "upload.wikimedia.org"},
             "meaning": {"job": "j2"}}
    mod = types.ModuleType("puffpai.wizard.find_parts")
    mod.found_for = lambda item_id: found if item_id == "pictures:commons:Cake.jpg" else None
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", mod)
    ctx = sup.Ctx()
    ctx.shared = {"needs": {"jobs": [{"id": "j2", "name": "Track themes from pictures"}]}}
    return {"calls": calls, "found": found, "ctx": ctx, "body": body}


ITEM = {"id": "pictures:commons:Cake.jpg", "label": "A layered cake"}


def test_a_stock_picture_is_kept_clean_with_its_credit(stock):
    out = pictures.apply_picture(ITEM, stock["ctx"])
    assert out["status"] == "added", out
    call = stock["calls"][0]
    assert call["hosts"] == {"upload.wikimedia.org"} and call["service"] == "picture_download"
    assert call["max_bytes"] == pictures.MAX_STOCK_BYTES
    rec = records.get(out["integration_id"])
    shelf = rec["pictures_dir"]
    assert shelf == "track-themes-from-pictures"
    files = [f for f in os.listdir(pictures.shelf_dir(shelf)) if f.endswith(".jpg")]
    assert len(files) == 1
    data = open(os.path.join(pictures.shelf_dir(shelf), files[0]), "rb").read()
    assert b"SECRET DESCRIPTION" not in data
    credit = pictures.credits(shelf)[0]
    assert credit["license"] == "CC0" and credit["credit"] == "Someone"
    # a second picture for the same job joins the same shelf
    out2 = pictures.apply_picture(ITEM, stock["ctx"])
    assert out2["integration_id"] == out["integration_id"]
    assert len(pictures.credits(shelf)) == 2


def test_a_picture_from_another_host_is_refused(stock):
    stock["found"]["url"] = "https://evil.example/Cake.jpg"
    out = pictures.apply_picture(ITEM, stock["ctx"])
    assert out["status"] == "refused" and stock["calls"] == []


def test_an_svg_download_is_refused(stock):
    stock["body"]["data"] = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    out = pictures.apply_picture(ITEM, stock["ctx"])
    assert out["status"] == "refused"
    assert not os.path.isdir(pictures.pictures_root()) or not any(
        f.endswith((".jpg", ".png")) for _d, _s, fs in os.walk(pictures.pictures_root())
        for f in fs)


def test_offline_leaves_it_out(stock):
    stock["ctx"].online = False
    out = pictures.apply_picture(ITEM, stock["ctx"])
    assert out["status"] == "failed" and stock["calls"] == []


def test_a_picture_set_project_is_reencoded(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    tree = tmp_path / "tree"
    sup.write(str(tree), "a.jpg", jpeg_with_exif())
    sup.write(str(tree), "b.png", png_bytes())
    sup.write(str(tree), "c.svg", "<svg/>")
    rec = records.blank(records.new_id("Mood pictures"), name="Mood pictures")
    got = pictures.import_tree(rec, str(tree))
    assert got["kept"] == 2 and "not_raster" in got["refused"]
    names = sorted(os.listdir(pictures.shelf_dir(got["shelf"])))
    assert [n for n in names if n.endswith((".jpg", ".png"))] and not any(
        n.endswith(".svg") for n in names)
    assert json.load(open(os.path.join(pictures.shelf_dir(got["shelf"]), "credits.json")))
