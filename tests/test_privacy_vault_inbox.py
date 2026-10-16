"""The write-only vault inbox: a locked vault can take a detail, only the PIN reads it."""

from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from puffpai.security import vault_inbox
from puffpai.security.vault import Vault, VaultError

PW = "correct horse battery"
SECRET = "555-867-5309"


def _all_bytes_under(*folders: str) -> bytes:
    blob = b""
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    blob += fh.read()
    return blob


def _item(value: str = SECRET) -> dict:
    return {"source": "test", "items": {"phone": [value]}}


@pytest.fixture()
def vdir(tmp_path):
    return str(tmp_path / "vault")


@pytest.fixture()
def locked(vdir):
    v = Vault(vault_dir=vdir)
    v.create(PW)
    v.lock()
    return v


def _records(v: Vault) -> list:
    return json.loads(v.retrieve(vault_inbox.PII_RECORD_NAME) or "[]")


def test_create_makes_a_public_key_and_hides_the_private_one(vdir):
    v = Vault(vault_dir=vdir)
    v.create(PW)
    assert vault_inbox.can_accept(vdir)
    name = vault_inbox.PRIVATE_KEY_NAME
    assert name not in v.list_secrets()
    assert v.retrieve(name) is None
    assert v.get_service_secret(name) is None
    assert v.delete(name) is False
    with pytest.raises(VaultError):
        v.store(name, "x")


def test_locked_deposit_is_unreadable_on_disk_and_filed_on_unlock(vdir, locked):
    # A different Vault object, as a different process would have.
    assert vault_inbox.deposit(vdir, _item())
    assert vault_inbox.pending_count(vdir) == 1
    assert SECRET.encode() not in _all_bytes_under(vdir, vault_inbox.inbox_dir(vdir))

    v = Vault(vault_dir=vdir)
    v.unlock(PW)
    recs = _records(v)
    assert recs and recs[-1]["items"]["phone"] == [SECRET]
    assert recs[-1]["filed_from"] == "locked_inbox"
    assert vault_inbox.pending_count(vdir) == 0


def test_no_public_key_means_no_deposit(tmp_path):
    assert vault_inbox.deposit(str(tmp_path / "nothing"), _item()) is False


def test_inbox_survives_recovery(vdir):
    v = Vault(vault_dir=vdir)
    _s, code = v.create_with_recovery(PW)
    v.lock()
    assert vault_inbox.deposit(vdir, _item("first"))
    v2 = Vault(vault_dir=vdir)
    v2.redeem_recovery_code(code, "a brand new passphrase")
    assert _records(v2)[-1]["items"]["phone"] == ["first"]
    v2.lock()
    assert vault_inbox.deposit(vdir, _item("second"))
    v3 = Vault(vault_dir=vdir)
    v3.unlock("a brand new passphrase")
    assert [r["items"]["phone"][0] for r in _records(v3)] == ["first", "second"]


def _swap_public_key(vdir: str, *, also_the_check: bool = False) -> bytes:
    attacker = X25519PrivateKey.generate()
    raw = attacker.public_key().public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw)
    pub = os.path.join(vault_inbox.inbox_dir(vdir), "inbox.pub.json")
    with open(pub, "w", encoding="utf-8") as fh:
        json.dump({"schema": vault_inbox.SCHEMA,
                   "public_key": base64.b64encode(raw).decode()}, fh)
    if also_the_check:
        with open(os.path.join(vdir, vault_inbox.CONFIRM_FILENAME), "w",
                  encoding="utf-8") as fh:
            json.dump({"schema": vault_inbox.SCHEMA,
                       "public_sha256": vault_inbox.public_sha256(raw)}, fh)
    return raw


def test_the_vault_records_its_inbox_key_hash_inside_and_beside_it(vdir, locked):
    assert vault_inbox.key_status(vdir) == "ok"
    v = Vault(vault_dir=vdir)
    v.unlock(PW)
    inside = v._reserved_get(vault_inbox.PUBLIC_HASH_NAME)
    assert inside and inside == vault_inbox._read_confirm(vdir)
    assert vault_inbox.PUBLIC_HASH_NAME not in v.list_secrets()


def test_a_swapped_public_key_is_never_sealed_to(vdir, locked):
    raw = _swap_public_key(vdir)
    assert vault_inbox.key_status(vdir) == "unconfirmed"
    assert vault_inbox.deposit_status(vdir, _item()) == vault_inbox.HELD
    assert vault_inbox.deposit(vdir, _item("555-000-1111")) is False
    assert vault_inbox.pending_count(vdir) == 0     # nothing sealed to it
    assert SECRET.encode() not in _all_bytes_under(
        vdir, vault_inbox.inbox_dir(vdir))
    notices = vault_inbox.owner_notices(vdir)
    assert [n["kind"] for n in notices] == ["unconfirmed"]
    assert "holding them in memory" in notices[0]["text"]
    v = Vault(vault_dir=vdir)
    v.unlock(PW)                                    # the held items are filed
    assert len(_records(v)) == 2
    assert vault_inbox.held_count(vdir) == 0
    kinds = [n["kind"] for n in vault_inbox.owner_notices(vdir)]
    assert "key_replaced" in kinds and "held_filed" in kinds
    pub = os.path.join(vault_inbox.inbox_dir(vdir), "inbox.pub.json")
    with open(pub, encoding="utf-8") as fh:        # the right key is back
        assert json.load(fh)["public_key"] != base64.b64encode(raw).decode()
    assert vault_inbox.key_status(vdir) == "ok"


def test_a_full_swap_is_reported_to_the_owner_at_unlock(vdir, locked):
    """Both the key and its check swapped (a same-user attacker can): the
    deposit is sealed to their key, and the next unlock says so plainly."""
    _swap_public_key(vdir, also_the_check=True)
    assert vault_inbox.deposit_status(vdir, _item()) == vault_inbox.SEALED
    v = Vault(vault_dir=vdir)
    v.unlock(PW)                                    # drain: cannot open it
    assert _records(v) == []
    aside = os.path.join(vault_inbox.inbox_dir(vdir), "unreadable")
    assert len(os.listdir(aside)) == 1
    texts = [n["text"] for n in vault_inbox.owner_notices(vdir)
             if n["kind"] == "wrong_key"]
    assert texts and "1 personal detail was locked with a key that is not " \
                     "your vault's" in texts[0]
    from puffpai import announcer, plain_words
    assert any("key that is not your vault's" in r["text"]
               for r in announcer.recent(10))
    assert plain_words.is_plain(texts[0])


def test_oversized_box_is_set_aside_not_filed(vdir, locked):
    big = os.path.join(vault_inbox.inbox_dir(vdir), "0000000000000-big.box")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("x" * (vault_inbox._MAX_BOX_BYTES + 1))
    v = Vault(vault_dir=vdir)
    v.unlock(PW)
    assert _records(v) == []
    assert os.listdir(os.path.join(vault_inbox.inbox_dir(vdir), "unreadable"))


def test_pending_cap_refuses_more(vdir, locked, monkeypatch):
    monkeypatch.setattr(vault_inbox, "MAX_PENDING", 2)
    assert vault_inbox.deposit(vdir, _item("a"))
    assert vault_inbox.deposit(vdir, _item("b"))
    assert vault_inbox.deposit(vdir, _item("c")) is False


def test_item_too_large_is_refused(vdir, locked):
    assert vault_inbox.deposit(vdir, _item("x" * (70 * 1024))) is False
    assert vault_inbox.pending_count(vdir) == 0
