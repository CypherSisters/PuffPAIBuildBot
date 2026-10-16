"""Uploads: the searchable copy never keeps a detail whose original was vaulted."""

from __future__ import annotations

import pytest

from puffpai import ingestion
from puffpai.ingestion import screen_text_for_pii
from puffpai.security.vault import Vault
from puffpai import plain_words  # noqa: E402  (the one banned list)

PW = "correct horse battery"
#: On top of plain_words' one list: privacy words a person misreads.
JARGON_EXTRA = ("pii", "redact", "re-upload")


@pytest.fixture()
def open_vault(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create(PW)
    return v


@pytest.fixture()
def locked_vault(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create(PW)
    v.lock()
    return v


def test_ssn_document_indexes_no_ssn(open_vault):
    text = "Tax notes.\nMy SSN is 123-45-6789 and I live at 42 Maple Street."
    res = screen_text_for_pii(text, "tax.txt", vault=open_vault)
    assert res.pii_vaulted
    assert "123-45-6789" not in res.text_for_index
    assert "42 Maple Street" not in res.text_for_index
    assert "kept private" in res.text_for_index
    docs = open_vault.list_documents()
    assert docs and b"123-45-6789" in open_vault.retrieve_document(docs[0])
    assert "auto_detected_pii" in open_vault.list_secrets()


def test_about_me_file_keeps_address_and_school_out(open_vault):
    text = ("About me. My name is Leo Marlow. I live at 42 Maple Street. "
            "I go to Lincoln Elementary. My birthday is March 3 2016.")
    res = screen_text_for_pii(text, "about.txt", vault=open_vault)
    for value in ("Leo Marlow", "42 Maple Street", "Lincoln Elementary",
                  "March 3 2016"):
        assert value not in res.text_for_index


def test_email_still_needs_the_vault(locked_vault):
    # email and phone were always vaulted; a richer classifier must not loosen it
    # The class is read at call time: another test reloads puffpai.ingestion,
    # and a class imported before that reload is not the one raised after it.
    with pytest.raises(ingestion.PiiVaultRefusal) as exc:
        screen_text_for_pii("contact leo.kid@example.com", "c.txt",
                            vault=locked_vault)
    msg = exc.value.message
    assert "email address" in msg and "vault is locked" in msg
    assert plain_words.is_plain(msg, also=JARGON_EXTRA), msg


def test_no_vault_refusal_is_plain(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "none"))
    with pytest.raises(ingestion.PiiVaultRefusal) as exc:
        screen_text_for_pii("SSN 123-45-6789", "s.txt", vault=v)
    msg = exc.value.message
    assert "Set up your vault" in msg
    assert plain_words.is_plain(msg, also=JARGON_EXTRA), msg


def test_full_name_only_is_indexed_as_is(locked_vault):
    text = "Pattern by name: Jane Doe. Cast on 120 stitches."
    res = screen_text_for_pii(text, "hat.txt", vault=locked_vault)
    assert res.pii_detected and not res.pii_vaulted
    assert res.text_for_index == text


def test_clean_document_passes_through(locked_vault):
    text = "Stegosaurus had about 17 plates on its back."
    res = screen_text_for_pii(text, "dino.txt", vault=locked_vault)
    assert not res.pii_detected and res.text_for_index == text
