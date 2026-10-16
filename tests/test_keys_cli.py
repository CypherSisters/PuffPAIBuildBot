"""`puffpai keys list|add|remove|test`: the terminal is equal to the page."""

from __future__ import annotations

import io
import sys

import pytest

from keys_support import (PIN, canary, fast_vault, install_vendor, reset_keys,
                          set_posture)
from puffpai import cli, plain_words
from puffpai.config import get_config
from puffpai.security import egress_doors, keys, outside_switches


@pytest.fixture
def vault(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    v = fast_vault(monkeypatch, cli._vault_dir(get_config()))  # noqa: SLF001
    v.lock()
    yield v
    reset_keys()


def _stdin(monkeypatch, *lines):
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{x}\n" for x in lines)))


def test_list_is_plain_and_says_how_to_add(vault, capsys):
    assert cli.main(["keys"]) == 0
    out = capsys.readouterr().out
    assert "Brave Search (brave_search) - Not added yet." in out
    assert "To add it: puffpai keys add brave_search" in out
    for line in out.splitlines():
        if "puffpai keys" in line or "(brave_search)" in line \
                or "(marginalia_search)" in line:
            continue
        assert not plain_words.check(line), line


def test_add_with_a_yes_stores_the_key(vault, monkeypatch, capsys):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("X-Subscription-Token", value)
    _stdin(monkeypatch, value, PIN)
    assert cli.main(["keys", "add", "brave_search", "--stdin", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Brave Search needs a free key: 1) open " in out
    assert "3) run: puffpai keys add brave_search" in out
    assert "That key works. It is locked in your vault." in out
    assert value not in out
    assert keys.key_status("brave_search", vault=vault)["state"] == "locked"


def test_a_key_on_the_command_line_is_refused_and_not_printed(vault,
                                                              monkeypatch,
                                                              capsys):
    vendor = install_vendor(monkeypatch)
    value = canary()
    assert cli.main(["keys", "add", "brave_search", value]) == 1
    captured = capsys.readouterr()
    assert value not in captured.out and value not in captured.err
    assert "never typed on the command line" in captured.out
    assert vendor.calls == []


def test_a_key_in_the_environment_is_never_read(vault, monkeypatch, capsys):
    vendor = install_vendor(monkeypatch)
    monkeypatch.setenv("BRAVE_SEARCH_KEY", canary())
    monkeypatch.setenv("PUFFPAI_KEY_BRAVE_SEARCH", canary())
    _stdin(monkeypatch, "")
    assert cli.main(["keys", "add", "brave_search", "--stdin", "--yes"]) == 1
    assert "does not look like a key" in capsys.readouterr().out
    assert vendor.calls == []


def test_a_wrong_pin_sends_nothing(vault, monkeypatch, capsys):
    vendor = install_vendor(monkeypatch)
    _stdin(monkeypatch, canary(), "not the right pin at all")
    assert cli.main(["keys", "add", "brave_search", "--stdin", "--yes"]) == 1
    assert "did not open the vault. Nothing was sent." in capsys.readouterr().out
    assert vendor.calls == []


def test_without_a_yes_nothing_is_added_or_sent(vault, monkeypatch, capsys):
    vendor = install_vendor(monkeypatch)
    _stdin(monkeypatch, canary(), PIN)
    assert cli.main(["keys", "add", "brave_search", "--stdin"]) == 1
    assert "Nothing was added" in capsys.readouterr().out
    assert vendor.calls == [] and keys._read_index() == {}  # noqa: SLF001


def test_remove_needs_nothing(vault, monkeypatch, capsys):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    _stdin(monkeypatch, value, PIN)
    assert cli.main(["keys", "add", "marginalia_search", "--stdin", "--yes"]) == 0
    assert cli.main(["keys", "remove", "marginalia_search"]) == 0
    assert "Removed. Anything that used it is paused." in capsys.readouterr().out
    assert keys.key_status("marginalia_search")["state"] == "missing"


def test_the_shared_test_key_tries_a_search_and_stores_nothing(vault,
                                                               monkeypatch,
                                                               capsys):
    vendor = install_vendor(monkeypatch)
    vendor.accept("API-Key", "public")
    outside_switches.set_switch("marginalia_search", True, by="owner")
    egress_doors.reset_cache()
    assert cli.main(["keys", "test", "marginalia_search", "--public",
                     "--words", "linear b"]) == 0
    out = capsys.readouterr().out
    assert "Marginalia Search (shared test key) found 1 result(s)" in out
    assert "puffpai keys add marginalia_search" in out
    assert vendor.calls[-1]["_headers"]["API-Key"] == "public"
    assert keys._read_index() == {}  # noqa: SLF001
    assert cli.main(["keys", "test", "brave_search", "--public"]) == 1
