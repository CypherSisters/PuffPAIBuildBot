"""Security acceptance for the Bridge, the Host header, keyless reads and the
vault's auto-lock. Runnable on its own; the security pass runs it as written.

    PYTHONPATH=src py -3.14 scripts/dev_sec_acceptance.py --out <folder>
        [--base-url http://127.0.0.1:<port>]   # also check a running backend

What it checks
--------------
1. **The Bridge up** ("Only what you switched on"). With the real guards
   installed (``lan_guard``/``bridge_guard`` on the socket audit hook), each
   way BuildBot reaches out is tried, and every connection that would leave
   this computer is recorded at the last step and stopped there: the sample
   look, live-card drafts and the plan's preview, the locked box's proxy
   hosts, a proxy on this computer, a browser app's outside sites, a
   service nobody switched on, and a bare socket. Expected: nothing dialed.
   A service the owner DID switch on is tried too and must be seen dialing
   (the control: a harness that saw nothing at all would prove nothing).
2. **The Host header** (DNS rebinding against the loopback backend): a
   foreign name gets 421 on public and keyed routes; this computer's own
   names are answered.
3. **Keyless reads**: every GET route, asked with no key, answers 401 unless
   it is on the short public list; the public ones are listed with what
   they answered.
4. **The vault locks itself** after its idle time while /system/status is
   polled (a page left open), and its key is gone from memory.
5. With ``--base-url``: 2 and the keyless read of an uploaded document,
   against a running backend over a real socket. Its key is read from
   ``PUFFPAI_ACCEPT_API_KEY`` and never printed.

Nothing leaves your computer without your consent, and this run asks for
none: name lookups are answered here (one made-up public address) and the
last connect is stopped before it opens. The state,
the key and the vault live in a fresh folder under ``--out``. Writes
``dev_sec_acceptance.json`` and ``.txt`` there. Exit code 0 only when every
check passed.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit

#: The made-up public address every outside name "resolves" to here.
FAR = "93.184.216.34"
RESULTS: list[dict] = []


def check(section: str, name: str, ok: bool, detail: Any = "") -> None:
    RESULTS.append({"section": section, "check": name, "pass": bool(ok),
                    "detail": detail if isinstance(detail, (str, int, float, list, dict))
                    else repr(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {section}: {name}"
          + (f" -- {detail}" if detail not in ("", None) else ""))


# -- the wire: record and stop every connect that would leave ---------------

DIALED: list = []
_real_connect = socket.socket.connect
_real_gai = socket.getaddrinfo


class Stopped(OSError):
    pass


def _here(host: Any) -> bool:
    text = str(host)
    return text.startswith("127.") or text in ("::1", "localhost")


def _connect(self, addr):
    sys.audit("socket.connect", self, addr)  # the guards run here, as in C
    host = addr[0] if isinstance(addr, tuple) else addr
    if _here(host):
        return _real_connect(self, addr)
    DIALED.append(f"{host}:{addr[1] if isinstance(addr, tuple) else ''}")
    raise Stopped(f"stopped before it left: {host}")


def _connect_ex(self, addr):
    try:
        _connect(self, addr)
        return 0
    except OSError:
        return 111


def _gai(host, port, *args, **kwargs):
    if _here(host):
        return _real_gai(host, port, *args, **kwargs)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (FAR, port or 443))]


def install_wire() -> None:
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.getaddrinfo = _gai


def _resolver(host, port=443):
    return [FAR]


def dialed_during(fn) -> tuple[list, str]:
    DIALED.clear()
    try:
        got = fn()
        ended = f"returned {str(got)[:120]}"
    except Exception as exc:  # noqa: BLE001 - a refusal is the expected end
        ended = f"{type(exc).__name__}: {str(exc)[:120]}"
    return list(DIALED), ended


# -- setup --------------------------------------------------------------------

def fresh_state(out: str) -> str:
    os.makedirs(out, exist_ok=True)
    state = tempfile.mkdtemp(prefix="state_", dir=out)
    os.environ["PUFFPAI_STATE_DIR"] = state
    os.environ["PUFFPAI_DATA_DIR"] = os.path.join(state, "data")
    os.environ["PUFFPAI_APPS_ROOT"] = os.path.join(state, "apps")
    os.environ["PUFF_PAIRING_STATE"] = os.path.join(state, "pairing", "pairing.json")
    os.environ["PUFFPAI_API_KEY"] = secrets.token_hex(32)
    os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("HTTP_PROXY", None)
    os.makedirs(os.environ["PUFFPAI_DATA_DIR"], exist_ok=True)
    return state


def write_manifest(bridge: str, run_egress: str = "enumerated") -> None:
    from puffpai.config import get_config
    from puffpai.security import bridge_guard, egress_doors, outside_switches
    cfg = get_config()
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(os.path.join(cfg.CONFIG_DIR, "build_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"posture": {"build_network": "online", "run_egress": run_egress},
                   "bridge": {"posture": bridge}}, fh)
    with open(os.path.join(cfg.CONFIG_DIR, "puffpai_config.json"), "w",
              encoding="utf-8") as fh:
        json.dump({}, fh)
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


# -- 1. the Bridge up ----------------------------------------------------------

def bridge_up() -> None:
    S = "Bridge up"
    from puffpai.security import (bridge_guard, egress_doors, lan_guard,
                                  outside_switches)
    from puffpai import source_fetch
    write_manifest("drawbridge_up")
    lan_guard.install()
    install_wire()
    check(S, "the Bridge reads as up", bridge_guard.reach_level() == "switched_on",
          bridge_guard.reach_level())

    # The control first: a service she switched on is let through to the wire.
    outside_switches.set_switch("code_search", True, by="owner")
    d, how = dialed_during(lambda: source_fetch.fetch_small(
        "https://api.github.com/search/repositories?q=garden", allowed_hosts={"api.github.com"},
        max_bytes=2048, service="code_search", honour_robots=False, resolver=_resolver))
    check(S, "control: a service she switched on reaches the wire", bool(d), {"dialed": d, "end": how})
    outside_switches.clear("code_search")

    d, how = dialed_during(lambda: source_fetch.fetch_small(
        "https://api.openverse.org/v1/images/?q=garden", allowed_hosts={"api.openverse.org"},
        max_bytes=2048, service="picture_search", honour_robots=False, resolver=_resolver))
    check(S, "a service nobody switched on dials nothing", not d, {"dialed": d, "end": how})

    d, how = dialed_during(lambda: egress_doors.build_probe_get(
        "https://api.example.org/sample", host="api.example.org", build_online=True,
        resolver=_resolver))
    check(S, "the sample look dials nothing", not d, {"dialed": d, "end": how})

    try:
        from puffpai import feed_generator
        from puffpai.catalogs import api_seeds
        from puffpai.wizard import factory
        cands = [c for c in api_seeds.candidates()][:3]
        for cand in cands:
            d, how = dialed_during(lambda c=cand: feed_generator.draft_feed_app(
                c, build_online=True, goal="a garden bot", chooser=None))
            check(S, f"a live card draft ({cand.get('id')}) dials nothing", not d,
                  {"dialed": d, "end": how})
            d, how = dialed_during(lambda c=cand: factory._preview(c, True))
            check(S, f"the plan's preview ({cand.get('id')}) dials nothing", not d,
                  {"dialed": d, "end": how})
        if not cands:
            check(S, "live cards: no seed candidates to try", False)
    except Exception as exc:  # noqa: BLE001
        check(S, "live cards could be tried", False, f"{type(exc).__name__}: {exc}")

    from puffpai.box import driver
    for what, service in (("install", "box_proj"), ("box_traffic", "box_proj")):
        req = driver.BoxRequest(run_id="r", project="p", argv=["x"],
                                allow_hosts=["registry.npmjs.org"], service=service,
                                what=what, folder=None, limits={}, stdin=None,
                                stdout_mode="text", env={})
        allowed = driver._runtime_hosts_allowed(req)
        check(S, f"the locked box's proxy lets no {what} host out", allowed is False, allowed)

    # A proxy on this computer must not carry a request past the Bridge.
    asked: list = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(5)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            asked.append(conn.recv(200).split(b"\r\n")[0].decode("latin-1"))
            conn.close()
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{port}"
    try:
        import requests
        d, how = dialed_during(lambda: requests.get("https://telemetry.example.org/x", timeout=3))
    finally:
        os.environ.pop("HTTPS_PROXY", None)
        srv.close()
    check(S, "a proxy on this computer is asked nothing", not asked and not d,
          {"proxy_asked": asked, "dialed": d, "end": how})

    # A browser app: one site switched on by her, one not.
    app = "garden-notes-abcdefgh"
    try:
        egress_doors.register_web_app_door(app, ["api.example.org", "cdn.example.net"])
        flag = egress_doors.web_app_door(app).config_flag
        # Her earlier yes to one site, as the app page's consent leaves it.
        egress_doors._store_web_consent(app, {"flag": flag, "kind": "web",  # noqa: SLF001
                                               "app": app, "open": True,
                                               "hosts_on": ["api.example.org"],
                                               "by": "owner"})
        assert egress_doors.app_web_hosts(app) == {"api.example.org": True,
                                                   "cdn.example.net": False}, flag
        d, how = dialed_during(lambda: egress_doors.app_web_request(
            app, "GET", "https://cdn.example.net/track.js"))
        check(S, "an app's site she did not switch on dials nothing", not d,
              {"dialed": d, "end": how})
        d, how = dialed_during(lambda: egress_doors.app_web_request(
            app, "GET", "https://api.example.org/data"))
        check(S, "control: an app's site she switched on reaches the wire", bool(d),
              {"dialed": d, "end": how})
    except Exception as exc:  # noqa: BLE001
        check(S, "the app checks could be run", False, f"{type(exc).__name__}: {exc}")
    finally:
        egress_doors.unregister_web_app_door(app)

    def bare():
        s = socket.socket()
        try:
            s.settimeout(2)
            s.connect((FAR, 443))
        finally:
            s.close()
    d, how = dialed_during(bare)
    check(S, "a bare socket to the internet dials nothing", not d, {"dialed": d, "end": how})


# -- 2 and 3: the Host header and keyless reads (in this process) -----------

def host_and_keyless() -> None:
    from puffpai import api_server
    app = api_server.app
    app.config.update(TESTING=True)
    client = app.test_client()
    key = {"Authorization": "Bearer " + os.environ["PUFFPAI_API_KEY"]}
    S = "Host header"
    for host in ("rebind.attacker.example", "rebind.attacker.example:8000",
                 "127.0.0.1.nip.io:8000", "192.168.1.77:8000", ""):
        codes = {path: client.get(path, headers={"Host": host, **(key if k else {})}).status_code
                 for path, k in (("/health", False), ("/v1/models", False),
                                 ("/system/status", True), ("/vault/status", True))}
        check(S, f"Host {host!r} is refused everywhere", set(codes.values()) == {421}, codes)
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000"):
        code = client.get("/health", headers={"Host": host}).status_code
        check(S, f"Host {host!r} is answered", code == 200, code)

    S = "Keyless reads"
    public = set(api_server._PUBLIC_ENDPOINTS)
    expected_public_get = {"health", "voice_capabilities", "openai_list_models",
                           "ollama_list_models", "knowledge_serve_asset"}
    open_endpoints: set = set()
    open_ones: dict = {}
    wrong: dict = {}
    count = 0
    for rule in app.url_map.iter_rules():
        if "GET" not in (rule.methods or ()) or rule.endpoint == "static":
            continue
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace(f"<{arg}>", "x")
            for conv in ("path", "int", "string", "float", "uuid"):
                path = path.replace(f"<{conv}:{arg}>", "1" if conv in ("int", "float") else "x")
        count += 1
        code = client.get(path, headers={"Host": "localhost"}).status_code
        if rule.endpoint in public:
            open_ones[path] = code
            open_endpoints.add(rule.endpoint)
        elif code != 401:
            wrong[path] = code
    check(S, f"every keyed GET route ({count} in all) answers 401 with no key", not wrong, wrong)
    check(S, "the GET routes open with no key are only the short list",
          open_endpoints <= expected_public_get,
          {"open_routes": open_ones, "unexpected": sorted(open_endpoints - expected_public_get)})
    check(S, "an uploaded document needs the key",
          "knowledge_serve_source" not in public
          and client.get("/knowledge/library/x/source", headers={"Host": "localhost"}).status_code == 401)


# -- 4. the vault locks itself ----------------------------------------------

def vault_autolock() -> None:
    S = "Vault auto-lock"
    from puffpai.security import vault as vault_mod
    from puffpai import api_server
    client = api_server.app.test_client()
    key = {"Authorization": "Bearer " + os.environ["PUFFPAI_API_KEY"],
           "Host": "localhost"}
    pin = "Kq7-" + secrets.token_hex(4) + "-Lv"
    made = client.post("/setup/vault", json={"password": pin}, headers=key)
    check(S, "a vault was made for the test", made.status_code == 200, made.status_code)
    opened = client.post("/vault/unlock", json={"password": pin}, headers=key)
    v = api_server._get_vault()
    check(S, "it opened", opened.status_code == 200 and v.is_unlocked, opened.status_code)
    limit = float(getattr(v, "idle_lock_seconds", vault_mod.IDLE_LOCK_SECONDS))
    start = time.time()
    while time.time() - start < limit + 4:
        client.get("/system/status", headers=key)
        time.sleep(1)
    check(S, f"polled /system/status for {limit + 4:.0f} s (idle limit {limit:.0f} s): "
             "it locked itself and its key is gone",
          not v.is_unlocked and getattr(v, "_data_key", None) is None,
          {"is_unlocked": v.is_unlocked})


# -- 5. a running backend --------------------------------------------------------

def live(base_url: str) -> None:
    S = "Live backend"
    parts = urlsplit(base_url)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    if port in (8100, 3100):
        check(S, "refused to touch port 8100/3100", False)
        return
    key = os.environ.get("PUFFPAI_ACCEPT_API_KEY", "")

    def ask(method, path, headers):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read(4096)
        conn.close()
        return resp.status, body

    for name in ("rebind.attacker.example", f"rebind.attacker.example:{port}",
                 f"127.0.0.1.nip.io:{port}"):
        code, _ = ask("GET", "/health", {"Host": name})
        check(S, f"Host {name!r} gets 421 over a real socket", code == 421, code)
        if key:
            code, _ = ask("GET", "/system/status",
                          {"Host": name, "Authorization": f"Bearer {key}"})
            check(S, f"Host {name!r} gets 421 even with the key", code == 421, code)
    code, _ = ask("GET", "/health", {"Host": f"localhost:{port}"})
    check(S, "its own name is answered", code == 200, code)
    code, body = ask("GET", "/knowledge/library/acceptance-probe/source",
                     {"Host": f"localhost:{port}"})
    check(S, "an uploaded document with no key: 401", code == 401, code)
    if key:
        code, _ = ask("GET", "/knowledge/library/acceptance-probe/source",
                      {"Host": f"localhost:{port}", "Authorization": f"Bearer {key}"})
        check(S, "with the key the route answers (404: no such document)", code == 404, code)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url", default="")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    state = fresh_state(out)
    print(f"state folder: {state}")
    # Before anything makes the vault: a short stand-in for its idle time, and
    # a quick key stretch (the test vault holds nothing).
    from puffpai.security import vault as vault_mod
    vault_mod.IDLE_LOCK_SECONDS = 6.0
    vault_mod._PBKDF2_ITERATIONS = 1000
    vault_mod._RECOVERY_PBKDF2_ITERATIONS = 1000
    if args.base_url:
        live(args.base_url)
    for part in (host_and_keyless, vault_autolock, bridge_up):
        try:
            part()
        except Exception as exc:  # noqa: BLE001 - a part that breaks is a FAIL
            check(part.__name__, "ran to its end", False, f"{type(exc).__name__}: {exc}")
    failed = [r for r in RESULTS if not r["pass"]]
    summary = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "checks": len(RESULTS),
               "failed": len(failed), "results": RESULTS}
    with open(os.path.join(out, "dev_sec_acceptance.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    lines = [f"{'PASS' if r['pass'] else 'FAIL'}  {r['section']}: {r['check']}"
             for r in RESULTS]
    lines.append(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    with open(os.path.join(out, "dev_sec_acceptance.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(lines[-1])
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
