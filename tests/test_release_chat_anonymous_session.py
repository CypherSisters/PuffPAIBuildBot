"""The shared null-identity bucket, and why there is no longer one.

THE BUG THESE TESTS LOCK SHUT
-----------------------------
Every browser behind the dashboard proxy reaches the backend from loopback. The
proxy asserts ``X-Puff-Device-Id`` — an HMAC of the device id under the pairing
secret — when it can derive one from a VERIFIED pairing cookie, and used to send
no header at all when it could not. The backend's fallback for "no device id" is
the socket peer, so every unidentified proxied request landed on the SAME key:
``peer:127.0.0.1``.

That key indexes ``_VAULT_CHAT_STATE``: a 300-second window in which protected
questions are answered out of the DECRYPTED vault with no further PIN. So one
bucket was shared by every caller the proxy could not identify, and one caller's
unlock was inheritable by the next for the rest of that window.

BuildBot has never issued a v1 cookie, so it does not have HomeBot's
identity-less-but-paired population. It reaches the identity-less state two
other ways, and both are in scope:

  * ``PUFF_PAIRING_ENFORCE=off`` — ``gateKindFor()`` returns "none" for every
    path, so an unpaired browser reaches the keyed proxy with no cookie;
  * a device revoked between the pairing gate and the header build: v2
    verification requires registry membership, so the proxy derives no identity
    for it even though the gate let it through a moment earlier.

WHAT IS ASSERTED
  1. Two anonymous proxied callers never get the same key.
  2. An anonymous caller cannot read a paired device's bucket.
  3. A paired device and direct LAN callers are unchanged.
  4. The marker is believed only from a trusted proxy peer.
  5. The vault-gated chat path answers an anonymous caller with the pairing
     remedy and writes NO state, so no PIN typed afterwards can unlock
     anything on that key.
  6. The proxy really sends the marker (asserted against the dashboard source).

Flask test client / synthetic request contexts only; nothing binds a port, no
real vault is opened, and no network call is made.
"""

import os
import tempfile
from pathlib import Path

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_anonchat_test_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PROXY_HEADERS = REPO_ROOT / "dashboard" / "app" / "_lib" / "server" / "proxyHeaders.ts"

#: Values shaped like the dashboard's derived id (32 hex chars).
DEVICE_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
DEVICE_B = "0f9e8d7c6b5a4938271605f4e3d2c1b0"

ANON_HEADER = "X-Puff-Anonymous"


@pytest.fixture
def app_ctx():
    api_server.app.config.update(TESTING=True)
    return api_server.app


@pytest.fixture
def clean_state():
    """Run with an empty _VAULT_CHAT_STATE and restore whatever was there."""
    saved = dict(api_server._VAULT_CHAT_STATE)
    api_server._VAULT_CHAT_STATE.clear()
    try:
        yield api_server._VAULT_CHAT_STATE
    finally:
        api_server._VAULT_CHAT_STATE.clear()
        api_server._VAULT_CHAT_STATE.update(saved)


def _client_id(app, *, headers=None, peer="127.0.0.1"):
    env = {"REMOTE_ADDR": peer}
    with app.test_request_context("/v1/chat/completions", method="POST",
                                  headers=headers or {}, environ_base=env):
        return api_server._get_chat_client_id()


# ── 1. two anonymous callers never share a key ──────────────────────────────

def test_the_marker_produces_an_anonymous_key(app_ctx):
    got = _client_id(app_ctx, headers={ANON_HEADER: "1"})
    assert got.startswith("anon:")
    assert api_server._is_anonymous_chat_client(got)


def test_no_device_id_from_the_proxy_is_also_anonymous(app_ctx):
    """The marker is belt; this is braces. A proxied request with NO device id
    must not fall back to the shared loopback bucket even if a future proxy
    forgets to set the marker."""
    assert _client_id(app_ctx).startswith("anon:")


def test_two_anonymous_callers_get_two_keys(app_ctx):
    assert _client_id(app_ctx, headers={ANON_HEADER: "1"}) != \
        _client_id(app_ctx, headers={ANON_HEADER: "1"})


def test_an_anonymous_key_is_never_reused_across_many_requests(app_ctx):
    keys = {_client_id(app_ctx, headers={ANON_HEADER: "1"}) for _ in range(25)}
    assert len(keys) == 25


def test_one_anonymous_unlock_cannot_be_inherited(app_ctx, clean_state):
    """The actual exploit: one unpaired browser unlocks, the next unpaired
    browser asks a protected question inside the 300 s window."""
    first = _client_id(app_ctx, headers={ANON_HEADER: "1"})
    clean_state[first] = {"vault_session": "first-session",
                          "unlocked_at": 1_000_000_000}
    second = _client_id(app_ctx, headers={ANON_HEADER: "1"})
    assert second != first
    assert clean_state.get(second) is None


def test_the_anonymous_namespace_cannot_be_forged(app_ctx):
    """A device id cannot contain ':' (the shape check's charset), so no
    proxy-asserted identity can ever land in the anon: namespace."""
    assert not api_server._DEVICE_SESSION_ID_RE.match("anon:deadbeef")
    got = _client_id(app_ctx, headers={"X-Puff-Device-Id": "anon:deadbeefcafe"})
    assert got.startswith("anon:")
    assert "deadbeefcafe" not in got


# ── 2. an anonymous caller cannot reach a paired device's bucket ────────────

def test_anonymous_cannot_read_a_paired_devices_bucket(app_ctx, clean_state):
    paired = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    clean_state[paired] = {"vault_session": "paired-session",
                           "unlocked_at": 1_000_000_000}
    anon = _client_id(app_ctx, headers={ANON_HEADER: "1"})
    assert anon != paired
    assert clean_state.get(anon) is None
    assert clean_state[paired]["vault_session"] == "paired-session"


def test_the_marker_wins_over_an_asserted_device_id(app_ctx, clean_state):
    """The proxy never sends both, so both means something upstream is wrong.
    The restrictive reading is the one that ships: anonymous."""
    paired = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    clean_state[paired] = {"vault_session": "paired-session"}
    both = _client_id(app_ctx, headers={ANON_HEADER: "1",
                                       "X-Puff-Device-Id": DEVICE_A})
    assert both.startswith("anon:")
    assert both != paired
    assert clean_state[paired]["vault_session"] == "paired-session"


# ── 3. everything that worked still works ──────────────────────────────────

def test_a_paired_device_is_unchanged(app_ctx):
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A}) == \
        f"device:{DEVICE_A}"
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A}) == \
        _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    assert _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_B}) != \
        _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})


def test_direct_lan_callers_are_unchanged(app_ctx):
    """CLI / Home Assistant / a script with the API key: one stable session per
    address, and the anonymous marker buys them nothing."""
    assert _client_id(app_ctx, peer="192.168.1.20") == "peer:192.168.1.20"
    assert _client_id(app_ctx, peer="192.168.1.20",
                      headers={ANON_HEADER: "1"}) == "peer:192.168.1.20"


def test_no_request_context_is_still_unknown():
    assert api_server._get_chat_client_id() == "unknown"


# ── 4. the marker is only believed from a trusted proxy peer ───────────────

def test_a_lan_client_cannot_make_itself_anonymous(app_ctx):
    """Not a security win for the attacker either way — it is asserted because
    the marker must mean 'the proxy said so', not 'somebody said so'."""
    assert _client_id(app_ctx, headers={ANON_HEADER: "1"},
                      peer="192.168.1.50") == "peer:192.168.1.50"


def test_a_named_trusted_proxy_gets_the_anonymous_rule_too(app_ctx, monkeypatch):
    monkeypatch.setenv("PUFFPAI_TRUSTED_PROXY_IPS", "10.0.0.5")
    assert _client_id(app_ctx, headers={ANON_HEADER: "1"},
                      peer="10.0.0.5").startswith("anon:")
    assert _client_id(app_ctx, headers={ANON_HEADER: "1"},
                      peer="10.0.0.6") == "peer:10.0.0.6"


@pytest.mark.parametrize("value", ["1", "0", "true", "yes", "  1  ", "whatever"])
def test_any_non_empty_marker_value_counts(app_ctx, value):
    """Fail closed on the value as well as the header: '0' is not a licence to be
    identified, because only our proxy sets the header at all and it only ever
    sets it to mean anonymous."""
    assert _client_id(app_ctx, headers={ANON_HEADER: value}).startswith("anon:")


def test_an_empty_marker_is_not_a_marker(app_ctx):
    """An empty value is no statement; the missing-device-id rule then applies
    anyway, so the outcome is still anonymous — never a shared bucket."""
    assert _client_id(app_ctx, headers={ANON_HEADER: "   "}).startswith("anon:")


# ── 5. the vault-gated chat path refuses, plainly, and stores nothing ─────

class _FakeVault:
    is_initialized = True

    def _validate_session(self, sid):  # pragma: no cover - must not be called
        raise AssertionError("an anonymous caller must not validate a session")


@pytest.fixture
def vault_with_docs(monkeypatch):
    """A vault that exists and holds documents, without touching a real one."""
    monkeypatch.setattr(api_server, "_get_vault", lambda: _FakeVault())
    monkeypatch.setattr(api_server, "_count_vaulted_documents", lambda v: 3)
    monkeypatch.setattr(
        api_server, "_load_vaulted_documents",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no vault read for an anonymous caller")),
    )


def _vault_answer(app, message, *, headers=None, peer="127.0.0.1", keyed=True):
    """The vault step for one request. *keyed*: the request carries the API
    key, as every request the dashboard's proxy sends does (its before-request
    hook is what marks it; a test request context runs no hooks). A caller with no key never reaches the vault from chat."""
    from flask import g
    env = {"REMOTE_ADDR": peer}
    with app.test_request_context("/v1/chat/completions", method="POST",
                                  headers=headers or {}, environ_base=env):
        g.puffpai_has_key = keyed
        return api_server._maybe_vault_gated_answer(message)


PROTECTED = "what are my allergies"


def test_anonymous_caller_is_told_to_pair(app_ctx, clean_state, vault_with_docs):
    answer = _vault_answer(app_ctx, PROTECTED, headers={ANON_HEADER: "1"})
    assert answer == api_server._VAULT_CHAT_UNPAIRED_MESSAGE
    assert "pair" in answer.lower()


def test_anonymous_caller_leaves_no_state_behind(app_ctx, clean_state,
                                                 vault_with_docs):
    """The important half: no pending_query is stored, so a PIN typed on the
    next turn has nothing to unlock and cannot cache a session either."""
    _vault_answer(app_ctx, PROTECTED, headers={ANON_HEADER: "1"})
    assert clean_state == {}


def test_anonymous_caller_is_never_prompted_for_a_pin(app_ctx, clean_state,
                                                      vault_with_docs):
    answer = _vault_answer(app_ctx, PROTECTED, headers={ANON_HEADER: "1"})
    assert "reply with your PIN" not in answer


def test_a_pin_from_an_anonymous_caller_unlocks_nothing(app_ctx, clean_state,
                                                        vault_with_docs):
    """Even with a pending prompt sitting in someone else's bucket, a PIN typed
    by an anonymous caller cannot reach it."""
    victim = _client_id(app_ctx, headers={"X-Puff-Device-Id": DEVICE_A})
    clean_state[victim] = {"pending_query": PROTECTED,
                           "pending_since": 9_999_999_999}
    assert _vault_answer(app_ctx, "123456", headers={ANON_HEADER: "1"}) is None
    assert clean_state[victim]["pending_query"] == PROTECTED
    assert "vault_session" not in clean_state[victim]


def test_a_paired_device_still_gets_the_pin_prompt(app_ctx, clean_state,
                                                   vault_with_docs):
    """The contrast that proves the refusal is about identity and nothing else:
    the same question from an identified device behaves exactly as before."""
    answer = _vault_answer(app_ctx, PROTECTED,
                           headers={"X-Puff-Device-Id": DEVICE_A})
    assert "PIN" in answer
    assert answer != api_server._VAULT_CHAT_UNPAIRED_MESSAGE
    assert clean_state[f"device:{DEVICE_A}"]["pending_query"] == PROTECTED


def test_a_direct_lan_caller_with_the_key_still_gets_the_pin_prompt(app_ctx, clean_state,
                                                                    vault_with_docs):
    answer = _vault_answer(app_ctx, PROTECTED, peer="192.168.1.20")
    assert "PIN" in answer
    assert clean_state["peer:192.168.1.20"]["pending_query"] == PROTECTED


def test_a_direct_lan_caller_with_no_key_gets_no_vault_step(app_ctx, clean_state,
                                                            vault_with_docs):
    """A caller with no key gets
    ordinary chat only, so no PIN prompt, no pending question and nothing to
    unlock; a PIN it sends next opens nothing."""
    assert _vault_answer(app_ctx, PROTECTED, peer="192.168.1.20", keyed=False) is None
    assert _vault_answer(app_ctx, "Tq7-ravenQ9", peer="192.168.1.20", keyed=False) is None
    assert clean_state == {}


def test_an_unprotected_question_is_not_affected(app_ctx, clean_state,
                                                 vault_with_docs):
    assert _vault_answer(app_ctx, "what's the weather like",
                         headers={ANON_HEADER: "1"}) is None
    assert clean_state == {}


# ── 6. the proxy's half of the contract ────────────────────────────────────

def test_the_dashboard_proxy_sets_the_marker_server_side():
    """The backend rule is only as good as the proxy that feeds it: assert the
    header name matches and that it is written, not relayed."""
    src = PROXY_HEADERS.read_text(encoding="utf-8")
    assert f'ANONYMOUS_HEADER = "{ANON_HEADER}"' in src, \
        "the dashboard must use the exact header name the backend reads"
    assert 'headers[ANONYMOUS_HEADER] = "1"' in src, \
        "the marker must be written server-side"
    # A relay allowlist keys on the LOWERCASE header name (header lookups are
    # case-insensitive, so that is the only spelling an entry can use). The
    # canonical constant above is mixed case, so a lowercase quoted copy anywhere
    # in the file would mean the marker had been made relayable.
    assert '"x-puff-anonymous"' not in src, \
        "the marker must never be a relayed client header"


def test_the_rule_is_documented_for_the_next_reader():
    doc = api_server._get_chat_client_id.__doc__ or ""
    assert ANON_HEADER in doc
    assert "anon:" in doc
    for word in ("inherit", "PUFF_PAIRING_ENFORCE"):
        assert word in doc, f"the shared-bucket bug must be documented: {word}"
