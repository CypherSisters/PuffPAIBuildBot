"""Add a ticked project: fetch, check, and only then add, under the plan's yes.

The plan's "Looks good" covers only what the plan showed (M8). For each
ticked ``code_project`` line:

1. The find is re-read from the server side (``find_parts.found_for``); the
   request's ``apply``, ``facts`` and ``requires`` are never read (S7).
2. It is downloaded at exactly the pinned commit (:mod:`.fetch`), sorted
   (:mod:`.classify`) and checked by Drawbridge (kind ``code_project``,
   :func:`code_project_rows`).
3. ``block`` -> ``refused``, nothing kept. ``hold``, or any plan fact that
   does not match (licence family, mode, size within +25 %, runtime hosts, the
   locked box) -> ``held`` with a new ``add_on`` ticket target
   ``held:<id>:<rows_sha[:16]>``; nothing of it runs until
   :func:`decide` hears a yes from a paired device. ``allow`` with matching
   facts -> added in its mode.

Modes: ``static_web`` through the app host
(``webapps.install.install_static``); ``web_build`` built only in the locked box
(the box driver: ``npm ci --ignore-scripts`` from the recorded lock, then the
``build`` script, then the exported files added like a static app);
``data_template`` (:mod:`.data_import`: a list app and passages);
``picture_set`` (:mod:`.pictures`). Server apps, command line tools,
libraries (``other``, ``cli_tool``), and ``web_build`` while the locked box is
not ready, run on this computer as their authors built them
(:mod:`.run_here`) after one yes that showed
the exact commands (stage ``host``). Each later stage (data, box lock, app,
host) can hold on its own and needs its own yes. Other parts that are
not in this build make the item end ``held`` or ``failed`` with "not in this
build yet".

One yes from the find screens: :func:`prepare_code_project` downloads and
checks a find and reads how it runs WITHOUT adding anything (status
``checked``); the yes card shows that; :func:`finish_prepared` then adds
exactly what the card showed (same files, same Drawbridge rows, same plan).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.integrate import classify, fetch, optional, records, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KIND = "code_project"
CHECKER_VERSION = "r3.2"
BLOCK_CODES = frozenset({"wrong_host", "not_pinned", "pin_changed", "no_license",
                         "not_open", "too_big", "orders", "tunnel", "hidden_loader"})
HOLD_CODES = frozenset({"license_mismatch", "size_over_plan", "scaffold",
                        "ai_unreviewed", "box_needed", "mode_changed"})
#: Modes that run the project on her own computer (a page, a built page, a
#: command line tool, its own server): the personal-use licence rule applies
#: (``run_licence``). A data set or picture set copied into the bot keeps the
#: stricter licence rows.
RUN_MODES = frozenset({"static_web", "web_build", "cli_tool", "other"})
SCAFFOLD_CODE_BYTES = 20_000
SCAFFOLD_FEATURE_LINES = 5
SIZE_SLACK = 1.25
#: Where a hold can happen: the download's own check, the data check, the
#: locked-box lock check, the app host's check. A yes covers one stage only.
#: ``files``: the unpacked files are not the ones she said yes to (every
#: earlier yes is void, see :func:`files_fingerprint`).
STAGES = ("code", "data", "box", "app", "files", "host")
_NAME_BANNED = re.compile(r"puffpai|buildbot|drawbridge|vault|update", re.I)
_DECIDE_LOCK = threading.Lock()


def _src_root(rec: dict) -> str:
    """``extraction_root()/<id>/src``, recomputed from the id: the path kept
    in the record is shown, never trusted."""
    return fetch.src_dir(rec["id"])  # raises FetchRefused without the helper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_link(path: str) -> bool:
    # A symlink or a junction, read from the entry itself
    # (os.path.isjunction is not in Python 3.11).
    from puffpai.security import owner_folders
    return owner_folders.is_link(path)


def files_fingerprint(root: str) -> str:
    """One sha256 over every entry under *root*: each path, its kind, and the
    bytes of each regular file. A link is recorded by where it points (never
    followed), so adding, removing, renaming or changing ANY file changes it,
    whatever the checks think of the change (a new outgoing address filed as
    "an account you set up yourself" can pass every check). "" when the folder cannot be read, which never matches, so
    an unreadable folder is asked about again (fail closed)."""
    h = hashlib.sha256()
    try:
        if not os.path.isdir(root) or _is_link(root):
            return ""
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            names = sorted(dirnames) + sorted(filenames)
            dirs = set(dirnames)
            # Never walk into a link or a Windows junction: it is recorded by
            # where it points instead.
            dirnames[:] = sorted(d for d in dirnames
                                 if not _is_link(os.path.join(dirpath, d)))
            for name in names:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                st = os.lstat(full)
                if _is_link(full):
                    h.update(b"L\x00" + rel.encode("utf-8", "surrogatepass") + b"\x00"
                             + os.readlink(full).encode("utf-8", "surrogatepass") + b"\x00")
                elif name in dirs:
                    h.update(b"D\x00" + rel.encode("utf-8", "surrogatepass") + b"\x00")
                elif os.path.isfile(full):
                    fh_sha = hashlib.sha256()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            fh_sha.update(chunk)
                    h.update(b"F\x00" + rel.encode("utf-8", "surrogatepass") + b"\x00"
                             + str(st.st_size).encode() + b"\x00"
                             + fh_sha.hexdigest().encode() + b"\x00")
                else:
                    h.update(b"S\x00" + rel.encode("utf-8", "surrogatepass") + b"\x00")
    except (OSError, ValueError) as exc:
        logger.warning("integrate: the files could not be read for the yes check: %s",
                       type(exc).__name__)
        return ""
    return h.hexdigest()


def _files_now(rec: dict) -> str:
    try:
        return files_fingerprint(_src_root(rec))
    except Exception:  # noqa: BLE001 - not knowing is "changed"
        return ""


# ---------------------------------------------------------------------------
# the Drawbridge checklist for code_project (pure over the draft)
# ---------------------------------------------------------------------------

def _row(check: str, ok: Optional[bool], why: str, code: str) -> dict[str, str]:
    result = "pass" if ok else ("unknown" if ok is None else "fail")
    return {"check": check, "result": result, "why": why, "code": code}


def _note(check: str, why: str, code: str) -> dict[str, str]:
    return {"check": check, "result": "note", "why": why, "code": code}


def _license_rows(lic: dict, plan: dict) -> list[dict[str, str]]:
    """The licence rows of the code_project checklist."""
    rows: list[dict[str, str]] = []
    has_file = bool(lic.get("file"))
    rows.append(_row("Has a licence file", has_file,
                     "It has a licence file." if has_file
                     else "It has no licence file.", "no_license"))
    plan_family = fetch.family_of(plan.get("license"))
    read_family = str(lic.get("family") or "none")
    is_open = bool(lic.get("open")) or (bool(lic.get("nc")) and plan_family == "CC-BY-NC")
    rows.append(_row("Free to use", is_open if has_file else None,
                     f"Its licence: {lic.get('words') or 'not known'}.", "not_open"))
    if has_file:
        rows.append(_row("Licence matches the plan", read_family == plan_family,
                         wording.reason_words("license_mismatch",
                                              words=lic.get("words") or read_family)
                         if read_family != plan_family else "Same licence as the plan.",
                         "license_mismatch"))
    return rows


def code_project_rows(draft: dict) -> list[dict[str, str]]:
    """The ``code_project`` checklist. Reads only the
    draft (facts recorded from the download and the tree scan)."""
    from puffpai import artifact_review, drawbridge
    rows: list[dict[str, str]] = []
    src = draft.get("source") or {}
    got = draft.get("fetch") or {}
    plan = draft.get("plan") or {}
    lic = (draft.get("license") or {}).get("read") or {}
    scan = draft.get("scan") or {}
    refused = str(draft.get("fetch_refused") or "")

    host = str(src.get("host") or "").lower()
    archive_host = fetch.FORGES.get(host, ("", ""))[0]
    host_ok = bool(archive_host) and refused != "wrong_host" and (
        refused != "" or str(got.get("final_host") or "") == archive_host)
    rows.append(_row("Comes from the place the plan named", host_ok,
                     "Downloaded from the project's own site." if host_ok
                     else "It did not come from the project's own site.",
                     "wrong_host"))
    pinned = str(src.get("pinned_commit") or "")
    pin_ok = bool(re.fullmatch(r"[0-9a-f]{40}", pinned)) and refused != "not_pinned" and (
        refused != "" or str(got.get("commit") or "") == pinned)
    rows.append(_row("Exactly the version the plan showed", pin_ok,
                     "It is the version the plan showed." if pin_ok
                     else "It is not the version the plan showed.", "not_pinned"))
    expect = str(got.get("expected_sha256") or "")
    same = refused != "pin_changed" and (not expect or expect == str(got.get("sha256") or ""))
    rows.append(_row("Has not changed since it was checked", same,
                     "The download is the same as before." if same
                     else "The download changed since it was checked.", "pin_changed"))
    if refused and refused not in ("wrong_host", "not_pinned", "pin_changed"):
        rows.append(_row("Downloaded", False if refused == "too_big" else None,
                         "It could not be downloaded as the plan said.",
                         "too_big" if refused == "too_big" else "download"))
        return rows

    if draft.get("own_project") is True:
        # Her own GitHub project (owner = the account she connected): what
        # others may do with it is hers to decide, so the licence rows are
        # waived. Every code check below still runs.
        rows.append(_row(wording.OWN_PROJECT, True, wording.OWN_PROJECT_WHY, "own_project"))
    elif str(draft.get("mode") or "") in RUN_MODES and isinstance(
            (draft.get("license") or {}).get("run"), dict):
        # Her own computer, for herself: the licence is a
        # note, never a stop. Every code check below still runs.
        from puffpai.integrate import run_licence
        rows += run_licence.rows((draft.get("license") or {}).get("run"))
    else:
        rows += _license_rows(lic, plan)
    size = int(got.get("bytes") or 0)
    unpacked = int(draft.get("extracted_bytes") or 0)
    small = size <= fetch.MAX_ARCHIVE_BYTES and unpacked <= fetch.MAX_EXTRACTED_BYTES
    rows.append(_row("Not too big", small, "Its size is fine." if small
                     else "It is too big to add.", "too_big"))
    if small and max(size, unpacked) >= fetch.BIG_NOTE_BYTES:
        rows.append(_note("Disk space", f"It is big: about "
                          f"{max(size, unpacked) / (1024 ** 3):.1f} GB on disk.", "big"))
    plan_size = int(plan.get("size_bytes") or 0)
    if plan_size <= 0:
        rows.append(_row("Size matches the plan", None,
                         "The plan did not say how big it is.", "size_over_plan"))
    else:
        within = size <= plan_size * SIZE_SLACK
        rows.append(_row("Size matches the plan", within,
                         "About the size the plan said." if within else
                         wording.reason_words("size_over_plan",
                                              mb=f"{size / (1024 * 1024):.1f}"),
                         "size_over_plan"))
    code_bytes = int(scan.get("code_bytes") or 0)
    features = max(int(draft.get("feature_lines") or 0),
                   int(scan.get("readme_feature_lines") or 0))
    starters = list(scan.get("starters") or [])
    # A data or picture project is not an app claiming features: little code
    # is what it should have. The starter-file check still applies.
    data_like = str(draft.get("mode") or "") in ("data_template", "picture_set")
    thin = (code_bytes < SCAFFOLD_CODE_BYTES and features >= SCAFFOLD_FEATURE_LINES
            and not data_like) or bool(starters)
    rows.append(_row("Has real code for what it claims", not thin,
                     wording.reason_words("scaffold") if thin
                     else "It has code for what it says it does.", "scaffold"))
    name = str(draft.get("name") or "")
    description = str(draft.get("description") or "")
    readme_head = str(scan.get("readme_head") or "")[:2048]
    orders = (artifact_review.reads_like_orders(name)
              or artifact_review.reads_like_orders(name, prose=True)
              or artifact_review.reads_like_orders(description, prose=True)
              or drawbridge.instruction_shaped(description)
              or artifact_review.reads_like_orders(readme_head, prose=True))
    rows.append(_row("Words read as a description, not orders", not orders,
                     "Its words read like orders to a computer." if orders
                     else "Its words read as a description.", "orders"))
    ai = bool(scan.get("ai_notice")) or bool(classify._AI_NOTICE_RE.search(description))  # noqa: SLF001
    rows.append(_row("Checked by a person", not ai,
                     wording.reason_words("ai_unreviewed") if ai
                     else "Nothing says it was left unchecked.", "ai_unreviewed"))
    tunnel = bool(scan.get("tunnel"))
    rows.append(_row("No hidden connections", not tunnel,
                     "It has code that opens a hidden connection." if tunnel
                     else "No hidden connections found.", "tunnel"))
    hidden = bool(scan.get("hidden_loader"))
    rows.append(_row("No hidden code", not hidden,
                     "Its page hides encoded code that it unpacks and runs." if hidden
                     else "No hidden encoded code found.", "hidden_loader"))
    install = list(scan.get("install_scripts") or [])
    box_needed = bool(install) and not bool(plan.get("box"))
    rows.append(_row("Needs nothing the plan did not show", not box_needed,
                     wording.reason_words("box_needed") if box_needed
                     else "Nothing needs to be installed first.", "box_needed"))
    mode = str(draft.get("mode") or "")
    same_mode = mode == str(plan.get("mode") or "")
    rows.append(_row("The kind of project the plan said", same_mode,
                     "The kind of project the plan said." if same_mode
                     else wording.reason_words("mode_changed"), "mode_changed"))
    if draft.get("archived"):
        rows.append(_note("Still looked after", "Its author has stopped changing it.",
                          "archived"))
    return rows


# ---------------------------------------------------------------------------
# plan facts, names, the draft
# ---------------------------------------------------------------------------

def plan_facts_from(found: dict) -> dict[str, Any]:
    """The facts the plan line showed, re-derived from the server's find."""
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    details = found.get("details") if isinstance(found.get("details"), dict) else {}
    read = str(repo.get("license_read") or "")
    lic = read if fetch.family_of(read) not in ("none",) else str(repo.get("license_reported") or "")
    mode = str(found.get("mode_guess") or "")
    try:
        size = int(float(repo.get("size_kb") or 0) * 1024)
    except (TypeError, ValueError):
        size = 0
    hosts = [str(h).lower() for h in (details.get("runtime_hosts_guess") or [])
             if isinstance(h, str)][:20]
    return {"license": lic, "mode": mode, "size_bytes": max(0, size),
            "runtime_hosts": hosts, "box": mode in ("web_build", "cli_tool")}


def friendly_name(found: dict) -> str:
    """The name to show: the find's friendly name unless it could mislead."""
    from puffpai import artifact_review
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    raw_fallback = str(repo.get("name") or found.get("name") or "An app")
    fallback = artifact_review.neutralise_label(raw_fallback, 60) or "An app"
    if artifact_review.label_orders(raw_fallback) or artifact_review.reads_like_orders(fallback) \
            or artifact_review.reads_like_orders(fallback.replace("-", " ").replace("_", " ")):
        fallback = "An app"
    raw = str(found.get("friendly") or "")
    name = artifact_review.neutralise_label(raw, 60)
    if not name or _NAME_BANNED.search(name) or artifact_review.reads_like_orders(name) \
            or artifact_review.label_orders(raw):
        return fallback
    return name


def _own_project(found: dict) -> bool:
    """Her own GitHub project: marked by puffpai.github_account from GitHub's
    own answer AND its owner is the account she connected, checked again now.
    Fails closed."""
    if not isinstance(found, dict) or found.get("own_project") is not True:
        return False
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    if str(repo.get("host") or "") != "github.com":
        return False
    try:
        from puffpai import github_account
        return github_account.is_own(repo.get("owner"))
    except Exception:  # noqa: BLE001
        return False


def _description(found: dict) -> str:
    return " ".join(str(found.get("what_it_is") or "").split())[:300]


def _purpose(found: dict) -> str:
    """One plain line about what it is, safe to show (never orders)."""
    from puffpai import artifact_review
    text = artifact_review.neutralise_label(_description(found), 120)
    return "" if (artifact_review.reads_like_orders(text)
                 or artifact_review.reads_like_orders(text, prose=True)) else text


def build_draft(rec: dict, *, got: dict, lic: dict, mode: str, scan: dict,
                description: str, feature_lines: int, archived: bool,
                refused: str = "", run_lic: Optional[dict] = None) -> dict[str, Any]:
    """The Drawbridge draft for one download. Everything a verdict depends on."""
    src = rec.get("from") or {}
    url = str(src.get("url") or "")
    m = re.fullmatch(r"https://([a-z0-9.-]+)/([^/]+)/([^/]+?)/?", url)
    return {
        "kind": KIND, "id": rec["id"], "name": rec.get("name") or "",
        "source": {"host": m.group(1) if m else "", "owner": m.group(2) if m else "",
                   "name": m.group(3) if m else "",
                   "pinned_commit": str(src.get("commit") or "")},
        "fetch": {k: got.get(k) for k in ("url", "commit", "sha256", "bytes",
                                          "final_host", "expected_sha256")},
        "fetch_refused": refused,
        "extracted_bytes": int(got.get("extracted_bytes") or 0),
        "license": {"reported": src.get("license_reported") or "", "read": lic,
                    **({"run": run_lic} if isinstance(run_lic, dict) else {})},
        "plan": rec.get("plan_facts") or {},
        "mode": mode,
        "scan": {k: scan.get(k) for k in (
            "code_bytes", "starters", "tunnel", "hidden_loader", "install_scripts",
            "readme_head",
            "readme_feature_lines", "ai_notice", "entry_html")},
        "feature_lines": int(feature_lines or 0),
        "description": description,
        "archived": bool(archived),
        "own_project": src.get("own_project") is True,
    }


def _stash_path(integration_id: str) -> str:
    return os.path.join(records.folder(integration_id), "check.json")


def _save_stash(integration_id: str, stash: dict) -> None:
    fsutil.atomic_write_json(_stash_path(integration_id), stash)


def _load_stash(integration_id: str) -> dict:
    data = fsutil.read_json(_stash_path(integration_id), default={})
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# the applier
# ---------------------------------------------------------------------------

def _progress_of(ctx: Any) -> Callable[[str], None]:
    fn = getattr(ctx, "progress", None)
    if callable(fn):
        def _say(line: str) -> None:
            try:
                fn(line)
            except Exception:  # noqa: BLE001 - a progress screen never sinks the build
                pass
        return _say
    return lambda _line: None


def _online(ctx: Any) -> bool:
    value = getattr(ctx, "online", None)
    if isinstance(value, bool):
        return value
    try:
        from puffpai import add_more
        return bool(add_more.online_allowed())
    except Exception:  # noqa: BLE001 - not knowing is "offline"
        return False


def _by(ctx: Any) -> str:
    via = str(getattr(ctx, "via", "") or "")
    if not via:
        return "wizard"
    return "chat" if via == "chat" else "add_page"


def _jobs(ctx: Any, need: Optional[str]) -> list[str]:
    shared = getattr(ctx, "shared", None)
    needs = (shared or {}).get("needs") if isinstance(shared, dict) else None
    jobs = (needs or {}).get("jobs") if isinstance(needs, dict) else None
    out = [str(j.get("name") or "")[:80] for j in (jobs or [])
           if isinstance(j, dict) and j.get("id") == need and j.get("name")]
    return out[:4]


def _result(status: str, line: str, lines: list[str], rec: Optional[dict] = None,
            **extra: Any) -> dict[str, Any]:
    out = {"status": status, "line": line, "lines": list(lines)}
    if rec is not None:
        out["integration_id"] = rec["id"]
        if status in ("added", "held"):
            out["undo"] = dict(rec.get("undo") or {})
        if rec.get("app_id"):
            out["app_id"] = rec["app_id"]
    out.update(extra)
    return out


def _cleanup(integration_id: str) -> None:
    """Remove the unpacked files and the download of a refused item."""
    try:
        os.remove(fetch.download_path(integration_id))
    except OSError:
        pass
    safe_archive = optional("puffpai.security.safe_archive")
    rm = getattr(safe_archive, "safe_rmtree", None) if safe_archive else None
    if rm is None:
        return
    root = safe_archive.extraction_root()
    path = os.path.join(root, integration_id)
    if os.path.lexists(path):
        try:
            rm(path, allowed_roots=[root])
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrate: could not clean up %s: %s", integration_id,
                           type(exc).__name__)


def _fail(rec: dict, line: str, lines: list[str],
          say: Callable[[str], None]) -> dict[str, Any]:
    """Something unexpected went wrong part way: whatever of it was made is
    taken back out and the record ends ``failed`` (never left ``checking``
    or ``building``). Fail closed."""
    for undo in (lambda: _uninstall(rec["app_id"]) if rec.get("app_id") else None,
                 lambda: _undo_data(rec) if rec.get("list_app_id") or rec.get("book_id")
                 else None,
                 lambda: _remove_box_project(rec)):
        try:
            undo()
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrate: clean-up after a failure: %s", type(exc).__name__)
    rec["status"] = "failed"
    records.save(rec)
    _cleanup(rec["id"])
    say(line)
    return _result("failed", f"{rec['name']}: {line}", lines, rec)


def _ticket_target(integration_id: str, sha: str) -> str:
    from puffpai.security import consent_tickets
    fn = getattr(consent_tickets, "target_held", None)
    if fn is None:
        return ""
    try:
        return str(fn(integration_id, sha))
    except Exception:  # noqa: BLE001
        return ""


def _hold(rec: dict, reasons: list[str], sha: str, words: str, lines: list[str],
          say: Callable[[str], None], *, stage: str, hosts: list[str] = (),
          codes: list[str] = (), line: str = "") -> dict:
    """Hold the item for her yes. What she is asked about is kept with it:
    the stage and fingerprint (the ticket target), the files as they are now
    (a yes covers exactly these files) and, for the app stage, the app
    host's own hold codes (the only ones her yes lets the app host pass)."""
    target = _ticket_target(rec["id"], sha)
    rec["status"] = "held"
    rec["held"] = {"reasons": list(reasons)[:12], "ticket_target": target}
    stash = _load_stash(rec["id"])
    stash["held"] = {"stage": stage, "sha": sha, "hosts": sorted(set(hosts)),
                     "files": _files_now(rec), "codes": sorted(set(codes))}
    _save_stash(rec["id"], stash)
    records.save(rec)
    line = line or wording.HELD.format(reason=words)
    say(line)
    tail = wording.HELD_WAITING if target else wording.NOT_IN_BUILD
    say(tail)
    return _result("held", f"{line} {tail}", lines, rec)


def _hold_words(verdict: dict, lic: dict, got: dict) -> str:
    rows = {r.get("code"): r for r in verdict.get("results") or []}
    first = next(iter(verdict.get("reasons") or []), "")
    if first == "license_mismatch":
        return wording.reason_words("license_mismatch",
                                    words=lic.get("words") or lic.get("family") or "")
    if first == "size_over_plan":
        if rows.get("size_over_plan", {}).get("result") == "unknown":
            return wording.REASON_OTHER
        return wording.reason_words("size_over_plan",
                                    mb=f"{int(got.get('bytes') or 0) / (1024 * 1024):.1f}")
    return wording.reason_words(first)


def apply_code_project(item: dict, ctx: Any, *, prepare: bool = False) -> dict[str, Any]:
    """Plan applier for ``code_project`` (factory I-6 and add_more).

    Returns ``{"status": "added|held|refused|failed", "line", "lines"}`` plus
    ``integration_id``, ``undo``, ``app_id`` when there is one.
    """
    say_out = _progress_of(ctx)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        say_out(line)

    label = str(item.get("label") or item.get("id") or "This project")[:120]
    find_parts = optional("puffpai.wizard.find_parts")
    found_for = getattr(find_parts, "found_for", None) if find_parts else None
    if found_for is None:
        return _result("failed", f"{label}: {wording.NOT_IN_BUILD}", lines)
    found = found_for(str(item.get("id") or ""))
    if not isinstance(found, dict):
        return _result("failed", f"{label}: BuildBot could not find that one any "
                                 f"more. Nothing was changed.", lines)
    name = friendly_name(found)
    if not _online(ctx):
        return _result("failed", f"{name}: {wording.OFFLINE}", lines)
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    meaning = found.get("meaning") if isinstance(found.get("meaning"), dict) else {}
    need = str(meaning.get("job") or "") or None
    iid = records.new_id(name)
    rec = records.blank(iid, name=name, purpose=_purpose(found),
                        by=_by(ctx), need=need)
    rec["from"].update({"source": str(found.get("source") or "")[:20],
                        "url": str(found.get("url") or "")[:300],
                        "commit": str(repo.get("pinned_commit") or "")[:40],
                        "license_reported": str(repo.get("license_reported") or "")[:60]})
    rec["plan_facts"] = plan_facts_from(found)
    rec["from"]["own_project"] = _own_project(found)
    records.save(rec)
    say(wording.ADDING.format(name=name))
    details = found.get("details") if isinstance(found.get("details"), dict) else {}
    feature_lines = len(details.get("feature_lines") or [])
    try:
        return _fetch_and_check(rec, found=found, feature_lines=feature_lines,
                                say=say, lines=lines, jobs=_jobs(ctx, need), prepare=prepare)
    except Exception as exc:  # noqa: BLE001 - one item never stops the plan
        logger.warning("integrate: adding failed: %s", type(exc).__name__)
        return _fail(rec, wording.SET_UP_FAILED, lines, say)


def _fetch_and_check(rec: dict, *, found: dict, feature_lines: int,
                     say: Callable[[str], None], lines: list[str],
                     jobs: list[str], prepare: bool = False) -> dict[str, Any]:
    from puffpai import drawbridge
    iid, name = rec["id"], rec["name"]
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    # A find fetched before at this
    # same pinned commit must be byte for byte what was downloaded then.
    expect = _first_download_sha(found, iid)
    try:
        got = fetch.fetch(iid, found, expect_sha256=expect)
    except fetch.FetchRefused as exc:
        if exc.code == "not_in_build":
            rec["status"] = "held"
            rec["held"] = {"reasons": ["not_in_build"], "ticket_target": ""}
            records.save(rec)
            return _result("held", f"{name}: {wording.NOT_IN_BUILD}", lines, rec)
        if exc.code in ("wrong_host", "not_pinned", "pin_changed", "too_big"):
            draft = build_draft(rec, got={"expected_sha256": expect or ""}, lic={}, mode="",
                                scan={},
                                description=_description(found),
                                feature_lines=feature_lines,
                                archived=bool(repo.get("archived")), refused=exc.code)
            verdict = drawbridge.check_addition(draft, by="toolsmith")
            return _refuse(rec, verdict, lines, say)
        rec["status"] = "failed"
        records.save(rec)
        _cleanup(iid)
        return _result("failed", f"{name}: {wording.DOWNLOAD_FAILED}", lines, rec)
    when = wording.month_words(str(repo.get("pushed_at") or ""))
    say(wording.DOWNLOADED.format(when=when, size=wording.size_words(got["bytes"]))
        if when else wording.DOWNLOADED_NO_DATE.format(size=wording.size_words(got["bytes"])))
    got["expected_sha256"] = expect or ""
    rec["from"].update({"archive_sha256": got["sha256"], "fetched_at": got["fetched_at"],
                        "commit": got["commit"]})
    rec["status"] = "checking"
    rec["files_root"] = os.path.dirname(got["src_dir"])
    records.save(rec)
    _save_stash(iid, {"description": _description(found),
                      "feature_lines": int(feature_lines),
                      "archived": bool(repo.get("archived")),
                      "got": {k: got.get(k) for k in ("url", "commit", "sha256", "bytes",
                                                      "final_host", "extracted_bytes",
                                                      "expected_sha256")},
                      "jobs": list(jobs)})
    return _check_and_continue(rec, say=say, lines=lines, accepted=None, prepare=prepare)


_SHA_HEX = re.compile(r"[0-9a-f]{64}")


def _first_download_sha(found: dict, iid: str) -> Optional[str]:
    """The sha-256 of the first download of this same find (its address at
    this same pinned commit) by another record of this bot, or None when it
    was never downloaded before. Never raises."""
    try:
        repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
        url = str(found.get("url") or "")[:300]
        commit = str(repo.get("pinned_commit") or "").lower()[:40]
        if not url or not re.fullmatch(r"[0-9a-f]{40}", commit):
            return None
        for other in records.all():
            src = other.get("from") if isinstance(other.get("from"), dict) else {}
            sha = str(src.get("archive_sha256") or "").lower()
            if other.get("id") != iid and src.get("url") == url \
                    and str(src.get("commit") or "").lower() == commit and _SHA_HEX.fullmatch(sha):
                return sha
    except Exception:  # noqa: BLE001 - no earlier download known
        return None
    return None


def _check(rec: dict) -> tuple[dict, dict, str, dict, dict]:
    """(verdict, lic, mode, scan, got) for the unpacked tree, recomputed now."""
    from puffpai import drawbridge
    stash = _load_stash(rec["id"])
    got = dict(stash.get("got") or {})
    src = _src_root(rec)
    lic = fetch.read_license(src, identity=fetch.identity_of(rec))
    mode, scan = classify.classify_tree(src)
    rec["from"]["license_read"] = lic.get("family") or ""
    run_lic = None
    if mode in RUN_MODES:
        from puffpai.integrate import run_licence
        run_lic = run_licence.read(src, reported=(rec.get("from") or {}).get("license_reported"))
        lic = dict(lic, run=run_lic)
        rec["from"]["license_read"] = (run_lic.get("name") if run_lic["kind"] == "open"
                                       else run_lic["kind"])[:60]
    draft = build_draft(rec, got=got, lic=lic, mode=mode, scan=scan,
                        description=str(stash.get("description") or ""),
                        feature_lines=int(stash.get("feature_lines") or 0),
                        archived=bool(stash.get("archived")), run_lic=run_lic)
    verdict = drawbridge.check_addition(draft, by="toolsmith")
    return verdict, lic, mode, scan, got


def _record_verdict(rec: dict, verdict: dict) -> None:
    rec["drawbridge"] = {"verdict": str(verdict.get("verdict") or ""),
                         "rows_sha": str(verdict.get("rows_sha") or ""),
                         "checker_version": str(verdict.get("checker_version") or ""),
                         "checked_at": str(verdict.get("checked_at") or ""),
                         "line": str(verdict.get("line") or "")[:300]}


def _refuse(rec: dict, verdict: dict, lines: list[str],
            say: Callable[[str], None]) -> dict[str, Any]:
    _record_verdict(rec, verdict)
    rec["status"] = "refused"
    records.save(rec)
    _remove_box_project(rec)       # a build refused after it ran leaves nothing in the box
    _cleanup(rec["id"])
    first = next(iter(verdict.get("reasons") or []), "")
    line = wording.STOPPED.format(reason=wording.block_words(first))
    say(line)
    return _result("refused", f"{rec['name']}: {line}", lines, rec)


def _approved(accepted: Optional[dict], stage: str, sha: str) -> bool:
    """True when the owner already said yes to exactly this *stage* hold.

    *accepted* maps a stage (``code``, ``app``) to the fingerprint she said
    yes to; it only grows through :func:`decide` (a redeemed, device-bound
    ticket). A yes to one stage never covers another, and a fingerprint that
    changed is not covered."""
    if not isinstance(accepted, dict) or not sha:
        return False
    return str(accepted.get(stage) or "") == sha


def _check_and_continue(rec: dict, *, say: Callable[[str], None], lines: list[str],
                        accepted: Optional[dict], prepare: bool = False) -> dict[str, Any]:
    """Check the unpacked tree; continue only on allow, or on exactly the
    holds the owner said yes to (*accepted* = ``{stage: sha}``, see
    :func:`_approved`). *prepare*: stop after the check and say what one yes
    would add (:func:`prepare_code_project`)."""
    from puffpai import drawbridge
    verdict, lic, mode, _scan, got = _check(rec)
    _record_verdict(rec, verdict)
    decision = verdict.get("verdict")
    sha = str(verdict.get("rows_sha") or "")
    rec["mode"] = mode if mode in records.MODES else None
    if decision == drawbridge.BLOCK:
        return _refuse(rec, verdict, lines, say)
    if accepted or _load_stash(rec["id"]).get("prepared"):
        # After a yes: the files must still be exactly the ones she said yes
        # to. Anything changed (even a change no check notices) voids every
        # earlier yes and she is asked again; nothing of it is added first.
        now = _files_now(rec)
        said = str(_load_stash(rec["id"]).get("files_yes") or "")
        if not now or now != said:
            fsha = hashlib.sha256(json.dumps({"files": now, "code": sha},
                                             sort_keys=True).encode()).hexdigest()
            return _hold(rec, ["files_changed"], fsha, wording.reason_words("files_changed"),
                         lines, say, stage="files")
    if prepare:
        return _prepared(rec, verdict=verdict, mode=mode, code_sha=sha,
                         words=_hold_words(verdict, lic, got)
                         if decision != drawbridge.ALLOW else "",
                         lines=lines, say=say, licence=lic.get("run"))
    if decision != drawbridge.ALLOW:
        if not _approved(accepted, "code", sha):
            words = _hold_words(verdict, lic, got)
            return _hold(rec, list(verdict.get("reasons") or []), sha, words, lines,
                         say, stage="code")
    else:
        own = (rec.get("from") or {}).get("own_project") is True
        run_lic = lic.get("run") if isinstance(lic.get("run"), dict) else {}
        say(wording.CHECKED.format(license_words=wording.OWN_PROJECT.lower() if own
                                   else (run_lic.get("words") or lic.get("words")
                                         or "free to use")))
        if run_lic.get("line") and not own:
            say(f"  {run_lic['line']}")
    records.save(rec)
    return _add_in_mode(rec, mode, say=say, lines=lines, code_sha=sha,
                        accepted=accepted)


def _add_in_mode(rec: dict, mode: str, *, say: Callable[[str], None],
                 lines: list[str], code_sha: str,
                 accepted: Optional[dict]) -> dict[str, Any]:
    if mode == "static_web":
        return _add_static(rec, say=say, lines=lines, code_sha=code_sha,
                           accepted=accepted)
    if mode == "data_template":
        return _add_data(rec, say=say, lines=lines, code_sha=code_sha, accepted=accepted)
    if mode == "picture_set":
        return _add_picture_set(rec, say=say, lines=lines)
    if mode == "web_build" and box_ready():
        return _add_web_build(rec, say=say, lines=lines, code_sha=code_sha,
                              accepted=accepted)
    # A server app, a command line tool, a library, and (while the locked box
    # is parked) a web app that needs a build: on this computer, as built.
    return _add_on_host(rec, mode, say=say, lines=lines, code_sha=code_sha,
                        accepted=accepted)


def _add_data(rec: dict, *, say: Callable[[str], None], lines: list[str],
              code_sha: str, accepted: Optional[dict]) -> dict[str, Any]:
    """``data_template``: the data files become a list app, text files become
    passages. Its own Drawbridge check first (stage ``data``)."""
    from puffpai import drawbridge
    from puffpai.integrate import data_import
    src = _src_root(rec)
    try:
        got = data_import.collect(src)
        draft = data_import.draft_for(rec, got)
    except Exception as exc:  # noqa: BLE001 - files that cannot be read are not added
        logger.warning("integrate: data files could not be read: %s", type(exc).__name__)
        return _fail(rec, wording.SET_UP_FAILED, lines, say)
    verdict = drawbridge.check_addition(draft, by="toolsmith")
    decision = verdict.get("verdict")
    if decision == drawbridge.BLOCK:
        return _refuse(rec, verdict, lines, say)
    if decision != drawbridge.ALLOW:
        sha = hashlib.sha256(json.dumps({"code": code_sha, "data": verdict.get("rows_sha")},
                                        sort_keys=True).encode()).hexdigest()
        if not _approved(accepted, "data", sha):
            first = next(iter(verdict.get("reasons") or []), "")
            return _hold(rec, list(verdict.get("reasons") or []), sha,
                         wording.reason_words(first), lines, say, stage="data")
    rec["status"] = "building"
    records.save(rec)
    src_from = rec.get("from") or {}
    lic = str(src_from.get("license_read") or src_from.get("license_reported") or "")
    credit = wording.DATA_CREDIT.format(url=str(src_from.get("url") or ""), license=lic or "?")
    out: dict[str, Any] = {"lists": {}}
    try:
        if got.get("lists"):
            out = data_import.make_list_app(rec, got, credit=credit)
            rec["list_app_id"] = out["app_id"]
        texts = data_import.add_texts(rec, got) if got.get("texts") else []
        if texts:
            rec["book_id"] = f"integration-{rec['id']}"
    except Exception as exc:  # noqa: BLE001 - a half-made list is taken back out
        logger.warning("integrate: data import failed: %s", type(exc).__name__)
        _undo_data(rec)
        rec["status"] = "failed"
        records.save(rec)
        _cleanup(rec["id"])
        say(wording.SET_UP_FAILED)
        return _result("failed", f"{rec['name']}: {wording.SET_UP_FAILED}", lines, rec)
    rec["mode"] = "data_template"
    rec["status"] = "ready"
    rec["held"] = {"reasons": [], "ticket_target": ""}
    rec["added_at"] = _now()
    rec["actions"] = [a["id"] for a in data_import.list_actions()
                      if a["integration"] == rec["id"]][:60] if rec.get("list_app_id") else []
    records.save(rec)
    _cleanup(rec["id"])
    total = sum(int(n) for n in (out.get("lists") or {}).values())
    line = wording.DATA_ADDED.format(name=rec["name"], rows=f"{total:,}") if total \
        else wording.TEXT_ADDED.format(name=rec["name"])
    say(line)
    return _result("added", wording.ADDED.format(name=rec["name"]), lines, rec)


def _undo_data(rec: dict) -> None:
    from puffpai.integrate import data_import
    if rec.get("list_app_id"):
        try:
            from puffpai import persona_scaffold
            persona_scaffold.remove(rec["list_app_id"])
        except Exception:  # noqa: BLE001
            pass
        rec["list_app_id"] = None
    if rec.get("book_id"):
        data_import.remove_texts(rec["id"])
        rec["book_id"] = None


def _add_picture_set(rec: dict, *, say: Callable[[str], None],
                     lines: list[str]) -> dict[str, Any]:
    """A picture-set project: every picture re-encoded onto its own shelf."""
    from puffpai.integrate import pictures
    got = pictures.import_tree(rec, _src_root(rec))
    if not got["kept"]:
        rec["status"] = "failed"
        records.save(rec)
        _cleanup(rec["id"])
        say(wording.PICTURES_NONE)
        return _result("failed", f"{rec['name']}: {wording.PICTURES_NONE}", lines, rec)
    rec["mode"] = "picture_set"
    rec["pictures_dir"] = got["shelf"]
    rec["status"] = "ready"
    rec["held"] = {"reasons": [], "ticket_target": ""}
    rec["added_at"] = _now()
    records.save(rec)
    _cleanup(rec["id"])
    line = wording.PICTURE_SET_ADDED.format(n=got["kept"], name=rec["name"])
    say(line)
    return _result("added", wording.ADDED.format(name=rec["name"]), lines, rec)


# -- web apps that must be built: only inside the locked box -------------------------------

NPM_HOST = "registry.npmjs.org"
#: The box's id rule for run ids and projects (``driver.ID_RE``).
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
BUILD_OUTPUTS = ("dist", "build", "out", "public")
#: A top folder of the project that may hold its own web front end
#: ("client/", "web/", "frontend/"): it has a package.json, an index.html or
#: a bundler config of its own.
_FRONT_MARKERS = ("package.json", "index.html", "vite.config.js", "vite.config.ts",
                  "vite.config.mjs", "webpack.config.js")
_FOLDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def build_output_candidates(src: str) -> list[str]:
    """Folders a build may have written, in the order they are tried: the
    project's own ``dist``, ``build``, ``out``; then the same inside each top
    folder that holds a front end of its own (``client/dist``); ``public``
    last (it is often the unbuilt template). Names come from the extracted
    tree and are kept to plain folder names (never a link, a dot folder or
    ``node_modules``)."""
    out = [d for d in BUILD_OUTPUTS if d != "public"]
    subs: list[str] = []
    try:
        entries = sorted(os.listdir(src))
    except OSError:
        entries = []
    for name in entries:
        path = os.path.join(src, name)
        if (name in BUILD_OUTPUTS or name == "node_modules" or not _FOLDER_NAME.match(name)
                or name.startswith(".") or os.path.islink(path) or not os.path.isdir(path)):
            continue
        if any(os.path.isfile(os.path.join(path, m)) for m in _FRONT_MARKERS):
            subs.append(name)
    for name in subs[:8]:
        out += [f"{name}/{d}" for d in ("dist", "build", "out")]
    return out + ["public"]
_RESOLVED_OK = re.compile(r"^https://registry\.npmjs\.org/")


def _inside_link(node: dict) -> bool:
    """A workspace link to a folder inside the project (``"link": true`` and
    a plain relative ``resolved``, as npm writes for ``client/``): not a
    download. The same rule as the box driver's lock scan."""
    res = node.get("resolved")
    return (node.get("link") is True and isinstance(res, str) and bool(res)
            and ":" not in res and not res.startswith(("/", "\\", ".."))
            and ".." not in res.replace("\\", "/").split("/"))


#: The npm lock files ``npm ci`` reads, the one it prefers first.
NPM_LOCKS = ("npm-shrinkwrap.json", "package-lock.json")
#: A dependency spec or a locked version that comes from the npm registry: a
#: version or range ("1.2.3", "^4 || ~5.1", "*"), a dist tag ("latest"), or
#: an alias to a registry package ("npm:string-width@4.2.3"). Anything with
#: a ":" or a "/" (git, a web address, a GitHub shorthand, a path), a path
#: start ("." or "~/"; "~1.2.3" is a range) or a tarball name is not.
_SPEC_REGISTRY = re.compile(
    r"^(?:(?!\.|~/)[^:/\\]{1,200}|npm:(?:@[\w.-]+/)?[\w.-]+(?:@[^:/\\]{0,100})?)$")
_SPEC_TARBALL = re.compile(r"(?i)\.(?:tgz|tar|tar\.gz)\s*$")
_DEP_MAPS = ("dependencies", "devDependencies", "optionalDependencies",
             "peerDependencies", "requires")


def _inside_spec(spec: str) -> bool:
    """``file:client`` or ``link:client``: a folder inside the project."""
    m = re.match(r"^(?:file|link):(.+)$", spec)
    if not m:
        return False
    path = m.group(1).replace("\\", "/")
    return (bool(path) and ":" not in path and not path.startswith("/")
            and ".." not in path.split("/"))


def _spec_offsite(spec: Any) -> bool:
    """A dependency spec that is not from the npm registry: ``github:x/y``,
    ``git+https://...``, ``https://...``, ``x/y`` (a GitHub shorthand),
    ``file:../elsewhere``. Anything unreadable counts as offsite."""
    if isinstance(spec, bool):
        return False                                  # "requires": true (lock v1)
    if not isinstance(spec, str):
        return True
    spec = spec.strip()
    if not spec or _inside_spec(spec):
        return False
    return not _SPEC_REGISTRY.match(spec) or bool(_SPEC_TARBALL.search(spec))


def _npm_lock_offsite(lock: Any) -> bool:
    """True when a parsed npm lock (v1, v2 or v3) names a part from anywhere
    but the npm registry."""
    def walk(node: Any, package: bool) -> bool:
        if isinstance(node, list):
            return any(walk(v, False) for v in node)
        if not isinstance(node, dict):
            return False
        res = node.get("resolved")
        if res is not None and not isinstance(res, str):
            return True
        if isinstance(res, str) and not _inside_link(node) and not _RESOLVED_OK.match(res):
            return True
        # A locked part's "version" says where it comes from when it is not a
        # version (lock v1: "github:owner/x", with or without "resolved").
        if package and "version" in node and _spec_offsite(node.get("version")) \
                and not _inside_link(node):
            return True
        for k, v in node.items():
            if k == "resolved":
                continue
            if k in _DEP_MAPS and isinstance(v, dict):
                for sub in v.values():
                    if isinstance(sub, dict):
                        if walk(sub, True):         # lock v1: nested locked parts
                            return True
                    elif _spec_offsite(sub):        # a spec: "github:x/y", "^1.2.0"
                        return True
                continue
            if k == "packages" and isinstance(v, dict):
                for name, sub in v.items():
                    if walk(sub, bool(name)):       # "" is the project itself
                        return True
                continue
            if walk(v, False):
                return True
        return False

    return walk(lock, False)


def lock_download_hosts(src: str) -> list[str]:
    """The web hosts an npm lock of the project downloads parts from, other
    than the npm registry the locked box reaches (``registry.yarnpkg.com``,
    ``github.com`` ...), sorted. A build needing them cannot work in the box,
    which says so instead of trying."""
    from urllib.parse import urlsplit
    hosts: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, dict):
            res = node.get("resolved")
            if isinstance(res, str) and re.match(r"(?i)^(?:git\+)?https?://", res):
                host = (urlsplit(res.removeprefix("git+")).hostname or "").lower()
                if host and host != NPM_HOST:
                    hosts.add(host)
            for k, v in node.items():
                if k != "resolved":
                    walk(v)

    for name in NPM_LOCKS:
        path = os.path.join(src, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                walk(json.loads(fh.read().decode("utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(hosts)


def _refused_hosts(result: Any) -> list[str]:
    """Hosts the box's proxy refused during a run (its own visit list)."""
    out: list[str] = []
    for visit in _box_value(result, "outside", []) or []:
        if isinstance(visit, dict) and visit.get("outcome") == "refused":
            host = str(visit.get("host") or "").split(":")[0].lower()
            if host and host not in out:
                out.append(host)
    return sorted(out)


class _BuildStopped(RuntimeError):
    """A box step failed because the box refused the hosts it wanted."""

    def __init__(self, hosts: list[str]):
        super().__init__("hosts")
        self.hosts = hosts


class _BuildStep(RuntimeError):
    """Which box build step failed ("put", "install",
    "build", "export", "no_page") and, when the box can tell, why ("node",
    "time", "memory", "space")."""

    def __init__(self, step: str, why: str = "", result: Any = None):
        super().__init__(step)
        self.step = step
        self.why = why
        self.result = result   # the box's result for that step


#: The box's own words for a Node.js version a part does not support (npm's
#: engine check, node-gyp, node-sass and the like). Only mapped to one of
#: the fixed reasons in wording.BUILD_WHY; the output itself is never shown.
_NODE_RUNTIME = re.compile(
    r"(?i)unsupported\s+(?:runtime|engine)|EBADENGINE|does\s+not\s+yet\s+support\s+your\s+"
    r"current\s+environment|node-gyp|gyp\s+ERR|node-sass|NODE_MODULE_VERSION|"
    r"engine\s+\"?node\"?\s+is\s+incompatible|requires\s+node")
_NO_SPACE = re.compile(r"(?i)\bENOSPC\b|no\s+space\s+left|EDQUOT|disk\s+quota")


def _box_why(result: Any) -> str:
    """The plain reason a box run failed, from what the box measured: "time",
    "memory", "space", "node", or "" when it cannot tell."""
    if _box_value(result, "timed_out") is True:
        return "time"
    if _box_value(result, "oom_killed") is True:
        return "memory"
    tail = str(_box_value(result, "stderr_tail", "") or "") + "\n" + str(
        _box_value(result, "stdout_tail", "") or "")
    if _NO_SPACE.search(tail):
        return "space"
    if _NODE_RUNTIME.search(tail):
        return "node"
    return ""


def build_failed_line(step: str, why: str = "") -> str:
    """The owner's sentence for a failed box build step (wording.BUILD_FAILED
    when the step is not known)."""
    line = wording.BUILD_FAILED_STEP.get(step)
    if line is None:
        return wording.BUILD_FAILED
    return line.format(why=wording.BUILD_WHY.get(why, ""))


#: The step reasons as the short causes the box driver records.
_WHY_CAUSE = {"time": "too_long", "memory": "memory", "space": "no_room", "node": "old_node"}


def _build_failure(driver: Any, exc: BaseException) -> dict:
    """``{"step", "cause", "line"}`` for a failed box build (one reading).
    The box driver's own reading of the failed step comes first; a driver
    that cannot read it gets the step-and-reason sentence. Only fixed words, never the project's output."""
    step = exc.step if isinstance(exc, _BuildStep) else "build"
    why = exc.why if isinstance(exc, _BuildStep) else ""
    no_page = step == "no_page"
    box_step = "export" if no_page else step
    fn = getattr(driver, "build_failure", None) if driver else None
    if callable(fn):
        try:
            got = fn(box_step, getattr(exc, "result", None), no_page=no_page)
            if isinstance(got, dict) and got.get("line"):
                return {"step": str(got.get("step") or box_step),
                        "cause": str(got.get("cause") or ""), "line": str(got["line"])}
        except Exception:  # noqa: BLE001 - fall back to the step-and-reason words
            pass
    cause = "no_page" if no_page else (_WHY_CAUSE.get(why) or f"{box_step}_failed")
    line = build_failed_line(step, why) if isinstance(exc, _BuildStep) else wording.BUILD_FAILED
    return {"step": box_step, "cause": cause, "line": line}


def npm_lock_used(src: str) -> str:
    """The lock file name ``npm ci`` reads in *src* (``npm-shrinkwrap.json``
    before ``package-lock.json``), or "" when there is none."""
    for name in NPM_LOCKS:
        if os.path.isfile(os.path.join(src, name)):
            return name
    return ""


def lock_offsite(src: str) -> Optional[bool]:
    """True when an npm lock of the project names a part from anywhere but the
    npm registry (``git+``, ``file:`` out of the project, ``http:``, another
    host, a GitHub shorthand, a link out of the project), in a download
    address or in a dependency spec; None when there is no npm lock or one of
    them cannot be read. EVERY npm lock present is read (``npm ci`` prefers
    ``npm-shrinkwrap.json``; a clean ``package-lock.json`` next to it proves
    nothing). A workspace link inside the project is not a download. When
    the box driver's lock scan is in this build, anything it finds counts too
    (either one is enough)."""
    raws: list[bytes] = []
    for name in NPM_LOCKS:
        path = os.path.join(src, name)
        if not os.path.lexists(path):
            continue
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            lock = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            return None
        if _npm_lock_offsite(lock):
            return True
        raws.append(raw)
    if not raws:
        return None
    locks = optional("puffpai.box.locks")
    scan = getattr(locks, "scan_npm_lock", None) if locks else None
    if callable(scan):
        for raw in raws:
            try:
                if scan(raw):
                    return True
            except Exception:  # noqa: BLE001 - a scan that breaks proves nothing
                return True
    return False


def _box_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _box_ok(result: Any) -> bool:
    if _box_value(result, "ok") is True:
        return True
    code = _box_value(result, "exit_code", _box_value(result, "returncode"))
    return code == 0


def _box_request(driver: Any, **fields: Any) -> Any:
    cls = getattr(driver, "BoxRequest", None)
    if cls is not None:
        try:
            return cls(**fields)
        except TypeError:
            pass
    return dict(fields)


def _project_tar(rec: dict, src: str) -> str:
    """The unpacked project as one tar for the locked box (regular files only,
    no links, no ``node_modules``, no ``.git``)."""
    import tarfile
    from puffpai.integrate import classify
    dest = os.path.join(os.path.dirname(src), "project.tar")
    with tarfile.open(dest, "w") as tar:
        for rel, full, _size in classify._walk(src):  # noqa: SLF001 - no links followed
            info = tar.gettarinfo(full, arcname=rel)
            if not info.isfile():
                continue
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(full, "rb") as fh:
                tar.addfile(info, fh)
    return dest


def _new_run_id(driver: Any) -> str:
    """A run id the box accepts (``^[a-z0-9][a-z0-9-]{0,39}$``): the box's own
    ``new_run_id`` when it has one, else the same shape."""
    fn = getattr(driver, "new_run_id", None) if driver else None
    if callable(fn):
        try:
            got = str(fn())
            if RUN_ID_RE.match(got):
                return got
        except Exception:  # noqa: BLE001
            pass
    import secrets
    return "r" + secrets.token_hex(8)


def _add_web_build(rec: dict, *, say: Callable[[str], None], lines: list[str],
                   code_sha: str, accepted: Optional[dict]) -> dict[str, Any]:
    """``web_build``: ``npm ci --ignore-scripts`` from the recorded lock and the
    ``build`` script, only inside the locked box; the built files are exported
    and added like a static app."""
    driver = optional("puffpai.box.driver")
    if not box_ready():
        # The box went away between the check and here: on this computer.
        return _add_on_host(rec, "web_build", say=say, lines=lines, code_sha=code_sha,
                            accepted=accepted)
    src = _src_root(rec)
    offsite = lock_offsite(src)
    lock_name = npm_lock_used(src)
    if offsite is None or not lock_name:
        # A build needs the exact recorded lock: none, and it is not rebuilt.
        rec["status"] = "held"
        rec["held"] = {"reasons": ["no_lock"], "ticket_target": ""}
        records.save(rec)
        say(wording.NO_LOCK)
        return _result("held", f"{rec['name']}: {wording.NO_LOCK}", lines, rec)
    # The recorded lock is the one npm ci reads (npm-shrinkwrap.json first);
    # the yes after a hold covers every npm lock in the project as it is now.
    shas = {}
    for name in NPM_LOCKS:
        path = os.path.join(src, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                shas[name] = hashlib.sha256(fh.read()).hexdigest()
    lock_sha = shas[lock_name]
    if offsite:
        sha = hashlib.sha256(json.dumps({"code": code_sha, "lock": lock_sha, "locks": shas},
                                        sort_keys=True).encode()).hexdigest()
        if not _approved(accepted, "box", sha):
            return _hold(rec, ["lock_offsite"], sha, wording.reason_words("lock_offsite"),
                         lines, say, stage="box")
        far = lock_download_hosts(src)
        if far:
            # Her yes covers these parts, but the locked box fetches parts
            # only from the npm registry: say so now, never build for minutes
            # and end with no reason.
            rec["status"] = "failed"
            records.save(rec)
            _cleanup(rec["id"])
            line = wording.BUILD_HOSTS.format(hosts=", ".join(far[:3]))
            say(line)
            return _result("failed", f"{rec['name']}: {line}", lines, rec)
    rec["lock"] = {"kind": "npm", "path": lock_name, "sha256": lock_sha}
    rec["box"] = {"project": rec["id"], "image_mb": 0}
    rec["status"] = "building"
    records.save(rec)
    say(wording.BUILDING_IN_BOX)
    service = "box_" + hashlib.sha256(rec["id"].encode()).hexdigest()[:24]
    limits = {"mem_mb": 2048, "pids": 256, "cpu_s": 900, "wall_s": 900, "fsize_mb": 512,
              "disk_mb": 2048}
    try:
        try:
            put = driver.put_project(rec["id"], _project_tar(rec, src))
        except Exception as exc:  # noqa: BLE001
            raise _BuildStep("put") from exc
        if not _box_ok(put):
            raise _BuildStep("put", "", put)
        for step, argv, hosts in (
                ("install", ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                 [NPM_HOST]),
                ("build", ["npm", "run", "build", "--ignore-scripts"], [])):
            req = _box_request(driver, run_id=_new_run_id(driver), project=rec["id"],
                               argv=argv, allow_hosts=hosts, service=service,
                               what="box_install", folder=None, limits=limits,
                               stdin="none", stdout_mode="capture", env={})
            got = driver.run(req)
            if not _box_ok(got):
                if _refused_hosts(got):
                    raise _BuildStopped(_refused_hosts(got))
                raise _BuildStep(step, _box_why(got), got)
        from puffpai.security import safe_archive
        web_dir = os.path.join(safe_archive.extraction_root(), rec["id"], "web")
        files: list[str] = []
        built_any = False
        for out_dir in build_output_candidates(src):
            if os.path.isdir(web_dir) and os.listdir(web_dir):
                # The box exports only into a new or empty folder.
                safe_archive.safe_rmtree(web_dir,
                                         allowed_roots=[safe_archive.extraction_root()])
            try:
                files = list(driver.export(rec["id"], out_dir, web_dir,
                                           max_bytes=fetch.MAX_ARCHIVE_BYTES) or [])
            except Exception:  # noqa: BLE001 - that folder was not built
                files = []
            built_any = built_any or bool(files)
            if any(str(f).replace("\\", "/").endswith("index.html") for f in files):
                break
        if not files or not any(str(f).replace("\\", "/").endswith("index.html")
                                for f in files):
            # Built files with no page to open ("a dashboard card",
            # a library) are said as that, not as a build that broke.
            raise _BuildStep("no_page" if built_any else "export")
    except Exception as exc:  # noqa: BLE001 - the project's own code did not build
        logger.info("integrate: box build failed: %s", type(exc).__name__)
        failure = None if isinstance(exc, _BuildStopped) else _build_failure(driver, exc)
        rec["status"] = "failed"
        if failure is not None:
            rec["build_failure"] = {"step": failure["step"], "cause": failure["cause"]}
        records.save(rec)
        _remove_box_project(rec)
        _cleanup(rec["id"])
        if failure is None:
            line = wording.BUILD_HOSTS.format(hosts=", ".join(exc.hosts[:3]))
        else:
            line = failure["line"]
        say(line)
        extra = {"build_failure": dict(rec["build_failure"])} if failure is not None else {}
        return _result("failed", f"{rec['name']}: {line}", lines, rec, **extra)
    entry = classify.scan_tree(web_dir).get("entry_html") or "index.html"
    files_dir = os.path.join(web_dir, *str(entry).split("/")[:-1])
    return _add_static(rec, say=say, lines=lines, code_sha=code_sha, accepted=accepted,
                       files_dir=files_dir)


def _remove_box_project(rec: dict) -> None:
    driver = optional("puffpai.box.driver")
    fn = getattr(driver, "remove_project", None) if driver else None
    if fn is None or not (rec.get("box") or {}).get("project"):
        return
    try:
        fn(rec["box"]["project"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("integrate: could not remove the box project: %s", type(exc).__name__)


def _add_static(rec: dict, *, say: Callable[[str], None], lines: list[str],
                code_sha: str, accepted: Optional[dict],
                files_dir: Optional[str] = None) -> dict[str, Any]:
    install = optional("puffpai.webapps.install")
    install_static = getattr(install, "install_static", None) if install else None
    if install_static is None:
        rec["status"] = "held"
        rec["held"] = {"reasons": ["not_in_build"], "ticket_target": ""}
        records.save(rec)
        return _result("held", f"{rec['name']}: {wording.NOT_IN_BUILD}", lines, rec)
    stash = _load_stash(rec["id"])
    if files_dir is None:
        src = _src_root(rec)
        scan = classify.scan_tree(src)
        entry = str(scan.get("entry_html") or "index.html")
        files_dir = os.path.join(src, *entry.split("/")[:-1])
    # Every install attempt gets its own never-reused id (and so its own
    # never-reused origin): an attempt that is held is taken back out, and a
    # later yes installs under a new id, never the old origin again.
    app_id = records.new_id(rec["name"])
    stash.setdefault("app_ids", [])
    stash["app_ids"] = [str(a) for a in stash["app_ids"] if records.valid_id(a)][-50:] + [app_id]
    _save_stash(rec["id"], stash)
    rec["status"] = "building"
    records.save(rec)
    src_from = rec.get("from") or {}
    plan_hosts = {str(h).lower() for h in (rec.get("plan_facts") or {}).get("runtime_hosts") or []}
    source = {"repo": src_from.get("url"), "commit": src_from.get("commit"),
              "license": src_from.get("license_read"),
              "sha256": src_from.get("archive_sha256"),
              # The plan's own outside addresses: the app host holds only for
              # ones the plan did not show (the same rule as below).
              "runtime_hosts": sorted(plan_hosts)}
    if isinstance(accepted, dict) and accepted.get("app"):
        # Her yes reaches the app host too, for exactly the app host's own
        # hold codes she was asked about (otherwise, after "Add it anyway",
        # the app host holds it again, the app never opens, and the owner
        # is told "Added"). Anything else the app host finds still holds,
        # and the fingerprint check below still has to match what she saw.
        source["allowed_holds"] = [str(c) for c in stash.get("app_codes") or []
                                   if isinstance(c, str)]
    web_added: list[str] = []

    def web_say(line: str) -> None:
        # The app host's own "Added <name>. Your bot can ..." becomes this
        # item's one closing line, said once (it was said by both).
        if _WEB_ADDED.match(str(line)):
            web_added.append(str(line))
            return
        say(line)
    result = install_static(app_id, files_dir, source=source,
                            name=rec["name"], purpose=rec.get("purpose") or "",
                            jobs=list(stash.get("jobs") or []), progress=web_say)
    result = result if isinstance(result, dict) else {}
    app_verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    decision = str(app_verdict.get("verdict") or "")
    hosts = [h for h in (result.get("hosts") or []) if isinstance(h, dict)]
    known = {str(h.get("host") or "").lower() for h in hosts}
    hosts += [h for h in _real_hosts_filed_as_yours(files_dir) if h["host"] not in known]
    new_hosts = sorted({str(h.get("host") or "").lower() for h in hosts} - plan_hosts - {""})
    if decision == "block":
        _uninstall(app_id)
        return _refuse(rec, {"reasons": _verdict_reasons(app_verdict)}, lines, say)
    failed = _failed_codes(app_verdict)
    if decision != "allow" or new_hosts or failed:
        app_sha = hashlib.sha256(json.dumps(
            {"code": code_sha, "app": _app_rows_fingerprint(app_verdict, app_id),
             "reasons": sorted(str(r) for r in app_verdict.get("reasons") or []),
             "hosts": new_hosts, "failed": failed}, sort_keys=True).encode()).hexdigest()
        if not _approved(accepted, "app", app_sha):
            _uninstall(app_id)
            reasons = (["runtime_hosts_new"] if new_hosts else []) + [
                r for r in (_verdict_reasons(app_verdict) if decision != "allow" or failed
                            else []) if r != "runtime_hosts_new" or not new_hosts]
            reasons = list(dict.fromkeys(reasons)) or ["check_failed"]
            words = "; ".join(dict.fromkeys(
                wording.reason_words("runtime_hosts_new", host=wording.hosts_words(new_hosts))
                if code == "runtime_hosts_new" and new_hosts else wording.reason_words(code)
                for code in reasons))
            # A yes builds it again, so the box keeps nothing while it waits.
            _remove_box_project(rec)
            return _hold(rec, reasons, app_sha, words, lines, say, stage="app",
                         hosts=new_hosts, codes=failed)
    if decision != "allow":
        # She said yes to exactly this, yet the app host did not set it up (it
        # kept it held): it cannot open, so it is never called added.
        _uninstall(app_id)
        return _fail(rec, wording.SET_UP_FAILED, lines, say)
    if str(result.get("app_id") or app_id) != app_id:
        # The app host installed it under another id than the one issued:
        # never trust that, take it back out.
        _uninstall(app_id)
        _uninstall(str(result.get("app_id")))
        rec["status"] = "failed"
        records.save(rec)
        _remove_box_project(rec)
        _cleanup(rec["id"])
        say(wording.SET_UP_FAILED)
        return _result("failed", f"{rec['name']}: {wording.SET_UP_FAILED}", lines, rec)
    rec["app_id"] = app_id
    rec["actions"] = [str(a.get("id") if isinstance(a, dict) else a)
                      for a in (result.get("actions") or [])][:60]
    rec["outside"] = [{"host": str(h.get("host") or "")[:120],
                       "for": str(h.get("for") or "")[:80],
                       "state": str(h.get("state") or "off")[:12]} for h in hosts][:20]
    rec["status"] = "ready"
    rec["held"] = {"reasons": [], "ticket_target": ""}
    rec["added_at"] = _now()
    records.save(rec)
    # The app runs from its own copy now: the unpacked download is not kept
    # beside it (otherwise a second copy, 626 KB in one case, is left).
    _cleanup(rec["id"])
    say(wording.SET_UP_WEB)
    abilities = [str(a) for a in (result.get("abilities") or []) if str(a).strip()]
    if abilities and result.get("drivable", True):
        say(wording.TAUGHT.format(abilities=", ".join(abilities[:8])))
    elif not _web_step_said_why(result):
        say(wording.CANNOT_DRIVE)
    return _result("added", web_added[-1] if web_added else wording.ADDED.format(name=rec["name"]),
                   lines, rec)


#: The app host's own closing line (``webapps.wording.added_line``).
_WEB_ADDED = re.compile(r"^Added .{1,200}\. (?:Your bot can (?:open it for you|do \d+ things? in it)"
                        r"|You can open it, but there are 0 things your bot can do in it by itself)\.$")


def _placeholder_host(host: str) -> bool:
    """A name that can never be a real address on the internet (the names
    kept for examples and tests: ``example.com/.net/.org`` and anything
    under them, and the ``.example``, ``.test``, ``.invalid``, ``.localhost``
    endings, ``localhost``)."""
    h = str(host or "").lower().rstrip(".")
    if h == "localhost" or h.rsplit(".", 1)[-1] in ("example", "test", "invalid", "localhost"):
        return True
    return any(h == base or h.endswith("." + base)
               for base in ("example.com", "example.net", "example.org"))


def _real_hosts_filed_as_yours(files_dir: str) -> list[dict]:
    """Outside addresses the app host's scan filed as "an account you would
    set up yourself" although they are real names on the internet (such as
    ``tracker.exfil-example.net``, which would otherwise be switched off with
    no question). Here they count as addresses the app wants to reach: named to
    her, held for her yes, and recorded switched off. Real placeholders
    (``api.example.com``) stay what they are."""
    scan_mod = optional("puffpai.webapps.scan")
    scan_fn = getattr(scan_mod, "scan", None) if scan_mod else None
    if scan_fn is None:
        return []
    try:
        found = scan_fn(files_dir)
    except Exception as exc:  # noqa: BLE001 - the app host scans the same files anyway
        logger.warning("integrate: the address scan failed: %s", type(exc).__name__)
        return []
    for_words = getattr(scan_mod, "FOR_WORDS", {}) or {}
    out = []
    for h in (found or {}).get("hosts") or []:
        if not isinstance(h, dict):
            continue
        name = str(h.get("host") or "").lower()
        if name and "yours" in (h.get("uses") or []) and not _placeholder_host(name):
            out.append({"host": name[:120],
                        "for": str(for_words.get(str(h.get("for") or ""))
                                   or for_words.get("") or "something the app does online"),
                        "state": "off"})
    return out[:20]


def _failed_codes(verdict: dict) -> list[str]:
    """The app host's failed check codes (its rows, else its reasons)."""
    rows = verdict.get("rows")
    if isinstance(rows, list) and rows:
        return sorted({str(r.get("code")) for r in rows
                       if isinstance(r, dict) and r.get("result") not in ("pass", "note")
                       and r.get("code")})
    if str(verdict.get("verdict") or "") in ("", "allow"):
        return []
    return sorted({str(r) for r in verdict.get("reasons") or [] if str(r)})


def _web_step_said_why(result: dict) -> bool:
    """True when the web app step's own lines already told the owner what her
    bot can and cannot do in the app ("It keeps nothing of its own, ..."), so
    a second, different reason is not added."""
    said = {str(line).strip() for line in (result.get("lines") or [])}
    web_wording = optional("puffpai.webapps.wording")
    known = {str(getattr(web_wording, n, "")).strip()
             for n in ("KEEPS_NOTHING", "CANNOT_DRIVE")} if web_wording else set()
    known.add(wording.CANNOT_DRIVE.strip())
    return bool(said & (known - {""}))


def _verdict_reasons(verdict: dict) -> list[str]:
    """The codes a verdict failed on: its ``reasons``, else the codes of its
    failed rows (the web app check gives rows only), else ``check_failed``.
    A code that stops the thing outright comes first, so the owner hears the
    real reason, never "the checks could not finish" when they did."""
    reasons = [str(r) for r in verdict.get("reasons") or [] if str(r)]
    if not reasons:
        reasons = [str(r.get("code")) for r in verdict.get("rows") or []
                   if isinstance(r, dict) and r.get("result") == "fail" and r.get("code")]
    reasons = list(dict.fromkeys(reasons)) or ["check_failed"]
    return sorted(reasons, key=lambda code: code not in wording.BLOCK_REASONS)


def _app_rows_fingerprint(app_verdict: dict, app_id: str) -> str:
    """A fingerprint of what the app check found that does not depend on the
    install's own id (each attempt has a new one). Uses the check's rows when
    it returns them, else its ``rows_sha``."""
    rows = app_verdict.get("results")
    if isinstance(rows, list) and rows:
        blob = json.dumps(rows, sort_keys=True, ensure_ascii=True, default=str)
        blob = blob.replace(app_id, "<app>")
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return str(app_verdict.get("rows_sha") or "")


def _uninstall(app_id: str) -> None:
    install = optional("puffpai.webapps.install")
    fn = getattr(install, "uninstall", None) if install else None
    if fn is None:
        return
    try:
        fn(app_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("integrate: uninstall of a held app failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# on this computer, as built
# ---------------------------------------------------------------------------

#: How long adding a server app waits for it to open before saying "starting".
HOST_READY_WAIT = 45.0
_HOST_MODES = {"node_server": "own_server", "python_server": "own_server",
               "compose": "own_server", "node_cli": "cli_tool", "python_cli": "cli_tool",
               "library": "library"}


def box_ready() -> bool:
    """True only when the locked box is in this build and ready now. While it
    is parked (or not set up), projects run on this computer instead."""
    driver = optional("puffpai.box.driver")
    needed = ("put_project", "run", "export", "status")
    if driver is None or not all(callable(getattr(driver, n, None)) for n in needed):
        return False
    try:
        status = driver.status()
    except Exception:  # noqa: BLE001 - not knowing is "not ready"
        return False
    return str(_box_value(status, "state", "") or "") == "ready"


def _host_plan(rec: dict, mode: str) -> dict:
    """How the project runs here (:func:`run_here.detect`), with the port kept
    for this item so the plan, and so what the yes covers, stays the same."""
    from puffpai.integrate import run_here
    stash = _load_stash(rec["id"])
    port = stash.get("host_port")
    if not isinstance(port, int):
        port = run_here.free_port()
        stash["host_port"] = port
        _save_stash(rec["id"], stash)
    return run_here.detect(_src_root(rec), port=port,
                           box_ready=(mode == "web_build" and box_ready()))


def _host_sha(code_sha: str, plan: dict) -> str:
    from puffpai.integrate import run_here
    return hashlib.sha256(json.dumps({"code": code_sha, "host": run_here.plan_sha(plan)},
                                     sort_keys=True).encode()).hexdigest()


def _installable(missing: list) -> bool:
    """Can BuildBot install every one of these programs here by itself?"""
    try:
        from puffpai import deps
        return bool(missing) and all(deps.can_install(str(p)) for p in missing)
    except Exception:  # noqa: BLE001
        return False


def _missing_lines(missing: list) -> list[str]:
    from puffpai import deps
    from puffpai.integrate import run_here
    out = []
    for prog in missing:
        words = run_here.PROGRAM_WORDS.get(str(prog), str(prog))
        note = deps.install_note(str(prog))
        out.append(f"It needs {words}, which is not on this computer yet: BuildBot installs "
                   f"it first." + (f" {note}" if note else ""))
    return out


def _host_card(rec: dict, plan: dict, code_sha: str) -> dict:
    from puffpai.integrate import run_here
    missing = run_here.missing_programs(plan) if plan.get("how") else []
    lines = run_here.card_lines(plan, rec["id"]) if plan.get("how") else []
    if missing and _installable(missing):
        lines = _missing_lines(missing) + lines
    return {"how": str(plan.get("how") or ""), "why_not": str(plan.get("why_not") or ""),
            "lines": lines,
            "commands": run_here.shown_lines(plan, rec["id"]) if plan.get("how") else [],
            "missing": missing, "port": plan.get("port"),
            "sha": _host_sha(code_sha, plan) if plan.get("how") else ""}


def _left_out_cannot_run(rec: dict, why: str, lines: list[str],
                         say: Callable[[str], None]) -> dict[str, Any]:
    rec["status"] = "held"
    rec["held"] = {"reasons": ["not_addable"], "ticket_target": ""}
    records.save(rec)
    line = wording.HOST_CANNOT.format(why=why)
    say(line)
    return _result("held", f"{rec['name']}: {line}", lines, rec)


def _add_on_host(rec: dict, mode: str, *, say: Callable[[str], None], lines: list[str],
                 code_sha: str, accepted: Optional[dict]) -> dict[str, Any]:
    """Run the project on this computer as its authors built it, after a yes
    to exactly this plan (stage ``host``)."""
    from puffpai.integrate import run_here
    plan = _host_plan(rec, mode)
    if not plan.get("how"):
        return _left_out_cannot_run(rec, str(plan.get("why_not") or "it does not say how "
                                             "it starts"), lines, say)
    card = _host_card(rec, plan, code_sha)
    stash = _load_stash(rec["id"])
    stash["host_card"] = card
    _save_stash(rec["id"], stash)
    if card["missing"] and _installable(card["missing"]) \
            and _approved(accepted, "host", card["sha"]):
        # Her yes covers what it needs to run (the card said so): installed now.
        from puffpai import deps
        for prog in card["missing"]:
            if not deps.install_program(str(prog), say).get("ok"):
                break
        card = dict(card, missing=run_here.missing_programs(plan))
        if card["missing"] and plan.get("how") == "compose":
            # Its containers cannot run here: its own way without Docker, if it has one.
            native = run_here.detect(_src_root(rec), port=plan.get("port")
                                     or run_here.free_port(), docker=False)
            if native.get("how") and not run_here.missing_programs(native):
                say(f"{rec['name']}: Docker is not ready, so BuildBot runs it its own way.")
                plan, card = native, dict(card, missing=[])
    if card["missing"]:
        # It is kept (never removed) until what it needs is on this computer.
        hook = run_here.install_missing({"integration": rec["id"]}, card["missing"])
        rec["status"] = "held"
        rec["held"] = {"reasons": ["needs_program"], "ticket_target": ""}
        records.save(rec)
        line = wording.HOST_NEEDS.format(line=hook["line"])
        say(line)
        return _result("held", f"{rec['name']}: {line}", lines, rec,
                       needs=list(card["missing"]))
    if not _approved(accepted, "host", card["sha"]):
        say(wording.HOST_ASK.format(name=rec["name"]))
        for line in card["lines"]:
            say(line)
        return _hold(rec, ["host_run"], card["sha"], wording.reason_words("host_run"),
                     lines, say, stage="host",
                     line=wording.HOST_HELD.format(name=rec["name"]))
    rec["status"] = "building"
    records.save(rec)
    got = run_here.set_up(rec["id"], _src_root(rec), plan, by=str(rec.get("by") or "owner"),
                          progress=say)
    if not got.get("ok") and plan.get("how") == "compose" \
            and got.get("code") not in ("bridge", "changed"):
        # Its containers could not be set up here (Docker said why): its own
        # way without Docker, when the project has one.
        native = run_here.detect(_src_root(rec), port=run_here.free_port(), docker=False)
        if native.get("how") and not run_here.missing_programs(native):
            say(f"{rec['name']}: its containers did not start here ({got.get('line')}); "
                f"BuildBot runs it its own way.")
            run_here.remove(rec["id"], by="buildbot")
            plan = dict(native, fallback_of="compose")
            got = run_here.set_up(rec["id"], _src_root(rec), plan,
                                  by=str(rec.get("by") or "owner"), progress=say)
    if not got.get("ok"):
        run_here.remove(rec["id"], by="buildbot")
        if got.get("code") == "needs":
            rec["status"] = "held"
            rec["held"] = {"reasons": ["needs_program"], "ticket_target": ""}
            records.save(rec)
            line = wording.HOST_NEEDS.format(line=got.get("line") or "")
            say(line)
            return _result("held", f"{rec['name']}: {line}", lines, rec)
        return _fail(rec, wording.HOST_FAILED.format(line=got.get("line") or ""), lines, say)
    say(got["line"])
    if plan["how"] == "web_build_static":
        files_dir = run_here.build_output(rec["id"])
        if not files_dir:
            run_here.remove(rec["id"], by="buildbot")
            return _fail(rec, build_failed_line("no_page"), lines, say)
        try:
            return _add_static(rec, say=say, lines=lines, code_sha=code_sha,
                               accepted=accepted, files_dir=files_dir)
        finally:
            # The app host keeps its own copy; the build folder is not kept.
            run_here.remove(rec["id"], by="buildbot")
    rec["mode"] = _HOST_MODES[plan["how"]]
    rec["status"] = "ready"
    rec["held"] = {"reasons": [], "ticket_target": ""}
    rec["added_at"] = _now()
    records.save(rec)
    _cleanup(rec["id"])          # the download; its own copy runs from host_apps
    if (plan.get("interfaces") or {}).get("mcp"):
        mcp = run_here.register_mcp(rec["id"], rec["name"], by=str(rec.get("by") or "owner"))
        if mcp.get("ok"):
            say(wording.HOST_MCP.format(line=mcp["line"]))
    if plan.get("start"):
        started = run_here.start(rec["id"], by=str(rec.get("by") or "owner"),
                                 wait=HOST_READY_WAIT)
        now = started.get("status") or {}
        if now.get("state") == "running":
            say(wording.HOST_RUNNING.format(url=now.get("url") or ""))
        elif started.get("ok") and now.get("state") == "starting":
            say(wording.HOST_STARTING)
        else:
            say(wording.HOST_NOT_STARTED.format(
                why=str(now.get("why") or started.get("line") or "")))
    elif plan["how"] in ("node_cli", "python_cli"):
        names = sorted(run_here.cli_commands(rec["id"]))
        if names:
            say(wording.HOST_TOOL_READY.format(names=", ".join(names[:6])))
    else:
        say(wording.HOST_LIBRARY)
    return _result("added", wording.ADDED.format(name=rec["name"]), lines, rec)


def _prepared(rec: dict, *, verdict: dict, mode: str, code_sha: str, words: str,
              lines: list[str], say: Callable[[str], None],
              licence: Optional[dict] = None) -> dict[str, Any]:
    """Checked, nothing added: what one yes would add (the yes card)."""
    from puffpai import drawbridge
    decision = str(verdict.get("verdict") or "")
    card: dict[str, Any] = {"verdict": decision, "code_sha": code_sha, "mode": mode,
                            "hold_words": words, "host": None, "host_sha": ""}
    card_lines = [wording.CARD_CHECKED if decision == drawbridge.ALLOW
                  else wording.CARD_HOLD.format(reason=words)]
    if (rec.get("from") or {}).get("own_project") is True:
        card["own_project"] = True
        card_lines.append(f"{wording.OWN_PROJECT}.")
    elif isinstance(licence, dict):
        # The licence for running it herself: what it is, and a plain note
        # when it has none, or terms about sharing it.
        card["licence"] = {k: licence.get(k) for k in ("kind", "name", "file", "words")}
        card_lines.append(licence.get("line") or f"Its licence: {licence.get('words')}.")
    host_mode = mode in ("other", "cli_tool") or (mode == "web_build" and not box_ready())
    if host_mode:
        plan = _host_plan(rec, mode)
        host = _host_card(rec, plan, code_sha)
        card["host"] = host
        card["host_sha"] = host["sha"]
        if not host["how"]:
            card_lines.append(wording.HOST_CANNOT.format(why=host["why_not"] or "it does not "
                                                         "say how it starts"))
        else:
            card_lines += host["lines"]
    elif mode == "web_build":
        card_lines.append(wording.CARD_BOX)
    else:
        card_lines.append(wording.CARD_PAGE)
    card["lines"] = card_lines
    stash = _load_stash(rec["id"])
    stash["prepared"] = card
    stash["files_yes"] = _files_now(rec)
    _save_stash(rec["id"], stash)
    rec["status"] = "checked"
    records.save(rec)
    return _result("checked", wording.CARD_READY.format(name=rec["name"]), lines, rec,
                   card=card)


def prepare_code_project(item: dict, ctx: Any) -> dict[str, Any]:
    """Download and check one find and read how it runs, adding nothing.
    ``{"status": "checked"|"refused"|"failed"|"held", "card"?, ...}``; the
    card (``lines``, ``code_sha``, ``host_sha``, ``host``) is what the yes
    card shows."""
    return apply_code_project(item, ctx, prepare=True)


def prepared_card(integration_id: str) -> Optional[dict]:
    """The card of a checked item still waiting for its one yes, or None."""
    rec = records.get(integration_id) if records.valid_id(integration_id) else None
    if rec is None or rec.get("status") != "checked":
        return None
    card = _load_stash(rec["id"]).get("prepared")
    return card if isinstance(card, dict) else None


def finish_prepared(integration_id: str, *, code_sha: str, host_sha: str = "",
                    progress: Callable[[str], None] = lambda _l: None) -> dict[str, Any]:
    """Her one yes to a checked item: add exactly what the card showed. The
    files, the Drawbridge rows and the plan must all still be what she was
    shown; anything changed is asked again, nothing of it is added."""
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        try:
            progress(line)
        except Exception:  # noqa: BLE001
            pass
    with _DECIDE_LOCK:
        card = prepared_card(integration_id)
        if card is None:
            return _result("failed", wording.NOT_WAITING, lines)
        rec = records.get(integration_id)
        if str(card.get("code_sha") or "") != str(code_sha or "") \
                or str(card.get("host_sha") or "") != str(host_sha or ""):
            return _result("failed", wording.STILL_HELD_CHANGED.format(name=rec["name"]),
                           lines, rec)
        from puffpai import drawbridge
        accepted: dict[str, str] = {}
        if card.get("verdict") != drawbridge.ALLOW:
            accepted["code"] = str(card["code_sha"])
        if card.get("host_sha"):
            accepted["host"] = str(card["host_sha"])
        stash = _load_stash(rec["id"])
        stash["accepted"] = accepted
        _save_stash(rec["id"], stash)
        rec["status"] = "checking"
        records.save(rec)
        try:
            return _check_and_continue(rec, say=say, lines=lines, accepted=accepted)
        except fetch.FetchRefused:
            return _result("held", f"{rec['name']}: {wording.NOT_IN_BUILD}", lines, rec)
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrate: adding after the yes failed: %s", type(exc).__name__)
            return _fail(rec, wording.SET_UP_FAILED, lines, say)


# ---------------------------------------------------------------------------
# the owner's yes to a held item
# ---------------------------------------------------------------------------

class DecideError(Exception):
    def __init__(self, line: str, code: str):
        super().__init__(line)
        self.line = line
        self.code = code


def decide(integration_id: str, yes: bool, *, device_id: str) -> dict[str, Any]:
    """The owner's answer to a held item, from a paired device (or terminal).

    A yes mints and redeems the ``add_on`` ticket ``held:<id>:<sha16>`` for
    *device_id*, re-checks the files, and continues only when the check says
    exactly what the owner was shown. One answer at a time. Raises
    :class:`DecideError`.
    """
    with _DECIDE_LOCK:
        return _decide(integration_id, yes, device_id=device_id)


def _decide(integration_id: str, yes: bool, *, device_id: str) -> dict[str, Any]:
    from puffpai.security import consent_tickets
    if not consent_tickets.usable_device(device_id):
        raise DecideError(wording.DECIDE_NOT_PAIRED, "not_paired")
    rec = records.get(integration_id)
    if rec is None:
        raise DecideError(wording.UNKNOWN_ITEM, "unknown")
    target = str((rec.get("held") or {}).get("ticket_target") or "")
    if rec.get("status") != "held" or not target:
        raise DecideError(wording.NOT_WAITING, "not_waiting")
    lines: list[str] = []
    say = lines.append
    if not yes:
        rec["status"] = "refused"
        rec["held"] = {"reasons": list((rec.get("held") or {}).get("reasons") or []),
                       "ticket_target": ""}
        records.save(rec)
        _remove_box_project(rec)
        _cleanup(rec["id"])
        return _result("refused", wording.LEFT_OUT.format(name=rec["name"]), lines, rec)
    full = _load_stash(rec["id"])
    stash = dict(full.get("held") or {})
    stage, sha = str(stash.get("stage") or ""), str(stash.get("sha") or "")
    if stage not in STAGES or not sha:
        raise DecideError(wording.NOT_WAITING, "not_waiting")
    if _ticket_target(rec["id"], sha) != target:
        raise DecideError(wording.NOT_IN_BUILD if not _ticket_target(rec["id"], "0" * 64)
                          else wording.NOT_WAITING, "target")
    sentence = f"Add {rec['name']} anyway, with what Drawbridge found."
    try:
        ticket = consent_tickets.mint("add_on", target, sentence, device_id)
    except consent_tickets.TicketRefused as exc:
        raise DecideError(exc.message, exc.code) from None
    ok, verdict = consent_tickets.redeem(ticket, device_id, "add_on", target)
    if not ok:
        raise DecideError(consent_tickets.refusal_words(verdict), verdict)
    # Her yes covers exactly this stage's finding; earlier yeses (for the
    # other stage) are kept, so a project held once for its files and once
    # for its app can finish.
    accepted = {k: str(v) for k, v in (full.get("accepted") or {}).items()
                if k in STAGES and isinstance(v, str)}
    if stage == "files":
        # A yes to changed files is a yes to look again, not to what the old
        # files were said yes to: every earlier yes is dropped.
        accepted = {}
        full.pop("app_codes", None)
    accepted[stage] = sha
    full["accepted"] = accepted
    # Her yes covers exactly the files as they were when she was asked.
    full["files_yes"] = str(stash.get("files") or "")
    if stage == "app":
        full["app_codes"] = [str(c) for c in stash.get("codes") or [] if isinstance(c, str)]
    _save_stash(rec["id"], full)
    try:
        out = _check_and_continue(rec, say=say, lines=lines, accepted=accepted)
    except fetch.FetchRefused:
        raise DecideError(wording.NOT_IN_BUILD, "not_in_build") from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("integrate: adding after a yes failed: %s", type(exc).__name__)
        return _fail(rec, wording.SET_UP_FAILED, lines, say)
    if out.get("status") == "held":
        now = dict(_load_stash(rec["id"]).get("held") or {})
        # "Changed" only when a stage she already said yes to now finds
        # something else, or the files are not the ones she said yes to; a
        # hold at the next stage is simply the next question.
        now_stage = str(now.get("stage") or "")
        if now_stage == "files" or (now_stage in accepted
                                    and accepted[now_stage] != str(now.get("sha") or "")):
            out["line"] = wording.STILL_HELD_CHANGED.format(name=rec["name"]) + " " + out["line"]
    return out


# ---------------------------------------------------------------------------
# wiring (called from factory_wiring.connect, before Drawbridge closes)
# ---------------------------------------------------------------------------

def resume_waiting(integration_id: str, *,
                   progress: Callable[[str], None] = lambda _l: None) -> dict[str, Any]:
    """Continue an item that waited for the locked box (``waiting_box``), for
    the box's Finish. Everything is checked again from the files already
    downloaded; only the yeses she already gave count."""
    rec = records.get(integration_id)
    if rec is None or rec.get("status") != "waiting_box":
        return _result("failed", wording.NOT_WAITING, [])
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        try:
            progress(line)
        except Exception:  # noqa: BLE001
            pass
    accepted = {k: str(v) for k, v in (_load_stash(rec["id"]).get("accepted") or {}).items()
                if k in STAGES and isinstance(v, str)}
    with _DECIDE_LOCK:
        try:
            return _check_and_continue(rec, say=say, lines=lines, accepted=accepted)
        except fetch.FetchRefused:
            return _result("held", f"{rec['name']}: {wording.NOT_IN_BUILD}", lines, rec)
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrate: finishing failed: %s", type(exc).__name__)
            return _fail(rec, wording.SET_UP_FAILED, lines, say)


def finish_waiting_item(entry: dict, progress: Callable[[str], None]) -> dict[str, Any]:
    """The box's Finish applier for ``code_project`` (registered with
    ``box.driver.register_waiting_applier``): ``{"status", "line"}``, status
    ``waiting`` when it has to wait again. Only the integration id is read
    from the entry; the record (which must still be ``waiting_box``) and the
    files already downloaded decide the rest."""
    item = entry.get("item") if isinstance(entry, dict) else None
    iid = str((item or {}).get("integration") or (item or {}).get("id") or "")
    if not records.ID_RE.match(iid):
        return {"status": "failed", "line": wording.NOT_WAITING}
    got = resume_waiting(iid, progress=progress)
    rec = records.get(iid) or {}
    status = "waiting" if rec.get("status") == "waiting_box" else str(got.get("status") or "failed")
    return {"status": status, "line": str(got.get("line") or "")}


def _remove_by_undo(undo: dict, by: str) -> dict[str, Any]:
    """add_more remover ``integration``: ``undo = {"kind": "integration", "ref": <id>}``."""
    ref = str((undo or {}).get("ref") or "")
    return records.remove(ref, by=str(by or "owner"))


def connect() -> list[str]:
    """Register this module's checkers, plan appliers, the remover, the action
    sources and the ability rows. Returns what was done. Called by
    ``factory_wiring.connect_round3`` before Drawbridge's registry closes."""
    from puffpai import add_more, build_abilities, drawbridge
    from puffpai.integrate import abilities, actions, data_import, palette, pictures
    done: list[str] = []
    for kind, rows_fn, block, hold, version in (
            (KIND, code_project_rows, BLOCK_CODES, HOLD_CODES, CHECKER_VERSION),
            (data_import.KIND, data_import.data_rows, data_import.BLOCK_CODES,
             data_import.HOLD_CODES, data_import.CHECKER_VERSION),
            (pictures.KIND, pictures.picture_rows, pictures.BLOCK_CODES,
             pictures.HOLD_CODES, pictures.CHECKER_VERSION),
            (palette.KIND, palette.palette_rows, palette.BLOCK_CODES,
             palette.HOLD_CODES, palette.CHECKER_VERSION)):
        drawbridge.register_checker(kind, rows_fn, block_codes=block, hold_codes=hold,
                                    version=version)
        done.append(f"drawbridge:{kind}")
    appliers = ((KIND, apply_code_project), ("data_import", apply_code_project),
                ("picture_set", pictures.apply_picture),
                ("folder_palette", palette.apply_palette))
    factory = optional("puffpai.wizard.factory")
    register_plan = getattr(factory, "register_plan_applier", None) if factory else None
    for kind, fn in appliers:
        if register_plan is not None:
            register_plan(kind, fn)
            done.append(f"plan_applier:{kind}")
        else:
            done.append(f"plan_applier:{kind} not in this build yet")
        add_more.register_applier(kind, fn)
        done.append(f"add_more:{kind}")
    add_more.register_remover("integration", _remove_by_undo)
    done.append("add_more_remover:integration")
    for kind, list_fn, run_fn in (("data_query", data_import.list_actions,
                                   data_import.run_action),
                                  ("palette", palette.list_actions, palette.run_action)):
        try:
            actions.register_source(kind, list_fn, run_fn)
            done.append(f"actions:{kind}")
        except ValueError as exc:  # already registered in this process
            done.append(f"actions:{kind} {exc}")
    build_abilities.register_provider(abilities.ability_rows)
    done.append("abilities:integrate")
    try:
        from puffpai.integrate import host_tools, run_here
        host_tools.register_all()
        done.append("tools:host_apps")
        threading.Thread(target=run_here.autostart_all, daemon=True,
                         name="host-apps-autostart").start()
        done.append("host_apps:autostart")
    except Exception as exc:  # noqa: BLE001 - the rest of the bot still starts
        logger.warning("integrate: host apps not connected: %s", type(exc).__name__)
        done.append("host_apps: not connected")
    driver = optional("puffpai.box.driver")
    register_waiting = getattr(driver, "register_waiting_applier", None) if driver else None
    if callable(register_waiting):
        register_waiting(KIND, finish_waiting_item)
        done.append(f"box_waiting:{KIND}")
    else:
        done.append(f"box_waiting:{KIND} not in this build yet")
    return done


__all__ = ["BLOCK_CODES", "CHECKER_VERSION", "DecideError", "HOLD_CODES", "KIND", "STAGES",
           "box_ready", "finish_prepared", "lock_offsite", "prepare_code_project",
           "prepared_card", "resume_waiting",
           "apply_code_project", "build_draft", "code_project_rows", "connect",
           "decide", "finish_waiting_item", "friendly_name", "plan_facts_from"]
