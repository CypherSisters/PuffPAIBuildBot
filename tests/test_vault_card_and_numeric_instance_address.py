"""Developer overview fixes.

1. The Developer overview Vault card said "Not made yet" for a vault whose
   drive is unplugged: the backend reports such a vault initialized:false with
   a 'missing' line, and vaultStatus() asked initialized first. The words file
   is run under node when node is on PATH; the order is always checked.
2. The cloud instance-data refusal went by spelling: 0xa9fea9fe and
   2852039166 are 169.254.169.254 to glibc (the box), but read as LAN names
   here. Numeric host spellings are now read the way the resolver reads them.

No network: name lookups are stubbed and the Test step's probe is recorded.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from puffpai.security import lan_posture

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDS = os.path.join(ROOT, "dashboard", "app", "dashboard", "developer",
                     "overviewWords.ts")
UNPLUGGED = {"initialized": False,
             "missing": "Your vault drive is not plugged in. Plug it in."}


def test_missing_is_asked_before_initialized():
    src = open(WORDS, encoding="utf-8").read()
    body = src[src.index("export function vaultStatus"):]
    assert body.index("vault.missing") < body.index("!vault.initialized")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_unplugged_vault_card_words_under_node(tmp_path):
    # Strip the type-only bits so plain node runs the real function body.
    src = open(WORDS, encoding="utf-8").read()
    src = src.replace(src[src.index("import type"):src.index(";\n") + 2], "")
    src = src.replace("export function vaultStatus(vault: VaultZone | null "
                      "| undefined): string", "function vaultStatus(vault)")
    script = tmp_path / "w.js"
    script.write_text(src + "\nconst c = JSON.parse(process.argv[2]);\n"
                      "console.log(JSON.stringify(c.map(vaultStatus)));\n",
                      encoding="utf-8")
    cases = [UNPLUGGED, {"initialized": False},
             {"initialized": True, "unlocked": False}]
    out = subprocess.run(["node", str(script), json.dumps(cases)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["Made, its drive is not plugged in",
                                      "Not made yet", "Made, locked"]


@pytest.mark.parametrize("spelling", [
    "0xa9fea9fe", "2852039166", "0251.0376.0251.0376",
    "0xA9.0xFE.0xA9.0xFE", "169.254.43518", "0xa9.16689662",
])
def test_numeric_spellings_of_instance_data_are_refused(spelling):
    assert lan_posture._numeric_ipv4(spelling) is not None
    assert str(lan_posture._numeric_ipv4(spelling)) == "169.254.169.254"
    for resolve in (False, True):
        found = lan_posture.check_url(
            "home_assistant", f"http://{spelling}:8123", "island",
            resolve=resolve,
            resolver=lambda h, t: (_ for _ in ()).throw(OSError("offline")))
        assert found is not None, (spelling, resolve)


@pytest.mark.parametrize("host,expected", [
    ("0177.1", "127.0.0.1"), ("3232235825", "192.168.1.49"),
    ("0x7f000001", "127.0.0.1"),
])
def test_numeric_spellings_read_as_the_resolver_does(host, expected):
    assert str(lan_posture._numeric_ipv4(host)) == expected


@pytest.mark.parametrize("host", [
    "homeassistant", "ha.local", "1_0", "0x", "08", "+1", " 1",
    "4294967296", "1.2.3.4.5", "256.1.1.1", "1.2.65536",
])
def test_names_and_out_of_range_numbers_are_not_addresses(host):
    assert lan_posture._numeric_ipv4(host) is None


def test_lan_names_keep_their_old_reading():
    assert lan_posture.classify_host("homeassistant") == lan_posture.LAN_PRESUMED
    assert lan_posture.check_url("home_assistant", "http://homeassistant:8123",
                                 "island") is None
    assert lan_posture.check_url("home_assistant", "http://3232235825:8123",
                                 "island") is None


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    from puffpai import build_manifest
    from puffpai.config import get_config
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: {"posture": {"run_egress": "island"}})

    def _no_dns(host, timeout):
        raise OSError("no network in unit tests")

    monkeypatch.setattr(lan_posture, "_resolve", _no_dns)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


@pytest.mark.parametrize("route", ["/setup/ha/test", "/setup/ha"])
@pytest.mark.parametrize("url", ["http://0xa9fea9fe", "http://2852039166"])
def test_setup_routes_refuse_hex_and_decimal_instance_data(scratch, monkeypatch,
                                                           route, url):
    from puffpai import api_server, setup_ops
    sent = []
    monkeypatch.setattr(setup_ops.requests, "get",
                        lambda u, **k: sent.append(u))
    api_server.app.config.update(TESTING=True)
    resp = api_server.app.test_client().post(
        route, json={"url": url, "token": "SECRETTOKEN"},
        headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert resp.status_code == 400, resp.get_json()
    assert sent == []
    assert "SECRETTOKEN" not in resp.get_data(as_text=True)
    assert setup_ops.ha_configured() is False
