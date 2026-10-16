"""A locked-path record moved to another name is refused.

The unattended tier stores one AES-GCM file per record with no associated
data, so the cipher alone does not tie a file to its name. The binding is in
the record: every reader checks the name written inside it (``service_id``
for a sign-up key, ``name`` for a tool server's headers) before using it. A
file copied over another record's name opens, and is then refused.
"""

from __future__ import annotations

import os
import shutil

import pytest

from keys_support import (canary, fast_vault, install_vendor,
                          machine_key_file, reset_keys, set_posture)
from puffpai.security import egress_doors, keys

pytestmark = pytest.mark.keyring_stub


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    reset_keys()
    set_posture("enumerated")
    yield
    reset_keys()


def test_a_tool_record_under_another_name_is_refused(monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    a, b = canary(), canary()
    keys.put_tool_secret("alpha", {"X-Api-Key": a}, vault=v)
    keys.put_tool_secret("beta", {"X-Api-Key": b}, vault=v)
    v.lock()
    assert keys.tool_secret("beta", vault=v) == {"X-Api-Key": b}
    folder = tmp_path / "vault"
    shutil.copyfile(folder / keys.tool_file("alpha"), folder / keys.tool_file("beta"))
    # The cipher opens the moved file (no associated data binds the name) ...
    moved = v._decrypt_service_record(  # noqa: SLF001
        str(folder / keys.tool_file("beta")), v._sdk_from_machine_key())  # noqa: SLF001
    assert moved["name"] == "alpha"
    # ... and the reader refuses it: alpha's headers never come back as beta's.
    assert keys.tool_secret("beta", vault=v) is None
    assert keys.tool_secret("alpha", vault=v) == {"X-Api-Key": a}


def test_a_key_record_under_another_name_is_refused(monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    keys.add_key("marginalia_search", value, vault=v, background=True)
    v.lock()
    folder = tmp_path / "vault"
    src = folder / keys.background_file("marginalia_search")
    assert src.exists()
    # The same bytes under a tool server's name: refused (wrong kind, wrong name).
    keys._write_tool_names({"gamma"})  # noqa: SLF001
    shutil.copyfile(src, folder / keys.tool_file("gamma"))
    assert keys.tool_secret("gamma", vault=v) is None
    os.remove(folder / keys.tool_file("gamma"))
