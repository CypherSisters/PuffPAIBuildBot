"""Toolsmith downloads exactly the version pinned when the plan was made.

No network: ``source_fetch.get_archive`` and ``security.safe_archive`` are
fakes with the pinned signatures.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai.integrate import fetch, records  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    apps = tmp_path / "apps"
    tree = tmp_path / "tree"
    sup.static_tree(str(tree))
    f = sup.found()
    archives = {sup.archive_url(f): sup.tar_gz_of(str(tree))}
    fakes = sup.install_fakes(monkeypatch, str(apps), archives=archives)
    return {"found": f, "apps": str(apps), "state": str(tmp_path / "state"),
            "archives": archives, "tree": str(tree), **fakes}


def test_the_archive_address_uses_the_pinned_commit():
    f = sup.found()
    url, host = fetch.archive_url(f)
    assert url == ("https://codeload.github.com/someone/wedding-planner/tar.gz/"
                   + sup.COMMIT)
    assert host == "codeload.github.com"
    cb = sup.found(host="codeberg.org")
    url, host = fetch.archive_url(cb)
    assert url == f"https://codeberg.org/someone/wedding-planner/archive/{sup.COMMIT}.tar.gz"
    assert host == "codeberg.org"


@pytest.mark.parametrize("commit", ["", "main", "HEAD", "752fb66", None, "g" * 40])
def test_no_pinned_commit_no_download(env, commit):
    f = sup.found(commit=commit)
    rid = records.new_id("x")
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(rid, f)
    assert exc.value.code == "not_pinned"
    assert env["fetch_calls"] == []


@pytest.mark.parametrize("host", ["gitlab.example", "github.com.evil.example", ""])
def test_only_known_forges(env, host):
    f = sup.found(host=host)
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(records.new_id("x"), f)
    assert exc.value.code == "wrong_host"
    assert env["fetch_calls"] == []


def test_owner_and_name_cannot_steer_the_address(env):
    f = sup.found(owner="..", name="x")
    with pytest.raises(fetch.FetchRefused):
        fetch.fetch(records.new_id("x"), f)
    f = sup.found(owner="a/b", name="x")
    with pytest.raises(fetch.FetchRefused):
        fetch.fetch(records.new_id("x"), f)


def test_download_goes_through_the_code_exit_and_lands_in_the_apps_folder(env):
    rid = records.new_id("Big Day")
    got = fetch.fetch(rid, env["found"])
    call = env["fetch_calls"][0]
    assert call["service"] == "code_download"
    assert call["allowed_hosts"] == {"codeload.github.com"}
    assert call["max_bytes"] == fetch.MAX_ARCHIVE_BYTES >= 2 * 1024 ** 3
    assert call["expect_sha256"] is None
    assert os.path.realpath(call["dest_path"]).startswith(
        os.path.realpath(os.path.join(env["state"], "integrations", rid)))
    root = os.path.realpath(env["apps"])
    assert os.path.realpath(got["src_dir"]) == os.path.join(root, rid, "src")
    assert os.path.isfile(os.path.join(got["src_dir"], "index.html"))
    # Outside the state (keys, vault) folder.
    assert not os.path.realpath(got["src_dir"]).startswith(os.path.realpath(env["state"]))
    assert got["commit"] == sup.COMMIT and len(got["sha256"]) == 64


def test_a_changed_archive_on_refetch_is_refused(env):
    rid = records.new_id("Big Day")
    first = fetch.fetch(rid, env["found"])
    # Same address, different bytes (a force-push would do this to a branch;
    # a commit address should never change, so a change is a stop).
    sup.write(env["tree"], "extra.js", "evil()")
    env["archives"][sup.archive_url(env["found"])] = sup.tar_gz_of(env["tree"])
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(rid, env["found"], expect_sha256=first["sha256"])
    assert exc.value.code == "pin_changed"
    assert env["fetch_calls"][-1]["expect_sha256"] == first["sha256"]


def test_a_get_archive_that_ignores_the_fingerprint_is_still_caught(env, monkeypatch):
    from puffpai import source_fetch
    rid = records.new_id("Big Day")
    first = fetch.fetch(rid, env["found"])

    def sloppy(url, *, allowed_hosts, max_bytes, dest_path, service,
               expect_sha256=None, max_redirects=2):
        with open(dest_path, "wb") as fh:
            fh.write(b"other")
        return {"path": dest_path, "sha256": "b" * 64, "bytes": 5,
                "final_host": "codeload.github.com"}
    monkeypatch.setattr(source_fetch, "get_archive", sloppy, raising=False)
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(rid, env["found"], expect_sha256=first["sha256"])
    assert exc.value.code == "pin_changed"


def test_a_redirect_to_another_host_is_refused(env, monkeypatch):
    from puffpai import source_fetch

    def moved(url, *, allowed_hosts, max_bytes, dest_path, service,
              expect_sha256=None, max_redirects=2):
        with open(dest_path, "wb") as fh:
            fh.write(b"x")
        return {"path": dest_path, "sha256": "c" * 64, "bytes": 1,
                "final_host": "objects.example"}
    monkeypatch.setattr(source_fetch, "get_archive", moved, raising=False)
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(records.new_id("x"), env["found"])
    assert exc.value.code == "wrong_host"


def test_without_the_download_helpers_nothing_is_fetched(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "puffpai.security.safe_archive", None)
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(records.new_id("x"), env["found"])
    assert exc.value.code == "not_in_build"
    assert env["fetch_calls"] == []


def test_the_licence_file_is_read_from_the_tree(tmp_path):
    root = str(tmp_path)
    assert fetch.read_license(root)["family"] == "none"
    sup.write(root, "LICENSE", sup.MIT_TEXT)
    got = fetch.read_license(root)
    assert got["family"] == "MIT" and got["open"] and got["file"] == "LICENSE"
    sup.write(root, "LICENSE", sup.APACHE_TEXT)
    assert fetch.read_license(root)["family"] == "Apache-2.0"
    sup.write(root, "LICENSE", sup.MIT_TEXT + "\n\"Commons Clause\" License Condition v1.0\n")
    got = fetch.read_license(root)
    assert got["family"] == "Commons-Clause" and not got["open"]
    sup.write(root, "LICENSE", "All rights reserved. Ask me first.")
    assert fetch.read_license(root)["family"] == "custom"


@pytest.mark.parametrize("spdx,family", [
    ("MIT", "MIT"), ("GPL-3.0-or-later", "GPL-3.0"), ("NOASSERTION", "none"),
    ("CC-BY-4.0 (from LICENSE file)", "CC-BY-4.0"), ("CC-BY-NC-SA-4.0", "CC-BY-NC"),
    ("BUSL-1.1", "BUSL"), ("MIT + Commons Clause", "Commons-Clause"),
    ("PLANKA Community License 1.1", "custom")])
def test_licence_families(spdx, family):
    assert fetch.family_of(spdx) == family


NL = chr(10)
CR = chr(13)


@pytest.mark.parametrize("bad", ["abc-abcdefgh" + NL, "abc-abcdefgh" + CR + NL,
                                 "abc-abcdefgh ", NL + "abc-abcdefgh", "ABC-abcdefgh"])
def test_an_id_with_a_trailing_newline_is_not_an_id(bad):
    assert records.valid_id("abc-abcdefgh")
    assert not records.valid_id(bad)
    assert not records.ID_RE.match(bad)       # other parts use the pattern directly
    with pytest.raises(records.RecordError):
        records.folder(bad)


@pytest.mark.parametrize("repo_over,code", [
    ({"owner": "evil" + NL}, "wrong_host"),
    ({"name": "x" + NL}, "wrong_host"),
    ({"pinned_commit": "a" * 40 + NL}, "not_pinned"),
])
def test_a_trailing_newline_cannot_steer_the_address(repo_over, code):
    f = sup.found()
    f["repo"].update(repo_over)
    with pytest.raises(fetch.FetchRefused) as err:
        fetch.archive_url(f)
    assert err.value.code == code



# -- the archive must hold the pinned commit ----

def test_an_archive_that_holds_another_commit_is_not_the_pinned_version(env):
    env["archives"][sup.archive_url(env["found"])] = sup.tar_gz_of(env["tree"],
                                                                    commit=sup.COMMIT_B)
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(records.new_id("Big Day"), env["found"])
    assert exc.value.code == "not_pinned"


def test_an_archive_that_does_not_name_its_commit_is_not_the_pinned_version(env, tmp_path):
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.USTAR_FORMAT) as tar:
        tar.add(os.path.join(env["tree"], "index.html"), arcname="proj/index.html")
    env["archives"][sup.archive_url(env["found"])] = buf.getvalue()
    with pytest.raises(fetch.FetchRefused) as exc:
        fetch.fetch(records.new_id("Big Day"), env["found"])
    assert exc.value.code == "not_pinned"


def test_archive_commit_reads_only_a_small_first_header(tmp_path):
    import gzip
    import tarfile
    info = tarfile.TarInfo("pax")
    info.type = tarfile.XGLTYPE
    info.size = 50 * 1024 * 1024
    path = tmp_path / "big.tgz"
    path.write_bytes(gzip.compress(info.tobuf(format=tarfile.USTAR_FORMAT) + b"\0" * 4096))
    assert fetch.archive_commit(str(path)) == ""
    (tmp_path / "junk.tgz").write_bytes(b"not gzip at all")
    assert fetch.archive_commit(str(tmp_path / "junk.tgz")) == ""
