"""The licence rule for running an app on the owner's own computer.

The rule: an app she runs for herself, on her own
computer, is never stopped over its licence. Any open licence passes (MIT,
BSD, Apache, ISC, MPL, LGPL, GPL, AGPL, EUPL, Unlicense, CC0 and the like);
a project with no licence, or with source-available or non-commercial terms
(Business Source, SSPL, Commons Clause, PolyForm, CC BY-NC, its own words),
may still run, with a plain note on the yes card about sharing it.

Only the licence rows change. Drawbridge's code checks (where it came from,
the pinned version, size, hidden connections, install scripts, words that
read like orders) are not touched, and neither is the stricter reader the
plan and the copying paths use (``catalogs.searchers.github.read_licence``):
a data set or picture set copied into the bot keeps that rule.

:func:`read` looks, in order, at licence files at the top of the project
(LICENSE, LICENCE, COPYING, UNLICENSE, with any ending, and LICENSE-MIT style
names), then in common folders (licenses/, legal/, docs/, .github/), then at
package.json, pyproject.toml, setup.cfg and Cargo.toml, then at the licence
the code site reported. Links are never followed; files are read up to
:data:`MAX_BYTES`. Everything it reads is data: nothing in a licence file
changes what BuildBot does beyond this note.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    tomllib = None  # type: ignore[assignment]

MAX_BYTES = 256 * 1024
OPEN, LIMITED, CUSTOM, NONE = "open", "limited", "custom", "none"

#: The exact words shown when no licence is found anywhere.
NO_LICENCE_LINE = ("This project has no licence file. You can run it for yourself; you may "
                   "not have the right to share it.")
LIMITED_LINE = ("Its licence ({name}) limits how it may be used or shared. You can run it "
                "for yourself; read its licence before you share it or use it for work.")
CUSTOM_LINE = ("It has licence words of its own that BuildBot cannot read for you. You can "
               "run it for yourself; read them before you share it or use it for work.")

#: Open licences (SPDX ids, lower case, without -only / -or-later / +).
OPEN_IDS = frozenset("""
mit mit-0 x11 bsd-2-clause bsd-3-clause bsd-3-clause-clear bsd-4-clause 0bsd apache-2.0
apache-1.1 isc mpl-2.0 mpl-1.1 lgpl-2.0 lgpl-2.1 lgpl-3.0 gpl-2.0 gpl-3.0 agpl-3.0
eupl-1.1 eupl-1.2 unlicense cc0-1.0 zlib wtfpl bsl-1.0 artistic-2.0 epl-1.0 epl-2.0
cddl-1.0 cddl-1.1 ms-pl ms-rl postgresql python-2.0 psf-2.0 ofl-1.1 cc-by-4.0 cc-by-3.0
cc-by-sa-4.0 cc-by-sa-3.0 unicode-dfs-2016 unicode-3.0 blueoak-1.0.0 upl-1.0 ncsa afl-3.0
osl-3.0 mulanpsl-2.0 ecl-2.0 cecill-2.1 vim isc-license gpl agpl lgpl bsd apache mpl
""".split())
#: Source-available or non-commercial terms: may run, with a note.
LIMITED_IDS = {
    "busl-1.1": "Business Source", "busl": "Business Source", "bsl-1.1": "Business Source",
    "sspl-1.0": "Server Side Public License", "sspl": "Server Side Public License",
    "elastic-2.0": "Elastic License", "commons-clause": "Commons Clause",
    "polyform-noncommercial-1.0.0": "PolyForm Noncommercial",
    "polyform-small-business-1.0.0": "PolyForm Small Business",
    "polyform-shield-1.0.0": "PolyForm Shield", "polyform-perimeter-1.0.0": "PolyForm Perimeter",
    "polyform-internal-use-1.0.0": "PolyForm Internal Use", "fsl-1.1-mit": "Functional Source",
    "fsl-1.1-apache-2.0": "Functional Source", "cc-by-nc-4.0": "CC BY-NC",
    "cc-by-nc-sa-4.0": "CC BY-NC-SA", "cc-by-nc-nd-4.0": "CC BY-NC-ND", "cc-by-nc-3.0": "CC BY-NC",
    "cc-by-nc-sa-3.0": "CC BY-NC-SA", "cc-by-nd-4.0": "CC BY-ND",
    "sustainable-use-1.0": "Sustainable Use License",
}

#: Words that add a restriction to whatever licence they sit in (checked first).
_LIMITING_TEXT = (
    ("commons clause", "Commons Clause"),
    ("business source license", "Business Source"),
    ("server side public license", "Server Side Public License"),
    ("elastic license", "Elastic License"),
    ("polyform noncommercial", "PolyForm Noncommercial"),
    ("polyform small business", "PolyForm Small Business"),
    ("polyform shield", "PolyForm Shield"),
    ("polyform perimeter", "PolyForm Perimeter"),
    ("polyform internal use", "PolyForm Internal Use"),
    ("functional source license", "Functional Source"),
    ("sustainable use license", "Sustainable Use License"),
    ("attribution-noncommercial", "CC BY-NC"),
    ("attribution noncommercial", "CC BY-NC"),
    ("(cc by-nc", "CC BY-NC"),
    ("cc by-nc ", "CC BY-NC"),
    ("for non-commercial use only", "non-commercial use only"),
    ("for noncommercial use only", "non-commercial use only"),
    ("for non-commercial purposes only", "non-commercial use only"),
    ("not for commercial use", "non-commercial use only"),
    ("may not be used for commercial purposes", "non-commercial use only"),
)
#: The GNU family by its title (the GPL 3 text names the Affero licence in
#: section 13, so the title decides first).
_GNU_TITLES = (("gnu affero general public license", "AGPL-3.0"),
               ("gnu lesser general public license", "LGPL"),
               ("gnu library general public license", "LGPL"),
               ("gnu general public license", "GPL"))
_OPEN_TEXT = (
    ("mozilla public license", "MPL-2.0"),
    ("european union public licen", "EUPL-1.2"),
    ("eclipse public license", "EPL-2.0"),
    ("apache license", "Apache-2.0"),
    ("this is free and unencumbered software released into the public domain", "Unlicense"),
    ("cc0 1.0 universal", "CC0-1.0"),
    ("creative commons zero", "CC0-1.0"),
    ("do what the fuck you want", "WTFPL"),
    ("boost software license", "BSL-1.0"),
    ("the artistic license 2.0", "Artistic-2.0"),
    ("common development and distribution license", "CDDL-1.0"),
    ("microsoft public license", "MS-PL"),
    ("universal permissive license", "UPL-1.0"),
    ("blue oak model license", "BlueOak-1.0.0"),
    ("postgresql license", "PostgreSQL"),
    ("sil open font license", "OFL-1.1"),
    ("mulan permissive software license", "MulanPSL-2.0"),
    ("creative commons attribution-sharealike", "CC-BY-SA-4.0"),
    ("creative commons attribution 4.0", "CC-BY-4.0"),
    ("permission is hereby granted, free of charge", "MIT"),
    ("altered source versions must be plainly marked", "Zlib"),
)

_FILE_RE = re.compile(r"^(?:un)?licen[cs]e(?:[._-].*)?$|^copying(?:[._-].*)?$", re.I)
_SUBFOLDERS = ("licenses", "licences", "license", "licence", "legal", "docs", "doc", ".github")
_SPDX_LINE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-() ]{2,120})")
_CLASSIFIER = {
    "mit license": "MIT", "bsd license": "BSD-3-Clause", "apache software license": "Apache-2.0",
    "isc license (iscl)": "ISC", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "gnu general public license v3 (gplv3)": "GPL-3.0",
    "gnu general public license v2 (gplv2)": "GPL-2.0",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0",
    "gnu affero general public license v3": "AGPL-3.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1",
    "the unlicense (unlicense)": "Unlicense", "zlib/libpng license": "Zlib",
    "european union public licence 1.2 (eupl 1.2)": "EUPL-1.2",
}
_RANK = {OPEN: 3, LIMITED: 2, CUSTOM: 1, NONE: 0}

#: Plain words for the common open families (the rest read "free to use (<id>)").
_OPEN_WORDS = {
    "MIT": "free to use (MIT)", "Apache-2.0": "free to use (Apache 2.0)",
    "BSD-2-Clause": "free to use (BSD)", "BSD-3-Clause": "free to use (BSD)",
    "ISC": "free to use (ISC)", "Unlicense": "free to use (public domain)",
    "CC0-1.0": "free to use (public domain)", "MPL-2.0": "free to use (Mozilla)",
    "GPL-2.0": "free to use, changes must stay open (GPL 2)",
    "GPL-3.0": "free to use, changes must stay open (GPL 3)",
    "AGPL-3.0": "free to use, changes must stay open (AGPL)",
    "LGPL": "free to use (LGPL)", "EUPL-1.2": "free to use, changes must stay open (EUPL)",
}


def _norm(spdx: str) -> str:
    low = spdx.strip().lower()
    for tail in ("-or-later", "-only", "+"):
        if low.endswith(tail):
            low = low[: -len(tail)]
    return low


def id_kind(spdx: Any) -> tuple[str, str]:
    """``(kind, name)`` for one SPDX id or a common family name."""
    raw = str(spdx or "").strip().strip("()")
    low = _norm(raw)
    if not low or low in ("none", "null", "unknown"):
        return NONE, ""
    if low in ("noassertion", "other"):
        # A code site's word for "it has a licence file I cannot name".
        return CUSTOM, ""
    if low in OPEN_IDS:
        return OPEN, raw
    if low in LIMITED_IDS:
        return LIMITED, LIMITED_IDS[low]
    for prefix, name in (("polyform", "PolyForm"), ("busl", "Business Source"),
                         ("sspl", "Server Side Public License"), ("cc-by-nc", "CC BY-NC"),
                         ("elastic", "Elastic License"), ("fsl-", "Functional Source")):
        if low.startswith(prefix):
            return LIMITED, name
    for prefix in ("gpl-", "agpl-", "lgpl-", "bsd-", "apache-", "mpl-", "eupl-", "epl-",
                   "cc-by-sa-", "cc-by-"):
        if low.startswith(prefix):
            return OPEN, raw
    words = {"mit": "MIT", "apache 2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
             "bsd": "BSD-3-Clause", "gplv3": "GPL-3.0", "gplv2": "GPL-2.0",
             "agplv3": "AGPL-3.0", "lgplv3": "LGPL-3.0", "public domain": "Unlicense"}
    if low in words:
        return OPEN, words[low]
    return CUSTOM, raw[:40]


def expression_kind(expr: Any) -> tuple[str, str]:
    """``(kind, shown)`` for an SPDX expression (``MIT OR Apache-2.0``,
    ``GPL-2.0-only WITH Classpath-exception-2.0``): OR takes the most open
    choice, AND the least open part."""
    text = " ".join(str(expr or "").split())
    if not text:
        return NONE, ""
    text = re.sub(r"\bWITH\s+[A-Za-z0-9.+-]+", "", text, flags=re.I)
    parts_or = [p for p in re.split(r"\s+OR\s+|/", text.replace("(", " ").replace(")", " "),
                                    flags=re.I) if p.strip()]
    best = (NONE, "")
    for alt in parts_or:
        worst: Optional[tuple[str, str]] = None
        for piece in re.split(r"\s+AND\s+", alt.strip(), flags=re.I):
            got = id_kind(piece)
            if worst is None or _RANK[got[0]] < _RANK[worst[0]]:
                worst = got
        if worst and _RANK[worst[0]] > _RANK[best[0]]:
            best = worst
    if best[0] == OPEN:
        return OPEN, " ".join(str(expr).split())[:60]
    return best


def text_kind(text: str) -> tuple[str, str]:
    """``(kind, name)`` for a licence file's words. A restriction anywhere
    in it wins over the licence it is added to; words it does not know are
    ``custom``."""
    low = " ".join(str(text or "").lower().split())
    if not low:
        return CUSTOM, ""
    for marker, name in _LIMITING_TEXT:
        if marker in low:
            return LIMITED, name
    m = _SPDX_LINE.search(str(text))
    if m:
        got = expression_kind(m.group(1))
        if got[0] != NONE:
            return got
    head = low[:1500]
    for where in (head, low):
        # The title that comes first names it (each text mentions the others).
        found = sorted((where.find(t), fam) for t, fam in _GNU_TITLES if t in where)
        if found:
            fam = found[0][1]
            if fam == "GPL":
                fam = "GPL-3.0" if "version 3" in where[:3000] else "GPL-2.0"
            elif fam == "LGPL":
                fam = "LGPL-3.0" if "version 3" in where[:3000] else "LGPL-2.1"
            return OPEN, fam
    for marker, fam in _OPEN_TEXT:
        if marker in low:
            return OPEN, fam
    if "redistribution and use in source and binary forms" in low:
        if "neither the name" in low or "endorse or promote" in low:
            return OPEN, "BSD-3-Clause"
        return OPEN, "BSD-2-Clause"
    if "permission to use, copy, modify, and/or distribute this software for any purpose" in low \
            or "permission to use, copy, modify, and distribute this software for any purpose" in low:
        return OPEN, "ISC"
    first = low[:200]
    for word, fam in (("mit license", "MIT"), ("bsd 3-clause", "BSD-3-Clause"),
                      ("bsd 2-clause", "BSD-2-Clause"), ("isc license", "ISC")):
        if first.startswith(word) or f" {word}" in first:
            return OPEN, fam
    return CUSTOM, ""


def _read(path: str) -> Optional[str]:
    """A plain file's text (never through a link), or None."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES)
    except OSError:
        return None
    return data.decode("utf-8", "replace")


def _licence_files(folder: str) -> list[str]:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if _FILE_RE.match(n)
            and not os.path.islink(os.path.join(folder, n))
            and os.path.isfile(os.path.join(folder, n))]


def _inside(root: str, rel: str) -> Optional[str]:
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/") or ":" in rel:
        return None
    return os.path.join(root, *rel.split("/"))


def _from_files(root: str) -> list[tuple[str, str, str]]:
    """``[(file, kind, name)]`` from licence files at the top, else in the
    common folders."""
    out = []
    for name in _licence_files(root):
        text = _read(os.path.join(root, name))
        if text is not None:
            out.append((name, *text_kind(text)))
    if out:
        return out
    for sub in _SUBFOLDERS:
        folder = os.path.join(root, sub)
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        for name in _licence_files(folder):
            text = _read(os.path.join(folder, name))
            if text is not None:
                out.append((f"{sub}/{name}", *text_kind(text)))
    return out


def _from_manifests(root: str) -> list[tuple[str, str, str]]:
    """``[(where, kind, name)]`` from the project's own manifest fields."""
    out: list[tuple[str, str, str]] = []
    text = _read(os.path.join(root, "package.json"))
    if text:
        try:
            pkg = json.loads(text)
        except ValueError:
            pkg = {}
        lic = pkg.get("license") if isinstance(pkg, dict) else None
        if isinstance(lic, dict):
            lic = lic.get("type")
        if not lic and isinstance(pkg, dict) and isinstance(pkg.get("licenses"), list):
            lic = " OR ".join(str(x.get("type") if isinstance(x, dict) else x)
                              for x in pkg["licenses"][:4])
        if isinstance(lic, str) and lic.strip():
            m = re.match(r"(?i)^see licen[cs]e in (.+)$", lic.strip())
            if m:
                path = _inside(root, m.group(1))
                body = _read(path) if path else None
                if body is not None:
                    out.append(("package.json", *text_kind(body)))
            elif lic.strip().upper() == "UNLICENSED":
                out.append(("package.json", CUSTOM, "all rights kept by its maker"))
            else:
                out.append(("package.json", *expression_kind(lic)))
    if tomllib is not None:
        for name in ("pyproject.toml", "Cargo.toml"):
            body = _read(os.path.join(root, name))
            if not body:
                continue
            try:
                data = tomllib.loads(body)
            except (ValueError, TypeError):
                continue
            proj = data.get("project") if name == "pyproject.toml" else data.get("package")
            proj = proj if isinstance(proj, dict) else {}
            poetry = ((data.get("tool") or {}).get("poetry") or {}) \
                if isinstance(data.get("tool"), dict) else {}
            lic = proj.get("license") or (poetry.get("license") if isinstance(poetry, dict)
                                          else None)
            if isinstance(lic, dict):
                if lic.get("file"):
                    path = _inside(root, str(lic["file"]))
                    file_text = _read(path) if path else None
                    if file_text is not None:
                        out.append((name, *text_kind(file_text)))
                        continue
                lic = lic.get("text")
            if isinstance(lic, str) and lic.strip():
                got = expression_kind(lic) if len(lic) < 80 else text_kind(lic)
                out.append((name, *got))
                continue
            for cls in proj.get("classifiers") or []:
                m = re.match(r"License :: (?:OSI Approved :: )?(.+)$", str(cls))
                if m and m.group(1).strip().lower() in _CLASSIFIER:
                    out.append((name, OPEN, _CLASSIFIER[m.group(1).strip().lower()]))
                    break
    body = _read(os.path.join(root, "setup.cfg"))
    if body:
        m = re.search(r"(?im)^\s*license\s*=\s*([^\n]{1,80})$", body)
        if m and not m.group(1).strip().lower().startswith("file:"):
            out.append(("setup.cfg", *expression_kind(m.group(1))))
    return out


def read(tree: str, *, reported: Any = "") -> dict[str, Any]:
    """The licence of the project at *tree* for running it here.

    ``{"kind", "name", "file", "source", "words", "line"}``: *kind* is
    ``open``, ``limited``, ``custom`` or ``none``; *line* is the plain note
    for the yes card ("" for an open licence, whose *words* say it)."""
    root = str(tree or "")
    found = _from_files(root)
    source = "file"
    if not found:
        found, source = _from_manifests(root), "manifest"
    if not found:
        got = expression_kind(reported)
        if got[0] != NONE:
            found, source = [("the code site", *got)], "reported"
    if not found:
        return describe(NONE, "", None, "")
    if any(k == LIMITED for _w, k, _n in found):
        where, kind, name = next(f for f in found if f[1] == LIMITED)
    elif any(k == OPEN for _w, k, _n in found):
        opens = [f for f in found if f[1] == OPEN]
        where, kind = opens[0][0], OPEN
        name = " or ".join(dict.fromkeys(n for _w, _k, n in opens if n))[:60]
    else:
        where, kind, name = found[0]
    return describe(kind, name, where if source == "file" else None, source,
                    where=where)


def describe(kind: str, name: str, file: Optional[str], source: str, *,
             where: str = "") -> dict[str, Any]:
    """The licence record with its plain words."""
    if kind == OPEN:
        words = _OPEN_WORDS.get(name, f"free to use ({name})" if name else "free to use")
        if source == "manifest" and where:
            words += f", as its {where} says"
        line = ""
    elif kind == LIMITED:
        words = f"limits on use or sharing ({name})"
        line = LIMITED_LINE.format(name=name or "its own terms")
    elif kind == CUSTOM:
        words = "its own licence words"
        line = CUSTOM_LINE
    else:
        words = "no licence file"
        line = NO_LICENCE_LINE
    return {"kind": kind, "name": name, "file": file, "source": source, "words": words,
            "line": line}


def rows(info: Optional[dict]) -> list[dict[str, str]]:
    """The licence row of a code project that runs on her own computer: a
    pass for an open licence, a plain note otherwise. Never a block or a
    hold: the licence decides nothing here."""
    info = info if isinstance(info, dict) else describe(NONE, "", None, "")
    kind = str(info.get("kind") or NONE)
    check = "Licence for running it yourself"
    if kind == OPEN:
        return [{"check": check, "result": "pass", "why": f"Its licence: {info.get('words')}.",
                 "code": "license_ok"}]
    return [{"check": check, "result": "note", "why": str(info.get("line") or NO_LICENCE_LINE),
             "code": "no_license" if kind == NONE else "not_open"}]


def note_for_reported(spdx_or_list: Any) -> str:
    """The plain note for a licence a list or code site reports (before
    anything is downloaded): "" for an open one."""
    if isinstance(spdx_or_list, (list, tuple)):
        text = " AND ".join(str(x) for x in spdx_or_list if x)
    else:
        text = str(spdx_or_list or "")
    kind, name = expression_kind(text)
    return describe(kind, name, None, "reported")["line"]


__all__ = ["CUSTOM", "LIMITED", "NONE", "NO_LICENCE_LINE", "OPEN", "describe",
           "expression_kind", "id_kind", "note_for_reported", "read", "rows", "text_kind"]
