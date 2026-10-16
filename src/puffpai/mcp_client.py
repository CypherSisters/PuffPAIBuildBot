"""Tool servers (MCP): other programs' tools, used by the bot in chat.

An MCP server is a program that tells a model what tools it has, then runs
them when asked. Home Assistant ships one (``/api/mcp``), and thousands of
others exist. This module keeps one saved list of them, connects, asks each
for its tools, and hands those tools to the chat's tool loop through
:mod:`puffpai.brain_tools`, so "turn on the porch light" becomes a real call.

A server is either:

* a web address (Streamable HTTP, or SSE when the address ends in ``/sse``),
  with optional headers (for a password such as Home Assistant's long-lived
  token), or
* a command this computer starts (stdio), for example
  ``npx -y @modelcontextprotocol/server-everything``.

What is kept where
------------------
* ``<state>/mcp_servers.json``: each server's name, address or command, and
  the tools it said it has (so the tools are ready after a restart without
  connecting). Never a header value.
* Header values (passwords) go into the vault through the keys broker
  (:func:`puffpai.security.keys.put_tool_secret`) and are read back only when
  a tool is used.

The few safety rules kept (BUILD_BRIEF.md, "Safety kept")
--------------------------------------------------------
* Each server has its own switch on "What leaves your computer" (id
  ``mcp_<name>``), switched on when she adds it, one tap off.
* Every connection writes one line to the outside-traffic log
  (:mod:`puffpai.security.egress_ledger`), never what was sent.
* Secrets stay in the vault.

MCP Apps (SEP-1865, ``io.modelcontextprotocol/ui``)
----------------------------------------------------
A tool may say, in its ``_meta.ui.resourceUri`` (or the older flat
``_meta["ui/resourceUri"]``), that its answer has a screen: a ``ui://``
resource on the same server holding one HTML page. The tool list keeps each
tool's ``_meta`` (cut down, see :func:`_meta_of`), and :func:`call_tool_with_ui`
reads that resource in the SAME session as the call, from the same server,
for the thing page to show in a locked frame (no network, no same-origin;
``dashboard/app/dashboard/things/[id]/McpAppFrame.tsx``). A tool whose
``visibility`` leaves out ``"model"`` is not handed to the chat's model, and
a call from an app's screen is refused for a tool that leaves out ``"app"``.

Each use opens a fresh connection and closes it again. That is slower than
holding one open (Hermes keeps each server in a long-lived task), but it is
small and cannot leak a stuck session; a server that must remember things
between calls is the one case it does not suit.

Copied in shape from Hermes Agent ``tools/mcp_tool.py`` and
``tools/mcp_tool_schema.py`` (MIT, Copyright (c) 2025 Nous Research): the tool
naming ``mcp__<server>__<tool>`` clamped to 64 characters, the input-schema
repair, and the "SDK is optional" import. See THIRD_PARTY_NOTICES.md.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import threading
import warnings
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "puffpai_mcp_servers/v1"
FILE_NAME = "mcp_servers.json"

#: A server's name: what the switch, the log and the tool names carry.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
#: Seconds to connect and list tools. A command that downloads itself first
#: (``npx -y ...``) needs longer the first time.
CONNECT_TIMEOUT = 30.0
STDIO_CONNECT_TIMEOUT = 180.0
#: Seconds one tool may take.
CALL_TIMEOUT = 60.0
#: A tool's answer is cut to this many characters before the model sees it.
MAX_RESULT_CHARS = 6000
#: At most this many tools from one server join the loop.
MAX_TOOLS_PER_SERVER = 40
#: A tool's kept ``_meta``, as JSON, at most this long (the ui part is kept
#: whatever the rest weighs).
MAX_META_CHARS = 4000
#: An app screen's HTML at most this large.
MAX_UI_BYTES = 1024 * 1024
#: A tool answer handed to an app screen, as JSON, at most this long.
MAX_APP_RESULT_CHARS = 200_000
UI_SCHEME = "ui://"
#: SEP-1865's HTML type, and the older spelling some servers still send.
UI_MIME_TYPES = ("text/html;profile=mcp-app", "text/html+mcp", "text/html")

HOME_ASSISTANT = "home_assistant"
HOME_ASSISTANT_LABEL = "Home Assistant"

#: The outside-traffic log's words for an MCP line (egress_ledger.WHAT keys).
WHAT_TOOLS = "mcp_tools"
WHAT_CALL = "mcp_call"

_lock = threading.RLock()

# mcp 1.x: ``streamablehttp_client`` takes the headers directly. Its newer
# twin wants a ready-made web client instead, which is more to own for no gain
# at this version (pinned <2 in requirements.txt), so the notice is quietened.
warnings.filterwarnings("ignore", message="Use `streamable_http_client` instead",
                        category=DeprecationWarning)


class McpError(Exception):
    """A plain sentence for the owner, and a short code for the program."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ── the tool loop's registry (puffpai.brain_tools) ─────────────────────────

def _register(name: str, description: str, json_schema: dict,
              fn: Callable[..., Any], needs_yes: bool = False) -> None:
    from puffpai import brain_tools
    brain_tools.register(name, description, json_schema, fn, needs_yes=needs_yes,
                         source="mcp")


def _unregister(name: str) -> None:
    from puffpai import brain_tools
    brain_tools.unregister(name)


def _flex(fn: Callable[[dict], str]) -> Callable[..., str]:
    """A tool function that takes its arguments as ``fn(args)``,
    ``fn(args, ctx)`` or ``fn(**args)``: whichever way the loop calls."""
    def call(args: Any = None, ctx: Any = None, **kwargs: Any) -> str:
        merged = dict(args) if isinstance(args, dict) else {}
        merged.update(kwargs)
        return fn(merged)
    return call


# ── names (Hermes tools/mcp_tool_schema.py, MIT) ────────────────────────────

_TOOL_NAME_MAX = 64


def _part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``, cut to 64 characters with a steady suffix."""
    full = f"mcp__{_part(server)}__{_part(tool)}"
    if len(full) <= _TOOL_NAME_MAX:
        return full
    suffix = "_" + hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return full[:_TOOL_NAME_MAX - len(suffix)] + suffix


def slug(text: Any) -> str:
    """A usable server name from anything she typed ("My Printer" ->
    ``my_printer``). Empty when nothing usable is left."""
    out = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    if out and not out[0].isalpha():
        out = "t_" + out
    return out[:24].rstrip("_")


def switch_id(name: str) -> str:
    """The server's switch on "What leaves your computer"."""
    return "mcp_" + name


def _schema(raw: Any) -> dict:
    """An input schema every model accepts (Hermes _normalize_mcp_input_schema,
    cut down): an object with ``properties`` and a ``required`` list that only
    names properties it has."""
    schema = dict(raw) if isinstance(raw, dict) else {}
    if not schema.get("type") or "properties" in schema:
        schema.setdefault("type", "object")
    if schema.get("type") == "object":
        props = schema.get("properties")
        schema["properties"] = props if isinstance(props, dict) else {}
        req = schema.get("required")
        schema["required"] = [r for r in req if isinstance(r, str)
                              and r in schema["properties"]] \
            if isinstance(req, list) else []
    return schema


# ── the saved list ──────────────────────────────────────────────────────────

def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), FILE_NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> dict[str, dict]:
    from puffpai import fsutil
    data = fsutil.read_json(_path(), default=None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    found = data.get("servers")
    if not isinstance(found, dict):
        return {}
    return {k: dict(v) for k, v in found.items()
            if isinstance(k, str) and NAME_RE.match(k) and isinstance(v, dict)}


def _write(servers: dict[str, dict]) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_path(), {"schema": SCHEMA, "servers": servers})


def parse_target(url_or_command: Any) -> dict:
    """``{"kind": "http"|"sse", "url"}`` or ``{"kind": "stdio", "command",
    "args"}``. Raises :class:`McpError` with a plain sentence."""
    if isinstance(url_or_command, (list, tuple)):
        parts = [str(p) for p in url_or_command if str(p).strip()]
        if not parts:
            raise McpError("Type a web address or a command.", "empty")
        return {"kind": "stdio", "command": parts[0], "args": parts[1:]}
    text = str(url_or_command or "").strip()
    if not text:
        raise McpError("Type a web address or a command.", "empty")
    if re.match(r"^https?://", text, re.I):
        try:
            parts = urlsplit(text)
        except ValueError:
            parts = None
        if parts is None or not parts.hostname:
            raise McpError("That web address cannot be read.", "address")
        if parts.username or parts.password:
            raise McpError("Put a password in the password box, not in the "
                           "address.", "address")
        kind = "sse" if parts.path.rstrip("/").endswith("/sse") else "http"
        return {"kind": kind, "url": text}
    try:
        words = shlex.split(text, posix=(os.name != "nt"))
    except ValueError:
        raise McpError("That command cannot be read.", "command") from None
    words = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w
             for w in words]
    if not words:
        raise McpError("Type a web address or a command.", "empty")
    return {"kind": "stdio", "command": words[0], "args": words[1:]}


def _shown_target(rec: dict) -> str:
    if rec.get("kind") == "stdio":
        return " ".join([str(rec.get("command") or "")]
                        + [str(a) for a in rec.get("args") or []])
    return str(rec.get("url") or "")


def _host(rec: dict) -> str:
    if rec.get("kind") == "stdio":
        return ""
    try:
        return (urlsplit(str(rec.get("url") or "")).hostname or "").lower()
    except ValueError:
        return ""


# ── the switch and the log ──────────────────────────────────────────────────

def _switch_is_off(name: str) -> bool:
    try:
        from puffpai.security import outside_switches
        return outside_switches.is_off(switch_id(name))
    except Exception as exc:  # unreadable: off, never on
        logger.debug("tool server switch unreadable: %s", type(exc).__name__)
        return True


def _set_switch(name: str, on: bool, by: str) -> None:
    try:
        from puffpai.security import egress_doors, outside_switches
        outside_switches.set_switch(switch_id(name), on, by=by)
        egress_doors.reset_cache()
    except Exception as exc:
        logger.warning("tool server %s switch not recorded: %s", name,
                       type(exc).__name__)


def _clear_switch(name: str) -> None:
    try:
        from puffpai.security import outside_switches
        outside_switches.clear(switch_id(name))
    except Exception as exc:
        logger.debug("tool server switch not cleared: %s", type(exc).__name__)


def _log(rec: dict, what: str, outcome: str, code: str = "") -> None:
    from puffpai.security import egress_ledger
    egress_ledger.record(switch_id(rec["name"]), host=_host(rec),
                         outcome=outcome, method="POST", what=what, code=code)


# ── talking to a server (the mcp package) ───────────────────────────────────

def sdk_available() -> bool:
    import importlib.util
    try:
        if importlib.util.find_spec("mcp") is not None:
            return True
    except (ImportError, ValueError):
        return False
    # Installed by the build's setup row (deps.install_mcp_sdk) into its own
    # folder: put LAST on the import path, so nothing already here is
    # replaced. Only that folder, never the shared tools folder.
    try:
        import sys

        from puffpai import deps
        folder = deps.mcp_sdk_dir()
        if not os.path.isfile(os.path.join(folder, "mcp", "__init__.py")):
            return False
        if folder not in sys.path:
            # As a site folder (still LAST on the path): its .pth files run, so
            # pywin32, which mcp needs on Windows, finds pywintypes (a plain
            # path entry left "No module named 'pywintypes'").
            import site
            site.addsitedir(folder)
            importlib.invalidate_caches()
        return importlib.util.find_spec("mcp") is not None
    except Exception:  # noqa: BLE001 - not usable: not available
        return False


def _errlog_path() -> str:
    from puffpai.config import get_state_dir
    folder = os.path.join(get_state_dir(create=True), "logs")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "mcp_servers.log")


def _errlog():
    """Where a started tool server's own messages go (not the bot's log)."""
    return open(_errlog_path(), "a", encoding="utf-8")  # noqa: SIM115


def _errlog_size() -> int:
    try:
        return os.path.getsize(_errlog_path())
    except OSError:
        return 0


def _server_said(since: int) -> str:
    """The last thing a started tool server wrote before it stopped (its own
    words, so she sees why: "no vault path supplied ...")."""
    try:
        with open(_errlog_path(), encoding="utf-8", errors="replace") as fh:
            fh.seek(since)
            text = fh.read(20000)
    except OSError:
        return ""
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.lower().startswith("npm warn")]
    errors = [ln for ln in lines if "error" in ln.lower()]
    said = (errors or lines)[-1] if (errors or lines) else ""
    return said[:200]


async def _with_session(rec: dict, headers: Optional[dict],
                        work: Callable[[Any], Any]) -> Any:
    from mcp import ClientSession
    kind = rec.get("kind")
    if kind == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client
        params = StdioServerParameters(command=str(rec["command"]),
                                       args=[str(a) for a in rec.get("args") or []])
        errlog = _errlog()
        try:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await work(session)
        finally:
            errlog.close()
    if kind == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(rec["url"], headers=headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await work(session)
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(rec["url"], headers=headers or None) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await work(session)


def _leaf(exc: BaseException) -> BaseException:
    """The first real error inside an exception group (anyio wraps them)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _plain(exc: BaseException, rec: dict) -> McpError:
    exc = _leaf(exc)
    if isinstance(exc, McpError):
        return exc
    where = _host(rec) or "the tool server"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        # True either way: with no password kept for it, it wants a sign-in.
        said = ("did not accept the password" if rec.get("has_password")
                else "wants you to sign in with your own account, which BuildBot cannot do "
                     "for you yet")
        return McpError(f"{where} {said}.", "password")
    # The mcp package's own error carries the server's words; a 404 while
    # starting comes back as "Session terminated".
    said = str(getattr(getattr(exc, "error", None), "message", "") or "")
    if status == 404 or said == "Session terminated":
        return McpError(f"{where} has no tool server at that address.", "missing")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return McpError(f"{where} did not answer in time.", "timeout")
    if isinstance(exc, FileNotFoundError):
        return McpError("This computer could not find that command.", "command")
    if isinstance(exc, OSError) or "connect" in type(exc).__name__.lower():
        return McpError(f"Could not reach {where}.", "unreachable")
    if said:
        return McpError(f"{where} said: {said[:160]}", "failed")
    return McpError(f"{where} did not work: {type(exc).__name__}.", "failed")


def _run(rec: dict, headers: Optional[dict], work: Callable[[Any], Any],
         timeout: float) -> Any:
    """Run one session to the end on its own thread and event loop.

    The thread starts here, in this module, so the Bridge's connection check
    (:mod:`puffpai.security.bridge_guard`) finds this listed exit on the stack
    of every connection the session makes."""
    if not sdk_available():
        raise McpError("Tool servers need the mcp package. Install it with: "
                       "pip install mcp", "no_sdk")
    box: dict[str, Any] = {}
    since = _errlog_size() if rec.get("kind") == "stdio" else 0

    def _session_thread() -> None:
        try:
            box["value"] = asyncio.run(asyncio.wait_for(
                _with_session(rec, headers, work), timeout))
        except BaseException as exc:  # noqa: BLE001 - handed back below
            box["error"] = exc

    worker = threading.Thread(target=_session_thread, daemon=True,
                              name=f"puffpai-mcp-{rec.get('name', '')}")
    worker.start()
    worker.join(timeout + 10)
    if worker.is_alive():
        raise McpError(f"{_host(rec) or 'The tool server'} did not answer in "
                       f"time.", "timeout")
    if "error" in box:
        plain = _plain(box["error"], rec)
        said = _server_said(since) if rec.get("kind") == "stdio" else ""
        if said and plain.code not in ("command", "timeout"):
            raise McpError(f"the tool server stopped. It said: {said}", "stopped")
        raise plain
    return box.get("value")


def _headers_for(rec: dict) -> Optional[dict]:
    if not rec.get("has_password"):
        return None
    from puffpai.security import keys
    found = keys.tool_secret(rec["name"])
    if found is None:
        raise McpError(f"{rec.get('label') or rec['name']} is connected, but it needs its "
                       f"password and your vault is locked. Open your vault on the "
                       f"Vault page (/dashboard/vault), then ask again. Nothing needs "
                       f"to be set up again.", "locked")
    return found


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None


def _meta_of(obj: Any) -> dict:
    """An MCP object's ``_meta`` as plain JSON (the SDK names it ``meta``).
    Cut to :data:`MAX_META_CHARS`; the ``ui`` part is kept either way."""
    raw = getattr(obj, "meta", None)
    if raw is None:
        raw = getattr(obj, "_meta", None)
    if raw is None and isinstance(obj, dict):
        raw = obj.get("_meta") or obj.get("meta")
    meta = _json_safe(raw)
    if not isinstance(meta, dict):
        return {}
    if len(json.dumps(meta, ensure_ascii=False)) <= MAX_META_CHARS:
        return meta
    keep = {k: meta[k] for k in ("ui", "ui/resourceUri") if k in meta}
    return keep if len(json.dumps(keep, ensure_ascii=False)) <= MAX_META_CHARS else {}


def ui_resource_uri(meta: Any) -> str:
    """The ``ui://`` screen a tool (or a result) names in its ``_meta``, or ""."""
    meta = meta if isinstance(meta, dict) else {}
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    uri = ui.get("resourceUri") or meta.get("ui/resourceUri") or ""
    if isinstance(uri, str) and uri.startswith(UI_SCHEME) and len(uri) <= 512 \
            and not any(c in uri for c in "\r\n\x00 "):
        return uri
    return ""


def visibility(tool_row: Any) -> list[str]:
    """Who may call a tool (SEP-1865): ``model``, ``app`` or both (the default)."""
    meta = (tool_row or {}).get("meta") if isinstance(tool_row, dict) else None
    ui = meta.get("ui") if isinstance(meta, dict) and isinstance(meta.get("ui"), dict) else {}
    got = ui.get("visibility")
    if isinstance(got, list):
        seen = [v for v in got if v in ("model", "app")]
        return seen
    return ["model", "app"]


def model_tools(tools: Any) -> list[dict]:
    """The tools the chat's model can use: every tool whose visibility has
    ``model`` (app-only tools are for their screen, not for chat)."""
    return [t for t in tools or [] if isinstance(t, dict) and "model" in visibility(t)]


def _tool_rows(listed: Any) -> list[dict]:
    rows = []
    for tool in getattr(listed, "tools", None) or []:
        name = getattr(tool, "name", "")
        if not isinstance(name, str) or not name:
            continue
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            schema = getattr(tool, "input_schema", None)
        row = {"name": name,
               "description": str(getattr(tool, "description", "") or "")[:1000],
               "input_schema": _schema(schema)}
        meta = _meta_of(tool)
        if meta:
            row["meta"] = meta
        rows.append(row)
    return rows[:MAX_TOOLS_PER_SERVER]


def _fetch_tools(rec: dict, headers: Optional[dict]) -> list[dict]:
    async def work(session):
        return _tool_rows(await session.list_tools())
    timeout = STDIO_CONNECT_TIMEOUT if rec.get("kind") == "stdio" else CONNECT_TIMEOUT
    try:
        tools = _run(rec, headers, work, timeout)
    except McpError:
        _log(rec, WHAT_TOOLS, "failed")
        raise
    _log(rec, WHAT_TOOLS, "done")
    return tools


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(str(getattr(block, "text", "")))
        elif kind == "image":
            parts.append("[a picture]")
        elif kind == "audio":
            parts.append("[a sound]")
        elif kind == "resource":
            res = getattr(block, "resource", None)
            parts.append(str(getattr(res, "text", "") or "[a file]"))
        elif kind == "resource_link":
            parts.append(f"[a link: {getattr(block, 'uri', '')}]")
    structured = getattr(result, "structuredContent", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    text = "\n".join(p for p in parts if p).strip() or "(the tool sent back nothing)"
    if getattr(result, "isError", False):
        text = "The tool said there was a problem: " + text
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[cut short]"
    return text


def _result_for_app(result: Any) -> dict:
    """The tool answer as an app screen receives it (a CallToolResult): text
    blocks, structured content and whether it was a problem. Pictures, sound
    and files are left out. Cut to :data:`MAX_APP_RESULT_CHARS`."""
    content = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", "") == "text":
            content.append({"type": "text", "text": str(getattr(block, "text", ""))})
    out: dict[str, Any] = {"content": content,
                           "isError": bool(getattr(result, "isError", False))}
    structured = _json_safe(getattr(result, "structuredContent", None))
    if structured is not None:
        out["structuredContent"] = structured
    if len(json.dumps(out, ensure_ascii=False)) > MAX_APP_RESULT_CHARS:
        text = _result_text(result)
        out = {"content": [{"type": "text", "text": text}], "isError": out["isError"]}
    return out


def _embedded_ui(result: Any) -> Optional[dict]:
    """An HTML screen sent inside the answer itself (a ``resource`` block
    whose address is ``ui://``), as some servers do."""
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", "") != "resource":
            continue
        res = getattr(block, "resource", None)
        uri = str(getattr(res, "uri", "") or "")
        if uri.startswith(UI_SCHEME):
            return _ui_page(uri, [res])
    return None


def _ui_page(uri: str, contents: Any) -> Optional[dict]:
    """``{"uri", "html"}`` from a ``resources/read`` answer for *uri*, or
    None when it is not one HTML page of a sensible size."""
    import base64
    for item in contents or []:
        if str(getattr(item, "uri", "") or "") != uri:
            continue
        mime = str(getattr(item, "mimeType", "") or "").replace(" ", "").lower()
        if not any(mime == t or mime.startswith(t + ";") for t in UI_MIME_TYPES):
            return None
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            blob = getattr(item, "blob", None)
            if not isinstance(blob, str) or len(blob) > MAX_UI_BYTES * 4 // 3 + 8:
                return None
            try:
                text = base64.b64decode(blob, validate=True).decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                return None
        if not text.strip() or len(text.encode("utf-8", errors="replace")) > MAX_UI_BYTES:
            return None
        return {"uri": uri, "html": text, "meta": _meta_of(item)}
    return None


def _read_ui(session: Any, uri: str) -> Any:
    """Read *uri* from the session's server. None when it cannot be read."""
    async def go():
        try:
            try:
                from pydantic import AnyUrl
                address: Any = AnyUrl(uri)
            except Exception:  # noqa: BLE001 - an SDK that takes plain text
                address = uri
            got = await session.read_resource(address)
        except Exception as exc:  # noqa: BLE001 - the answer still shows
            logger.info("mcp: app screen not read (%s)", type(exc).__name__)
            return None
        return _ui_page(uri, getattr(got, "contents", None))
    return go()


# ── the public calls ────────────────────────────────────────────────────────

def _public(rec: dict) -> dict:
    return {
        "name": rec["name"],
        "label": rec.get("label") or rec["name"],
        "kind": rec.get("kind"),
        "address": _shown_target(rec),
        "has_password": bool(rec.get("has_password")),
        "tools": [t["name"] for t in rec.get("tools") or []],
        "tool_count": len(rec.get("tools") or []),
        "switched_on": not _switch_is_off(rec["name"]),
        "added_at": rec.get("added_at", ""),
        "source": rec.get("source", ""),
    }


def servers() -> list[dict]:
    """Every saved tool server, in plain fields. Never a password."""
    with _lock:
        return [_public(r) for _, r in sorted(_read().items())]


def server(name: str) -> Optional[dict]:
    rec = _read().get(str(name or ""))
    return _public(rec) if rec else None


def list_tools(name: str) -> list[dict]:
    """The tools *name* said it has, as last seen."""
    rec = _read().get(str(name or ""))
    return [dict(t) for t in (rec or {}).get("tools") or []]


#: Query names in a server's address that look like a key (never shown).
_KEYISH_QUERY = re.compile(r"(?i)(?:^|&)[^=&]*(?:key|token|secret|password|auth)[^=&]*=")


def setup_row(name: str, url_or_command: Any, *, label: Optional[str] = None) -> dict:
    """The ONE setup row for a tool server the build wants to connect:
    its host for a web address, the exact
    command for one this computer starts. Opens nothing and saves nothing;
    :func:`add_server` runs only after the build's one Yes.

    ``{"kind": "mcp_host"|"mcp_command", "app", "host", "command", "words",
    "target", "address_has_key"}``. The words never show an address's query
    (a key could sit there); ``address_has_key`` says one seems to. Raises
    :class:`McpError` for an address or command that cannot be read."""
    target = parse_target(url_or_command)
    shown = str(label or name or "This tool")[:60]
    if target["kind"] == "stdio":
        command = _shown_target(target)[:300]
        return {"kind": "mcp_command", "app": shown, "host": "", "command": command,
                "words": f"Run {command} on this computer for {shown}",
                "target": target, "address_has_key": False}
    host = _host(target)
    try:
        query = urlsplit(target["url"]).query
    except ValueError:
        query = ""
    return {"kind": "mcp_host", "app": shown, "host": host, "command": "",
            "words": f"Allow {shown} to reach {host}", "target": target,
            "address_has_key": bool(_KEYISH_QUERY.search(query or ""))}


def add_server(name: str, url_or_command: Any, headers: Optional[dict] = None,
               *, label: Optional[str] = None, by: str = "owner",
               keep_working_locked: bool = True, source: str = "") -> dict:
    """Add (or replace) a tool server and hand its tools to the loop.

    Connects first: a server that does not answer is not kept, and neither is
    its password. Header values go into the vault, never into the list.
    Returns ``{"ok", "message", "server"}``; never raises for her mistakes.
    """
    from puffpai.security import keys
    try:
        name = str(name or "")
        if not NAME_RE.match(name):
            name = slug(name or label or "")
        if not NAME_RE.match(name):
            raise McpError("Give the tool a short name.", "name")
        target = parse_target(url_or_command)
        clean_headers = None
        if headers:
            if target["kind"] == "stdio":
                raise McpError("A command cannot take a password here.", "headers")
            clean_headers = {str(k): str(v) for k, v in dict(headers).items()}
            # Before anything is sent: a password that cannot be locked away
            # is not sent anywhere either.
            if not keys._is_open(keys._vault()):  # noqa: SLF001
                raise McpError("Your vault is locked. Open it first, then try "
                               "again.", "locked")
        rec = {"name": name, "label": str(label or name)[:60], **target,
               "has_password": bool(clean_headers), "added_at": _now(),
               "added_by": by, "source": str(source or "")[:200]}
        tools = _fetch_tools(rec, clean_headers)
    except McpError as exc:
        return {"ok": False, "message": exc.message, "code": exc.code}
    with _lock:
        if clean_headers:
            try:
                kept = keys.put_tool_secret(name, clean_headers,
                                            keep_working_locked=keep_working_locked,
                                            by=by)
            except keys.KeyRefused as exc:
                return {"ok": False, "message": str(exc), "code": exc.code}
            rec["works_when_locked"] = bool(kept.get("kept_when_locked"))
        else:
            keys.remove_tool_secret(name, by=by)
        rec["tools"] = tools
        old = _read()
        if name in old:
            _unregister_server_tools(old[name])
        old[name] = rec
        _write(old)
        _set_switch(name, True, by)
        _register_server_tools(rec)
    n = len(model_tools(tools))
    shown = rec["label"]
    message = (f"{shown} is added. It has {n} tool{'s' if n != 1 else ''} your "
               f"bot can use in chat.")
    if clean_headers and not rec.get("works_when_locked"):
        message += " Its password is in your vault, so it works while your vault is open."
    elif clean_headers:
        message += " Its password is locked in your vault."
    return {"ok": True, "message": message, "server": _public(rec)}


def remove_server(name: str, *, by: str = "owner") -> dict:
    """Remove a tool server: its tools leave the loop, its password leaves
    the vault, its switch leaves the list. The log lines stay."""
    from puffpai.security import keys
    name = str(name or "")
    with _lock:
        saved = _read()
        rec = saved.pop(name, None)
        if rec is None:
            return {"ok": False, "message": "There is no tool by that name.",
                    "code": "unknown"}
        _write(saved)
        _unregister_server_tools(rec)
        keys.remove_tool_secret(name, by=by)
        _clear_switch(name)
    return {"ok": True, "message": f"{rec.get('label') or name} is removed."}


def refresh_server(name: str) -> dict:
    """Ask a saved server for its tools again."""
    with _lock:
        rec = _read().get(str(name or ""))
    if rec is None:
        return {"ok": False, "message": "There is no tool by that name.",
                "code": "unknown"}
    if _switch_is_off(rec["name"]):
        _log(rec, WHAT_TOOLS, "refused", "switched_off")
        return {"ok": False, "code": "switched_off",
                "message": _off_words(rec)}
    try:
        tools = _fetch_tools(rec, _headers_for(rec))
    except McpError as exc:
        return {"ok": False, "message": exc.message, "code": exc.code}
    with _lock:
        saved = _read()
        if rec["name"] not in saved:
            return {"ok": False, "message": "That tool was removed.",
                    "code": "unknown"}
        _unregister_server_tools(saved[rec["name"]])
        saved[rec["name"]]["tools"] = tools
        _write(saved)
        _register_server_tools(saved[rec["name"]])
    return {"ok": True, "message": f"{rec.get('label') or name} has "
                                   f"{len(model_tools(tools))} tools.",
            "server": _public(saved[rec["name"]])}


def _off_words(rec: dict) -> str:
    return (f"{rec.get('label') or rec['name']} is switched off. Switch it on in "
            f"What leaves your computer to use it.")


def call_tool(name: str, tool: str, arguments: Optional[dict] = None,
              *, timeout: float = CALL_TIMEOUT) -> str:
    """Use one tool. Returns text for the model; a problem comes back as a
    plain sentence, never an exception."""
    rec = _read().get(str(name or ""))
    if rec is None:
        return "That tool server was removed, so this tool cannot be used."
    if _switch_is_off(rec["name"]):
        _log(rec, WHAT_CALL, "refused", "switched_off")
        return _off_words(rec)
    args = arguments if isinstance(arguments, dict) else {}

    async def work(session):
        return _result_text(await session.call_tool(tool, args))
    try:
        text = _run(rec, _headers_for(rec), work, timeout)
    except McpError as exc:
        _log(rec, WHAT_CALL, "failed")
        return exc.message
    _log(rec, WHAT_CALL, "done")
    return text


def call_tool_with_ui(name: str, tool: str, arguments: Optional[dict] = None,
                      *, from_app: bool = False,
                      timeout: float = CALL_TIMEOUT) -> dict:
    """Use one tool from the thing page, and fetch its app screen too.

    ``{"text", "result", "ui"}``: *text* is what :func:`call_tool` returns,
    *result* the answer as an app screen receives it, *ui* ``{"uri",
    "html"}`` when the tool (or its answer) names a ``ui://`` screen that
    the same server serves, in the same session, else None. *from_app* is a
    call made by an app screen: refused for a tool that is not visible to
    apps, and never fetches another screen."""
    rec = _read().get(str(name or ""))
    if rec is None:
        return {"text": "That tool server was removed, so this tool cannot be used.",
                "result": None, "ui": None}
    row = next((t for t in rec.get("tools") or [] if t.get("name") == tool), None)
    if row is None:
        return {"text": "That tool server has no tool called that.", "result": None,
                "ui": None}
    if from_app and "app" not in visibility(row):
        return {"text": "That tool is not one its screen may use.", "result": None,
                "ui": None, "refused": True}
    if _switch_is_off(rec["name"]):
        _log(rec, WHAT_CALL, "refused", "switched_off")
        return {"text": _off_words(rec), "result": None, "ui": None}
    args = arguments if isinstance(arguments, dict) else {}
    want = "" if from_app else ui_resource_uri(row.get("meta"))

    async def work(session):
        result = await session.call_tool(tool, args)
        page = None
        if not from_app:
            uri = want or ui_resource_uri(_meta_of(result))
            page = await _read_ui(session, uri) if uri else _embedded_ui(result)
        return _result_text(result), _result_for_app(result), page
    try:
        text, for_app, page = _run(rec, _headers_for(rec), work, timeout)
    except McpError as exc:
        _log(rec, WHAT_CALL, "failed")
        return {"text": exc.message, "result": None, "ui": None}
    _log(rec, WHAT_CALL, "done")
    return {"text": text, "result": for_app, "ui": page}


# ── the tools in the loop ───────────────────────────────────────────────────

def _caller(server_name: str, tool: str) -> Callable[..., str]:
    return _flex(lambda args: call_tool(server_name, tool, args))


def _register_server_tools(rec: dict) -> list[str]:
    label = rec.get("label") or rec["name"]
    names = []
    for t in rec.get("tools") or []:
        if "model" not in visibility(t):
            continue            # SEP-1865: an app-only tool is not the model's
        full = tool_name(rec["name"], t["name"])
        description = (t.get("description") or t["name"]).strip()
        _register(full, f"{description} (from {label})"[:1024],
                  _schema(t.get("input_schema")), _caller(rec["name"], t["name"]),
                  needs_yes=False)
        names.append(full)
    return names


def _unregister_server_tools(rec: dict) -> None:
    for t in rec.get("tools") or []:
        _unregister(tool_name(rec["name"], t["name"]))


def register_all() -> int:
    """Hand every saved server's tools to the loop (at start-up), plus the
    chat tools that add and remove servers. Returns how many server tools."""
    register_chat_tools()
    try:
        from puffpai import skill_learn
        skill_learn.register_chat_tools()
    except Exception as exc:  # noqa: BLE001 - the house tools still work
        logger.warning("learn-a-tool chat tools not added: %s", type(exc).__name__)
    count = 0
    with _lock:
        for rec in _read().values():
            count += len(_register_server_tools(rec))
    return count


# ── Home Assistant ──────────────────────────────────────────────────────────

def home_assistant_url(address: Any) -> str:
    """``http://homeassistant.local:8123`` (or with ``/api/mcp`` already)
    becomes the MCP address ``.../api/mcp``. Raises :class:`McpError`."""
    text = str(address or "").strip()
    if not text:
        raise McpError("Type your Home Assistant address, for example "
                       "http://homeassistant.local:8123", "address")
    if not re.match(r"^https?://", text, re.I):
        text = "http://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        parts = None
    if parts is None or not parts.hostname:
        raise McpError("That Home Assistant address cannot be read.", "address")
    base = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    for tail in ("/api/mcp", "/api"):
        if base.lower().endswith(tail):
            base = base[: -len(tail)]
            break
    return base + "/api/mcp"


def connect_home_assistant(address: Any, token: Any, *, by: str = "owner",
                           keep_working_locked: bool = True) -> dict:
    """"Connect my Home Assistant": its MCP server, its long-lived token in
    the vault, its tools in the loop."""
    try:
        url = home_assistant_url(address)
    except McpError as exc:
        return {"ok": False, "message": exc.message, "code": exc.code}
    token = str(token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return {"ok": False, "code": "token",
                "message": ("Home Assistant needs a long-lived access token. Make "
                            "one in Home Assistant under your profile, Security, "
                            "Long-lived access tokens, then paste it here.")}
    out = add_server(HOME_ASSISTANT, url, {"Authorization": f"Bearer {token}"},
                     label=HOME_ASSISTANT_LABEL, by=by,
                     keep_working_locked=keep_working_locked,
                     source="Home Assistant's own MCP Server")
    if not out["ok"] and out.get("code") == "missing":
        out["message"] += (" Turn on the Model Context Protocol Server in Home "
                           "Assistant (Settings, Devices and services, Add "
                           "integration), then try again.")
    return out


# ── rows on "What leaves your computer" ─────────────────────────────────────

def outside_rows() -> list[dict]:
    """One row per tool server, for :func:`puffpai.outside_services.rows`."""
    rows = []
    for rec in _read().values():
        stored = None
        try:
            from puffpai.security import outside_switches
            stored = outside_switches.entry(switch_id(rec["name"]))
        except Exception:
            stored = None
        on = not _switch_is_off(rec["name"])
        local = rec.get("kind") == "stdio"
        tools = [t["name"] for t in (rec.get("tools") or [])[:6]]
        if local:
            leaves = ("What your bot asks the tool to do. The tool is a program "
                      "on this computer; anything it sends on is up to that "
                      "program.")
        else:
            leaves = ("What your bot asks the tool to do"
                      + (", with the password from your vault" if rec.get("has_password")
                         else "")
                      + ", each time a tool is used.")
        rows.append({
            "id": switch_id(rec["name"]),
            "kind": "mcp",
            "name": f"Tool server: {rec.get('label') or rec['name']}",
            "for": ("Tools your bot can use in chat"
                    + (f": {', '.join(tools)}." if tools else ".")),
            "what_leaves": leaves,
            "websites": ["this computer"] if local else [_host(rec) or "its address"],
            "state": "on" if on else "off",
            "state_line": "Switched on." if on else "Switched off.",
            "can_switch": True,
            "turned_on_by": (stored or {}).get("by", "") if on else "",
            "turned_on_at": (stored or {}).get("at", "") if on else "",
        })
    return rows


# ── chat tools: connect, add, remove, list (needs her yes) ──────────────────

HA_PAGE = "/dashboard/homeassistant"
_HINT_FILE = "mcp_home_assistant_address.json"


def remember_home_assistant_address(address: Any) -> str:
    """Keep the address she said in chat, so the Smart Home page's token box
    is filled in. Never a token."""
    from puffpai import fsutil
    from puffpai.config import get_state_dir
    url = home_assistant_url(address)
    base = url[: -len("/api/mcp")]
    fsutil.atomic_write_json(os.path.join(get_state_dir(create=True), _HINT_FILE),
                             {"address": base, "at": _now()})
    return base


def home_assistant_address_hint() -> str:
    from puffpai import fsutil
    from puffpai.config import get_state_dir
    data = fsutil.read_json(os.path.join(get_state_dir(create=True), _HINT_FILE),
                            default=None)
    found = data.get("address") if isinstance(data, dict) else ""
    return found if isinstance(found, str) else ""


def _chat_connect_ha(args: dict) -> str:
    token = str(args.get("token") or "").strip()
    saved = _read().get(HOME_ASSISTANT)
    if saved and not token:
        tools = ", ".join(t["name"] for t in (saved.get("tools") or [])[:8])
        return (f"Home Assistant is already connected, with these tools: {tools}. "
                f"Use them for the owner's home devices. If one says the vault is "
                f"locked, ask her to open her vault on the Vault page.")
    if token and not token.startswith("["):  # "[Pasted here: ...]" is not one
        return connect_home_assistant(args.get("address"), token,
                                      by="owner")["message"]
    # Chat never holds the token: a pasted key is swapped out before the
    # model sees it (api_server._swap_pasted_key). The token box is on the
    # Smart Home page, with the address she gave already filled in.
    try:
        base = remember_home_assistant_address(args.get("address"))
    except McpError as exc:
        return exc.message
    return (f"Almost done. Open the Smart Home page ({HA_PAGE}) and press "
            f"Connect my Home Assistant. Your address {base} is filled in; "
            f"paste your long-lived access token there. It goes straight into "
            f"your vault and is never kept in this chat.")


def _chat_add(args: dict) -> str:
    target = args.get("address_or_command") or args.get("address") or args.get("command")
    label = str(args.get("name") or "").strip()
    name = slug(label) or slug(str(target or "")) or "my_tool"
    return add_server(name, target, label=label or None, by="owner")["message"]


def _chat_remove(args: dict) -> str:
    return remove_server(slug(args.get("name")) or str(args.get("name") or ""),
                         by="owner")["message"]


def _chat_list(args: dict) -> str:
    found = servers()
    if not found:
        return "No tool servers are added yet."
    return "\n".join(
        f"- {s['label']} ({s['name']}): {s['tool_count']} tools, "
        f"{'on' if s['switched_on'] else 'switched off'}" for s in found)


CHAT_TOOLS: tuple[tuple[str, str, dict, Callable[[dict], str], bool], ...] = (
    ("connect_home_assistant",
     "Connect the owner's Home Assistant so its lights and devices become tools "
     "you can use. Ask for its address (like http://homeassistant.local:8123). "
     "Never ask for the token in chat: this tool tells the owner where to "
     "paste it, so it goes straight into the vault.",
     {"type": "object",
      "properties": {"address": {"type": "string",
                                 "description": "Home Assistant's address"}},
      "required": ["address"]},
     _chat_connect_ha, False),
    ("add_tool_server",
     "Add the owner's own tool: an MCP tool server at a web address, or a "
     "command that starts one on this computer. Its tools then become yours.",
     {"type": "object",
      "properties": {"name": {"type": "string", "description": "A short name"},
                     "address_or_command": {
                         "type": "string",
                         "description": "A web address or a command"}},
      "required": ["address_or_command"]},
     _chat_add, True),
    ("remove_tool_server",
     "Remove a tool server the owner added, and its tools.",
     {"type": "object",
      "properties": {"name": {"type": "string"}}, "required": ["name"]},
     _chat_remove, True),
    ("list_tool_servers",
     "List the tool servers added to this bot and how many tools each has.",
     {"type": "object", "properties": {}, "required": []},
     _chat_list, False),
)


def register_chat_tools() -> list[str]:
    names = []
    for name, description, schema, fn, needs_yes in CHAT_TOOLS:
        _register(name, description, schema, _flex(fn), needs_yes=needs_yes)
        names.append(name)
    return names


__all__ = [
    "CALL_TIMEOUT", "CHAT_TOOLS", "HOME_ASSISTANT", "McpError", "NAME_RE",
    "add_server", "call_tool", "call_tool_with_ui", "connect_home_assistant",
    "home_assistant_address_hint", "home_assistant_url", "list_tools",
    "outside_rows", "parse_target", "remember_home_assistant_address",
    "refresh_server", "register_all", "register_chat_tools", "remove_server",
    "sdk_available", "server", "servers", "setup_row", "slug", "switch_id", "tool_name",
    "model_tools", "ui_resource_uri", "visibility",
]
