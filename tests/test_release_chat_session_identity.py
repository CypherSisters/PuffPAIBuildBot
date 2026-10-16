"""What keys the vault-unlocked chat session, and who may choose it.

THE BUG THESE TESTS LOCK SHUT
-----------------------------
``_get_chat_client_id()`` used to return ``X-Client-Id`` or, failing that, the
socket address. That value keys ``_VAULT_CHAT_STATE``: a 300-second window in
which protected questions ("what are my allergies") are answered out of the
DECRYPTED vault with no further PIN. The dashboard proxy relayed every client
``X-*`` header upstream, and the header's default value was the caller's LAN IP,
so any paired device could send a household member's ``X-Client-Id`` and read
that member's vault content. Two bugs in one: a security decision keyed on
client-controlled input, and a proxy that made all client input reachable.

WHAT IS ASSERTED
  1. ``X-Client-Id`` is ignored entirely — it can neither select nor create a
     session, from loopback or anywhere else.
  2. ``X-Puff-Device-Id`` is honoured ONLY from a trusted proxy peer, and only
     when it is shaped like a real derived id.
  3. Two different devices get two different keys; the same device is stable.
  4. Direct LAN clients (CLI, Home Assistant) still work, keyed on the socket
     peer, which no header can change. A trusted-proxy request that asserts no
     identity gets a single-use ``anon:`` key instead of the shared loopback
     bucket — that half of the rule lives in
     ``test_release_chat_anonymous_session.py``.
  5. The state dict really is keyed on the result, so an ignored header cannot
     reach another member's cached vault session.

Flask test client only; nothing binds a port and no vault is opened.
"""

import os
import tempfile

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_chatid_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP_DATA_DIR

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402

API_KEY = api_server._API_KEY

#: A value shaped like the dashboard's derived id (32 hex chars).
DEVICE_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
DEVICE_B = "0f9e8d7c6b5a4938271605f4e3d2c1b0"


@pytest.fixture
def app_ctx():
    """A request context factory bound to the real app."""
    api_server.app.config.update(TESTING=True)
    return api_server.app


def _client_id(app, *, headers=None, peer="127.0.0.1"):
    """_get_chat_client_id() under a synthetic request."""
    env = {"REMOTE_ADDR": peer}
    with app.test_request_context("/v1/chat/completions", method="POST",
                                  headers=headers or {}, environ_base=env):
        return api_server._get_chat_client_id()


# ── 1. X-Client-Id is dead ──────────────────────────────────────────────────

def test_x_client_id_is_ignored_from_loopback(app_ctx):
    """The header that used to pick the session no longer appears in the key.

    From loopback with no asserted device identity the key is now a single-use
    ``anon:`` value (see test_release_chat_anonymous_session.py), so what is
    asserted here is that the client's value contributes NOTHING to it.
    """
    got = _client_id(app_ctx, headers={"X-Client-Id": "192.168.1.42"})
    assert "192.168.1.42" not in got
    assert got.startswith("anon:")


def test_x_client_id_is_ignored_from_the_lan(app_ctx):
    got = _client_id(app_ctx, headers={"X-Client-Id": "192.168.1.42"},
                     peer="192.168.1.99")
    assert got == "peer:192.168.1.99"


def test_x_client_id_cannot_select_another_members_session(app_ctx):
    """The spoof attempt lands in its OWN bucket, not the victim's."""
    api_server._VAULT_CHAT_STATE.clear()
    victim = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    api_server._VAULT_CHAT_STATE[victim] = {"vault_session": "victim-session"}

    # The attacker is a paired LAN device talking through the proxy. It sends the
    # old header, and (separately) the victim's own address, hoping to land on
    # the cached unlock.
    attacker = _client_id(
        app_ctx,
        headers={"X-Client-Id": victim, "X-Client-Id-2": DEVICE_A},
        peer="192.168.1.50",
    )
    assert attacker != victim
    assert api_server._VAULT_CHAT_STATE.get(attacker) is None
    assert api_server._VAULT_CHAT_STATE[victim]["vault_session"] == "victim-session"
    api_server._VAULT_CHAT_STATE.clear()


# ── 2. the proxy-set header, honoured only from a trusted peer ──────────────

def test_device_header_honoured_from_loopback(app_ctx):
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A}) == \
        f"device:{DEVICE_A}"


def test_device_header_honoured_from_ipv4_mapped_loopback(app_ctx):
    """::ffff:127.0.0.1 is loopback spelled the IPv6 way."""
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="::ffff:127.0.0.1") == f"device:{DEVICE_A}"


def test_device_header_honoured_over_ipv6_loopback(app_ctx):
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="::1") == f"device:{DEVICE_A}"


def test_device_header_IGNORED_from_a_lan_peer(app_ctx):
    """A LAN client with the API key cannot borrow a device id it read out of
    /api/pair/devices, because it is not a trusted proxy peer."""
    got = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                     peer="192.168.1.50")
    assert got == "peer:192.168.1.50"
    assert DEVICE_A not in got


def test_device_header_ignored_from_a_public_peer(app_ctx):
    got = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                     peer="203.0.113.7")
    assert got == "peer:203.0.113.7"


@pytest.mark.parametrize("bad", [
    "",                      # empty
    "   ",                   # whitespace only
    "short",                 # under the length floor
    "a" * 65,                # over the ceiling
    "has spaces in it aaaa",  # not the charset
    "path/traversal/../x",   # separators
    "semi;colon;injection",
    # A cookie-shaped value: dots are not in the charset, so a whole pairing
    # cookie pasted into this header is rejected rather than used as a key.
    "v2.aaaaaaaaaaaa.1758000000.1789536000." + "f" * 64,
    # (A newline cannot be tested here: werkzeug refuses to build the header at
    # all, which is the WSGI layer making the same decision one step earlier.)
])
def test_malformed_device_header_is_discarded(app_ctx, bad):
    """A value that is not plainly a derived id is discarded, not used as a key.

    From a trusted proxy peer the fallback is an ``anon:`` key, not the shared
    loopback peer bucket: garbage in the identity header is exactly as
    unidentified as no header at all, and must not join everyone else's unlock.
    """
    got = _client_id(app_ctx, headers={"X-Puff-Device-Id": bad})
    assert got.startswith("anon:")


def test_trusted_peer_list_is_extensible_but_closed_by_default(app_ctx, monkeypatch):
    """A split-out dashboard host can be named; nothing else is trusted."""
    monkeypatch.setenv("PUFFPAI_TRUSTED_PROXY_IPS", "10.0.0.5")
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="10.0.0.5") == f"device:{DEVICE_A}"
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="10.0.0.6") == "peer:10.0.0.6"
    monkeypatch.delenv("PUFFPAI_TRUSTED_PROXY_IPS")
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="10.0.0.5") == "peer:10.0.0.5"


def test_no_wildcard_trusts_everyone(app_ctx, monkeypatch):
    """'*' is a literal, not a wildcard: there is no way to trust all peers."""
    monkeypatch.setenv("PUFFPAI_TRUSTED_PROXY_IPS", "*")
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A},
                      peer="192.168.1.50") == "peer:192.168.1.50"


# ── 3. separation and stability ─────────────────────────────────────────────

def test_two_devices_get_two_keys(app_ctx):
    a = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    b = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_B})
    assert a != b


def test_the_same_device_is_stable_across_requests(app_ctx):
    a1 = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    a2 = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    assert a1 == a2


def test_a_device_key_never_collides_with_a_peer_key(app_ctx):
    """Namespaced prefixes, so a device id that looks like an address cannot
    land in an address bucket."""
    device = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    peer = _client_id(app_ctx, peer="192.168.1.20")
    anon = _client_id(app_ctx)
    assert device.startswith("device:")
    assert peer.startswith("peer:")
    assert anon.startswith("anon:")
    assert len({device, peer, anon}) == 3


# ── 4. direct clients keep working ──────────────────────────────────────────

def test_direct_client_with_no_headers_still_gets_a_key(app_ctx):
    """The CLI and Home Assistant send no device header and must still work.

    They reach the backend from a LAN address, which is not a trusted proxy
    peer, so they keep their stable per-address bucket. A caller on LOOPBACK
    with no asserted identity is indistinguishable from the dashboard proxy's
    unidentified traffic and is therefore treated as anonymous; a local process
    that wants a durable session asserts its own ``X-Puff-Device-Id``.
    """
    assert _client_id(app_ctx, peer="192.168.1.20") == "peer:192.168.1.20"
    assert _client_id(app_ctx, peer="10.0.0.9") == "peer:10.0.0.9"


def test_missing_remote_addr_degrades_to_unknown(app_ctx):
    """No socket address at all (unix socket, odd WSGI server) -> one shared
    bucket, which is the restrictive direction: it grants nobody another
    identity."""
    with app_ctx.test_request_context("/v1/chat/completions", method="POST",
                                      environ_base={"REMOTE_ADDR": None}):
        assert api_server._get_chat_client_id() == "unknown"


def test_no_request_context_at_all(app_ctx):
    """Called off a request (a background task): never raises."""
    assert api_server._get_chat_client_id() == "unknown"


# ── 5. the header is not advertised to browsers ─────────────────────────────

def test_cors_no_longer_invites_x_client_id():
    """Advertising a header we refuse to honour only invites callers to send it,
    and X-Puff-Device-Id is server-set so a preflight must not permit it."""
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    resp = client.options(
        "/v1/chat/completions",
        headers={"Origin": "http://localhost:3003",
                 "Access-Control-Request-Method": "POST"},
    )
    allowed = resp.headers.get("Access-Control-Allow-Headers", "")
    assert "X-Client-Id" not in allowed
    assert "X-Puff-Device-Id" not in allowed
    # The one client header that legitimately passes is still advertised.
    assert "X-Vault-Token" in allowed


# ── 6. the docstring documents the rule (it is the contract) ────────────────

def test_the_rule_is_documented_for_the_next_reader():
    doc = api_server._get_chat_client_id.__doc__ or ""
    assert "X-Puff-Device-Id" in doc
    assert "X-Client-Id" in doc          # names what was removed and why
    assert "PUFFPAI_TRUSTED_PROXY_IPS" in doc
    for word in ("CLI", "Home Assistant"):
        assert word in doc, f"direct-client rule must be documented: {word}"
