"""Vault idle time, PIN rules and local proxies.

* Status polling is not use: /system/status leaves the vault's idle time
  alone, so an open home page cannot keep the vault open.
* Every way to make a vault holds to the same PIN rules (the Vault page's
  own route included), and the PIN rules see dressed-up patterns.
* With the Bridge up, a proxy on this computer is refused: it would carry a
  request past the Bridge.
* Chat asks the vault itself whether to ask for the PIN, reads the documents
  inside it, and the vault still locks itself after a chat opened it.
* One count of outside services everywhere, whole sentences for "switched on
  by", and plain names for log lines with no row.
* Plain words: no "door" from the key list, Bridge lines that claim only what
  is enforced, "not plugged in" on /system/status.
* /setup/ollama saves only a model Ollama has.
* Importing the server writes nothing to the owner's password store.

No network: every outside call is faked, and the only sockets are on this
computer.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading

import pytest

import conftest
from puffpai import (api_server, front_page, outside_services, plain_words,
                     setup_ops, zone_bridge, zone_vault)
from puffpai.config import get_config
from puffpai.security import (bridge_guard, egress_doors, egress_ledger, keys,
                              outside_switches, privacy_explainer)
from puffpai.security import vault as vault_mod
from puffpai.security.vault import Vault

API_KEY = api_server._API_KEY
GOOD_PIN = "Tulip-9-Harbor"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _auth(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    headers.update(extra or {})
    return headers


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def served(monkeypatch, tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    clock = _Clock()
    v._clock = clock
    v.add_lock_listener(api_server._on_vault_locked)
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    api_server._VAULT_CHAT_STATE.clear()
    yield v, clock
    v.lock()
    api_server._vault_sessions.clear()
    api_server._VAULT_CHAT_STATE.clear()


def _write_manifest(bridge=None, run_egress="enumerated"):
    cfg = get_config()
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    data = {"posture": {"build_network": "online", "run_egress": run_egress}}
    if bridge is not None:
        data["bridge"] = {"posture": bridge}
    with open(os.path.join(cfg.CONFIG_DIR, "build_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(data, fh)
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


@pytest.fixture
def bridge_up():
    _write_manifest("drawbridge_up")
    yield
    bridge_guard.forget_enforced()


@pytest.fixture
def bridge_open():
    _write_manifest("fenced")
    yield
    bridge_guard.forget_enforced()


# ── 1. a status read is not use ─────────────────────────────────────────────

def test_the_status_readers_do_not_restart_the_idle_time(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"), idle_lock_seconds=300)
    clock = _Clock()
    v._clock = clock
    v.create(GOOD_PIN)
    v.store("a", "1")
    v.store_document("d", b"text")
    for _ in range(9):
        clock.now += 30
        assert v.item_count() == 2
        assert v.holds_documents() is True
    clock.now += 31                       # 301 s after the last real use
    assert v.item_count() is None and v.holds_documents() is None
    assert not v.is_unlocked and v._data_key is None


def test_polling_system_status_does_not_keep_the_vault_open(served, client):
    v, clock = served
    got = client.post("/vault/unlock", json={"password": GOOD_PIN, "initialize": True},
                      headers=_auth())
    assert got.status_code == 200
    v.store("x", "1")                     # real use at t=0
    for _ in range(9):                    # the home page, every 30 s
        clock.now += 30
        status = client.get("/system/status", headers=_auth()).get_json()
        assert status["vault"]["status"] == "unlocked"
        assert status["vault"]["item_count"] == 1
    clock.now += 31
    status = client.get("/system/status", headers=_auth()).get_json()
    assert status["vault"]["status"] == "locked"
    assert not v.is_unlocked and v._data_key is None


def test_real_use_still_keeps_it_open(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"), idle_lock_seconds=300)
    clock = _Clock()
    v._clock = clock
    v.create(GOOD_PIN)
    for _ in range(5):
        clock.now += 200
        v.list_secrets()                  # the owner reading her vault
    assert v.is_unlocked


# ── 2. one PIN rule on every way to make a vault ────────────────────────────

@pytest.mark.parametrize("pin", ["12121212", "11112222", "abcabcab", "20242024",
                                 "correct horse battery staple"])
def test_the_vault_pages_create_route_uses_the_pin_rules(served, client, pin):
    v, _clock = served
    got = client.post("/vault/unlock", json={"password": pin, "initialize": True},
                      headers=_auth())
    assert got.status_code == 400
    body = got.get_json()
    assert body["success"] is False and body["weak_password"] is True
    assert body["message"] == setup_ops.validate_vault_pin(pin)[1]
    assert pin not in body["message"]
    assert not v.is_initialized
    same = client.post("/setup/vault", json={"password": pin}, headers=_auth())
    assert same.status_code == 400


def test_a_good_pin_still_makes_the_vault_from_the_vault_page(served, client):
    v, _clock = served
    got = client.post("/vault/unlock", json={"password": GOOD_PIN, "initialize": True},
                      headers=_auth())
    assert got.status_code == 200 and got.get_json()["recovery_code"]
    assert v.is_initialized and v.is_unlocked


NEWLY_REFUSED = ["12-34-56-78", "1a2b3c4d5e", "a1b2c3d4", "0000000001",
                 "1111111a", "aaaa1111", "12121212a", "112233445566", "1qazxsw2",
                 "qazwsxedc1", "p@ssw0rd1", "iloveyou1", "letmein12",
                 "0192837465", "a.b.c.d.e.f.g.h", "P@ssw0rd!", "Summer2024",
                 "zaq1xsw2cde3", "aabbccddee", "2718281828", "3141592653"]
STILL_GOOD = ["Tq7-ravenQ", "k2m9x4p7Lz", "Blue-Heron-58", "mY3#vault8",
              "7fjq2kx9wp", "purple!Otter93", "Rz8qWm4t", "Sparrow&Moss4",
              "9fK2-mP7q", "8316047295", "h4ppy-Cl0ud-17", "583920174x",
              "5839201746", "Tulip-9-Harbor", "Maple-4-Lantern",
              "a-different-strong-pin", "7bS!k2@Lp", "UPPER-lower-123",
              "only-letters-and-dashes", "Pass1234word", "4815162342"]
# "hunter2hunter2" left this list in the final fix: typed twice is refused.


@pytest.mark.parametrize("pin", NEWLY_REFUSED)
def test_dressed_up_patterns_are_refused(pin):
    ok, why = setup_ops.validate_vault_pin(pin)
    assert not ok and why and pin not in why
    assert "—" not in why and not plain_words.check(why)


@pytest.mark.parametrize("pin", STILL_GOOD)
def test_good_pins_are_still_fine(pin):
    assert setup_ops.validate_vault_pin(pin) == (True, "")


def test_the_vault_floor_still_takes_a_spaced_passphrase():
    # The PIN rule is the wizard's and the pages'; the vault's own floor keeps
    # a strong passphrase for code that makes a vault directly.
    assert vault_mod.validate_vault_secret("correct horse battery staple")[0]


# ── 3. a proxy on this computer cannot carry anything past the Bridge ───────

@pytest.fixture
def local_proxy(monkeypatch):
    """A fake proxy on this computer that records what it is asked."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                 "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(0.5)
    asked: list[str] = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            try:
                conn.settimeout(1)
                asked.append(conn.recv(200).split(b"\r\n")[0].decode(errors="replace"))
            except OSError:
                pass
            finally:
                conn.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    port = srv.getsockname()[1]
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{port}")
    yield port, asked
    stop.set()
    srv.close()
    worker.join(2)


def test_an_unlisted_request_through_a_local_proxy_is_stopped(bridge_up, local_proxy):
    import requests
    port, asked = local_proxy
    before = len(egress_ledger.read(1.0, limit=egress_ledger.MAX_READ))
    with pytest.raises(requests.exceptions.RequestException):
        requests.get("https://telemetry.example.org/phone-home", timeout=3)
    assert asked == []                       # the proxy was never reached
    lines = egress_ledger.read(1.0, limit=egress_ledger.MAX_READ)
    assert len(lines) > before
    assert lines[0]["code"] == bridge_guard.PROXY_CODE
    assert lines[0]["service"] == bridge_guard.LEDGER_SERVICE_OTHER


def test_the_proxy_check_itself(bridge_up, local_proxy):
    port, _asked = local_proxy
    sock = socket.socket()
    try:
        with pytest.raises(bridge_guard.BridgeRefused) as exc:
            bridge_guard.check(sock, ("127.0.0.1", port))
        assert exc.value.user_message == zone_bridge.REFUSED_LINE
        # Anything else on this computer (Ollama, the dashboard) is untouched.
        bridge_guard.check(sock, ("127.0.0.1", port + 1 if port < 65535 else port - 1))
    finally:
        sock.close()


def test_a_listed_exit_cannot_use_the_proxy_either(bridge_up, local_proxy):
    port, _asked = local_proxy
    exit_code = compile("import sys\ndef dial(addr, sock=None):\n"
                        "    sys.audit('socket.connect', sock, addr)\n",
                        os.path.join(conftest._SRC_ROOT, "security", "pinned_net.py"),
                        "exec")
    scope: dict = {}
    exec(exit_code, scope)
    sock = socket.socket()
    try:
        with pytest.raises(bridge_guard.BridgeRefused):
            scope["dial"](("127.0.0.1", port), sock)
    finally:
        sock.close()


def test_with_the_bridge_open_a_local_proxy_is_not_judged(bridge_open, local_proxy):
    port, _asked = local_proxy
    sock = socket.socket()
    try:
        bridge_guard.check(sock, ("127.0.0.1", port))
    finally:
        sock.close()


# A proxy named in the call itself, not in the settings:
# requests.get(..., proxies={"https": "http://127.0.0.1:<port>"})
# reached a proxy on this computer, which was asked CONNECT 198.51.100.20:443).

@pytest.fixture
def named_proxy(local_proxy, monkeypatch):
    """The fake local proxy, with nothing in the settings pointing at it."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    from puffpai.security import lan_guard
    lan_guard.install()
    return local_proxy


def test_the_proxy_entry_points_are_guarded():
    got = bridge_guard.guard_proxy_entry_points()
    assert {"http.client.HTTPConnection.set_tunnel",
            "http.client.HTTPConnection.putrequest",
            "urllib.request.Request.set_proxy",
            "urllib3.poolmanager.ProxyManager.__init__",
            "requests.adapters.HTTPAdapter.proxy_manager_for"} <= set(got)
    assert bridge_guard.guard_proxy_entry_points() == got      # safe again


def test_a_proxy_named_in_a_requests_call_is_stopped(bridge_up, named_proxy):
    import requests
    port, asked = named_proxy
    with pytest.raises(requests.exceptions.RequestException) as exc:
        requests.get("https://198.51.100.20/x", timeout=3,
                     proxies={"https": f"http://127.0.0.1:{port}"})
    assert isinstance(exc.value, bridge_guard.BridgeRefused)
    assert exc.value.user_message == zone_bridge.REFUSED_LINE
    with pytest.raises(requests.exceptions.RequestException):
        requests.Session().get("http://198.51.100.20/x", timeout=3,
                               proxies={"http": f"http://127.0.0.1:{port}"})
    assert asked == []                       # the proxy was never reached
    line = egress_ledger.read(1.0, limit=egress_ledger.MAX_READ)[0]
    assert line["code"] == bridge_guard.PROXY_CODE


def test_a_proxy_named_in_a_urllib_call_is_stopped(bridge_up, named_proxy):
    import urllib.error
    import urllib.request
    port, asked = named_proxy
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": f"http://127.0.0.1:{port}",
                                     "http": f"http://127.0.0.1:{port}"}))
    for url in ("https://198.51.100.20/x", "http://198.51.100.20/x"):
        with pytest.raises(urllib.error.URLError) as exc:
            opener.open(url, timeout=3)
        assert isinstance(exc.value, bridge_guard.BridgeRefused)
    assert asked == []


def test_a_tunnel_or_proxy_manager_made_by_hand_is_stopped(bridge_up, named_proxy):
    import http.client
    import urllib3
    port, asked = named_proxy
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=3)
    with pytest.raises(bridge_guard.BridgeRefused):
        conn.set_tunnel("198.51.100.20", 443)
    with pytest.raises(bridge_guard.BridgeRefused):
        urllib3.ProxyManager(f"http://127.0.0.1:{port}")
    assert asked == []


def test_buildbots_own_proxy_free_calls_are_untouched(bridge_up, named_proxy):
    import requests
    from puffpai.security import lan_guard
    port, asked = named_proxy
    with pytest.raises(requests.exceptions.ConnectionError) as exc:
        # the fake server reads the request line and hangs up
        requests.get(f"http://127.0.0.1:{port}/api/tags", timeout=3,
                     proxies=lan_guard.no_proxies())
    assert not isinstance(exc.value, bridge_guard.BridgeRefused)
    assert asked and asked[0].startswith("GET /api/tags")


def test_with_the_bridge_open_a_named_proxy_is_used(bridge_open, named_proxy):
    import requests
    port, asked = named_proxy
    with pytest.raises(requests.exceptions.RequestException) as exc:
        requests.get("https://198.51.100.20/x", timeout=3,
                     proxies={"https": f"http://127.0.0.1:{port}"})
    assert not isinstance(exc.value, bridge_guard.BridgeRefused)
    assert asked and asked[0].startswith("CONNECT 198.51.100.20:443")


# A UDP datagram sent to an address without connecting first:
# unconnected sendto passed the Bridge unseen).

def test_a_datagram_to_the_internet_is_stopped(bridge_up):
    from puffpai.security import lan_guard
    lan_guard.install()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(bridge_guard.BridgeRefused):
            sock.sendto(b"x", ("198.51.100.20", 53))
        with pytest.raises(bridge_guard.BridgeRefused):
            sock.sendto(b"x", 0, ("198.51.100.20", 53))
        if hasattr(sock, "sendmsg"):  # not on Windows
            with pytest.raises(bridge_guard.BridgeRefused):
                sock.sendmsg([b"x"], [], 0, ("198.51.100.20", 53))
    finally:
        sock.close()
    line = egress_ledger.read(1.0, limit=egress_ledger.MAX_READ)[0]
    assert line["code"] == bridge_guard.LEDGER_CODE


def test_a_datagram_on_this_computer_still_goes(bridge_up):
    from puffpai.security import lan_guard
    lan_guard.install()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        tx.sendto(b"hello", rx.getsockname())
        assert rx.recvfrom(16)[0] == b"hello"
    finally:
        rx.close()
        tx.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the Proactor loop is Windows only")
def test_asyncios_windows_loop_sends_no_datagram_past_the_bridge(bridge_up):
    from asyncio import windows_events
    from puffpai.security import lan_guard
    lan_guard.install()
    assert getattr(windows_events.IocpProactor.sendto, "_puffpai_guarded", False)
    proactor = windows_events.IocpProactor()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        proactor._register_with_iocp(sock)
        with pytest.raises(bridge_guard.BridgeRefused):
            proactor.sendto(sock, b"x", 0, ("198.51.100.20", 53))
    finally:
        sock.close()
        proactor.close()


def test_the_refusal_reads_plainly_in_the_log():
    words = outside_services._STOPPED[bridge_guard.PROXY_CODE]
    assert "proxy" in words and "—" not in words
    assert bridge_guard.PROXY_CODE in egress_ledger.REASON_CODES


# ── 4. chat opens the vault, and it still locks itself ──────────────────────

DEVICE = "0123456789abcdef0123456789abcdef"


def _chat(message):
    from flask import g
    env = {"REMOTE_ADDR": "127.0.0.1"}
    with api_server.app.test_request_context(
            "/v1/chat/completions", method="POST",
            headers={"X-Puff-Device-Id": DEVICE}, environ_base=env):
        # The paired dashboard's requests carry the key (its proxy adds it;
        # the before-request hook that marks it does not run here). A caller
        # with no key never reaches the vault from chat.
        g.puffpai_has_key = True
        return api_server._maybe_vault_gated_answer(message)


@pytest.fixture
def vault_with_a_document(served, monkeypatch):
    v, clock = served
    v.create(GOOD_PIN)
    v.store_document("tax_form.txt", b"Name: Test Person\nSSN: 000-12-3456\n")
    v.lock()
    seen: list[list[dict]] = []

    def answer(query, docs):
        seen.append(docs)
        return "from the vault: " + " | ".join(d["text"] for d in docs)

    monkeypatch.setattr(api_server, "_synthesize_from_vault", answer)
    return v, clock, seen


def test_the_gate_asks_the_vault_itself(vault_with_a_document):
    v, _clock, _seen = vault_with_a_document
    assert not any(f.startswith("doc_") for f in os.listdir(v.vault_dir))
    assert api_server._vault_may_hold_documents(v) is True        # locked: ask
    assert v.unlock(GOOD_PIN)
    assert api_server._vault_may_hold_documents(v) is True
    empty = Vault(vault_dir=os.path.join(os.path.dirname(v.vault_dir), "e"))
    empty.create(GOOD_PIN)
    assert api_server._vault_may_hold_documents(empty) is False    # open, empty
    empty.lock()


def test_chat_opens_the_vault_and_answers_from_its_document(vault_with_a_document):
    v, clock, seen = vault_with_a_document
    prompt = _chat("what is my social security number?")
    assert "reply with your vault PIN" in prompt and "Zone" not in prompt
    answer = _chat(GOOD_PIN)
    assert "000-12-3456" in answer and v.is_unlocked
    # A follow-up in the same chat is answered without the PIN.
    assert "000-12-3456" in _chat("what is my date of birth?")
    assert len(seen) == 2


def test_a_vault_opened_from_chat_locks_itself(vault_with_a_document):
    v, clock, _seen = vault_with_a_document
    _chat("what is my social security number?")
    _chat(GOOD_PIN)
    assert v.is_unlocked
    clock.now += 301
    vault_mod.watch_once()                  # the server side, no request
    assert not v.is_unlocked and v._data_key is None
    assert "vault_session" not in api_server._VAULT_CHAT_STATE[f"device:{DEVICE}"]
    again = _chat("what is my social security number?")
    assert "reply with your vault PIN" in again


def test_a_wrong_pin_opens_nothing(vault_with_a_document):
    v, _clock, seen = vault_with_a_document
    _chat("what is my social security number?")
    assert "didn't open" in _chat("Wrong-Pin-42")
    assert not v.is_unlocked and seen == []


@pytest.mark.parametrize("text, pin_like", [
    ("Tq7-ravenQ", True), ("1234", True), ("x" * 128, True), ("x" * 129, False),
    ("two words", False), ("abc", False), ("", False), ("caf\u00e9-caf\u00e9", False)])
def test_a_pin_with_marks_is_recognised(text, pin_like):
    assert api_server._is_pin_like(text) is pin_like


def test_the_chat_vault_words_are_plain():
    for words in (api_server._VAULT_CHAT_UNPAIRED_MESSAGE,):
        assert "Zone" not in words and "—" not in words


# ── 5. one count of outside services, whole sentences ───────────────────────

def test_every_count_comes_from_the_one_list(bridge_open, client):
    rows = outside_services.rows()
    on = sum(1 for r in rows if r["state"] == "on")
    assert on > 0     # as built, with the Bridge down: BuildBot's own tools
    assert len(privacy_explainer.open_services()) == on
    assert front_page._outside_on() == on
    got = client.get("/system/privacy", headers=_auth()).get_json()
    assert got["outside_services_on"] == on
    page = outside_services.page()
    assert f"{on} outside services are switched on" in page["line"]
    summary = " ".join(privacy_explainer.plain_summary(None, vault=None))
    assert "Every outside service is off" not in summary


def test_with_the_bridge_up_every_count_says_none(bridge_up, client):
    assert privacy_explainer.open_services() == []
    assert front_page._outside_on() == 0
    got = client.get("/system/privacy", headers=_auth()).get_json()
    assert got["outside_services_on"] == 0


def test_as_built_rows_say_why_they_are_on(bridge_open):
    for row in outside_services.rows():
        if row["kind"] != "as_built" or row["state"] != "on":
            continue
        assert row["turned_on_by"] == "" and row["turned_on_line"] == ""
        assert row["state_line"].startswith(outside_services.AS_BUILT_ON)
        assert "built in" not in row["state_line"]


@pytest.mark.parametrize("by, at, line", [
    ("you", "today", "Switched on by you, today."),
    ("you", "", "Switched on by you."),
    ("when your bot was made", "", "Switched on when your bot was made."),
    ("when BuildBot was installed", "3 May",
     "Switched on when BuildBot was installed, 3 May."),
    ("", "today", ""),
])
def test_switched_on_by_is_one_whole_sentence(by, at, line):
    assert outside_services.turned_on_line(by, at) == line


def test_the_outside_page_shows_the_whole_sentence():
    page = open(os.path.join(REPO, "dashboard", "app", "dashboard", "outside",
                             "page.tsx"), encoding="utf-8").read()
    assert "Switched on by {service.turned_on_by}" not in page
    assert "service.turned_on_line" in page


def test_the_private_stuff_card_never_claims_what_it_did_not_read():
    card = open(os.path.join(REPO, "dashboard", "app", "dashboard", "_components",
                             "AsideMenu", "index.tsx"), encoding="utf-8").read()
    assert '? "Kept on this computer"' not in card
    # The card's words moved to the shared consent words.
    words = open(os.path.join(REPO, "dashboard", "app", "dashboard", "_lib",
                              "consentWords.ts"), encoding="utf-8").read()
    assert "countWords(live)" in card
    assert "Outside services not checked" in words


def test_the_hidden_browsers_refusals_have_a_plain_name(monkeypatch):
    rec = {"t": "2026-09-28T12:00:00+00:00", "service": "app_runner",
           "host": "edge.microsoft.com", "what": "request", "outcome": "refused",
           "code": "not_allowed", "status": "", "bytes_in": None}
    monkeypatch.setattr(egress_ledger, "read", lambda *a, **k: [rec])
    line = outside_services.log_rows()[0]
    assert line["service"] == "The hidden browser Toolsmith uses to try apps"
    # These are one plain line saying they were stopped.
    assert line["result"] == outside_services.QUIET_STOP_RESULT
    assert "stopped" in line["result"] and "none of them reached the internet" in line["result"]
    assert "An outside service" not in line["line"]
    assert not plain_words.check(line["service"] + " " + line["result"])


# ── 6. plain words ──────────────────────────────────────────────────────────

def test_the_key_list_never_says_door():
    row = {"id": "github", "name": "GitHub", "for": "x", "signup_url": "https://github.com/x",
           "door": "code_search", "how_long": "1 minute", "cost_note": "free",
           "card_required": False, "personal_use_only": False,
           "placement": {"in": "header", "name": "Authorization"},
           "hosts": ["api.github.com"]}
    why = keys.row_problem(row)
    assert why and "door" not in why.lower()
    missing = dict(row, door="")
    why = keys.row_problem(missing)
    assert why and "door" not in why.lower()


def test_the_key_list_log_line_never_says_door(caplog):
    import logging
    keys._catalog_cache.update({"path": None, "mtime": None, "rows": {}})
    with caplog.at_level(logging.WARNING):
        keys.catalog()
    assert "door" not in caplog.text.lower()


@pytest.mark.parametrize("level", ["switched_on", "sealed"])
def test_the_bridge_lines_claim_only_what_is_enforced(level):
    line = zone_bridge.ENFORCED_LINES[level]
    assert "reaches nothing" not in line and "Nothing reaches" not in line
    assert zone_bridge.NAME_LOOKUP_LINE in line
    assert not plain_words.check(line) and "—" not in line


def test_the_drawbridge_summary_says_name_lookups_still_leave():
    entry = zone_bridge.posture_by_id("drawbridge_up")
    assert "reaches nothing" not in entry["summary"]
    assert "Looking up a website's address still asks your network" in entry["summary"]
    with open(os.path.join(REPO, "config", "bridge_postures.json"), encoding="utf-8") as fh:
        shipped = {p["id"]: p for p in json.load(fh)["postures"]}
    assert shipped["drawbridge_up"]["summary"] == entry["summary"]


def test_a_missing_drive_reads_not_plugged_in_on_system_status(client):
    v = Vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)
    api_server._vault_instance = None
    try:
        got = client.get("/system/status", headers=_auth()).get_json()["vault"]
        assert got["status"] == "not_plugged_in"
        assert got["message"].startswith("Your vault drive is not plugged in.")
        assert not os.path.exists(v.vault_dir)
    finally:
        api_server._vault_instance = None


# ── 7. /setup/ollama saves only a model Ollama has ──────────────────────────

@pytest.fixture
def ollama_models(monkeypatch):
    saved: list = []
    state = {"models": ["qwen3:30b-a3b", "llama3.2:latest"]}
    monkeypatch.setattr(setup_ops, "list_models",
                        lambda url=None, timeout=3: state["models"])
    monkeypatch.setattr(setup_ops, "set_ollama_config",
                        lambda model, url=None: saved.append((model, url)) or "cfg")
    return state, saved


def test_a_model_ollama_does_not_have_is_not_saved(client, ollama_models):
    _state, saved = ollama_models
    got = client.post("/setup/ollama", json={"model": "definitely-not-a-real-model-xyz-999"},
                      headers=_auth())
    assert got.status_code == 400 and got.get_json()["model_missing"] is True
    assert saved == []


@pytest.mark.parametrize("model", ["qwen3:30b-a3b", "llama3.2"])
def test_an_installed_model_is_saved(client, ollama_models, model):
    _state, saved = ollama_models
    got = client.post("/setup/ollama", json={"model": model}, headers=_auth())
    assert got.status_code == 200 and saved == [(model, None)]


def test_nothing_is_saved_when_ollama_does_not_answer(client, ollama_models):
    state, saved = ollama_models
    state["models"] = None
    got = client.post("/setup/ollama", json={"model": "qwen3:30b-a3b"}, headers=_auth())
    assert got.status_code == 503 and saved == []


def test_an_internet_address_for_the_brain_is_refused(client, ollama_models):
    _state, saved = ollama_models
    got = client.post("/setup/ollama", json={"model": "qwen3:30b-a3b",
                                             "url": "http://203.0.113.9:11434"},
                      headers=_auth())
    assert got.status_code == 400 and saved == []


# ── 8. importing the server writes nothing to the password store ────────────

def test_a_read_only_resolve_writes_nothing(monkeypatch):
    from puffpai.security import api_key_store
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    monkeypatch.setattr(api_key_store, "stored", lambda state=None: ("", ""))
    monkeypatch.setattr(api_key_store, "store_new",
                        lambda *a, **k: pytest.fail("a store write at import"))
    monkeypatch.setattr(api_key_store, "move_old_entry",
                        lambda *a, **k: pytest.fail("a store write at import"))
    monkeypatch.setattr(api_server, "_API_KEY_UNSAVED", False)
    key = api_server._load_or_create_api_key(persist=False)
    assert len(key) >= api_server.MIN_API_KEY_LEN and api_server._API_KEY_UNSAVED


def test_the_key_is_kept_when_the_server_starts(monkeypatch):
    from puffpai.security import api_key_store
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    kept: list = []
    monkeypatch.setattr(api_key_store, "stored", lambda state=None: ("", ""))
    monkeypatch.setattr(api_key_store, "move_old_entry", lambda *a, **k: "")
    monkeypatch.setattr(api_key_store, "store_new",
                        lambda key, **k: kept.append(key) or "the test store")
    monkeypatch.setattr(api_server, "_migrate_legacy_key_file", lambda: None)
    monkeypatch.setattr(api_server, "_API_KEY", "k" * 64)
    monkeypatch.setattr(api_server, "_API_KEY_UNSAVED", True)
    api_server.persist_api_key()
    assert kept == ["k" * 64] and api_server._API_KEY_UNSAVED is False
    api_server.persist_api_key()                     # once only
    assert kept == ["k" * 64]


def test_a_key_stored_meanwhile_wins(monkeypatch):
    from puffpai.security import api_key_store
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    monkeypatch.setattr(api_key_store, "stored", lambda state=None: ("x", "s" * 64))
    monkeypatch.setattr(api_key_store, "move_old_entry", lambda *a, **k: "")
    monkeypatch.setattr(api_key_store, "store_new",
                        lambda *a, **k: pytest.fail("must not overwrite"))
    monkeypatch.setattr(api_server, "_API_KEY", "k" * 64)
    monkeypatch.setattr(api_server, "_API_KEY_UNSAVED", True)
    api_server.persist_api_key()
    assert api_server._API_KEY == "s" * 64


def test_the_server_start_keeps_the_key():
    src = open(os.path.join(REPO, "src", "puffpai", "api_server.py"),
               encoding="utf-8").read()
    assert "_API_KEY = validate_api_key(_load_or_create_api_key(persist=False))" in src
    start = src.index("def start_background_services")
    assert "persist_api_key()" in src[start:start + 1200]


def test_importing_the_server_in_a_new_process_writes_no_entry(tmp_path):
    """The whole import, in a child process, with a password store that
    records every write to a file (never the owner's real one)."""
    shim = tmp_path / "recording_keyring.py"
    shim.write_text(
        "import os\n"
        "from keyring.backend import KeyringBackend\n"
        "class Rec(KeyringBackend):\n"
        "    priority = 1\n"
        "    def get_password(self, s, u):\n"
        "        return None\n"
        "    def set_password(self, s, u, p):\n"
        "        with open(os.environ['REC_FILE'], 'a') as fh:\n"
        "            fh.write(s + '/' + u + '\\n')\n"
        "    def delete_password(self, s, u):\n"
        "        pass\n", encoding="utf-8")
    record = tmp_path / "writes.txt"
    state = tmp_path / "state"
    env = dict(os.environ)
    env.pop("PUFFPAI_API_KEY", None)
    env.update({"PYTHON_KEYRING_BACKEND": "recording_keyring.Rec",
                "REC_FILE": str(record),
                "PUFFPAI_STATE_DIR": str(state),
                "PUFFPAI_DATA_DIR": str(tmp_path / "data"),
                "PUFFPAI_APPS_ROOT": str(tmp_path / "apps"),
                "PYTHONPATH": os.pathsep.join([str(tmp_path),
                                               os.path.join(REPO, "src")]),
                "PYTHONDONTWRITEBYTECODE": "1"})
    code = ("import keyring, puffpai.api_server as a; "
            "print(type(keyring.get_keyring()).__name__, a._API_KEY_UNSAVED)")
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "Rec True"
    assert not record.exists() or record.read_text() == ""
    assert not (state / "api_key.txt").exists()


# ── 9. the browser holds to the same new shapes ─────────────────────────────

def _parity_corpus() -> list[str]:
    import random
    rng = random.Random(20260928)
    pool = "abcdefghijklmnopqrstuvwxyz0123456789-!@.qazwsx1234"
    made = ["".join(rng.choice(pool) for _ in range(rng.randint(8, 14)))
            for _ in range(400)]
    seeds = ["password", "iloveyou", "letmein", "summer", "qwerty", "1qaz",
             "abcd", "1234", "2wsx3edc"]
    made += [rng.choice(seeds) + str(rng.randint(0, 999)) for _ in range(60)]
    made += ["".join(rng.choice("12ab") for _ in range(10)) for _ in range(40)]
    return NEWLY_REFUSED + STILL_GOOD + made


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_and_the_backend_agree_on_the_new_shapes():
    """dashboard/app/_lib/vaultPin.ts, run for real through node, refuses
    exactly what setup_ops.validate_vault_pin refuses: never looser (a PIN
    the page accepts and the server refuses), never stricter."""
    from test_vault_pin_crosslang import _node_refusals
    corpus = _parity_corpus()
    browser = _node_refusals(corpus)
    differ = [(p, bool(why), setup_ops.validate_vault_pin(p)[0])
              for p, why in zip(corpus, browser)
              if (not why) != setup_ops.validate_vault_pin(p)[0]]
    assert differ == []
    assert all(browser[corpus.index(p)] for p in NEWLY_REFUSED)


# ── 5. a PIN typed into chat never reaches the log ──────────────────────────
# The run log line for /v1/chat/completions showed
# "Maple-9e6754-Comet", a PIN typed in answer to the vault prompt.

@pytest.mark.parametrize("pin", ["Maple-9e6754-Comet", "Tulip-9-Harbor",
                                 "7394028165", "Sparrowmoss", " Tq7-ravenQ "])
def test_a_pin_shaped_reply_is_not_shown_in_the_log(pin):
    assert api_server._pii_log_preview(pin) == api_server.PIN_LIKE_LOG_LINE
    assert api_server._pii_log_preview([{"type": "text", "text": pin}]) == \
        api_server.PIN_LIKE_LOG_LINE
    assert pin.strip() not in api_server.PIN_LIKE_LOG_LINE


def test_the_chat_route_does_not_print_the_pin(client, capsys, monkeypatch):
    monkeypatch.setattr(api_server, "_scan_and_redact_message",
                        lambda m: (_ for _ in ()).throw(RuntimeError("stop here")))
    try:
        client.post("/v1/chat/completions", headers=_auth(),
                    json={"messages": [{"role": "user",
                                        "content": "Maple-9e6754-Comet"}]})
    except RuntimeError:
        pass
    out = capsys.readouterr()
    assert "Maple-9e6754-Comet" not in out.out + out.err
    assert api_server.PIN_LIKE_LOG_LINE in out.out


def test_ordinary_messages_are_still_shown_in_the_log():
    assert api_server._pii_log_preview("what is a comet?") == "what is a comet?"
    assert api_server._pii_log_preview("hi") == "hi"
    assert "—" not in api_server.PIN_LIKE_LOG_LINE


# -- 6. proxies named through other libraries ------------------
# With the Bridge up a fake proxy on this
# computer still received requests from httpx (proxy= and mounts), aiohttp
# (proxy=), websocket-client (http_proxy_host) and plain http.client asked
# for a full address. Each is now refused before the proxy is reached.

_TARGET = "198.51.100.20"


def test_httpx_with_a_named_proxy_is_stopped(bridge_up, named_proxy):
    httpx = pytest.importorskip("httpx")
    port, asked = named_proxy
    px = f"http://127.0.0.1:{port}"
    for url in (f"http://{_TARGET}/x", f"https://{_TARGET}/x"):
        with pytest.raises(bridge_guard.BridgeRefused):
            httpx.get(url, proxy=px, timeout=3)
    client = httpx.Client(mounts={"http://": httpx.HTTPTransport(proxy=px)})
    try:
        with pytest.raises(bridge_guard.BridgeRefused):
            client.get(f"http://{_TARGET}/y", timeout=3)
    finally:
        client.close()
    assert asked == []


def test_aiohttp_with_a_named_proxy_is_stopped(bridge_up, named_proxy):
    aiohttp = pytest.importorskip("aiohttp")
    import asyncio
    port, asked = named_proxy

    async def go():
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{_TARGET}/z",
                                   proxy=f"http://127.0.0.1:{port}",
                                   timeout=aiohttp.ClientTimeout(total=3)) as got:
                return got.status

    with pytest.raises(aiohttp.ClientConnectionError) as exc:
        asyncio.run(go())
    assert isinstance(exc.value, bridge_guard.BridgeRefused)
    assert asked == []


def test_websocket_client_with_a_named_proxy_is_stopped(bridge_up, named_proxy):
    websocket = pytest.importorskip("websocket")
    port, asked = named_proxy
    with pytest.raises(bridge_guard.BridgeRefused):
        websocket.create_connection(f"ws://{_TARGET}/ws",
                                    http_proxy_host="127.0.0.1",
                                    http_proxy_port=port, proxy_type="http",
                                    timeout=3)
    assert asked == []


def test_http_client_asked_for_another_computer_is_stopped(bridge_up, named_proxy):
    import http.client
    port, asked = named_proxy
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        with pytest.raises(bridge_guard.BridgeRefused):
            conn.request("GET", f"http://{_TARGET}/abs")
    finally:
        conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        with pytest.raises(bridge_guard.BridgeRefused):
            conn.request("CONNECT", f"{_TARGET}:443")
    finally:
        conn.close()
    assert asked == []


def test_http_client_asking_its_own_computer_still_goes(bridge_up, named_proxy):
    import http.client
    import time
    port, asked = named_proxy
    paths = ("/api/tags", f"http://127.0.0.1:{port}/api/tags",
             f"/go?to=http://{_TARGET}/x")   # an address in a query is a path
    for path in paths:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            conn.request("GET", path)
        finally:
            conn.close()
    deadline = time.monotonic() + 3
    while len(asked) < len(paths) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sorted(line.split(" ")[1] for line in asked) == sorted(paths)


def test_with_the_bridge_open_httpx_uses_a_named_proxy(bridge_open, named_proxy):
    httpx = pytest.importorskip("httpx")
    port, asked = named_proxy
    with pytest.raises(httpx.HTTPError):   # the fake proxy hangs up
        httpx.get(f"http://{_TARGET}/x", proxy=f"http://127.0.0.1:{port}",
                  timeout=3)
    assert asked and asked[0].startswith(f"GET http://{_TARGET}/x")


def test_a_library_loaded_after_the_guard_is_watched_too(tmp_path):
    """httpx, aiohttp and websocket-client are wrapped when they are first
    imported, so BuildBot does not load them just to watch them."""
    pytest.importorskip("httpx")
    code = (
        "import sys\n"
        "from puffpai.security import bridge_guard\n"
        "assert 'httpcore' not in sys.modules\n"
        "bridge_guard.guard_proxy_entry_points()\n"
        "assert 'httpcore' not in sys.modules\n"
        "import httpx\n"
        "from httpcore._sync.http_proxy import ForwardHTTPConnection as F\n"
        "from httpcore._async.http_proxy import AsyncTunnelHTTPConnection as T\n"
        "assert getattr(F.__init__, '_puffpai_bridge_guarded', False)\n"
        "assert getattr(T.__init__, '_puffpai_bridge_guarded', False)\n"
        "print('watched')\n")
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"),
               PYTHONDONTWRITEBYTECODE="1",
               PUFFPAI_STATE_DIR=str(tmp_path / "state"),
               PUFFPAI_APPS_ROOT=str(tmp_path / "apps"))
    got = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stderr[-2000:]
    assert "watched" in got.stdout
