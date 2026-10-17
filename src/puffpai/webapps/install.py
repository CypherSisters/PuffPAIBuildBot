"""Toolsmith adds a static browser app: copy, scan, Drawbridge check, compile, check.

``install_static(app_id, files_dir, ...)``:

1. the id must be one the caller issued (``integrate.records.new_id``) and
   match ``^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}$``;
2. the app's files are copied into ``<apps root>/<id>/web/`` (outside state,
   keys and vault): regular files only, links and names Windows would bend
   (trailing dots or spaces, device names, colons) are left out, 200 MB and
   20,000 files at most;
3. the static scan (:mod:`puffpai.webapps.scan`) and Drawbridge's
   ``static_web`` rows decide ``allow``, ``hold`` or ``block``; nothing of the
   app runs before ``allow``;
4. on throwaway check copies with every outside host off, Toolsmith's
   compiler finds the app's add forms and stored lists
   (:mod:`puffpai.webapps.compile_form`) and Drawbridge checks each action
   (:mod:`puffpai.webapps.recipe_check`, R1-R12 with a test run and the
   stored-data effect proof);
5. the app is served at its own address and its checked actions appear.

Parts an app loads from the internet (code, styles, lettering) must be saved
once while adding it; until that is in this build, such an app is held with a
plain line and nothing is added.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from typing import Any, Callable, Optional

from puffpai import webapps
from puffpai.webapps import compile_form, compile_rows, recipes, scan as _scan, wording
from puffpai.webapps import recipe_check as rc

MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 20000
CHECKER_VERSION = "r3.1"
SKIP_DIRS = {".git", ".hg", ".svn"}
_BAD_NAME = re.compile(r'[\x00-\x1f<>:"|?*\\]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?$", re.I)
_NAME_BANNED = re.compile(r"puffpai|buildbot|drawbridge|vault|update", re.I)
_CALLS = re.compile(r"\b(?:video|voice|phone)?\s*calls?\b|\bmeetings?\b|\bconferenc", re.I)

BLOCK_CODES = frozenset({"not_regular", "direct_connections", "orders", "no_entry", "too_big"})
HOLD_CODES = frozenset({"runtime_hosts_new", "parts_needed", "not_a_tool", "mode_changed"})


class InstallError(ValueError):
    pass


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def _is_link(entry: os.DirEntry) -> bool:
    # A symlink, a junction or any other reparse point, read from the entry
    # itself (DirEntry.is_junction is not in Python 3.11).
    return entry.is_symlink() or webapps.is_link(entry.path)


def _name_ok(name: str) -> bool:
    return not (_BAD_NAME.search(name) or name.endswith((".", " ")) or name.startswith(" ")
                or _RESERVED.match(name) or name in (".", "..") or len(name) > 255)


def copy_tree(src: str, dest: str) -> dict:
    """Copy regular files only. {"files","bytes","skipped":[{"name","why"}],"too_big"}."""
    report = {"files": 0, "bytes": 0, "skipped": [], "too_big": False}
    os.makedirs(dest, exist_ok=False)

    def walk(s: str, d: str, rel: str) -> None:
        with os.scandir(s) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            r = f"{rel}{e.name}"
            if _is_link(e):
                report["skipped"].append({"name": r[:200], "why": "link"})
                continue
            if not _name_ok(e.name):
                report["skipped"].append({"name": r[:200], "why": "name"})
                continue
            if e.is_dir(follow_symlinks=False):
                if e.name in SKIP_DIRS:
                    continue
                os.makedirs(os.path.join(d, e.name), exist_ok=False)
                walk(e.path, os.path.join(d, e.name), r + "/")
            elif e.is_file(follow_symlinks=False):
                st = e.stat(follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    report["skipped"].append({"name": r[:200], "why": "not_regular"})
                    continue
                if report["files"] + 1 > MAX_FILES or report["bytes"] + st.st_size > MAX_TOTAL_BYTES:
                    report["too_big"] = True
                    raise InstallError("too big")
                with open(e.path, "rb") as fin, open(os.path.join(d, e.name), "xb") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
                report["files"] += 1
                report["bytes"] += st.st_size
            else:
                report["skipped"].append({"name": r[:200], "why": "not_regular"})

    try:
        walk(src, dest, "")
    except InstallError:
        pass
    return report


def _within(root: str, path: str) -> bool:
    r = os.path.normcase(os.path.realpath(root)).rstrip("\\/")
    p = os.path.normcase(os.path.abspath(path))
    return p == r or p.startswith(r + os.sep)


def remove_tree(path: str, *, allowed_roots: list[str]) -> None:
    """Delete ``path`` without ever following a link, only inside ``allowed_roots``."""
    if not os.path.lexists(path):
        return
    if not any(_within(r, path) and os.path.normcase(os.path.abspath(path)) !=
               os.path.normcase(os.path.realpath(r)) for r in allowed_roots):
        raise InstallError("outside the allowed folders")
    sa = webapps._optional("puffpai.security.safe_archive")
    if sa is not None and hasattr(sa, "safe_rmtree"):
        sa.safe_rmtree(path, allowed_roots=allowed_roots)
        return

    def rm(p: str) -> None:
        if os.path.islink(p):
            os.unlink(p)
            return
        if webapps.is_link(p):              # a junction: the link itself only
            os.rmdir(p) if os.path.isdir(p) else os.unlink(p)
            return
        if os.path.isdir(p):
            with os.scandir(p) as it:
                entries = list(it)
            for e in entries:
                rm(e.path)
            os.rmdir(p)
            return
        try:
            os.chmod(p, stat.S_IWRITE)
        except OSError:
            pass
        os.remove(p)

    rm(path)


# ---------------------------------------------------------------------------
# Drawbridge's static_web rows
# ---------------------------------------------------------------------------

def friendly_name(name: str, source: dict, title: str = "") -> str:
    """The name shown on the dashboard: never one that borrows the bot's own words."""
    def fine(text: str) -> bool:
        from puffpai import artifact_review
        return bool(text) and not _NAME_BANNED.search(text) and not rc._orders(text) \
            and not rc.BANNED_WORDS.search(text) and not artifact_review.reads_like_orders(text)

    from puffpai import app_registry, artifact_review

    def shown_ok(text: str) -> bool:
        # the same rule the app list uses for a browser app's name, so a name
        # chosen here is never one the manifest check then refuses
        return app_registry._display_text(text, app_registry.MAX_WEB_NAME_LEN)

    limit = min(60, app_registry.MAX_WEB_NAME_LEN)
    for cand in (name, title):
        # an emoji family or flag is joined by zero-width joiners; the joiner
        # is dropped (the emoji stay), every other hidden character refuses
        # the name below
        raw = " ".join(str(cand or "").replace("\u200d", "").split())[:limit].strip()
        if not fine(raw):
            continue
        cleaned = artifact_review.neutralise_label(raw, limit).strip()
        # the cleaner changes a name only when something in it was not plain
        if cleaned == raw and fine(cleaned) and not re.search(r"[<>\[\]{}`\\]", cleaned) \
                and shown_ok(cleaned):
            return cleaned
    repo = str((source or {}).get("repo") or (source or {}).get("url") or "")
    repo_name = repo.rstrip("/").rsplit("/", 1)[-1]
    if re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo_name or ""):
        # a long repository name is cut to what the app list can show
        repo_name = repo_name[:limit].rstrip("._-")
        if repo_name and not _NAME_BANNED.search(repo_name) and shown_ok(repo_name):
            return repo_name
    return "Web app"


#: Words that aim an instruction-shaped sentence at a model, a tool or the
#: owner's yes, never at the person reading the app.
_AIMED_AT_A_MODEL = re.compile(
    r"\b(?:ai|a\.i\.|assistant|model|llms?|chat\s?bots?|bot|agent|system\s+(?:prompt|message)|"
    r"prompt|instructions?|tools?|function|already\s+(?:said|agreed|approved|confirmed)|"
    r"said\s+yes|pre-?approved|without\s+asking|remove_all|delete_all|owner)\b", re.I)


def static_web_rows(facts: dict) -> list[dict]:
    """Checklist rows (pass/fail with a code) for one static app."""
    sc = facts.get("scan") or {}
    copy = facts.get("copy") or {}
    source = facts.get("source") or {}
    rows: list[dict] = []

    def row(code: str, ok: bool, why: str = "") -> None:
        rows.append({"code": code, "result": "pass" if ok else "fail", "why": why[:200]})

    row("too_big", not copy.get("too_big"), "over 200 MB or 20,000 files" if copy.get("too_big") else "")
    links = [s for s in copy.get("skipped") or [] if s.get("why") in ("link", "not_regular")]
    row("not_regular", not links, f"{len(links)} links or special files" if links else "")
    row("no_entry", bool(facts.get("has_entry")), "" if facts.get("has_entry") else "no index.html")
    calls_purpose = bool(_CALLS.search(str(facts.get("purpose") or "")))
    row("direct_connections", not sc.get("direct_connections") or calls_purpose,
        "makes direct connections the Bridge cannot see" if sc.get("direct_connections") and not calls_purpose else "")
    texts = [str(sc.get("title") or "")] + [str(t) for t in sc.get("text_sample") or []]
    from puffpai import artifact_review
    # The app's own screen text is prose: an example address in it ("z.B.
    # 192.168.10.2:8083 oder https://host:8083/fhem", EasyTiley) is not an
    # order. Orders aimed at a model still count; where
    # the app may connect is checked by its own rows (hosts, CSP).
    # A privacy notice ("You must not
    # upload personal data about third parties ...") is prose for the person
    # using the app. An instruction-shaped sentence counts only when it is
    # aimed at a model or tool, or claims a yes (fail closed for those).
    orders = [t for t in texts if t and (artifact_review.reads_like_orders(t, prose=True)
                                         or (rc._orders(t) and _AIMED_AT_A_MODEL.search(t)))]
    row("orders", not orders, "text that reads like orders" if orders else "")
    planned = {str(h).lower() for h in source.get("runtime_hosts") or []}
    new_hosts = [h for h in sc.get("needs_internet_while_running") or [] if h not in planned]
    row("runtime_hosts_new", not new_hosts, ", ".join(new_hosts[:5]))
    parts = list(sc.get("parts_to_save") or [])
    # passes only when nothing is left to load from outside (the saved copies
    # are the app's own files now), leaving out lettering (fonts): the CSP
    # stops those loads anyway and the app shows other lettering, so a font
    # never holds an app (Google Fonts, for one)
    fonts = {str(h.get("host")) for h in sc.get("hosts") or []
             if isinstance(h, dict) and h.get("for") == "fonts"}
    parts = [p for p in parts if p not in fonts]
    row("parts_needed", not parts, ", ".join(parts[:5]))
    # A page with no script and no controls is a document, not a tool. A tool
    # that stores nothing of its own (a board, a timer, dice) is still a tool:
    # it opens inside the bot, there is just nothing for the bot to do in it.
    is_tool = bool(sc.get("keeps_data") or sc.get("interactive"))
    row("not_a_tool", is_tool, "" if is_tool else "a page with nothing to use")
    mode = source.get("mode")
    row("mode_changed", mode in (None, "", "static_web"), f"planned as {mode}" if mode not in (None, "", "static_web") else "")
    return rows


def decide(rows: list[dict], *, allowed_holds: frozenset = frozenset()) -> str:
    failed = {r["code"] for r in rows if r["result"] != "pass"}
    if failed & BLOCK_CODES:
        return "block"
    if (failed & HOLD_CODES) - set(allowed_holds):
        return "hold"
    return "allow"


def drawbridge_rows(draft: dict) -> list[dict]:
    """Rows for Drawbridge's ``static_web`` checker."""
    rows = (draft or {}).get("rows")
    if not isinstance(rows, list):
        rows = static_web_rows(draft or {})
    return [{"code": r.get("code"), "result": r.get("result")} for r in rows]


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _source_facts(source: dict) -> dict:
    keep = {}
    for k in ("repo", "url", "commit", "license", "sha256", "archive_sha256", "mode"):
        v = (source or {}).get(k)
        if isinstance(v, str) and len(v) <= 300:
            keep[k] = v
    return keep


#: Joins an emoji family or flag; dropped, the emoji stay.
_JOINER = chr(0x200D)
#: A zero-width space marks a word break; read as a space.
_ZERO_WIDTH_SPACE = chr(0x200B)


def blurb(purpose: str, name: str) -> str:
    """The line shown under the app's name: the plan's purpose, cleaned by
    the same rule the manifest check uses (plain printable text on one line),
    so a manifest written here is never then refused. Hidden characters
    (direction overrides, zero-width marks) are dropped, spaces of every kind
    become one plain space, and a purpose that is empty after that, or reads
    like orders, gives the app's name instead."""
    import unicodedata
    from puffpai import app_registry, artifact_review
    out = []
    for ch in str(purpose or ""):
        cat = unicodedata.category(ch)
        if ch == _JOINER:
            continue
        if ch.isspace() or cat[0] == "Z" or ch == _ZERO_WIDTH_SPACE:
            out.append(" ")
        elif cat[0] != "C":
            out.append(ch)
    text = " ".join("".join(out).split())[:120].strip()
    shown = app_registry._display_text(text, app_registry.MAX_UI_BLURB_LEN)
    if shown and not rc.BANNED_WORDS.search(text) and not rc._orders(text) \
            and not artifact_review.reads_like_orders(text):
        return text
    return name


def _manifest(app_id: str, name: str, purpose: str, source: dict, runtime_hosts: list[dict]) -> dict:
    src = _source_facts(source)
    return {
        "schema": "puffpai_app/v1", "id": app_id, "name": name,
        "blurb": blurb(purpose, name),
        "kind": "web",
        "web": {"source": {k: src.get(k, "") for k in ("repo", "commit", "license", "sha256")},
                "files_root": os.path.dirname(webapps.web_dir(app_id)), "entry": "index.html",
                "storage": "mirror"},
        "outside": [{"host": h["host"], "for": h["for"], "learned": "scan"} for h in runtime_hosts],
        "ui": {"route": f"/dashboard/apps/{app_id}"},
    }


def _host_rows(sc: dict) -> list[dict]:
    out = []
    for h in sc.get("hosts") or []:
        if h.get("needed_while_running"):
            out.append({"host": h["host"], "for": _scan.FOR_WORDS.get(h.get("for") or "", _scan.FOR_WORDS[""]),
                        "state": "off"})
    return out


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

#: Toolsmith's order for which runtime sites get the app's two Bridge places
#: (data first, then signing in, maps, anything else).
_DOOR_ORDER = {"api": 0, "auth": 1, "maps": 2, "": 3, "fonts": 4, "cdn": 5}


def _register_door(app_id: str, sc: dict) -> list[str]:
    """Give the app its place on the Bridge for the sites it needs while running.

    Every site starts off; the owner switches each one on with a yes. At most
    two sites (the Bridge's bound for an app); the rest stay unreachable.
    Nothing happens when the Bridge's browser-app part is not in this build.
    """
    ed = webapps._optional("puffpai.security.egress_doors")
    reg = getattr(ed, "register_web_app_door", None) if ed is not None else None
    if not callable(reg):
        return []
    wanted = [h for h in sc.get("hosts") or [] if h.get("needed_while_running")]
    wanted.sort(key=lambda h: (_DOOR_ORDER.get(str(h.get("for") or ""), 3), h.get("host")))
    names = [str(h["host"]) for h in wanted][:2]
    if not names:
        return []
    try:
        reg(app_id, names)
    except ValueError:
        return []
    return names


def _close_door(app_id: str) -> None:
    ed = webapps._optional("puffpai.security.egress_doors")
    off = getattr(ed, "set_app_web_hosts", None) if ed is not None else None
    if callable(off):
        try:
            off(app_id, [], by="owner")
        except Exception:  # noqa: BLE001 - the app's address is gone anyway
            pass


def _rescan_registry() -> None:
    """Let the dashboard's app list see the added or removed app now."""
    try:
        from puffpai import app_registry
        app_registry.rescan()
    except Exception:  # noqa: BLE001 - the next rescan picks it up
        pass


# ---------------------------------------------------------------------------
# learning what the bot can do in an app: progress lines and a time cap
# ---------------------------------------------------------------------------

#: The longest Toolsmith spends learning one app. What is not tried by then
#: is left out (never allowed), and the owner is told how far it got. A
#: safety stop, not a speed-up: the wedding app needs about 7 minutes for all
#: 19 things, and a 4-minute cap left out its "Remove a guest".
LEARN_CAP_S = 600.0
#: At most one progress line this often while learning.
PROGRESS_EVERY_S = 15.0


class _TimeUp(RuntimeError):
    """The learning time is over: the hidden browser is asked nothing more."""


class _Learning:
    """Counts what was tried, says plain progress lines, and ends learning at
    :data:`LEARN_CAP_S`."""

    def __init__(self, say: Callable[[str], None], cap_s: Optional[float] = None) -> None:
        self.say = say if callable(say) else (lambda _line: None)
        self.cap = LEARN_CAP_S if cap_s is None else float(cap_s)
        self.start = self.last = time.monotonic()
        self.total = self.tried = 0
        self.stopped = False
        self.told_found = False

    def over(self) -> bool:
        if not self.stopped and time.monotonic() - self.start >= self.cap:
            self.stopped = True
        return self.stopped

    def _line(self, line: str, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last >= PROGRESS_EVERY_S:
            self.last = now
            self.say(line)

    def form(self, done: int, total: int) -> None:
        self._line(wording.LEARN_FORMS.format(i=done + 1, n=total))

    def button(self, done: int, total: int) -> None:
        self._line(wording.LEARN_BUTTONS.format(i=done + 1, n=total))

    def found(self, n: int) -> None:
        more = max(0, int(n))
        self.total += more
        if not self.told_found and self.total:
            self.told_found = True
            self._line(wording.LEARN_FOUND.format(n=self.total), force=True)
        elif self.told_found and more:
            # More things turned up after "found 12": say
            # so once, and stop giving a count whose end keeps moving.
            self.grew = True
            self._line(wording.LEARN_FOUND_MORE.format(
                n=more, things="thing" if more == 1 else "things"), force=True)

    def tried_one(self) -> None:
        self.tried += 1
        if getattr(self, "grew", False) or self.tried > self.total:
            self._line(wording.LEARN_TRIED_SO_FAR.format(i=self.tried))
        else:
            self._line(wording.LEARN_TRIED.format(i=self.tried, n=self.total))

    def done(self, allowed: int) -> None:
        if self.stopped:
            self._line(wording.learn_stopped_line(max(1, int(round(self.cap / 60))), self.tried,
                                                  max(self.total, self.tried)), force=True)
        self.say(f"Drawbridge checked {self.tried} things the bot could do in the app; "
                 f"{allowed} passed.")


#: How long a fresh copy of the app may write its own entries before they are kept.
EXAMPLES_SETTLE_S = 1.5


def _keep_examples(app_id: str, drv: Any, pool: Any) -> int:
    """Open one fresh throwaway copy, let the app write what it writes by
    itself, and keep those entries as the app's own examples."""
    from puffpai.webapps import examples
    label = pool.take()
    try:
        drv.open(label, fresh=True)
        time.sleep(EXAMPLES_SETTLE_S)
        storage = (drv.storage(label) or {}).get("localStorage") or {}
    finally:
        drv.close(label)
    return examples.save(app_id, storage)


class _TimedDriver:
    """The hidden browser, asked nothing more once the learning time is
    over (closing a page is always allowed, so nothing is left open)."""

    def __init__(self, driver: Any, clock: _Learning) -> None:
        self._driver = driver
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._driver, name)
        if name == "close" or not callable(attr):
            return attr

        def call(*a: Any, **kw: Any) -> Any:
            if self._clock.over():
                raise _TimeUp()
            return attr(*a, **kw)
        return call


def _check_and_compile(app_id: str, sc: dict, progress: Callable[[str], None]) -> dict:
    from puffpai.webapps import host, runner_host
    if sc.get("indexeddb_only"):
        return {"drivable": False, "line": wording.NOT_DRIVABLE, "recipes": []}
    if not sc.get("keeps_data"):
        # nothing stored means nothing to add, change or read: no compiling
        return {"drivable": False, "line": wording.KEEPS_NOTHING, "recipes": []}
    ok, line = runner_host.availability()
    if not ok:
        return {"drivable": False, "line": line, "recipes": []}
    if host.runner_secret() is None:
        return {"drivable": False, "line": wording.RUNNER_FAILED, "recipes": []}
    sealing = recipes.sealing_available()
    sha = rc.app_sha(webapps.web_dir(app_id))
    kept: list[dict] = []
    clock = _Learning(progress)
    try:
        pool = runner_host.CopyPool(app_id)
    except runner_host.RunnerError as exc:
        return {"drivable": False, "line": str(exc) or wording.RUNNER_FAILED, "recipes": []}
    drv = _TimedDriver(pool.driver, clock)
    try:
        _keep_examples(app_id, drv, pool)
    except Exception:  # noqa: BLE001 - without them the bot only says less
        pass
    if not sealing:
        # nothing compiled could be sealed, so nothing would ever load: fail closed
        pool.close()
        return {"drivable": False, "line": wording.NO_SEAL_KEY, "recipes": []}
    own_keys: set = set(sc.get("localStorage_keys") or [])
    found: dict = {"compiled": [], "reads": [], "skipped": []}
    row_skipped: list = []

    def check(rec: dict, **kw: Any) -> None:
        v = rc.check_recipe(rec, own_keys=own_keys, current_app_sha=sha, driver=drv,
                            new_copy=pool.take, keeps_mirror=True, **kw)
        rec["checked"] = {k: v[k] for k in ("verdict", "effect", "effect_proven", "undoable",
                                            "rows", "rows_sha", "recipe_sha", "app_sha", "at",
                                            "checker_version")}
        kept.append(rec)
        clock.tried_one()

    def learn() -> None:
        found.update(compile_form.compile_all(drv, pool.take, on_form=clock.form))
        for c in found["compiled"]:
            own_keys.update(c.get("written_keys") or [])
        clock.found(len(found["compiled"]) + len(found["reads"]))
        adds: dict[tuple, dict] = {}
        for c in found["compiled"]:
            if clock.over():
                return
            rec = recipes.stamp(c["recipe"], sha)
            check(rec, test_args=c["tests"])
            if rec["checked"]["verdict"] == "allow":
                adds.setdefault((c["storage_key"], c["list"]), c)
        # reading a list back comes before the row controls (the slowest part),
        # so a time cap never leaves the bot unable to read what it added
        for c in found["reads"]:
            if clock.over():
                return
            add = adds.get((c["storage_key"], c["list"]))
            rec = recipes.stamp(c["read"], sha)
            seed = None
            if add is not None:
                body, tests = add["recipe"], add["tests"]
                seed = (lambda label, body=body, tests=tests: drv.run(label, body, tests))
            check(rec, test_args={}, seed=seed)
        # the controls on the list rows each allowed add form fills
        taken = {c["recipe"]["name"] for c in found["compiled"]} | \
            {c["read"]["name"] for c in found["reads"]}
        # buttons that change one stored number or yes/no (a counter, a
        # switch): quick to try, so before the row controls
        buttons = compile_form.compile_buttons(drv, pool.take, taken=taken,
                                               on_button=clock.button)
        row_skipped.extend(buttons["skipped"])
        for c in buttons["compiled"]:
            own_keys.update(c.get("written_keys") or [])
            taken.add(c["recipe"]["name"])
        taken |= {c["read"]["name"] for c in buttons["reads"]}
        clock.found(len(buttons["compiled"]) + len(buttons["reads"]))
        first: dict[str, dict] = {}
        for c in buttons["compiled"]:
            if clock.over():
                return
            rec = recipes.stamp(c["recipe"], sha)
            check(rec, test_args={})
            if rec["checked"]["verdict"] == "allow":
                first.setdefault(c["storage_key"], c["recipe"])
        for c in buttons["reads"]:
            if clock.over():
                return
            body = first.get(c["storage_key"])
            seed = (lambda label, body=body: drv.run(label, body, {})) if body else None
            check(recipes.stamp(c["read"], sha), test_args={}, seed=seed)
        for add in adds.values():
            if clock.over():
                return
            rows = compile_rows.compile_rows(drv, pool.take, add, taken=taken)
            row_skipped.extend(rows["skipped"])
            clock.found(len(rows["compiled"]))
            for r in rows["compiled"]:
                if clock.over():
                    return
                rec = recipes.stamp(r["recipe"], sha)
                seed = (lambda label, s=r["seed"]: s(drv, label))
                check(rec, test_args=r["tests"], seed=seed)

    try:
        try:
            learn()
        except _TimeUp:
            clock.stopped = True
        # a check the time cut short never ends in allow (it blocks), and
        # what was never tried is simply not there
        allowed = [r for r in kept if r["checked"]["verdict"] == "allow"]
        clock.done(len(allowed))
        recipes.save(app_id, sha, kept)
        return {"drivable": bool(allowed), "line": "" if allowed else wording.NOT_DRIVABLE,
                "recipes": kept, "skipped": (found.get("skipped") or []) + row_skipped,
                "stopped": clock.stopped}
    finally:
        pool.close()


def install_static(app_id: str, files_dir: str, *, source: dict, name: str, purpose: str,
                   jobs: list[str], progress: Callable[[str], None]) -> dict:
    if not webapps.valid_app_id(app_id):
        raise ValueError("app_id must be an id the caller issued")
    say = progress if callable(progress) else (lambda _line: None)
    src = os.path.realpath(files_dir)
    if not os.path.isdir(src):
        raise ValueError("files_dir is not a folder")
    from puffpai.webapps import host
    rec = host.read_record(app_id)
    if rec is not None and rec.get("status") == "installed":
        raise ValueError("that app id is already installed")
    if app_id in host.removed_ids():
        # an address is never used for a second app (its old data may linger
        # in a browser that has not loaded the goodbye page yet)
        raise ValueError("that app id was removed; ids are never used again")
    root = webapps.files_root()
    app_root = os.path.join(root, app_id)
    web = webapps.web_dir(app_id)
    if _within(web, src) or _within(src, app_root):
        raise ValueError("files_dir overlaps the app's own folder")
    if os.path.lexists(app_root):
        remove_tree(app_root, allowed_roots=[root])
    os.makedirs(app_root, exist_ok=True)
    say("Toolsmith is copying the app's files.")
    copy = copy_tree(src, web)
    sc = _scan.scan(web) if not copy["too_big"] else {}
    friendly = friendly_name(name, source, sc.get("title") or "")
    facts = {"scan": sc, "copy": copy, "source": source, "purpose": purpose,
             "has_entry": os.path.isfile(os.path.join(web, "index.html"))}
    rows = static_web_rows(facts)
    vendored = None
    vendor = webapps._optional("puffpai.webapps.vendor")
    first_failed = {r["code"] for r in rows if r["result"] != "pass"}
    # Save the parts once when nothing else holds the app: the ones it needs
    # (else it is held) and the fonts (a font that cannot be saved holds nothing)
    if sc.get("parts_to_save") and vendor is not None \
            and not first_failed - {"parts_needed"}:
        # Save the parts it loads from the internet once (after the owner's yes
        # to adding it, through the audited exit), then look at the app again:
        # the saved copies are checked like the rest of its files.
        vendored = vendor.save_parts(app_id, web, progress=say)
        sc = _scan.scan(web)
        facts = dict(facts, scan=sc, parts_saved=bool(vendored.get("ok")))
        rows = static_web_rows(facts)
    allowed_holds = frozenset(str(x) for x in (source or {}).get("allowed_holds") or [])
    verdict_word = decide(rows, allowed_holds=allowed_holds)
    rows_sha = hashlib.sha256(rc.canonical(rows).encode("utf-8")).hexdigest()
    verdict = {"verdict": verdict_word, "rows": rows, "rows_sha": rows_sha,
               "checker_version": CHECKER_VERSION,
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    runtime_hosts = _host_rows(sc)
    record = {"app_id": app_id, "name": friendly, "purpose": str(purpose or "")[:200],
              "jobs": [str(j)[:80] for j in (jobs or [])][:12], "source": _source_facts(source),
              "entry": "index.html", "status": "checking",
              "scan": {k: sc.get(k) for k in ("hosts", "features", "localStorage_keys", "keeps_data",
                                               "indexeddb_only", "direct_connections", "links",
                                               "network_calls_with_runtime_address")},
              "verdict": verdict, "drivable": False,
              "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rec_path = os.path.join(webapps.app_state_dir(app_id), "web.json")
    base = {"app_id": app_id, "origin": host.origin_for(app_id), "actions": [], "abilities": [],
            "hosts": runtime_hosts, "verdict": verdict, "drivable": False}
    if verdict_word != "allow":
        failed = {r["code"] for r in rows if r["result"] != "pass"}
        if verdict_word == "block":
            line = wording.BLOCKED
        elif failed & HOLD_CODES == {"parts_needed"}:
            line = wording.PARTS_NOT_SAVED if vendored is not None else wording.PARTS_NEEDED
        else:
            line = wording.HELD
        record["status"] = "blocked" if verdict_word == "block" else "held"
        _write_json(rec_path, record)
        remove_tree(app_root, allowed_roots=[root])
        host.refresh()
        say(line)
        return dict(base, lines=[line])
    _write_json(rec_path, record)
    _write_json(os.path.join(webapps.apps_state_dir(app_id), "app.json"),
                _manifest(app_id, friendly, purpose, source, runtime_hosts))
    say("Drawbridge checked the app. Toolsmith is finding what your bot can do in it.")
    result = _check_and_compile(app_id, sc, say)
    record.update(status="installed", drivable=bool(result["drivable"]),
                  app_sha=recipes.current_app_sha(app_id))
    _write_json(rec_path, record)
    host.refresh()
    _register_door(app_id, sc)
    _rescan_registry()
    from puffpai.webapps import actions, abilities
    acts = [a for a in actions.list_actions() if a["integration"] == app_id]
    lines = [wording.added_line(friendly, len(acts))]
    if not result["drivable"]:
        lines.append(result["line"] or wording.NOT_DRIVABLE)
    for line in lines:
        say(line)
    return dict(base, actions=acts, abilities=abilities.says_for(app_id), lines=lines,
                drivable=bool(result["drivable"]))


def uninstall(app_id: str) -> None:
    """Forget the app: its address stops serving it, its data is cleared from
    the bot's hidden browser, and its files, state and actions are removed.

    The address then answers only ``/__puffpai/bye`` (``Clear-Site-Data``),
    which the dashboard loads in a hidden frame so the owner's own browser
    forgets what the app kept there too; a runner started later clears the
    address from its profile as well (:func:`host.removed_ids`).
    """
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    from puffpai.webapps import host, runner_host
    # First: nothing serves it any more, its actions are gone, and every
    # outside site it had is switched off.
    host.mark_removed(app_id)
    host.refresh()
    _close_door(app_id)
    r = runner_host.current()
    if r is not None:
        try:
            r.call("clear", timeout=20, label=app_id)
            r.call("close", timeout=20, label=app_id)
        except runner_host.RunnerError:
            pass
    from puffpai.config import get_state_dir
    state = get_state_dir()
    remove_tree(webapps.apps_state_dir(app_id), allowed_roots=[os.path.join(state, "apps")])
    remove_tree(webapps.app_state_dir(app_id), allowed_roots=[webapps.state_root()])
    root = webapps.files_root()
    remove_tree(os.path.join(root, app_id), allowed_roots=[root])
    host.refresh()
    _rescan_registry()


__all__ = ["install_static", "uninstall", "static_web_rows", "decide", "friendly_name",
           "copy_tree", "remove_tree", "drawbridge_rows", "BLOCK_CODES", "HOLD_CODES"]
