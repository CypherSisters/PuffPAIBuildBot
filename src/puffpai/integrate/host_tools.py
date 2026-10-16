"""The brain's way into apps that run on this computer, through THEIR interface.

An app added by :mod:`puffpai.integrate.run_here` keeps its own interface;
nothing here rebuilds it:

* an MCP tool server: registered as a tool server (``mcp_client``) when it is
  set up, so its own tools reach the brain the usual way;
* an OpenAPI description (a file it ships, or ``/openapi.json`` it serves):
  ``use_local_app`` calls one of the operations it lists, on 127.0.0.1 only;
* command line tools: their full commands are listed for ``run_command``
  (which asks her yes for every command, as always);
* ANY app with a page (the universal baseline): ``read_app`` opens its page
  on 127.0.0.1 in a headless browser (no window, its own empty profile), lets
  its scripts run, and gives the brain the page's visible text (title,
  headings, lists, tables, numbers) and its links, so the brain can answer
  "what is on it" for any web app with no app-specific code;
* anything else: the brain can say where it opens, and start it.

``use_local_app`` asks her yes (the chat's own Yes card) for every call that
is not a plain read (anything but GET); ``start_local_app`` always asks (it
runs a program). A provider makes the tools only while such an app exists.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

PROVIDER = "host_apps"
MAX_OPS = 12
#: Where a running Toolsmith app may describe its own API (OpenAPI/Swagger).
OPENAPI_PATHS = ("/openapi.json", "/swagger.json", "/api/openapi.json", "/docs/openapi.json")
#: app id -> ((port, started_at), ops): read once per start of the app.
_OPS_CACHE: dict[str, tuple[tuple, list]] = {}

USE_SCHEMA = {
    "type": "object",
    "properties": {
        "app": {"type": "string", "description": "The app's name, as listed."},
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {"type": "string", "description": "One of the paths it lists, with "
                                                  "its {parts} filled in."},
        "body": {"type": "object", "description": "What to send (not for GET)."},
    },
    "required": ["app", "method", "path"],
    "additionalProperties": False,
}
START_SCHEMA = {
    "type": "object",
    "properties": {"app": {"type": "string", "description": "The app's name, as listed."}},
    "required": ["app"],
    "additionalProperties": False,
}


READ_SCHEMA = {
    "type": "object",
    "properties": {
        "app": {"type": "string", "description": "The app's name, as listed."},
        "path": {"type": "string", "description": "Another page of it, like /news "
                                                  "(default: its main page)."},
    },
    "required": ["app"],
    "additionalProperties": False,
}
#: How much of a page's text the brain is given (it must fit the prompt).
READ_CHARS = 7000
READ_LINKS = 25


class _Text:
    """The visible text of a rendered page: title, headings, blocks, links."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
    BLOCK = frozenset({"p", "div", "li", "tr", "section", "article", "header", "footer",
                       "h1", "h2", "h3", "h4", "h5", "h6", "br", "td", "th", "dt", "dd",
                       "main", "nav", "aside", "ul", "ol", "table", "button", "a"})

    def __init__(self) -> None:
        from html.parser import HTMLParser
        outer = self
        self.title, self.lines, self.links, self._buf = "", [], [], []
        self._skip, self._in_title, self._href = 0, False, ""
        self.description = ""

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):  # noqa: ANN001
                outer._start(tag, dict(attrs))

            def handle_endtag(self, tag):  # noqa: ANN001
                outer._end(tag)

            def handle_data(self, data):  # noqa: ANN001
                outer._data(data)
        self.parser = P(convert_charrefs=True)

    def _flush(self, prefix: str = "") -> None:
        text = " ".join(" ".join(self._buf).split())
        self._buf = []
        if text and (not self.lines or self.lines[-1] != prefix + text):
            self.lines.append(prefix + text)

    def _start(self, tag: str, attrs: dict) -> None:
        if tag == "title":
            self._in_title = True
        if tag == "meta" and not self.description and str(
                attrs.get("name") or attrs.get("property") or "").lower() in (
                "description", "og:description"):
            self.description = " ".join(str(attrs.get("content") or "").split())[:300]
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self._flush()
        if tag == "a":
            self._href = str(attrs.get("href") or "")

    def _end(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            if tag == "a" and self._buf and self._href and len(self.links) < READ_LINKS:
                self.links.append(f"{' '.join(' '.join(self._buf).split())[:60]} -> "
                                  f"{self._href[:120]}")
            self._flush("# " if tag in ("h1", "h2", "h3") else "")

    def _data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)


def page_text(html: str) -> dict:
    """``{"title", "text", "links"}`` of a rendered page (visible text only)."""
    t = _Text()
    try:
        t.parser.feed(str(html or ""))
        t.parser.close()
    except Exception:  # noqa: BLE001 - a broken page still gives what was read
        pass
    t._flush()  # noqa: SLF001
    text = "\n".join(t.lines)
    return {"title": " ".join(t.title.split())[:200], "text": text[:READ_CHARS],
            "cut": len(text) > READ_CHARS, "links": t.links, "description": t.description}


#: How much of a page the plain read takes.
PLAIN_BYTES = 2 * 1024 * 1024


def plain_read(url: str) -> Optional[dict]:
    """The page at *url* (127.0.0.1 only) without a browser: one plain GET,
    then its title, description and visible text. None unless it answered 200."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # lan-egress: 127.0.0.1 only (checked by the caller), no proxy
    try:
        with opener.open(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            raw = resp.read(PLAIN_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except (OSError, ValueError):
        return None
    got = page_text(raw.decode(charset, "replace"))
    if got.get("description"):
        got["text"] = (got["description"] + "\n" + got["text"]).strip()[:READ_CHARS]
    return got


def _answers(url: str) -> bool:
    """Does anything answer HTTP at *url* (127.0.0.1 only; any status counts)?"""
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # lan-egress: 127.0.0.1 only (checked by the caller), no proxy
    try:
        with opener.open(url, timeout=10):
            return True
    except urllib.error.HTTPError:
        return True              # it answered (a login redirect, a 401, a 404 page)
    except (OSError, ValueError):
        return False


def read_page(url: str) -> dict:
    """Open *url* (127.0.0.1 only) in a headless browser and read it (a plain
    read of its HTML when no browser can be driven)."""
    import urllib.parse
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return {"ok": False, "said": "Only apps on this computer can be read."}
    if not _answers(url):
        # (A browser would show its own "can't reach this page" screen.)
        return {"ok": False, "said": "Nothing answers at that address yet."}
    from puffpai.integrate import page_reader
    seen = page_reader.read(url)
    if seen is not None:
        # What a person sees once the page filled itself in (icon-only lines left out).
        lines = [ln.strip() for ln in str(seen.get("text") or "").splitlines()
                 if len(ln.strip()) > 2]
        text = "\n".join(lines)
        got = {"title": str(seen.get("title") or "")[:200], "text": text[:READ_CHARS],
               "cut": len(text) > READ_CHARS,
               "links": [f"{t} -> {h}" for t, h in (seen.get("links") or [])][:READ_LINKS]}
    else:
        # No browser could be driven here: read the page as plain HTML.
        got = plain_read(url)
        if got is None:
            return {"ok": False, "said": "Its page could not be opened (no browser here, "
                                         "and a plain read got no page)."}
    if not got["text"].strip():
        return {"ok": False, "said": "Its page showed no text yet; it may still be loading.",
                "title": got["title"]}
    return {"ok": True, "url": url, **got}


def _read(args: dict, ctx: Any = None) -> Any:
    import urllib.parse
    app = _find(_apps(), args.get("app"))
    if app is None:
        return {"ok": False, "said": "There is no app by that name on this computer."}
    url = str(app["status"].get("url") or "")
    if app["status"].get("state") != "running" or not url:
        # A stopped app is started first (its yes is asked by _read_question).
        got = _start({"app": app["id"]}, ctx)
        url = str(got.get("open") or "")
        if not got.get("ok") or not url:
            return {"ok": False, "said": got.get("said") or
                    f"{app['name']} is not running and did not start."}
    path = str(args.get("path") or "").strip()
    if path:
        if not path.startswith("/") or path.startswith("//"):
            return {"ok": False, "said": "Give a path on the app, like /news."}
        url = urllib.parse.urljoin(url, path)
    return read_page(url)


def toolsmith_ops(app: dict) -> list[dict]:
    """The operations a running Toolsmith app lists in its own OpenAPI or
    Swagger description (read on 127.0.0.1, once per start), or []."""
    from puffpai.integrate import run_here
    if not app.get("running") or not app.get("port"):
        return []
    key = (app.get("port"), app.get("started_at"))
    hit = _OPS_CACHE.get(app["id"])
    if hit and hit[0] == key:
        return hit[1]
    ops: list[dict] = []
    for path in OPENAPI_PATHS:
        try:
            code, raw = run_here._local_request(int(app["port"]), "GET", path,  # noqa: SLF001
                                                timeout=5)
            doc = json.loads(raw.decode("utf-8", "replace")) if code == 200 else None
            ops = run_here.ops_from_doc(doc)
        except Exception:  # noqa: BLE001 - not JSON, not there: the next place
            ops = []
        if ops:
            break
    _OPS_CACHE[app["id"]] = (key, ops)
    return ops


def _apps() -> list[dict]:
    """``[{"id", "name", "status", "ops", "cli"}]`` for every app set up here,
    running or not."""
    from puffpai.integrate import records, run_here
    out = []
    try:
        # The ready-made apps Toolsmith got working (build.toolsmith_run).
        from puffpai.build import toolsmith_run
        for a in toolsmith_run.running_apps():
            try:
                ops = toolsmith_ops(a)
            except Exception:  # noqa: BLE001 - it is still listed
                ops = []
            out.append({"id": a["id"], "name": a["name"][:60], "ops": ops, "cli": {},
                        "toolsmith": True, "port": a.get("port"),
                        "status": {"state": "running" if a["running"] else "stopped",
                                   "url": a["url"] if a["running"] else "", "can_run": True,
                                   "how_words": "a ready-made app Toolsmith runs"}})
    except Exception as exc:  # noqa: BLE001 - the others are still listed
        logger.debug("host tools: toolsmith apps (%s)", type(exc).__name__)
    for iid in run_here.all_ids():
        rec = records.get(iid)
        if not rec or rec.get("status") != "ready":
            continue
        if (run_here.read_state(iid).get("plan") or {}).get("how") == "assembled":
            continue    # an assembled app's tools come from puffpai.assemble.api only
        try:
            now = run_here.status(iid)
            ops = run_here.operations(iid) if now["state"] == "running" else []
            cli = run_here.cli_commands(iid)
        except Exception as exc:  # noqa: BLE001 - one app never hides the others
            logger.debug("host tools: %s", type(exc).__name__)
            continue
        out.append({"id": iid, "name": str(rec.get("name") or iid)[:60], "status": now,
                    "ops": ops, "cli": cli})
    return out


def _find(apps: list[dict], name: Any) -> Optional[dict]:
    import re

    def key(text: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    want = key(name)
    # "worldmonitor", "World Monitor" and "world-monitor" are one name.
    named = [a for a in apps if want and (key(a["name"]) == want or key(a["id"]) == want
                                          or key(a["id"]).startswith(want))]
    # Two apps may share a name (an older copy): the running one first.
    named.sort(key=lambda a: (a.get("status") or {}).get("state") != "running")
    return named[0] if named else None


def describe(apps: list[dict]) -> str:
    """The apps on this computer and how the brain can use each one."""
    lines = []
    for a in apps:
        st = a["status"]
        where = f"open at {st['url']}" if st.get("url") else (
            "not running" if st.get("can_run") else "nothing to open")
        lines.append(f"- {a['name']}: {st.get('how_words') or 'an app'}; {where}"
                     + ("; read_app shows what is on its page" if st.get("url") else "") + ".")
        for op in a["ops"][:MAX_OPS]:
            lines.append(f"    {op['method']} {op['path']}"
                         + (f"  ({op['summary']})" if op.get("summary") else ""))
        for name, cmd in list(a["cli"].items())[:4]:
            lines.append(f"    command {name}: {cmd} (use run_command)")
    return "\n".join(lines)


def _use(args: dict, ctx: Any = None) -> Any:
    from puffpai.integrate import run_here
    app = _find(_apps(), args.get("app"))
    if app is None:
        return {"ok": False, "said": "There is no app by that name on this computer."}
    method, path = str(args.get("method") or "GET").upper(), str(args.get("path") or "")
    if not app.get("toolsmith"):
        return run_here.call(app["id"], method, path, args.get("body"))
    if app["status"].get("state") != "running":
        return {"ok": False, "line": "It is not running. Start it first."}
    if not any(op["method"] == method and run_here._path_matches(op["path"], path)  # noqa: SLF001
               for op in app["ops"]):
        return {"ok": False, "line": "That is not one of the calls it offers."}
    try:
        code, raw = run_here._local_request(int(app["port"]), method, path,  # noqa: SLF001
                                            args.get("body"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "line": f"It did not answer ({type(exc).__name__})."}
    text = raw.decode("utf-8", "replace")
    try:
        from puffpai import brain_commands
        text = brain_commands.hide_secrets(text)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": 200 <= code < 300, "status": code, "answer": text[:8000]}


def _use_question(args: dict, ctx: Any = None) -> Optional[dict]:
    method = str((args or {}).get("method") or "GET").upper()
    if method == "GET":
        return None
    body = (args or {}).get("body")
    shown = json.dumps(body, ensure_ascii=False)[:600] if body is not None else ""
    return {"title": f"May I send this to {str(args.get('app') or 'the app')[:60]}?",
            "detail": f"{method} {str(args.get('path') or '')[:200]}"
                      + (f" with {shown}" if shown else "")
                      + ". It runs on this computer."}


def _start(args: dict, ctx: Any = None) -> Any:
    from puffpai.integrate import run_here
    app = _find(_apps(), args.get("app"))
    if app is None:
        return {"ok": False, "said": "There is no app by that name on this computer."}
    if app.get("toolsmith"):
        from puffpai.build import toolsmith_run
        got = toolsmith_run.restart(app["id"], wait=60.0)
        return {"ok": bool(got.get("ok")), "said": got.get("said"), "open": got.get("url") or ""}
    got = run_here.start(app["id"], by="chat", wait=30.0)
    now = got.get("status") or {}
    return {"ok": bool(got.get("ok")), "said": got.get("line"), "open": now.get("url") or ""}


def _start_question(args: dict, ctx: Any = None) -> Optional[dict]:
    return {"title": f"May I start {str((args or {}).get('app') or 'the app')[:60]}?",
            "detail": "It runs on this computer with your permissions, bound to 127.0.0.1."}


def _read_question(args: dict, ctx: Any = None) -> Optional[dict]:
    """Reading asks nothing; reading a stopped app starts it, and that start
    asks the same yes as start_local_app."""
    try:
        app = _find(_apps(), (args or {}).get("app"))
    except Exception:  # noqa: BLE001 - unsure if it runs: ask, never skip the yes
        return _start_question(args, ctx)
    if app is None or app["status"].get("state") == "running":
        return None
    return _start_question({"app": app["name"]}, ctx)


def provide(ctx: Any = None) -> list:
    from puffpai import brain_tools
    try:
        apps = _apps()
    except Exception as exc:  # noqa: BLE001
        logger.debug("host tools provider: %s", type(exc).__name__)
        return []
    if not apps:
        return []
    # Each app's status and what it does are in the system prompt's
    # "Installed" block (puffpai.installed); the tools only name them.
    names = ", ".join(dict.fromkeys(a["name"] for a in apps))
    tools = [brain_tools.make(
        "read_app",
        "See what is on the page of an app the owner runs on this computer (its "
        "current headlines, lists, numbers, tables and links), to answer questions "
        "about it or summarize it. Always use this when asked what an app shows. A "
        "stopped app is started first. Apps: " + names, READ_SCHEMA, _read,
        needs_yes=_read_question, source=PROVIDER,
        progress=lambda a: f"Looking at {str((a or {}).get('app') or 'the app')[:60]}"),
        brain_tools.make(
        "start_local_app",
        "Start an app the owner added that runs on this computer. Apps: " + names,
        START_SCHEMA, _start, needs_yes=_start_question, source=PROVIDER)]
    with_ops = [a for a in apps if a["ops"]]
    if with_ops:
        tools.append(brain_tools.make(
            "use_local_app",
            "Use an app on this computer through the calls it offers itself (only "
            "the ones listed). Calls that change something ask the owner first.\n"
            + describe(with_ops), USE_SCHEMA, _use, needs_yes=_use_question,
            source=PROVIDER))
    return tools


def register_all() -> None:
    from puffpai import brain_tools
    brain_tools.register_provider(PROVIDER, provide)


__all__ = ["OPENAPI_PATHS", "describe", "page_text", "provide", "read_page", "register_all",
           "toolsmith_ops"]
