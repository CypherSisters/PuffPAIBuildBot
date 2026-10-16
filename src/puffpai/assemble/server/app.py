"""The assembled app's own small server (one process per app, standard library only).

Started by :mod:`puffpai.integrate.run_here` (plan type ``assembled``) as
``python -I -B app.py --port N --app-id ID --folder DIR --gateway-file F
--scaffold DIR``, bound to 127.0.0.1 inside a Job Object, watched like every
app that runs on this computer.

It serves:

* ``/`` and the prebuilt views from the scaffold folder (files only, never a
  path outside it);
* ``/spec.json``: the app's ``assembled_app/v1`` spec, with ``media_proxy``
  set to ``/media`` (the page sends outside pictures and streams there);
* ``/settings.json`` (``{"values"}``; a key is only ``{"set": bool}``) and
  ``POST /settings`` ``{key, value}``: her settings, kept by BuildBot;
* ``/openapi.json``: one ``GET /api/<action>`` per spec action, so the brain
  sees the actions as tools (``integrate.host_tools``);
* ``/api/<action>`` (GET with query values, or POST with JSON): asks BuildBot
  (``POST /assemble/<id>/action`` on the gateway, 127.0.0.1) to run it;
* ``/media?url=<address>`` (or ``u=``): pictures and stream pieces (HLS playlists and segments, audio,
  video), fetched by BuildBot through the Bridge from the sites the spec
  names, with Range answered here.

It makes NO outside call itself: everything outside goes through BuildBot,
which uses the Bridge under each site's switch. Its token for the gateway is
read from its own folder for every request (never from its command line or
environment). It answers only requests addressed to 127.0.0.1 or localhost
on its own port (a page elsewhere that points its own name here gets 421).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
import argparse
import http.server
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
          ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
          ".txt": "text/plain; charset=utf-8", ".map": "application/json"}
_PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges",
                 "cache-control")
_MAX_BODY = 64 * 1024

FALLBACK_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head><body><h1>{title}</h1>
<p>Its views are not here yet. Its actions:</p><ul>{items}</ul></body></html>"""


def _schema_type(kind):
    return {"number": "number", "integer": "integer", "boolean": "boolean"}.get(
        str(kind or "string"), "string")


def openapi_for(spec):
    """OpenAPI 3 for the spec's actions: ``GET /api/<action>`` with query values."""
    paths = {}
    for act in (spec.get("actions") or [])[:40] if isinstance(spec, dict) else []:
        if not isinstance(act, dict) or not _ACTION_RE.match(str(act.get("id") or "")):
            continue
        params = []
        inputs = act.get("input") if isinstance(act.get("input"), dict) else {}
        for name, kind in list(inputs.items())[:12]:
            if re.match(r"^[a-z][a-z0-9_]{0,39}\Z", str(name)):
                params.append({"name": name, "in": "query", "required": False,
                               "schema": {"type": _schema_type(kind)}})
        words = str(act.get("label") or act.get("summary") or act["id"].replace("_", " "))
        paths[f"/api/{act['id']}"] = {"get": {
            "operationId": act["id"], "summary": " ".join(words.split())[:120],
            "parameters": params,
            "responses": {"200": {"description": "rows"}}}}
    return {"openapi": "3.0.3",
            "info": {"title": str((spec or {}).get("title") or "Assembled app")[:80],
                     "version": "1"},
            "paths": paths}


class Server:
    def __init__(self, args):
        self.port = int(args.port)
        self.app_id = args.app_id
        self.folder = os.path.realpath(args.folder)
        self.gateway_file = args.gateway_file
        self.scaffold = os.path.realpath(args.scaffold) if args.scaffold else ""
        self.start_dashboard_port = int(getattr(args, "dashboard_port", 0) or 0)

    @property
    def frame_ancestors(self):
        """Only the dashboard (its thing page's "Use it here") may show this
        app in a frame; no other page, not even the app's own. Read on every
        answer from the gateway file BuildBot keeps current, so a dashboard
        that moved to another port is not blocked until a restart."""
        port = self.start_dashboard_port
        try:
            with open(self.gateway_file, "r", encoding="utf-8") as fh:
                port = int(json.load(fh).get("dashboard_port") or port)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return (f"http://127.0.0.1:{port} http://localhost:{port}"
                if 0 < port < 65536 else "'none'")

    def spec(self):
        with open(os.path.join(self.folder, "spec.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}

    def token(self):
        with open(os.path.join(self.folder, "server", "token"), "r", encoding="ascii") as fh:
            return fh.read().strip()

    def gateway(self):
        with open(self.gateway_file, "r", encoding="utf-8") as fh:
            port = int(json.load(fh)["port"])
        return f"http://127.0.0.1:{port}/assemble/{urllib.parse.quote(self.app_id)}"

    def ask(self, what, method="POST", body=None, query="", headers=None):
        """One request to BuildBot's gateway on 127.0.0.1: (status, headers, bytes)."""
        url = self.gateway() + "/" + what + (("?" + query) if query else "")
        data = None if body is None else json.dumps(body).encode("utf-8")
        send = {"X-Puff-Token": self.token()}
        if data is not None:
            send["Content-Type"] = "application/json"
        send.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=send)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # lan-egress: BuildBot's gateway on 127.0.0.1 only
        try:
            with opener.open(req, timeout=120) as resp:  # lan-egress: BuildBot's gateway on 127.0.0.1 only
                return resp.status, dict(resp.headers.items()), resp.read(60 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()) if exc.headers else {}, \
                exc.read(256 * 1024) if exc.fp else b""


def make_handler(server):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "BuildBotAssembled/1"
        sys_version = ""

        def log_message(self, *_a):
            pass

        def _host_ok(self):
            values = self.headers.get_all("Host") or []
            if len(values) != 1:
                return False
            return values[0].strip().lower() in (f"127.0.0.1:{server.port}",
                                                 f"localhost:{server.port}")

        def _own_page(self, with_body):
            """Not another website's page in her browser: a browser says where a
            request came from (Origin, Sec-Fetch-Site), and only this app's own
            page may change a setting or run an action. A body must be JSON, so
            another site cannot send one without asking first (and this server
            never says yes to that)."""
            own = (f"http://127.0.0.1:{server.port}", f"http://localhost:{server.port}")
            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin and origin not in own:
                return False
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if site and site not in ("same-origin", "none"):
                return False
            if with_body:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return False
            return True

        def _send(self, status, body, ctype="application/json", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            frames = " https://www.youtube-nocookie.com" if self._youtube() else ""
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; "
                             "media-src 'self' blob:; worker-src 'self' blob:; "
                             "style-src 'self' 'unsafe-inline'; "
                             f"connect-src 'self'; frame-src 'self'{frames}; "
                             "object-src 'none'; base-uri 'none'; "
                             f"frame-ancestors {server.frame_ancestors}")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, obj):
            if status >= 400 and isinstance(obj, dict) and "line" in obj:
                obj.setdefault("error", obj["line"])     # the page reads "error"
            self._send(status, json.dumps(obj).encode("utf-8"))

        def _youtube(self):
            try:
                views = server.spec().get("views") or []
            except (OSError, ValueError):
                return False
            return any(isinstance(v, dict) and v.get("type") == "player"
                       and v.get("mode") == "youtube" for v in views)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._json(421, {"ok": False, "line": "Wrong address."})
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            try:
                if path == "/spec.json":
                    spec = server.spec()
                    spec["media_proxy"] = "/media"
                    return self._json(200, spec)
                if path == "/settings.json":
                    status, _h, body = server.ask("settings", method="GET")
                    return self._send(status, body or b"{}")
                if path == "/openapi.json":
                    return self._json(200, openapi_for(server.spec()))
                if path.startswith("/api/"):
                    if not self._own_page(False):
                        return self._json(403, {"ok": False, "line": "Only this app's own "
                                                "page can do that."})
                    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
                    return self._action(path[5:], query)
                if path == "/media":
                    return self._media(parts.query)
                return self._static(path)
            except (OSError, ValueError, KeyError) as exc:
                return self._json(500, {"ok": False, "line": "The app could not answer "
                                        f"({type(exc).__name__})."})

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if size < 0 or size > _MAX_BODY:
                return self._json(413, {"ok": False, "line": "Too big."})
            # Read first: an answer sent before the body is read is dropped on Windows.
            raw = self.rfile.read(size) if size else b"{}"
            if not self._host_ok():
                return self._json(421, {"ok": False, "line": "Wrong address."})
            path = urllib.parse.urlsplit(self.path).path
            if path != "/settings" and not path.startswith("/api/"):
                return self._json(404, {"ok": False, "line": "Not here."})
            if not self._own_page(bool(size)):
                return self._json(403, {"ok": False, "line": "Only this app's own page can "
                                        "do that."})
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                return self._json(400, {"ok": False, "line": "That was not JSON."})
            if path == "/settings":
                if not isinstance(data, dict):
                    return self._json(400, {"error": "That was not a setting."})
                status, _h, body = server.ask("settings", body={
                    "key": data.get("key"), "value": data.get("value")})
                return self._send(status, body or b"{}")
            inp = data.get("input", data) if isinstance(data, dict) else {}
            try:
                return self._action(path[5:], inp if isinstance(inp, dict) else {})
            except (OSError, ValueError, KeyError) as exc:
                return self._json(500, {"ok": False, "line": "The app could not answer "
                                        f"({type(exc).__name__})."})

        def _action(self, name, inp):
            if not _ACTION_RE.match(name):
                return self._json(404, {"ok": False, "line": "No such action."})
            status, _h, body = server.ask("action", body={"action": name, "input": inp})
            return self._send(status, body or b"{}")

        def _media(self, query):
            q = dict(urllib.parse.parse_qsl(query))
            url = q.get("url") or q.get("u") or ""
            if not url.startswith("https://"):
                return self._json(400, {"ok": False, "line": "Not a stream address."})
            extra = {}
            if self.headers.get("Range"):
                extra["Range"] = self.headers["Range"][:100]
            status, headers, body = server.ask(
                "media", method="GET", query=urllib.parse.urlencode({"u": url}),
                headers=extra)
            low = {k.lower(): v for k, v in headers.items()}
            keep = {k: low[k] for k in _PASS_HEADERS if k in low and k != "content-length"
                    and k != "content-type"}
            return self._send(status, body, low.get("content-type",
                                                    "application/octet-stream"), keep)

        def _static(self, path):
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            if "\\" in rel or "\x00" in rel or ":" in rel \
                    or any(p in ("", ".", "..") for p in rel.split("/")):
                return self._json(404, {"ok": False, "line": "Not here."})
            if server.scaffold and os.path.isdir(server.scaffold):
                full = os.path.realpath(os.path.join(server.scaffold, *rel.split("/")))
                inside = full.startswith(server.scaffold + os.sep)
                ext = os.path.splitext(full)[1].lower()
                if inside and os.path.isfile(full) and ext in _TYPES:
                    with open(full, "rb") as fh:
                        return self._send(200, fh.read(8 * 1024 * 1024), _TYPES[ext])
            if rel == "index.html":
                spec = server.spec()
                items = "".join(
                    f"<li>/api/{a.get('id')}</li>" for a in spec.get("actions") or []
                    if isinstance(a, dict) and _ACTION_RE.match(str(a.get("id") or "")))
                title = re.sub(r"[<>&\"']", "", str(spec.get("title") or "Assembled app"))[:80]
                page = FALLBACK_PAGE.format(title=title, items=items)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return self._json(404, {"ok": False, "line": "Not here."})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(prog="assembled-server")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--gateway-file", required=True)
    parser.add_argument("--scaffold", default="")
    parser.add_argument("--dashboard-port", dest="dashboard_port", type=int, default=0)
    args = parser.parse_args(argv)
    mimetypes.init([])        # never the registry's types
    server = Server(args)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", server.port), make_handler(server))
    httpd.daemon_threads = True
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
