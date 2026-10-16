"""The Hermes skill list (``skills-index.json``) and Hermes's way of matching it.

Hermes Agent publishes one file listing about 100,000 skills from ClawHub,
skills.sh, GitHub collections, LobeHub, browse.sh and its own official set,
rebuilt every day. BuildBot reads it at most once a day (about 40 MB, under the
"Hermes skill list" switch), keeps a small copy on this computer, and matches
words the way Hermes does (``tools/skills_hub_official.py``,
``HermesIndexSource.search``): a find must contain the words; exact name first,
then name starting with them, then a whole word of the name, then inside the
name, then anywhere; the list's own order breaks ties.

Matching adapted from Hermes Agent (MIT, Copyright (c) 2025 Nous Research).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Iterable, Optional

from puffpai.finding import net
from puffpai.finding import wording as W

HOST = "nousresearch.github.io"
URL = f"https://{HOST}/hermes-agent/docs/api/skills-index.json"
MAX_BYTES = 96 * 1024 * 1024
TTL_S = 24 * 3600
KEEP = ("name", "description", "source", "identifier", "trust_level", "repo", "path", "tags")

_LOCK = threading.Lock()
_LOAD_LOCK = threading.Lock()
_MEM: dict[str, Any] = {"at": 0.0, "rows": None}


def _copy_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "cache", "finding", "hermes_skills.json")


def _compact(data: Any) -> list[dict]:
    rows = (data or {}).get("skills") if isinstance(data, dict) else None
    out: list[dict] = []
    for s in rows or []:
        if isinstance(s, dict) and s.get("identifier"):
            row = {k: s.get(k) for k in KEEP}
            row["tags"] = [str(t) for t in (s.get("tags") or [])][:12]
            out.append(row)
    return out


def load(*, refresh: bool = False) -> Optional[list[dict]]:
    """The list's rows, from memory, the copy on disk, or the site. None when
    it could not be had (the switch is off, or the site did not answer)."""
    with _LOAD_LOCK:     # one download at a time, however many searches wait
        return _load(refresh)


def _load(refresh: bool) -> Optional[list[dict]]:
    now = time.time()
    with _LOCK:
        if not refresh and _MEM["rows"] is not None and now - _MEM["at"] < TTL_S:
            return _MEM["rows"]
    path = _copy_path()
    if not refresh:
        try:
            if now - os.path.getmtime(path) < TTL_S:
                with open(path, encoding="utf-8") as fh:
                    rows = json.load(fh)
                if isinstance(rows, list):
                    with _LOCK:
                        _MEM.update(at=os.path.getmtime(path), rows=rows)
                    return rows
        except (OSError, ValueError):
            pass
    ans = net.get(URL, hosts=[HOST], service="shelf_hermes", max_bytes=MAX_BYTES, cache_s=0)
    rows = _compact(ans.json()) if ans.ok else []
    if not rows:
        try:   # an older copy is better than nothing
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            return old if isinstance(old, list) else None
        except (OSError, ValueError):
            return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    with _LOCK:
        _MEM.update(at=now, rows=rows)
    return rows


def forget() -> None:
    with _LOCK:
        _MEM.update(at=0.0, rows=None)


def rank(rows: Iterable[dict], words: str,
         fields: Callable[[dict], tuple[str, str, list, str]]) -> list[dict]:
    """Hermes's ranking: rows whose text holds *words*, best first.

    *fields* gives ``(name, description, tags, identifier)`` for a row."""
    q = " ".join(str(words or "").lower().split())
    if not q:
        return []
    scored: list[tuple[int, int, dict]] = []
    for i, row in enumerate(rows):
        name, desc, tags, ident = fields(row)
        name = name.lower()
        hay = " ".join([name, desc.lower(), " ".join(t.lower() for t in tags), ident.lower()])
        if q not in hay:
            continue
        ranks = (name == q, name.startswith(q), q in name.split(), q in name, True)
        scored.append((ranks.index(True), i, row))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in scored]


def _fields(row: dict) -> tuple[str, str, list, str]:
    return (str(row.get("name") or ""), str(row.get("description") or ""),
            list(row.get("tags") or []), str(row.get("identifier") or ""))


_SHELF_OF = {"official": "Hermes", "github": "GitHub", "clawhub": "ClawHub",
             "skills.sh": "skills.sh", "lobehub": "LobeHub", "browse-sh": "browse.sh"}


def search(words: str, limit: int = 10, rows: Optional[list[dict]] = None) -> list[dict]:
    """Finds from the Hermes list, in Hermes's order. Single words are tried
    when the whole phrase matches nothing (Hermes matches the phrase as one)."""
    from puffpai.finding import sites
    rows = load() if rows is None else rows
    if not rows:
        return []
    hits = rank(rows, words, _fields)
    if not hits:
        for w in sorted(str(words or "").split(), key=len, reverse=True)[:2]:
            if len(w) > 3:
                hits = rank(rows, w, _fields)
                if hits:
                    break
    out: list[dict] = []
    for i, row in enumerate(hits[:limit]):
        src = str(row.get("source") or "")
        ident = str(row.get("identifier") or "")
        trust = str(row.get("trust_level") or "")
        repo, path = str(row.get("repo") or ""), str(row.get("path") or "")
        if src == "clawhub":
            fid, can = f"clawhub:{ident}", "install"
        elif src == "skills.sh" and ident.startswith("skills-sh/") and ident.count("/") == 3:
            fid, can = "skills.sh:" + ident.removeprefix("skills-sh/"), "install"
        elif src in ("official", "github") and repo.count("/") == 1 and path:
            fid, can = f"hermes:{ident}", "install"
        else:
            fid, can = f"hermes:{ident}", "look"
        desc = str(row.get("description") or "")
        if desc.startswith("Indexed by skills.sh from"):
            desc = f"A skill in the {repo} project on GitHub." if repo else ""
        verdict = "builtin" if trust == "builtin" else ("trusted" if trust == "trusted" else "")
        url = f"https://github.com/{repo}/tree/HEAD/{path}" if repo and path else (
            f"https://clawhub.ai/skills/{ident}" if src == "clawhub" else "")
        out.append(sites.make(
            site="hermes", type_="skill", id=fid, name=str(row.get("name") or ident),
            by=repo.split("/")[0] if repo else _SHELF_OF.get(src, src), what=desc, url=url,
            verdict=verdict, verdict_shelf="Hermes" if trust == "builtin" else "the Hermes list",
            can=can, look_why=W.LOOK_WHY["shelf"] if can == "look" else "", rank=i,
            raw={"source": src, "identifier": ident, "repo": repo, "path": path,
                 "trust": trust}))
    return out


__all__ = ["HOST", "URL", "forget", "load", "rank", "search"]
