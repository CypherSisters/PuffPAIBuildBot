"""Setup: ONE list of everything the chosen parts need, for every job.

Rows come from what was picked (never from what was dropped):

* programs and packages a skill or tool needs (:func:`puffpai.deps.make_plan`
  over :func:`puffpai.deps.read`, so only what is missing here),
* keys: a skill's own keys, a data source that needs a sign-up key, a job
  that needs an account key,
* settings: the home town, once, for every job that needs a place,
* outside addresses: a tool server's address (added after the yes), a browser
  app's "maybe" hosts ("Allow <app> to reach <host>"),
* switches: the Bridge switch each data source sits behind.

The same row for two jobs is one row with both job ids. Keys and settings
need the owner's own input; the rest is done by her one yes.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional

from puffpai.build import schema as S
from puffpai.build import classify, sources
from puffpai.build.classify import hosts_of, skill_keys
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Rows only the owner can complete (a value she types). The rest her yes covers.
OWNER_INPUT = frozenset({"key", "setting"})
#: Rows her one Yes switches on (only the ones shown on the list).
YES_SWITCHES = frozenset({"switch", "address"})
HOME_ROW = "setting-home-town"
#: The service id of a website from BuildBot's own list (catalogs.api_seeds).
SEED_PREFIX = "seed:"


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60] or "x"


def _home_set() -> bool:
    try:
        from puffpai import home_location
        return home_location.is_set()
    except Exception:  # noqa: BLE001
        return False


def origin_of(c: dict) -> Optional[dict]:
    """The publisher a found skill comes from, the way the installer will lock
    it (``finding.install._skill_source``), or None. A ClawHub find with no
    owner in its id (``clawhub:news``) has no known publisher yet, so none of
    its siblings is offered (and make installs none: it installs exactly the
    rows listed here)."""
    ref = str(c.get("ref") or "")
    if ref.startswith("clawhub:"):
        owner, _, slug = ref.split(":", 1)[1].rpartition("/")
        return {"source": "clawhub", "owner": owner, "slug": slug} if owner and slug else None
    if ref.startswith("skills.sh:"):
        bits = ref.split(":", 1)[1].split("/", 2)
        if len(bits) == 3 and all(bits):
            return {"source": "github", "repo": f"{bits[0]}/{bits[1]}", "folder": bits[2]}
    return None


def _skill_plan(c: dict) -> dict:
    """deps.make_plan for one skill candidate: its missing steps and keys.
    Sibling skills come only through the one publisher rule
    (:func:`puffpai.deps.siblings_offered`): exactly what the installer
    accepts for this publisher."""
    from puffpai import deps
    folder = str(c.get("folder") or "")
    try:
        if folder:
            needs = deps.read_skill(folder)
        else:
            needs = deps.read(text=c.get("skill_md") or "", origin=origin_of(c))
        return deps.make_plan(folder, thing=f"build-{_slug(c['ref'])}", needs=needs)
    except Exception as exc:  # noqa: BLE001 - unknown needs: nothing listed
        logger.info("build setup: needs not read (%s)", type(exc).__name__)
        return {"steps": [], "manual": [], "keys": []}


#: BuildBot's own switches a list may show, in the owner's words.
PLAIN_SWITCH = {"sample_look": "Take one look at each website first"}


def _named(name: str, c: dict) -> str:
    """A part's name with where it comes from ("Route, by acme"), so two
    finds with the same name never read as one."""
    ref = str(c.get("ref") or "")
    who = ""
    if ref.startswith(("clawhub:", "skills.sh:")):
        body = ref.split(":", 1)[1]
        who = body.rsplit("/", 1)[0] if "/" in body else ""
    elif ":" in ref:
        who = ref.split(":", 1)[1].split("/", 1)[0] if "/" in ref else ""
    return f"{name}, by {who}" if who and who.lower() != str(name).lower() else str(name)


def _seed_needed() -> tuple:
    """The switches making a live card needs besides the website's own
    (``assemble.seed_switch.NEEDED``), named on the list so her one Yes
    covers them too."""
    try:
        from puffpai.assemble import seed_switch
        return tuple(seed_switch.NEEDED)
    except Exception:  # noqa: BLE001
        return ()


def _outside_on(sid: str) -> bool:
    try:
        from puffpai import outside_services
        row = outside_services.row(sid)
        return bool(row) and row.get("state") == "on"
    except Exception:  # noqa: BLE001
        return False


def _outside_row(sid: str) -> Optional[dict]:
    try:
        from puffpai import outside_services
        return outside_services.row(sid)
    except Exception:  # noqa: BLE001
        return None


# ── what make does, named on the list (make refuses anything else) ──────────
#
# Every install, tool connection, app add and app start make performs maps to
# a row of the one setup list, marked ``make``; make.make checks the row is
# on the list her Yes covers before it acts (without it, a tool could be
# built with no row).

#: ``row["make"]``: add (and start) an app as it is.
MAKE_APP = "app"
#: ``row["make"]``: connect a tool server (an address or a command).
MAKE_MCP = "mcp"
#: ``row["make"]``: install a found skill.
MAKE_INSTALL = "install"
#: ``row["make"]``: install the mcp package tool servers need (one row).
MAKE_SDK = "sdk"
#: ``row["make"]``: download a private project of hers through her GitHub
#: account, check it, and add it when the checks pass (github_account).
MAKE_PRIVATE = "private_repo"
#: ``row["make"]``: Toolsmith makes a small app from a code project of the
#: job (``job["code"]``, build.apps) when no ready-made app runs.
MAKE_CODE = "make_code"
#: Code projects Toolsmith makes an app from, at most, per job.
MAKE_TRIES = 2
MAKES = (MAKE_APP, MAKE_MCP, MAKE_INSTALL, MAKE_SDK, MAKE_PRIVATE, MAKE_CODE)
SDK_ROW = "package-mcp-sdk"


def _sdk_row(add: Any, job: dict, ref: str) -> None:
    """Tool servers need the mcp package; when it is not on this computer,
    ONE row offers it (her Yes covers installing it)."""
    if classify._mcp_sdk():
        return
    add("package", "mcp-sdk", "Install the mcp package tool servers need",
        "From PyPI, the 1.x version BuildBot is made for, checked by Drawbridge first.",
        job, step="pip", name="mcp", make=MAKE_SDK, ref=ref)


def _with_licence_notes(detail: str, refs: list, parts: dict) -> str:
    """The row's words plus one plain licence note per app on it whose
    licence (as its list or code site shows it, before anything is
    downloaded) is missing or limits sharing. Running it for herself is
    never stopped over its licence (``integrate.run_licence``); the files'
    own licence is read again after the download."""
    try:
        from puffpai.integrate import run_licence
    except Exception:  # noqa: BLE001
        return detail
    notes = []
    for ref in refs:
        c = parts.get(ref) or {}
        if str(c.get("source") or "") not in ("awesome-selfhosted", "github", "codeberg"):
            continue
        line = run_licence.note_for_reported(c.get("licences") or c.get("licence") or "")
        if line:
            notes.append(f"{c.get('name') or ref}: {line}")
    text = " ".join([detail] + [n for n in notes if n not in detail])
    return text


#: The switch app downloads go under (``integrate.fetch.SERVICE``).
DOWNLOADS = "code_download"
#: A program an app needs that is not on this computer: (row, what to do).
NEEDS_WORDS = {
    "docker": ("Docker, to run {name}",
               "{name} runs only with Docker. BuildBot does not install Docker: install "
               "Docker Desktop yourself, then press Check again."),
    "node": ("Node.js, to run {name}",
             "{name} runs with Node.js, which BuildBot did not find. Install Node.js 22 or "
             "newer, run BuildBot's installer again, then press Check again."),
}


#: The same rows when BuildBot can install the program itself.
INSTALL_WORDS = {
    "docker": ("Install Docker Desktop, to run {name}",
               "{name} runs in its own containers. BuildBot installs Docker Desktop after your "
               "yes and starts it. {note}"),
    "node": ("Install Node.js, to run {name}",
             "{name} runs with Node.js. BuildBot installs it after your yes. {note}"),
}


def _can_install(prog: str) -> bool:
    if prog not in INSTALL_WORDS:
        return False
    try:
        from puffpai import deps
        return deps.can_install(prog)
    except Exception:  # noqa: BLE001
        return False


def _note(prog: str) -> str:
    try:
        from puffpai import deps
        return deps.install_note(prog)
    except Exception:  # noqa: BLE001
        return ""


def _app_needs(c: dict, prepared: dict) -> list[str]:
    """Programs an app needs that this computer does not have (from its
    checked run plan, or, before that, from what the app list says)."""
    missing = [str(p) for p in prepared.get("needs") or [] if str(p) in NEEDS_WORDS]
    if not missing and classify.needs_docker(c):
        missing = ["docker"]       # a program's name for the list, never started from here
    return missing


def _hers(c: dict) -> bool:
    try:
        from puffpai import github_account
        return github_account.is_hers(c)
    except Exception:  # noqa: BLE001
        return False


def _on_this_computer(c: dict) -> bool:
    name = str(c.get("folder") or c.get("skill") or "")
    if not name:
        return False
    try:
        from puffpai import skills
        return skills.find(name) is not None
    except Exception:  # noqa: BLE001
        return False


def _skill_text(c: dict) -> str:
    for key in ("skill_md", "text"):
        text = c.get(key)
        if isinstance(text, str) and text.startswith("---"):
            return text
    return ""


def _mcp_of(c: dict) -> tuple[str, str]:
    """``(url, command)`` make would connect for a tool part (one is "")."""
    mcp = c.get("mcp")
    facts = c.get("facts") if isinstance(c.get("facts"), dict) else {}
    if not mcp and isinstance(facts.get("mcp_url"), str) and facts["mcp_url"]:
        return str(facts["mcp_url"]), ""
    if isinstance(mcp, dict):
        if mcp.get("url"):
            return str(mcp["url"]), ""
        command = mcp.get("command")
        return "", (str(command) if command and command != "stdio" else "")
    if isinstance(mcp, str) and mcp:
        return (mcp, "") if mcp.lower().startswith(("http://", "https://")) else ("", mcp)
    if isinstance(mcp, list) and mcp:
        return "", " ".join(str(x) for x in mcp)
    if c.get("type") == "skill" and c.get("class") == S.TOOL:
        how = _skill_how(c)
        if how["kind"] == "remote" and how["endpoint_kind"] == "mcp" and how["endpoint"]:
            return str(how["endpoint"]), ""
    return "", ""


def _skill_how(c: dict) -> dict:
    from puffpai import skills
    text = _skill_text(c)
    if text:
        return skills.how_it_runs_text(text, "")
    name = str(c.get("folder") or c.get("skill") or "")
    return skills.how_it_runs(name) if name else skills.how_it_runs_text("")


def _installs(c: dict) -> bool:
    """Will make install this found skill (route B)? A guide or book skill
    the bot reads, or a command line skill, that is not on this computer
    yet; never one keyed request (a data source)."""
    if _on_this_computer(c) or classify.key_request(c):
        return False
    cls = c.get("class")
    if cls in (S.GUIDE, S.TEXT):
        return True
    url, command = _mcp_of(c)
    return cls == S.TOOL and not url and not command and _skill_how(c)["kind"] == "cli"


# ── the pictures a website's card shows: listed before the Yes ──────────────

MEDIA_SCHEMA = "build_media_hosts/v1"
MEDIA_MAX = 6


def media_path() -> str:
    import os

    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "build", "media_hosts.json")


def _read_media() -> dict:
    from puffpai import fsutil
    data = fsutil.read_json(media_path(), default=None)
    if isinstance(data, dict) and data.get("schema") == MEDIA_SCHEMA \
            and isinstance(data.get("refs"), dict):
        return data
    return {"schema": MEDIA_SCHEMA, "refs": {}}


def known_media_hosts(ref: str) -> list[str]:
    """The picture hosts a sample of *ref* showed before (kept per part)."""
    got = (_read_media()["refs"].get(str(ref)) or {})
    return [h for h in got.get("hosts") or [] if isinstance(h, str)] \
        if isinstance(got, dict) else []


def note_media_hosts(ref: str, hosts: list[str], *, looked: bool = False) -> None:
    """Keep the picture hosts a sample of *ref* showed, so the next list
    names them before her Yes. Never raises."""
    import os
    try:
        from puffpai import fsutil
        from puffpai.security import egress_doors
        data = _read_media()
        old = data["refs"].get(str(ref)) if isinstance(data["refs"].get(str(ref)), dict) else {}
        merged = list(dict.fromkeys(
            [h for h in old.get("hosts") or [] if isinstance(h, str)]
            + [str(h).lower() for h in hosts if egress_doors.is_registrable_hostname(
                str(h).lower())]))[:MEDIA_MAX]
        data["refs"][str(ref)] = {"hosts": merged, "looked": bool(looked or old.get("looked"))}
        os.makedirs(os.path.dirname(media_path()), exist_ok=True)
        fsutil.atomic_write_json(media_path(), data)
    except Exception as exc:  # noqa: BLE001 - listed at the Yes instead
        logger.info("build setup: picture hosts not kept (%s)", type(exc).__name__)


def _seed_row(c: dict) -> Optional[dict]:
    service = str(c.get("service") or c.get("ref") or "")
    if not service.startswith(SEED_PREFIX):
        return None
    try:
        from puffpai.assemble import seed_switch
        return seed_switch._row(service[len(SEED_PREFIX):])  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None


def _template_host(value: Any) -> str:
    try:
        host = (urllib.parse.urlsplit(str(value or "")).hostname or "").lower()
    except ValueError:
        return ""
    from puffpai.security import egress_doors
    return host if egress_doors.is_registrable_hostname(host) else ""


def _looked_media(ref: str, row: dict) -> list[str]:
    """One sample look at a website from our own list, at plan time, ONLY
    when her "looking at a service before adding it" switch is on and the
    bot may go online: the picture hosts its card could show. Kept, so it is
    looked at once. [] otherwise. Never raises."""
    if (_read_media()["refs"].get(ref) or {}).get("looked"):
        return []
    try:
        from puffpai import add_more, feed_generator
        from puffpai.catalogs import api_seeds
        from puffpai.security import egress_doors
        if egress_doors.sample_look_held() or not add_more.online_allowed():
            return []
        cand = feed_generator.check_candidate(api_seeds.to_candidate(row))
        _t, _p, look_at = feed_generator.card_address(cand, feed_generator.topic_for("", cand))
        doc = feed_generator._look(cand, look_at,  # noqa: SLF001 - the Bridge's one look
                                   build_online=add_more.online_allowed())
        hosts = [str(o.get("image_host")).lower() for o in feed_generator.card_options(cand, doc)
                 if o.get("image_host") and o.get("image_host") != cand["host"]]
        hosts += [h for h in picture_hosts(doc) if h != cand["host"]]
    except Exception as exc:  # noqa: BLE001 - not looked: the Yes checks again
        logger.info("build setup: no sample look (%s)", type(exc).__name__)
        return []
    note_media_hosts(ref, hosts, looked=True)
    return hosts


def picture_hosts(document: Any, limit: int = 400) -> list[str]:
    """Every picture host in a sample reply, across ALL its items (a gallery
    whose pictures come from a different site per item lists each once, so
    later items do not keep adding rows). Reads only."""
    from puffpai import feed_generator
    out: list[str] = []
    seen = [0]

    def walk(node: Any, key: str) -> None:
        if seen[0] > limit or len(out) >= MEDIA_MAX:
            return
        seen[0] += 1
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif feed_generator._looks_like_image(node, key):  # noqa: SLF001 - one rule
            h = _template_host(str(node).strip())
            if h and h not in out:
                out.append(h)
    walk(document, "")
    return out


def seed_hosts(c: dict) -> list[str]:
    """Every host a website from our own list makes its card reach: its own,
    the picture host its row declares, and the picture hosts a sample showed
    (at plan time, or at an earlier Yes)."""
    row = _seed_row(c) or {}
    ref = str(c.get("ref") or c.get("service") or "")
    out = [str(h).lower() for h in hosts_of(c)]
    if row.get("host"):
        out.insert(0, str(row["host"]).lower())
    for key in ("image_template", "card_url", "lookup_url"):
        out.append(_template_host(row.get(key) or c.get(key)))
    out += known_media_hosts(ref)
    if row:
        out += _looked_media(ref, row)
    return [h for h in dict.fromkeys(out) if h][:MEDIA_MAX]


def card_hosts(c: dict, service: str) -> list[str]:
    """Every host a switch for an existing live card opens (its Bridge row's
    websites), with the part's own hosts first."""
    out = [str(h).lower() for h in hosts_of(c)]
    row = _outside_row(service) if service and not service.startswith(SEED_PREFIX) else None
    if row and row.get("kind") == "card":
        out += [str(h).lower() for h in row.get("websites") or [] if "." in str(h)]
    return [h for h in dict.fromkeys(out) if h]


#: What a data source's switch row covers besides its own sites (her yes to
#: it lets pictures its own results point to through the Bridge's picture
#: door: egress_doors.result_picture_get).
PICTURES_TOO = ", and the picture sites its results point to"


def _reach(hosts: list[str]) -> str:
    if len(hosts) <= 1:
        return hosts[0] if hosts else "its own site"
    return ", ".join(hosts[:-1]) + " and " + hosts[-1]


def readme_words(c: dict) -> str:
    """What the app's own README says to run ("" when it was not read)."""
    steps = [str(s) for s in c.get("readme_steps") or [] if s][:8]
    if not steps:
        return ""
    port = c.get("readme_port")
    opened = f", then open localhost:{port}" if port else ""
    return (f"Its README says to run: {'; '.join(steps)}{opened}. BuildBot runs it that "
            "way in the app's own folder, on a free local port.")


#: ``row["make"]``: Toolsmith may run these exact commands beyond an app's
#: folder (build.make.APPROVE); ``row["admin"]`` names the ones that run with
#: her admin yes (the normal UAC or sudo prompt).
MAKE_APPROVE = "approve"


def _approve_row(rows: dict, job: dict, c: dict, ref: str, apps: list[dict],
                 needs: list[str]) -> None:
    """Up front, before her one Yes: everything its README says it needs
    beyond its own folder (``c["beyond"]``, read when it was found), on the
    ONE row "Let Toolsmith set up <App> beyond its own folder". Only that
    app's own README: a backup that needs more asks on its own row when it
    is tried (``make.ask_approval``). Programs another row already installs
    are not named twice."""
    from puffpai.build import make, toolsmith_run
    items: list[dict] = []
    for a in apps:
        for i in a.get("beyond") or []:
            if not isinstance(i, dict) or not i.get("what"):
                continue
            tool = str(i.get("tool") or "")
            if i.get("kind") == "tool" and toolsmith_run.TOOL_PROGRAM.get(
                    tool, tool.lower()) in needs:
                continue
            if all(x.get("what") != i.get("what") for x in items):
                items.append(i)
    if not items:
        return
    rid = make.approve_row_id(ref)
    name = str(c.get("name") or ref)
    row = rows.get(rid)
    detail = toolsmith_run.beyond_words(name, items)[:4000]
    # Only a command she can read in full on the row is covered by her yes.
    commands = [str(i["command"]).strip() for i in items
                if i.get("command") and str(i["command"]).strip() in detail]
    admin = [c for c in commands if toolsmith_run.needs_admin(c)]
    if row is None:
        rows[rid] = {"id": rid, "kind": "program", "job_ids": [job["id"]], "done": False,
                     "label": f"Let Toolsmith set up {name} beyond its own folder",
                     "detail": detail,
                     "make": MAKE_APPROVE, "ref": ref, "items": items[:40],
                     "commands": commands, "admin": admin}
    elif job["id"] not in row["job_ids"]:
        row["job_ids"].append(job["id"])


def make_code_words(code: list[dict]) -> tuple[str, str]:
    """``(label, detail)`` of the one row for making an app from code."""
    first = code[0]
    label = (f"If no ready-made app runs, Toolsmith makes one from {first['full_name']} "
             f"({first.get('kind') or 'a library'})")
    detail = ("Toolsmith downloads it, Drawbridge checks its code first, then Toolsmith "
              "writes a small page that does this job with it, in its own folder on this "
              "computer, and opens it in your browser.")
    if len(code) > 1:
        detail += f" If that does not work, it tries {code[1]['full_name']} the same way."
    return label, detail


def make_code_row(add: Any, job: dict, code: list[dict]) -> None:
    """The job's one row her Yes covers for making an app from its code."""
    label, detail = make_code_words(code)
    row = add("program", f"make-from-{code[0]['full_name']}", label, detail, job,
              make=MAKE_CODE, ref=code[0]["ref"], refs=[c["ref"] for c in code])
    for c in code:
        if c["ref"] not in row["refs"]:
            row["refs"].append(c["ref"])
    add("switch", DOWNLOADS, "Turn on app downloads",
        "Only the apps on this list are downloaded, from where their makers "
        "keep them.", job, service=DOWNLOADS, done=_outside_on(DOWNLOADS))


def rows_for(jobs: list[dict], parts: dict[str, dict]) -> list[dict]:
    """The setup list for *jobs* (their picks and ingredients in *parts*)."""
    rows: dict[str, dict] = {}

    def add(kind: str, rkey: str, label: str, detail: str, job: dict, **extra: Any) -> dict:
        rid = f"{kind}-{_slug(rkey)}"
        row = rows.get(rid)
        if row is None:
            row = rows[rid] = {"id": rid, "kind": kind, "label": label, "detail": detail,
                               "job_ids": [], "done": False, **extra}
        else:
            for k, v in extra.items():
                if k == "make" and v and not row.get("make"):
                    row["make"] = v
        if job["id"] not in row["job_ids"]:
            row["job_ids"].append(job["id"])
        return row

    def source_switches(c: dict, ref: str, name: str, job: dict) -> None:
        # Every host the part CALLS gets its own place on the Bridge
        # (build.sources): one switch row per host, all named here.
        for host in sources.part_hosts(c):
            add("switch", sources.switch_id(host), f"Turn on {name}"
                + (f" ({host})" if len(sources.part_hosts(c)) > 1 else ""),
                f"{host}{PICTURES_TOO}.", job,
                service=sources.switch_id(host), host=host, hosts=[host], ref=ref,
                done=sources.is_on(host))

    for job in jobs:
        if job.get("route") == S.D:
            continue
        key_rows = 0
        first_app = True
        for ref in job.get("ingredients") or []:
            c = parts.get(ref)
            if not c or c.get("builtin"):
                continue
            name = c.get("name") or ref
            is_skill = c.get("type") == "skill"
            cls = c.get("class")
            if is_skill and job.get("route") == S.B and _installs(c):
                # make installs a found skill (the normal install, its checks).
                add("package", f"skill-install-{ref}", f"Install the {name} skill",
                    "BuildBot installs it the normal way, with its checks.", job,
                    make=MAKE_INSTALL, ref=ref)
            if cls == S.APP and job.get("route") == S.A and first_app:
                first_app = False
                prepared = c.get("prepared") if isinstance(c.get("prepared"), dict) else {}
                if c.get("private") and _hers(c):
                    add("program", f"private-{ref}", f"Use your private project {name}",
                        "BuildBot downloads it from your GitHub after your yes, Drawbridge "
                        "checks it, and it is added only when the checks pass. Your "
                        "token is never saved with it.", job, make=MAKE_PRIVATE, ref=ref)
                elif c.get("integration_id") or prepared.get("integration_id"):
                    runs = str(c.get("mode") or prepared.get("mode") or "") in (
                        "own_server", "server_app") or str(prepared.get("how") or "") in (
                        "node_server", "python_server", "compose")
                    add("program", f"app-{ref}", f"Add {name}"
                        + (" and start it on this computer" if runs else ""),
                        " ".join(x for x in (readme_words(c), "Exactly the copy that was "
                                             "checked. It opens on its own screen.") if x),
                        job, make=MAKE_APP, ref=ref)
                elif (c.get("check") or {}).get("later"):
                    # Downloaded and checked after the Yes; the next apps that
                    # fit are named here too, tried in order if it cannot start.
                    alts = [a for a in job.get("alternates") or [] if parts.get(a)]
                    row = add("program", f"app-{ref}", f"Download {name} and start it on "
                              "this computer", "Drawbridge checks it first; it is added only "
                              "when the checks pass.", job, make=MAKE_APP, ref=ref, refs=[ref])
                    # One row for an app several jobs use: every backup any of
                    # them may try is named on it (make tries only those).
                    for a in alts:
                        if a not in row["refs"]:
                            row["refs"].append(a)
                    names = [str((parts.get(a) or {}).get("name") or a) for a in row["refs"][1:]]
                    if names:
                        row["detail"] = ("Drawbridge checks it first; it is added only when "
                                         "the checks pass. If it cannot start here, BuildBot "
                                         f"tries {' and then '.join(names)} the same way.")
                    row["detail"] = _with_licence_notes(row["detail"], row["refs"], parts)
                    said = readme_words(c)
                    if said and said not in row["detail"]:
                        row["detail"] = f"{said} {row['detail']}"
                        row["commands"] = list(c.get("readme_steps") or [])
                    more = ("If it ships its own containers it runs in Docker. What it needs "
                            "to run here (Docker, Node.js, Python) is installed first; an "
                            "install that asks for an admin yes or a restart says so. "
                            "Toolsmith runs it as its README says, in the app's own folder.")
                    if more not in row["detail"]:
                        row["detail"] = f"{row['detail']} {more}"
                    _approve_row(rows, job, c, ref, [c],
                                 _app_needs(c, prepared))
                    add("switch", DOWNLOADS, "Turn on app downloads",
                        "Only the apps on this list are downloaded, from where their makers "
                        "keep them.", job, service=DOWNLOADS, done=_outside_on(DOWNLOADS))
                for prog in _app_needs(c, prepared):
                    if _can_install(prog):
                        # Installed after her one Yes, before the app.
                        add("program", f"needs-{prog}", INSTALL_WORDS[prog][0].format(name=name),
                            INSTALL_WORDS[prog][1].format(name=name, note=_note(prog)).strip(),
                            job, name=prog, ref=ref, install=prog)
                    else:
                        add("program", f"needs-{prog}", NEEDS_WORDS[prog][0].format(name=name),
                            NEEDS_WORDS[prog][1].format(name=name), job, manual=True, name=prog,
                            ref=ref)
            if is_skill and cls in (S.TOOL, S.GUIDE):
                plan = _skill_plan(c)
                for step in plan.get("steps") or []:
                    if step.get("kind") == "skill":
                        # A sibling skill from the same publisher: the one
                        # rule the installer uses, so it is offered only when
                        # make will really install it (and make installs
                        # only the ones listed here).
                        add("package", f"skill-{step['name']}",
                            f"The {step['name']} skill, from the same publisher",
                            f"{name} uses it.", job, step="skill", name=step["name"],
                            ref=ref)
                        continue
                    if cls != S.TOOL:
                        continue
                    kind = "package" if step.get("kind") in ("pip", "npm") else "program"
                    add(kind, step["name"], str(step.get("words") or step["name"]),
                        f"For {name}.", job, step=step["kind"], name=step["name"], ref=ref)
                if cls == S.TOOL:
                    for line in plan.get("manual") or []:
                        add("program", line.split(":", 1)[0], line.split(":", 1)[0],
                            line, job, manual=True, ref=ref)
                    for k in plan.get("keys") or []:
                        add("key", f"{c.get('folder') or ref}-{k['name']}",
                            f"Your {k['name']} key for {_named(name, c)}",
                            f"Get it at {k['get_url']}." if k.get("get_url") else
                            "It goes into your vault.", job, skill=str(c.get("folder") or ""),
                            name=k["name"], ref=ref, skill_key=True)
                        key_rows += 1
            if is_skill and cls == S.DATA:
                # A skill that is an outside service (a plain API, or one keyed
                # request to its own service): never a command. Every host it
                # calls, and its own key, kept in the vault.
                source_switches(c, ref, name, job)
                for k in skill_keys(c):
                    add("key", f"{c.get('folder') or ref}-{k}", f"Your {k} key for {_named(name, c)}",
                        f"It goes into your vault and is sent only to "
                        f"{' and '.join(sources.part_hosts(c)[:2]) or 'its own service'}.",
                        job, skill=str(c.get("folder") or ""), name=k, ref=ref, skill_key=True)
                    key_rows += 1
            mcp_url, mcp_command = _mcp_of(c)
            if mcp_url:
                host = (urllib.parse.urlsplit(mcp_url).hostname or mcp_url).lower()
                add("address", host, f"Connect to {name}", f"It reaches {host}.", job,
                    host=host, hosts=[host], mcp_url=mcp_url, ref=ref, make=MAKE_MCP)
                _sdk_row(add, job, ref)
            elif mcp_command:
                add("program", mcp_command, f"Run {name} on this computer",
                    f"It starts with: {mcp_command}", job, command=mcp_command, ref=ref,
                    make=MAKE_MCP)
                _sdk_row(add, job, ref)
            plan_mcp = (c.get("plan") or {}).get("interfaces") if isinstance(
                c.get("plan"), dict) else None
            if c.get("integration_id") and isinstance(plan_mcp, dict) and plan_mcp.get("mcp"):
                add("program", f"mcp-{ref}", f"Run {name} as a tool on this computer",
                    "Exactly the copy that was checked.", job, make=MAKE_MCP, ref=ref)
                _sdk_row(add, job, ref)
            for host in c.get("maybe_hosts") or []:
                add("address", f"{c.get('app_id') or ref}-{host}",
                    f"Allow {name} to reach {host}", "It asks for this while it runs.", job,
                    host=str(host), hosts=[str(host)], app_id=str(c.get("app_id") or ""),
                    ref=ref)
            if not is_skill and cls == S.DATA and sources.part_hosts(c):
                # A data source that is not a website from our own list.
                source_switches(c, ref, name, job)
            elif cls == S.DATA and not is_skill and hosts_of(c):
                service = str(c.get("service") or ref)
                hosts = seed_hosts(c) if service.startswith(SEED_PREFIX) \
                    else card_hosts(c, service)
                add("switch", service, f"Turn on {name}",
                    f"{_reach(hosts)}{PICTURES_TOO}.", job, service=service,
                    host=hosts[0], hosts=hosts, ref=ref, done=bool(c.get("switch_on")))
                if service.startswith(SEED_PREFIX):
                    # Adding a website from BuildBot's own list takes one look
                    # at it first: that switch is named here, so the one Yes
                    # never turns on anything the list did not show.
                    for sid, words in _seed_needed():
                        add("switch", sid, PLAIN_SWITCH.get(sid, f"Turn on {words}"),
                            "BuildBot takes one look at each website before adding it.",
                            job, service=sid, done=_outside_on(sid))
            if cls in (S.DATA, S.TOOL) and not is_skill \
                    and ("key" in (c.get("needs") or [])
                         or c.get("auth") not in (None, "", "none", "unknown")):
                add("key", c.get("service") or ref, f"Your key for {name}",
                    "Sign up on its site, then put the key here. It goes into your vault.", job,
                    service=str(c.get("service") or ref), ref=ref)
                key_rows += 1
        code = [c for c in job.get("code") or [] if isinstance(c, dict) and c.get("ref")
                and c.get("full_name")][:MAKE_TRIES]
        if job.get("route") == S.A and code:
            make_code_row(add, job, code)
        needs = job.get("needs") or []
        # The town: for a job about where she lives, and for any job whose
        # data source asks for it (a weather forecast for "watering").
        if ("place" in needs and job.get("route") != S.C) or _parts_need_place(job, parts):
            add("setting", "home-town", "Your town",
                "Type your town. Its rough position is looked up once through the Bridge "
                "(the town lookup); only the town is kept.", job, key="home",
                done=_home_set())
        # A job read as "needs an account" gets a key row only from a part
        # that really takes a key (above): a row no part uses could never be
        # filled in or used.
    out = list(rows.values())
    return out + _online_rows(out, parts)


def _online_rows(rows: list[dict], parts: dict[str, dict]) -> list[dict]:
    """"Let <Bot> go online" when jobs reach outside sites and the bot is set
    to stay off the internet (:mod:`puffpai.build.online`)."""
    reach = [jid for r in rows if r["kind"] in YES_SWITCHES or r.get("key") == "home"
             for jid in r["job_ids"]]
    if not reach:
        return []
    try:
        from puffpai.build import online
        if online.is_online() or not online.can_go_online():
            return []
        return [online.row_for(list(dict.fromkeys(reach)))]
    except Exception:  # noqa: BLE001 - unknown posture: no row
        return []


def her_part(row: dict) -> bool:
    """Does this row still need the owner's own input before her Yes? A
    row her Yes does (going online) does not; a town typed while the bot is
    still offline is looked up right after the Yes."""
    if row.get("kind") not in OWNER_INPUT or row.get("done") or row.get("by_yes"):
        return False
    if row.get("pending"):
        return False
    return not waits_for_install(row)


_PLACE_NEEDS = frozenset({"home_location", "location", "zip_code", "place"})


def _parts_need_place(job: dict, parts: dict[str, dict]) -> bool:
    """Does a data source this job puts together ask for where she lives? (A
    picture gallery of Earth does not, even when the job was read as "near
    me".)"""
    for ref in job.get("ingredients") or []:
        c = parts.get(ref) or {}
        if set(c.get("needs") or []) & _PLACE_NEEDS or "{lat" in str(c.get("url") or ""):
            return True
    return False


def also_needs_row(build: dict, ref: str, host: str, service: str = "") -> Optional[dict]:
    """The ONE row per source for sites it turned out to need after her Yes
    (a picture host, a redirect): "<job> also needs <host>". A second site
    for the same source joins that row (never a row per site). It is not
    part of what the Yes was for and is never switched on by it: it pauses
    only its own jobs (:func:`job_status` -> needs_setup) until she switches
    it on from the list, with its own ticket. Returns the row when it was
    added or grew, else None (the site is on the list already)."""
    from puffpai.security import egress_doors
    host = str(host or "").strip().rstrip(".").lower()
    if not egress_doors.is_registrable_hostname(host):
        return None
    jobs = [j for j in build.get("jobs") or [] if j.get("route") != S.D
            and ref in (j.get("ingredients") or [])]
    if not jobs:
        return None
    setup = build.setdefault("setup", [])
    for r in setup:
        if r.get("ref") == ref and not r.get("also") \
                and host in (r.get("hosts") or [r.get("host")]):
            return None
    rid = f"switch-also-{_slug(ref)}"[:100]
    row = next((r for r in setup if r.get("id") == rid), None)
    if row is None:
        row = {"id": rid, "kind": "switch", "job_ids": [j["id"] for j in jobs],
               "done": False, "service": service, "hosts": [], "ref": ref, "also": True}
        setup.append(row)
    elif host in (row.get("hosts") or []) and not row.get("done"):
        return None
    row["hosts"] = list(dict.fromkeys(list(row.get("hosts") or []) + [host]))[:MEDIA_MAX]
    row["host"] = row["hosts"][0]
    row["done"] = False
    shown = _reach(row["hosts"])
    row["label"] = f"{jobs[0]['name']} also needs {shown}"
    row["detail"] = (f"It turned out to need {shown} too. Only this job waits; switch it on "
                     f"here to let it through.")
    return row


def pending_also(job: dict, rows: list[dict]) -> list[str]:
    """The sites an "also needs" row still waits on for *job*."""
    out: list[str] = []
    for r in rows or []:
        if r.get("also") and not r.get("done") and job.get("id") in (r.get("job_ids") or []):
            out += [h for h in r.get("hosts") or [r.get("host")] if h and h not in out]
    return out


def also_reason(hosts: list[str]) -> str:
    return (f"It also needs {_reach(hosts)}, which was not on your list. Switch it on in "
            f"the setup list to let it through.")


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """New rows, keeping what was already done (and old rows for other jobs)."""
    done = {r["id"]: r for r in old}
    out = []
    for r in new:
        was = done.get(r["id"], {})
        if was.get("done"):
            r["done"] = True
        if r.get("skill_key") and not r.get("skill") and was.get("skill"):
            r["skill"] = was["skill"]         # installed after the Yes: its folder
        out.append(r)
    ids = {r["id"] for r in out}
    out += [r for r in old if r["id"] not in ids]
    return out


def waits_for_install(row: dict) -> bool:
    """A found skill's own key: it gets its place in the vault once make has
    installed the skill (after the Yes), so it cannot hold the job back
    before that; it is asked for on the list right after."""
    return row.get("kind") == "key" and bool(row.get("skill_key")) and not row.get("skill")


def job_status(job: dict, rows: list[dict]) -> str:
    """planned -> nothing_found / needs_setup / waiting_yes."""
    if job.get("route") == S.D:
        return S.NOTHING_FOUND
    mine = [r for r in rows if job["id"] in r.get("job_ids", [])]
    if any(her_part(r) for r in mine):
        return S.NEEDS_SETUP
    if pending_also(job, rows):
        return S.NEEDS_SETUP
    return S.WAITING_YES


def apply_statuses(build: dict, only_open: bool = True) -> None:
    """Set each job's status from its route and its rows (jobs already built
    or being built keep theirs)."""
    for job in build["jobs"]:
        if only_open and job.get("status") in (S.QUEUED, S.BUILDING, S.WORKING, S.NOT_YET):
            continue
        job["status"] = job_status(job, build["setup"])
        if job["status"] == S.NEEDS_SETUP:
            also = pending_also(job, build["setup"])
            owner_input = any(her_part(r) and job["id"] in r.get("job_ids", [])
                              for r in build["setup"])
            job["reason"] = also_reason(also) if also and not owner_input else \
                "Something on the setup list needs you first."
        elif job["status"] == S.WAITING_YES:
            job["reason"] = ""


__all__ = ["HOME_ROW", "MAKES", "MAKE_APP", "MAKE_APPROVE", "MAKE_CODE", "MAKE_INSTALL", "MAKE_MCP",
           "MAKE_TRIES", "make_code_row", "make_code_words",
           "MAKE_PRIVATE",
           "OWNER_INPUT",
           "MAKE_SDK", "SDK_ROW", "SEED_PREFIX", "YES_SWITCHES", "also_needs_row", "apply_statuses", "card_hosts",
           "also_reason", "her_part", "job_status", "known_media_hosts", "media_path", "merge",
           "pending_also", "picture_hosts",
           "note_media_hosts", "origin_of", "rows_for", "seed_hosts", "waits_for_install"]
