"""App ids come from the caller in one shape; check-copy addresses are never reused."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))

from puffpai import webapps  # noqa: E402
from puffpai.webapps import host, install  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "webapps", "rows_delete")

BAD_IDS = ["", "shelf", "shelf-abcdefg", "shelf-abcdefghi", "Shelf-abcdefgh", "shelf-abcdefg1",
           "shelf-ABCDEFGH", "-shelf-abcdefgh", "shelf_keeper-abcdefgh", "a" * 31 + "-abcdefgh",
           "../shelf-abcdefgh", "shelf-abcdefgh/", "shelf-abcdefgh\n", "chk-abcdefghijklmnop",
           "shelf.keeper-abcdefgh", None, 12]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_install_refuses_an_id_of_another_shape(bad, tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    with pytest.raises(ValueError):
        install.install_static(bad, FIXTURE, source={}, name="x", purpose="", jobs=[],
                               progress=lambda _l: None)
    assert not os.path.exists(tmp_path / "apps") or not os.listdir(tmp_path / "apps")


@pytest.mark.parametrize("good", ["shelf-abcdefgh", "a-22222222", "my-app-2-a2b3c4d5",
                                  "x" * 30 + "-abcdefgh", "0abc-zzzzzzzz"])
def test_the_issued_shape_is_accepted(good):
    assert webapps.valid_app_id(good)
    assert host.origin_for(good).startswith(f"http://{good}.localhost:")


@pytest.mark.parametrize("bad", BAD_IDS[:-2])
def test_origin_for_refuses_other_shapes(bad):
    if isinstance(bad, str) and webapps.CHECK_RE.match(bad):
        return  # a check copy label is a valid address label of its own kind
    with pytest.raises(ValueError):
        host.origin_for(bad)


def test_check_copy_addresses_are_unique_and_never_an_app_id():
    seen = set()
    for _ in range(1000):
        label = host.new_check_copy("shelf-abcdefgh")
        assert webapps.CHECK_RE.match(label)
        assert not webapps.ID_RE.match(label)
        assert label not in seen
        seen.add(label)
    for label in seen:
        host.drop_check_copy(label)
    # dropped labels are never handed out again in this process
    again = {host.new_check_copy("shelf-abcdefgh") for _ in range(200)}
    assert not (again & seen)
    for label in again:
        host.drop_check_copy(label)


def test_a_check_copy_needs_a_real_app_id():
    for bad in ("", "chk-abcdefghijklmnop", "nope"):
        with pytest.raises(ValueError):
            host.new_check_copy(bad)
