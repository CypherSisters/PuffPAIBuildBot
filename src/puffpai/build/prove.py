"""Prove: does a made job answer a real ask? (one build foundation)

``prove(job, build, made=...) -> {"state": "working"|"not_yet", "reason"}``.
"Working" means it can answer a real ask, never "something was installed".
It reuses Assemble's proof (:mod:`puffpai.assemble.accept`): the job's sample
ask goes through the chat's own tool loop with ONLY that job's tools on offer,
and what came back must give the job's view what it needs.

* **A** (an app as it is): a light check. Its own screen is there: the
  server it runs is running on 127.0.0.1 and its page answers (HTTP 200
  with a page), or the browser app is added. No sample ask: the app is
  used as it is (chat may still use its tools when it has some).
* **B** (tools): an MCP server's tools answer the sample ask, and the answer
  fits the view (pictures for a gallery, positions for a map, numbers over
  time for a chart, something to play for a player, real text for a list);
  a CLI skill has every program it declares; a reminder's routine is on; a
  book or text skill is there for a learn/ask job.
* **C** (a view put together): the assembled app shows ``VIEW[output]`` and
  every check of its spec passes (:func:`accept.prove`: the action answers,
  the view gets what it needs, the brain uses it).

An outside site an app was refused while being proven becomes ONE
"Allow <app> to reach <host>" row (``build.make.note_runtime_refusal``); the
proof then says so instead of failing silently.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Any, Callable, Optional

from puffpai.build import make as make_mod
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

WORKING = "working"
NOT_YET = "not_yet"
SYSTEM_NOTE = ("Answer the owner's question with the tools you have. Call the tool that "
               "fits, then answer briefly from what it gave back.")
#: What each view needs, in her words (for a reason).
VIEW_NEEDS = {"gallery": "pictures", "map": "positions on a map",
              "chart": "numbers over time", "player": "something to play",
              "list": "real text", "form": "a working action"}

_IMAGE_URL = re.compile(r"https://[^\s\"'<>]+\.(?:png|jpe?g|gif|webp|avif|svg)\b", re.I)
_IMAGE_KEY = re.compile(r"(?i)\"(?:image|img|thumbnail|picture|photo|href)[a-z_]*\"\s*:\s*\"https://")
_LAT = re.compile(r"(?i)\b(?:lat|latitude)\b\"?\s*[:=]\s*\"?-?\d{1,2}(?:\.\d+)?")
_LON = re.compile(r"(?i)\b(?:lon|lng|long|longitude)\b\"?\s*[:=]\s*\"?-?\d{1,3}(?:\.\d+)?")
_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_STREAM = re.compile(r"https://[^\s\"'<>]+(?:\.(?:mp3|m3u8?|aac|ogg|opus|mp4|webm|pls)\b|"
                     r"youtube\.com/watch|youtu\.be/)", re.I)


def _result(state: str, reason: str = "", **extra: Any) -> dict:
    return {"state": state, "reason": str(reason or ""), **extra}


def view_fits(view: str, text: str) -> str:
    """"" when a tool's answer *text* gives *view* what it needs, else the
    plain reason. Any subject; reads the text only."""
    text = str(text or "")
    if '"error"' in text[:40] and len(text) < 400:
        return "the tool answered with an error"
    if view == "gallery":
        ok = bool(_IMAGE_URL.search(text) or _IMAGE_KEY.search(text))
    elif view == "map":
        ok = bool(_LAT.search(text) and _LON.search(text))
    elif view == "chart":
        ok = bool(_TIME.search(text)) and len(_NUMBER.findall(text)) >= 3
    elif view == "player":
        ok = bool(_STREAM.search(text))
    elif view == "form":
        ok = bool(text.strip())
    else:
        words = re.findall(r"[A-Za-z]{3,}", text)
        ok = len(words) >= 6
    return "" if ok else f"its answer has no {VIEW_NEEDS.get(view, 'real text')}"


def ask_with_tools(ask: str, tools: list, *, backend: Any = None,
                   max_steps: int = 4) -> tuple[bool, str, list[tuple[str, str]]]:
    """``(ran, reason, [(tool, answer text)])``: *ask* through the chat's tool
    loop with only *tools*; ran when one of them was used."""
    from puffpai import brain_loop
    from puffpai.assemble import accept
    if not tools:
        return False, "it has no tools for the brain", []
    conversation = f"prove-{secrets.token_hex(6)}"
    ctx: dict = {"conversation_id": conversation, "caller": "owner"}
    messages = [{"role": "system", "content": SYSTEM_NOTE},
                {"role": "user", "content": str(ask or "")}]
    try:
        got = brain_loop.run(messages, ctx=ctx, backend=backend or accept._default_backend(),  # noqa: SLF001
                             tools=tools, max_steps=max_steps)
    except Exception as exc:  # noqa: BLE001 - no brain is a reason, not a crash
        return False, f"the brain did not answer ({type(exc).__name__})", []
    names = {t.name for t in tools}
    used = [u for u in got.get("used") or [] if u in names]
    outputs: list[tuple[str, str]] = []
    lock = getattr(brain_loop, "_earlier_lock", None)
    store = getattr(brain_loop, "_earlier", None)
    if lock is not None and isinstance(store, dict):
        with lock:
            rows = store.pop(conversation, [])
        outputs = [(str(r.get("tool")), str(r.get("result") or "")) for r in rows
                   if isinstance(r, dict) and r.get("tool") in names]
    if not used:
        return False, "the brain answered without using its tools", outputs
    return True, "", outputs


def _server_tools(server: str) -> list:
    from puffpai import brain_tools, mcp_client
    out = []
    for t in mcp_client.list_tools(server) or []:
        if "model" not in mcp_client.visibility(t):
            continue
        tool = brain_tools.find(mcp_client.tool_name(server, t["name"]))
        if tool is not None:
            out.append(tool)
    return out


def _new_asks(before: set[str], apps: set[str]) -> str:
    """The words of a runtime row recorded during this proof, or ""."""
    for row in make_mod.runtime_rows():
        if row.get("id") not in before and row.get("app_id") in apps:
            return f"{row.get('words')} is on your setup list"
    return ""


def prove(job: Any, build: Any, *, made: Optional[dict] = None, backend: Any = None,
          hooks: Optional[dict] = None) -> dict:
    """``{"state": "working"|"not_yet", "reason"}`` for one made job. Never
    raises. *made* is :func:`puffpai.build.make.make`'s answer. *hooks*
    replace the real parts (tests): ``ask(ask, tools) -> (ran, reason,
    outputs)``, ``tools(server) -> [Tool]``, ``status(iid)``,
    ``web_record(app_id)``, ``routine(id)``, ``spec(app_id)``,
    ``accept(app_id, spec) -> {cap: {"ok", "reason"}}``."""
    job = job if isinstance(job, dict) else {}
    if made is None:
        made = make_mod.load_made(job)      # the build pipeline calls prove(job, build)
    made = made if isinstance(made, dict) else {}
    h = dict(hooks or {})
    if not made.get("ok"):
        return _result(NOT_YET, made.get("reason") or "it was not made yet")
    before = {str(r.get("id")) for r in make_mod.runtime_rows(waiting_only=False)}
    route = str(job.get("route") or "")
    try:
        if route == "A":
            got, apps = _prove_app(job, made, h, backend)
        elif route == "B":
            got, apps = _prove_tools(job, made, h, backend)
        elif route == "C":
            got, apps = _prove_view(job, made, h, backend)
        else:
            return _result(NOT_YET, make_mod.NOTHING_FOUND)
    except Exception as exc:  # noqa: BLE001 - a proof that crashed is not yet
        logger.warning("build prove: %s stopped (%s)", job.get("id"), type(exc).__name__)
        return _result(NOT_YET, f"the proof stopped ({type(exc).__name__})")
    asked = _new_asks(before, apps)
    if asked:
        return _result(NOT_YET, asked)
    return got


# -- A ------------------------------------------------------------------------------

def _prove_app(job: dict, made: dict, h: dict, backend: Any) -> tuple[dict, set[str]]:
    screen = made.get("screen") if isinstance(made.get("screen"), dict) else {}
    iid = str(screen.get("integration_id") or (made.get("made") or {}).get("integration_id")
              or "")
    web_id = str(screen.get("app_id") or "")
    apps = {web_id} if web_id else set()
    if screen.get("toolsmith_app"):
        # Toolsmith's own check (done) saw real content; once more now, and
        # the brain must be able to answer the job's ask from its page.
        name = str((made.get("made") or {}).get("app_name") or "")
        url = str(screen.get("url") or "")
        why = (h.get("page") or page_answers)(url) if url else "it has no address"
        if not why and ("read_ask" in h or "page" not in h):
            # The same reader Toolsmith's done() and read_app use, so its
            # "working (582 words)" and this check can never disagree.
            why = (h.get("read_page_words") or _toolsmith_page_words)(url)
        if why:
            return _result(NOT_YET, f"{name}: {why}" if name else why), apps
        return _result(WORKING), apps
    if iid and not web_id:
        status = h.get("status")
        if status is None:
            from puffpai.integrate import run_here
            status = run_here.status
        now = status(iid) or {}
        # A heavy app's first start (its database set up, migrations) can
        # take minutes: it is given up to START_WAIT_S while it says starting.
        import time as _time
        until = _time.monotonic() + float(h.get("start_wait", 0 if "status" in h
                                                 else START_WAIT_S))
        while (now.get("can_run") and now.get("state") == "starting"
               and _time.monotonic() < until):
            _time.sleep(START_POLL_S)
            now = status(iid) or {}
        name = str((made.get("made") or {}).get("app_name") or "")
        named = (lambda why: f"{name}: {why}" if name and not why.startswith(name)  # noqa: E731
                 else why)
        if now.get("can_run") and now.get("state") != "running":
            why = now.get("why") or ("it is still starting" if now.get("state") == "starting"
                                     else "it is not running")
            return _result(NOT_YET, named(str(why))), apps
        url = str(now.get("url") or screen.get("url") or "")
        if now.get("can_run") and url:
            why = (h.get("page") or page_answers)(url)
            if why:
                return _result(NOT_YET, named(why)), apps
            if "read_ask" in h or "status" not in h:
                # The brain can see it: the job's own ask is answered from
                # what read_app shows on its page (any web app, no app code).
                why = _read_answer(job, name, h, backend)
                if why:
                    return _result(NOT_YET, named(why)), apps
    if web_id:
        record = h.get("web_record")
        if record is None:
            from puffpai.webapps import host as webhost
            record = webhost.read_record
        if not record(web_id):
            return _result(NOT_YET, "its screen is not there"), apps
    return _result(WORKING), apps


def _toolsmith_page_words(url: str) -> str:
    """"" when ``host_tools.read_page`` (what the brain's read_app shows)
    finds real words on a Toolsmith app's page, with the same bar as
    Toolsmith's own done(); else the reader's reason."""
    from puffpai.integrate import host_tools
    got = host_tools.read_page(url)
    words = len(re.findall(r"[A-Za-z]{3,}", str(got.get("text") or "")))
    if got.get("ok") and words >= 3:
        return ""
    return "the brain looked at its page but found no content there yet" + (
        f" ({got.get('said')})" if got.get("said") else "")


def _read_answer(job: dict, name: str, h: dict, backend: Any) -> str:
    """"" when the job's sample ask, through the chat's tool loop with only
    ``read_app``, used it and got the app's page text back; else the reason."""
    ask = h.get("read_ask")
    if ask is None:
        from puffpai.integrate import host_tools
        tools = [t for t in host_tools.provide() if t.name == "read_app"]
        question = (f"{job.get('ask') or job.get('does') or job.get('name')} "
                    f"(look at {name or 'the app'} with read_app)").strip()
        ran, why, outputs = ask_with_tools(question, tools, backend=backend)
    else:
        ran, why, outputs = ask(job)
    if not ran:
        return why or "the brain could not look at its page"
    if not any('"ok": true' in text.replace("'ok': True", '"ok": true')
               and len(re.findall(r"[A-Za-z]{3,}", text)) >= 12 for _t, text in outputs):
        return "the brain looked at its page but found no content there yet"
    return ""


#: How long an app that just started may take before its page shows real
#: content (an app in containers can take minutes on its first start).
PAGE_WAIT_S = 600.0
#: How long a first start may say "starting" before it is not yet (10 minutes).
START_WAIT_S = 600.0
START_POLL_S = 5.0
PAGE_TRY_EVERY_S = 3.0
#: A page with this many words of its own text is real content.
PAGE_WORDS = 1
_SCRIPT_STYLE = re.compile(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>")
_TAG = re.compile(r"(?s)<[^>]+>")
_UI_TAG = re.compile(r"(?i)<(?:input|button|form|img|svg|canvas|select|textarea|table|video)\b")
_BODY = re.compile(r"(?is)<body\b[^>]*>(.*)</body>")


def page_words(html: str) -> int:
    """How many words of visible text a page has (scripts and styles left out)."""
    body = _BODY.search(html)
    text = _TAG.sub(" ", _SCRIPT_STYLE.sub(" ", body.group(1) if body else html))
    return len(re.findall(r"[^\W\d_]{2,}", text))


def real_content(html: str) -> bool:
    """Real content, not an empty page shell: words or controls of its own."""
    body = _BODY.search(html)
    inner = _SCRIPT_STYLE.sub(" ", body.group(1) if body else html)
    return page_words(html) >= PAGE_WORDS or bool(_UI_TAG.search(inner))


def rendered(url: str, timeout: float = 60.0) -> Optional[str]:
    """The page after its own scripts ran (a headless browser on this
    computer, its own empty profile, no window), or None when there is none."""
    import subprocess
    import tempfile
    from puffpai import procflags
    from puffpai.integrate import page_reader
    browser = page_reader.browser_path()
    if not browser:
        return None
    with tempfile.TemporaryDirectory(prefix="bb-page-") as profile:
        try:
            got = subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--no-default-browser-check", "--disable-extensions",
                 "--disable-background-networking", f"--user-data-dir={profile}",
                 "--virtual-time-budget=15000", "--dump-dom", url],
                capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                **procflags.hidden())
        except (OSError, subprocess.TimeoutExpired):
            return None
        finally:
            from puffpai.integrate import page_reader
            page_reader.stop_profile_browsers(profile)   # the browser its launcher left
    if got.returncode != 0:
        return None
    return got.stdout.decode("utf-8", "replace")


#: Seam for the tests (``url -> html or None``).
_render: Optional[Callable[[str], Optional[str]]] = None


def page_answers(url: str, *, wait_s: float = PAGE_WAIT_S) -> str:
    """"" when the app's own page on this computer shows real content (HTTP
    200 after its own redirects, with words or controls of its own; a page
    that is only a shell for its scripts is looked at after they ran), else
    the plain reason. Keeps trying while it is still starting. Only loopback
    addresses are asked (nothing leaves the computer)."""
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return "its page is not on this computer"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # lan-egress: 127.0.0.1 only (checked above), no proxy
    end = time.monotonic() + max(0.0, wait_s)
    why = "its page did not answer"
    while True:
        try:
            with opener.open(url, timeout=10) as resp:
                raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
                body = raw.lower()
                ctype = str(resp.headers.get("Content-Type") or "").lower()
                if resp.status == 200 and ("html" in ctype or "<html" in body
                                           or "<!doctype html" in body):
                    if real_content(raw):
                        return ""
                    if "<script" in body:
                        # A shell its scripts fill in: looked at after they ran.
                        after = (_render or rendered)(resp.geturl() or url)
                        if after is None or real_content(after):
                            return ""        # (no browser here to run them: trusted)
                    why = "its page stayed empty (it showed no content of its own)"
                else:
                    why = f"its page answered without a page (HTTP {resp.status})"
        except urllib.error.HTTPError as exc:
            why = f"its page answered HTTP {exc.code}"
        except (OSError, ValueError):
            why = "its page did not answer"
        if time.monotonic() >= end:
            return why
        time.sleep(PAGE_TRY_EVERY_S)


# -- B ------------------------------------------------------------------------------

def _tools_answer(job: dict, servers: list[str], h: dict, backend: Any) -> dict:
    tools_of = h.get("tools") or _server_tools
    tools = [t for s in servers for t in tools_of(s)]
    ask = h.get("ask") or (lambda a, ts: ask_with_tools(a, ts, backend=backend))
    ran, why, outputs = ask(str(job.get("ask") or job.get("does") or job.get("name") or ""),
                            tools)
    if not ran:
        return _result(NOT_YET, why)
    view = make_mod.view_for(job)
    problems = [view_fits(view, text) for _tool, text in outputs] or \
        ["its tools gave nothing back"]
    if all(problems):
        return _result(NOT_YET, problems[0])
    return _result(WORKING)


def _prove_tools(job: dict, made: dict, h: dict, backend: Any) -> tuple[dict, set[str]]:
    parts = made.get("made") if isinstance(made.get("made"), dict) else {}
    reasons: list[str] = []
    if parts.get("servers"):
        got = _tools_answer(job, list(parts["servers"]), h, backend)
        if got["state"] == WORKING:
            return got, set()
        reasons.append(got["reason"])
    for folder in parts.get("skills") or []:
        why = _cli_ready(folder)
        if why:
            reasons.append(why)
            continue
        # Its programs are here; that alone is not "working". The job's own
        # sample ask must lead the bot to this skill, like every other route.
        got = _cli_answer(job, folder, h, backend)
        if got["state"] == WORKING:
            return got, set()
        reasons.append(got["reason"])
    routine = h.get("routine")
    if routine is None:
        from puffpai import routines
        routine = routines.get_routine
    for rid in parts.get("routines") or []:
        row = routine(rid) or {}
        if row.get("enabled"):
            return _result(WORKING), set()
        reasons.append("its reminder is switched off")
    if parts.get("texts") and str(job.get("action") or "") in ("learn", "ask"):
        return _result(WORKING), set()
    return _result(NOT_YET, reasons[0] if reasons else "nothing it made answers yet"), set()


def _cli_answer(job: dict, folder: str, h: dict, backend: Any) -> dict:
    ask = str(job.get("ask") or job.get("does") or job.get("name") or "")
    fn = h.get("ask_cli") or (lambda a, f: ask_skill(a, f, backend=backend))
    used, why = fn(ask, folder)
    return _result(WORKING) if used else _result(NOT_YET, why or "it did not use the skill")


def ask_skill(ask: str, folder: str, *, backend: Any = None,
              max_steps: int = 4) -> tuple[bool, str]:
    """``(used, reason)``: *ask* through the chat's own tool loop with the
    skill tools (``skill_view``, ``run_command``). Used when the bot reads
    this skill and asks to run one of its programs for it.

    NOTHING RUNS: a command still needs her yes for that command (the chat's
    rule for run_command), so the question the bot would ask her is recorded
    and answered with no answer. This proves the bot picks this skill for the
    job's own ask and knows what to run; the first real run is still hers."""
    from puffpai import brain_loop, brain_tools, skills
    from puffpai.assemble import accept
    rec = skills.find(folder)
    if rec is None:
        return False, f"the skill {folder} is not installed"
    req = skills.requirements(rec.get("frontmatter"))
    bins = set(req["bins"] + req["any_bins"] + skills.how_it_runs(rec).get("bins", []))
    asked: list[dict] = []

    def record(question: Any) -> None:
        if isinstance(question, dict):
            asked.append(question)
        return None                      # no answer: nothing is run

    conversation = f"prove-{secrets.token_hex(6)}"
    ctx: dict = {"conversation_id": conversation, "caller": "owner", "ask": record}
    tools = [t for t in (brain_tools.find("skill_view", ctx), brain_tools.find("run_command", ctx))
             if t is not None]
    if len(tools) < 2:
        return False, "the bot has no way to use a skill here"
    messages = [{"role": "system", "content": SYSTEM_NOTE},
                {"role": "user", "content": ask}]
    try:
        brain_loop.run(messages, ctx=ctx, backend=backend or accept._default_backend(),  # noqa: SLF001
                       tools=tools, max_steps=max_steps)
    except Exception as exc:  # noqa: BLE001 - no brain is a reason, not a crash
        return False, f"the brain did not answer ({type(exc).__name__})"
    finally:
        lock = getattr(brain_loop, "_earlier_lock", None)
        store = getattr(brain_loop, "_earlier", None)
        if lock is not None and isinstance(store, dict):
            with lock:
                store.pop(conversation, None)
    root = os.path.normcase(os.path.dirname(str(rec.get("path") or "")))
    for q in asked:
        words = str(q.get("command") or "").split()
        first = os.path.basename(words[0]).strip("\"'") if words else ""
        where = os.path.normcase(str(q.get("folder") or ""))
        if first in bins or (root and where == root):
            return True, ""
    return False, f"for \"{ask[:80]}\" the bot did not use {folder}"


def _cli_ready(folder: str) -> str:
    from puffpai import skills
    rec = skills.find(folder)
    if rec is None:
        return f"the skill {folder} is not installed"
    missing = skills.missing_programs(skills.requirements(rec.get("frontmatter")))
    return f"it still needs {', '.join(missing)}" if missing else ""


# -- C ------------------------------------------------------------------------------

def _prove_view(job: dict, made: dict, h: dict, backend: Any) -> tuple[dict, set[str]]:
    app_id = str((made.get("made") or {}).get("app_id") or
                 (made.get("screen") or {}).get("app_id") or "")
    apps = {app_id} if app_id else set()
    spec_of = h.get("spec")
    if spec_of is None:
        from puffpai.assemble import api as assemble_api
        spec_of = assemble_api.load_spec
    spec = spec_of(app_id) if app_id else None
    if not isinstance(spec, dict):
        return _result(NOT_YET, "it was not put together yet"), apps
    view = make_mod.view_for(job)
    shown = {str(v.get("type")) for v in spec.get("views") or [] if isinstance(v, dict)}
    if view not in shown:
        return _result(NOT_YET, f"it does not show {VIEW_NEEDS.get(view, view)}"), apps
    accept_fn: Optional[Callable] = h.get("accept")
    if accept_fn is None:
        from puffpai.assemble import accept
        accept_fn = lambda a, s: accept.prove(a, s, backend=backend)  # noqa: E731
    results = accept_fn(app_id, spec) or {}
    if not results:
        return _result(NOT_YET, "it has no check"), apps
    bad = next((r for r in results.values() if not r.get("ok")), None)
    if bad is not None:
        return _result(NOT_YET, str(bad.get("reason") or "its check did not pass")), apps
    return _result(WORKING), apps


__all__ = ["NOT_YET", "VIEW_NEEDS", "WORKING", "ask_skill", "ask_with_tools", "prove",
           "view_fits"]
