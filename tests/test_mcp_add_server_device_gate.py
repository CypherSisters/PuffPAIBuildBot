"""POST /mcp/servers with the key and a made-up device id is refused before
anything connects (real pairing store, no network)."""
from __future__ import annotations

import pytest

from puffpai import api_server, mcp_client

pytestmark = pytest.mark.real_pairing


def test_a_made_up_device_cannot_add_a_tool_server(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(tmp_path / "pairing" / "pairing.json"))
    called = []
    monkeypatch.setattr(mcp_client, "add_server", lambda *a, **k: called.append(1) or {})
    api_server.app.config.update(TESTING=True)
    r = api_server.app.test_client().post(
        "/mcp/servers", json={"name": "x", "target": "npx -y something"},
        headers={"Authorization": f"Bearer {api_server._API_KEY}",
                 "X-Puff-Device-Id": "deviceaaaa000001"})
    assert r.status_code == 403 and called == []
