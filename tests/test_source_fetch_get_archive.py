"""source_fetch.get_archive: a project's archive at its pinned version.

No network: ``source_fetch._default_transport`` and ``_default_resolver`` are
replaced for each test. Proven here: hosts off the list, plain http, inside
addresses, a third redirect, an over-size body and a fingerprint mismatch are
refused with a one-word code and leave no file behind; one log line per hop.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from puffpai import source_fetch
from puffpai.security import egress_ledger, outside_switches

GOOD = b"PK\x03\x04 pretend archive bytes " * 50
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
AT = "@"  # kept apart so no line reads like an email address


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = dict(headers or {})

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    table: dict = {}
    calls: list = []
    answers = {"addr": ["140.82.112.4"]}

    def transport(url, headers, timeout):
        calls.append(url)
        got = table.get(url)
        if got is None:
            return Resp(404)
        return got() if callable(got) else got

    monkeypatch.setattr(source_fetch, "_default_transport", transport)
    monkeypatch.setattr(source_fetch, "_default_resolver", lambda h: answers["addr"])
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_BY_HOST", {})
    source_fetch.clear_caches()
    outside_switches.forget()
    return table, calls, answers


def _lines():
    return list(reversed(egress_ledger.read(1, limit=100)))


def _get(url, dest, **kw):
    kw.setdefault("allowed_hosts", {"codeload.github.com", "github.com"})
    kw.setdefault("max_bytes", 1_000_000)
    kw.setdefault("service", "code_download")
    return source_fetch.get_archive(url, dest_path=str(dest), **kw)


URL = "https://codeload.github.com/o/r/tar.gz/0123456789abcdef0123456789abcdef01234567"


def test_a_good_download_with_its_fingerprint(net, tmp_path):
    table, calls, _ = net
    table[URL] = lambda: Resp(200, GOOD, {"Content-Length": str(len(GOOD))})
    dest = tmp_path / "dl" / "download.tar.gz"
    got = _get(URL, dest, expect_sha256=GOOD_SHA)
    assert got == {"path": str(dest), "sha256": GOOD_SHA, "bytes": len(GOOD),
                   "final_host": "codeload.github.com"}
    assert dest.read_bytes() == GOOD
    (line,) = _lines()
    assert (line["service"], line["what"], line["outcome"], line["path"]) == (
        "code_download", "code_download", "done", "")
    assert line["bytes_in"] == len(GOOD)


def test_one_line_per_hop_through_a_redirect(net, tmp_path):
    table, calls, _ = net
    start = "https://github.com/o/r/archive/0123456789abcdef.tar.gz"
    table[start] = Resp(302, b"", {"Location": URL})
    table[URL] = lambda: Resp(200, GOOD)
    got = _get(start, tmp_path / "a.tgz")
    assert got["final_host"] == "codeload.github.com"
    lines = _lines()
    assert [(l["host"], l["outcome"]) for l in lines] == [
        ("github.com", "done"), ("codeload.github.com", "done")]
    assert calls == [start, URL]


@pytest.mark.parametrize("url,code", [
    ("https://evil.example/x.tar.gz", "host"),
    ("http://codeload.github.com/o/r/tar.gz/abc", "not_encrypted"),
    ("https://codeload.github.com:444/o/r", "not_allowed"),
    ("https://me:pw" + AT + "codeload.github.com/o/r", "sign_in_refused"),
])
def test_refused_before_sending(net, tmp_path, url, code):
    _table, calls, _ = net
    dest = tmp_path / "x.tgz"
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(url, dest)
    assert exc.value.code == code and exc.value.code in source_fetch.REASON_CODES
    assert calls == [] and not dest.exists()
    assert _lines()[-1]["code"] == code


def test_an_inside_address_is_refused(net, tmp_path):
    _table, calls, answers = net
    answers["addr"] = ["192.168.1.20"]
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, tmp_path / "x.tgz")
    assert exc.value.code == "inside_address" and calls == []


def test_a_redirect_to_an_unlisted_host_is_refused(net, tmp_path):
    table, calls, _ = net
    table[URL] = Resp(302, b"", {"Location": "https://evil.example/x"})
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, tmp_path / "x.tgz")
    assert exc.value.code == "host"
    assert calls == [URL]


def test_a_third_redirect_is_refused(net, tmp_path):
    table, calls, _ = net
    hops = [f"https://github.com/hop{i}" for i in range(4)]
    for here, there in zip(hops, hops[1:]):
        table[here] = Resp(302, b"", {"Location": there})
    table[hops[-1]] = lambda: Resp(200, GOOD)
    dest = tmp_path / "x.tgz"
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(hops[0], dest)
    assert exc.value.code == "redirects"
    assert calls == hops[:3] and not dest.exists()
    assert len(_lines()) == 4 and _lines()[-1]["code"] == "redirects"


def test_max_redirects_is_capped_at_two(net, tmp_path):
    table, calls, _ = net
    hops = [f"https://github.com/hop{i}" for i in range(4)]
    for here, there in zip(hops, hops[1:]):
        table[here] = Resp(302, b"", {"Location": there})
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(hops[0], tmp_path / "x.tgz", max_redirects=9)
    assert exc.value.code == "redirects" and len(calls) == 3


def test_an_over_size_body_is_refused_and_nothing_is_left(net, tmp_path):
    table, _calls, _ = net
    table[URL] = lambda: Resp(200, GOOD)  # no Content-Length: counted while streaming
    dest = tmp_path / "x.tgz"
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, dest, max_bytes=100)
    assert exc.value.code == "too_big"
    assert not dest.exists() and not (tmp_path / "x.tgz.part").exists()
    line = _lines()[-1]
    assert (line["outcome"], line["code"]) == ("refused", "too_big")


def test_a_declared_over_size_body_is_refused_before_reading(net, tmp_path):
    table, _calls, _ = net
    table[URL] = lambda: Resp(200, GOOD, {"Content-Length": "999999999"})
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, tmp_path / "x.tgz", max_bytes=100)
    assert exc.value.code == "too_big"


def test_a_fingerprint_mismatch_is_refused_and_nothing_is_left(net, tmp_path):
    table, _calls, _ = net
    table[URL] = lambda: Resp(200, GOOD + b"tampered")
    dest = tmp_path / "x.tgz"
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, dest, expect_sha256=GOOD_SHA)
    assert exc.value.code == "sha_mismatch"
    assert not dest.exists() and not (tmp_path / "x.tgz.part").exists()
    assert _lines()[-1]["code"] == "sha_mismatch"


def test_a_bad_fingerprint_or_service_is_refused_before_sending(net, tmp_path):
    _table, calls, _ = net
    with pytest.raises(source_fetch.FetchError):
        _get(URL, tmp_path / "x.tgz", expect_sha256="not-a-sha")
    with pytest.raises(source_fetch.FetchError):
        _get(URL, tmp_path / "x.tgz", service="code_search")
    with pytest.raises(source_fetch.FetchError):
        _get(URL, "relative/path.tgz")
    assert calls == []


def test_a_non_200_answer_is_refused(net, tmp_path):
    table, _calls, _ = net
    table[URL] = lambda: Resp(403, b"rate limited")
    dest = tmp_path / "x.tgz"
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, dest)
    assert exc.value.code == "bad_answer" and not dest.exists()


def test_the_switch_off_refuses(net, tmp_path):
    _table, calls, _ = net
    outside_switches.set_switch("code_download", False, by="owner")
    with pytest.raises(source_fetch.FetchError) as exc:
        _get(URL, tmp_path / "x.tgz")
    assert exc.value.code == "switched_off" and calls == []


def test_never_saved_inside_the_vault(net, tmp_path):
    from puffpai.security.vault import _default_vault_dir
    vault = _default_vault_dir()
    with pytest.raises(source_fetch.FetchError):
        _get(URL, os.path.join(vault, "x.tgz"))
    with pytest.raises(source_fetch.FetchError):
        _get(URL, os.path.join(vault.rstrip("\\/") + ".inbox", "x.tgz"))
    assert net[1] == []


def test_a_leftover_part_file_hard_linked_elsewhere_is_not_written_through(net, tmp_path):
    table, _calls, _ = net
    table[URL] = lambda: Resp(200, GOOD)
    other = tmp_path / "someone_elses.txt"
    other.write_bytes(b"precious")
    dest = tmp_path / "dl" / "x.tgz"
    dest.parent.mkdir()
    os.link(other, str(dest) + ".part")
    got = _get(URL, dest)
    assert got["sha256"] == GOOD_SHA and dest.read_bytes() == GOOD
    assert other.read_bytes() == b"precious"


# -- bad characters and garbled answers are FetchError ---------------

@pytest.mark.parametrize("url", [
    "https://codeload.github.com/a b", "https://codeload.github.com/a\x7fb",
    "https://codeload.github.com/a\r\nX: y", "https://codeload.github.com/caf\u00e9"])
def test_an_address_with_spaces_or_control_characters_is_a_fetch_error(net, tmp_path, url):
    _table, calls, _ = net
    dest = tmp_path / "dl" / "a.zip"
    with pytest.raises(source_fetch.FetchError) as err:
        _get(url, dest)
    assert err.value.code == "not_allowed"
    assert calls == [] and not dest.exists()
    (line,) = _lines()
    assert (line["outcome"], line["code"]) == ("refused", "not_allowed")


def test_a_garbled_answer_is_a_fetch_error(net, tmp_path, monkeypatch):
    import http.client

    def broken(url, headers, timeout):
        raise http.client.BadStatusLine("garbage")
    monkeypatch.setattr(source_fetch, "_default_transport", broken)
    with pytest.raises(source_fetch.FetchError) as err:
        _get(URL, tmp_path / "dl" / "a.zip")
    assert err.value.code == "bad_answer"


def test_a_cut_off_download_is_a_fetch_error_and_leaves_nothing(net, tmp_path):
    import http.client
    table, _calls, _ = net

    class Cut(Resp):
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"par")
    table[URL] = lambda: Cut(200)
    dest = tmp_path / "dl" / "a.zip"
    with pytest.raises(source_fetch.FetchError) as err:
        _get(URL, dest)
    assert err.value.code == "no_answer"
    assert not dest.exists() and not os.path.exists(str(dest) + ".part")
