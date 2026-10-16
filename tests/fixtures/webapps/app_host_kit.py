"""Test helpers for the app host (loopback only, a free port per run for lane L8's tests)."""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))


def _test_port() -> int:
    """A free loopback port for this test run (``PUFFPAI_L8_TEST_PORT`` wins).

    Several lanes run the merged suite on this machine at once, and the host
    asks for its port exclusively, so the pinned 4381 collided between runs
    (even "4381 when free" lost the race to a run that starts and stops its
    host every test).
    """
    import socket
    given = os.environ.get("PUFFPAI_L8_TEST_PORT", "").strip()
    if given.isdigit():
        return int(given)
    for want in (0,):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", want))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return 4381


PORT = _test_port()


def load_fake_driver():
    spec = importlib.util.spec_from_file_location("r3_webapps_fake_driver",
                                                  os.path.join(HERE, "fake_driver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seal_key(monkeypatch, tmp_path) -> str:
    """Give the test its own recipe seal key file (outside the apps folder)."""
    keydir = os.path.join(str(tmp_path), "seal-key")
    os.makedirs(keydir, exist_ok=True)
    path = os.path.join(keydir, "recipe_seal.key")
    monkeypatch.setenv("PUFFPAI_WEBAPPS_SEAL_KEY_FILE", path)
    return path


def place_app(app_id: str, fixture: str = "rows_delete", *, status: str = "installed",
              extra: dict | None = None) -> str:
    """Put a fixture app's files and an installed record in the pinned roots."""
    from puffpai import webapps
    from puffpai.webapps import host
    web = webapps.web_dir(app_id)
    shutil.rmtree(os.path.dirname(web), ignore_errors=True)
    shutil.copytree(os.path.join(HERE, fixture), web)
    for rel, body in (extra or {}).items():
        p = os.path.join(web, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    os.makedirs(webapps.app_state_dir(app_id), exist_ok=True)
    with open(os.path.join(webapps.app_state_dir(app_id), "web.json"), "w", encoding="utf-8") as fh:
        json.dump({"app_id": app_id, "status": status, "name": "Shelf Keeper"}, fh)
    host.refresh()
    return web


def request(method: str, path: str, *, host_header: str | None, headers: dict | None = None,
            body: bytes | None = None, raw_path: bool = False):
    """(status, headers dict lowercased, body bytes). Sends exactly the headers given."""
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    try:
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host_header is not None:
            c.putheader("Host", host_header)
        for k, v in (headers or {}).items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    c.putheader(k, item)
            else:
                c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        data = r.read()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data, r.getheaders()
    finally:
        c.close()


def frame_get(app_id: str, path: str = "/"):
    """Load a page the way the dashboard frame does, and return its nonce."""
    status, hdrs, body, _ = request("GET", path, host_header=f"{app_id}.localhost:{PORT}",
                                    headers={"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-site"})
    text = body.decode("utf-8", "replace")
    nonce = None
    marker = "window.__PUFFPAI__="
    if marker in text:
        conf = text.split(marker, 1)[1].split(";</script>", 1)[0]
        nonce = json.loads(conf)["nonce"]
    return status, hdrs, text, nonce
