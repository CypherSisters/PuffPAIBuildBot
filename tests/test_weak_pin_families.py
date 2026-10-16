"""The PIN rule closes whole families, the same in the browser.

In one probe, 27 of 111 fresh weak PINs were taken, among them the X and
diamond keypad shapes drawn in another order (9517538426, and with a letter
added, which made a real vault through POST /vault/unlock), a month and a
year (Dec2020abc), a name and a date (Mia03142015, two names and a year), a
holiday and a year (Christmas2026, while Summer2024 was refused), a word and
a run of digits (purple1234), phone numbers (5558675309, (555)8675309) and
the best-known passphrase of all. Each family is closed here as a class;
good PINs stay good; the browser's vaultPin.ts says the same as the server.
Names in this file are made up.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess

import pytest

from puffpai import api_server, plain_words, setup_ops
from puffpai.security import vault as vault_mod
from puffpai.security.vault import Vault

import test_vault_pin_crosslang as crosslang

API_KEY = api_server._API_KEY


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


FAMILIES = {
    "keypad, any order": ["9517538426", "9517538426q", "7539518426", "1593578426",
                          "8426159753", "1590753456", "q9517538426"],
    "keypad path": ["1254789632", "3698745212", "7895412365"],
    "month and year": ["Dec2020abc", "Jan2025xyz", "25dec2020", "Oct312024"],
    "name and date": ["Mia03142015", "Ava07042018", "LiamNoah2015", "Olivia2014",
                      "3/14/2015xx"],
    "holiday and year": ["Christmas2026", "thanksgiving", "Easter2025", "xmas2026",
                         "Halloween2026", "Diwali2024!"],
    "word and digits": ["jesus12345", "purple1234", "roblox1234", "dragon12345",
                        "Chelsea1905", "Morgan!!!!"],
    "phone": ["5558675309", "(555)8675309", "867-5309-55", "8675309867",
              "2125550147", "(212)5550147"],
    "passphrase": ["correcthorsebatterystaple", "correcthorse", "batterystaple",
                   "tr0ub4dor&3", "ilovemykids", "ilovemywife", "mypinnumber"],
}
WEAK = [p for pins in FAMILIES.values() for p in pins]

STILL_GOOD = ["Tulip-9-Harbor", "Maple-4-Lantern", "a-different-strong-pin",
              "7bS!k2@Lp", "UPPER-lower-123", "Qz9!vR2#mL", "k8Rv2pQx7n",
              "Wb7#qL2zR9", "p4N!x8Lr2v", "river-7-copper-lamp", "4826193705",
              "7305918264", "6031958274", "8316047295", "Blue-Heron-58",
              "purple!Otter93", "Sparrow&Moss4", "Copper-3-Walnut",
              "marble-quartz-ember4"]


@pytest.mark.parametrize("pin", WEAK)
def test_each_family_is_refused_by_the_server(pin):
    ok, why = setup_ops.validate_vault_pin(pin)
    assert not ok, pin
    assert why and pin not in why and "—" not in why
    assert not plain_words.check(why)


@pytest.mark.parametrize("pin", STILL_GOOD)
def test_good_pins_stay_good(pin):
    assert setup_ops.validate_vault_pin(pin) == (True, "")


def test_the_x_and_diamond_in_another_order_is_below_the_vault_floor_too():
    # The vault's own floor (every way a vault is made) refuses the shapes
    # whatever order they are drawn in.
    for pin in ("9517538426", "9517538426q", "1593578426"):
        assert not vault_mod.validate_vault_secret(pin)[0], pin


def test_a_random_pin_is_still_rarely_refused():
    rng = random.Random(20261003)
    pins = ["".join(rng.choice("0123456789") for _ in range(10)) for _ in range(4000)]
    refused = sum(1 for p in pins if not setup_ops.validate_vault_pin(p)[0])
    assert refused / len(pins) < 0.025, refused     # about 1.4% (measured)
    mixed = ["".join(rng.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$")
                     for _ in range(10)) for _ in range(2000)]
    assert sum(1 for p in mixed if not setup_ops.validate_vault_pin(p)[0]) <= 4


# -- the vault page's own create route -----------------------------------------

@pytest.fixture
def served(monkeypatch, tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    yield v
    v.lock()
    api_server._vault_sessions.clear()


@pytest.mark.parametrize("pin", ["9517538426q", "9517538426", "Dec2020abc",
                                 "Christmas2026", "5558675309"])
def test_the_create_route_makes_no_vault_with_a_weak_pin(served, pin):
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    got = client.post("/vault/unlock", json={"password": pin, "initialize": True},
                      headers={"Authorization": f"Bearer {API_KEY}"})
    assert got.status_code == 400
    body = got.get_json()
    assert body["success"] is False and body["weak_password"] is True
    assert pin not in json.dumps(body)
    assert not served.is_initialized


# -- the browser says the same --------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_rule_is_the_server_rule():
    rng = random.Random(77)
    sample = ["".join(rng.choice("0123456789") for _ in range(10)) for _ in range(600)]
    sample += ["".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                       for _ in range(rng.choice((8, 10, 12)))) for _ in range(400)]
    pins = WEAK + STILL_GOOD + sample
    browser = crosslang._node_refusals(pins)
    server = [not setup_ops.validate_vault_pin(p)[0] for p in pins]
    assert [p for p, why, back in zip(pins, browser, server) if bool(why) != back] == []
    assert all(browser[:len(WEAK)])
