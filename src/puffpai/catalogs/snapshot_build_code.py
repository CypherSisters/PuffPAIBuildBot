"""Build the shipped catalog snapshots for "where to look" (run by a maintainer, never at run time).

Two snapshots, each a deterministic gzip JSON file (the same input always
gives the same bytes, so the vector files' ``snapshot_sha256`` stays valid):

* ``awesome_selfhosted_<date>.json.gz`` from the awesome-selfhosted-data
  archive (a GitHub ``tar.gz`` of the repository). The YAML files are parsed
  here with ``yaml.safe_load`` only; the running bot never imports YAML. The
  archive is read member by member in memory (nothing is extracted to disk),
  only regular files under ``software/`` and ``tags/`` and the two licence
  lists, each at most 256 KB. The source commit comes from the archive's own
  header. The data is CC BY-SA 3.0: the snapshot keeps the attribution and is
  shared under the same licence (THIRD_PARTY_NOTICES.md).
* ``hacs_<date>.json.gz`` from the HACS default lists and HACS's published
  data files (integration, plugin, theme). Rows are look-only: BuildBot shows
  them as things to look at, never adds them.

It also builds the ``licence_words`` part of ``config/where_to_look.json``:
the long licences BuildBot recognises, word by word (see
:func:`build_licence_words`).

Usage::

    py -3.14 -m puffpai.catalogs.snapshot_build_code awesome <asd.tar.gz> <out.json.gz>
    py -3.14 -m puffpai.catalogs.snapshot_build_code hacs <folder> <out.json.gz> <commit>
    py -3.14 -m puffpai.catalogs.snapshot_build_code licence [--check]

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import sys
import tarfile
from typing import Any, Optional

AWESOME_SCHEMA = "awesome_selfhosted_snapshot/v1"
HACS_SCHEMA = "hacs_snapshot/v1"
AWESOME_SOURCE = "https://github.com/awesome-selfhosted/awesome-selfhosted-data"
HACS_SOURCES = ("https://github.com/hacs/default", "https://data-v2.hacs.xyz/")
MEMBER_MAX = 256 * 1024
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_REPO_URL_RE = re.compile(r"^https://(github\.com|codeberg\.org)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)"
                          r"(?:\.git)?/?$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def slug(text: str) -> str:
    return _SLUG_RE.sub("-", str(text or "").lower()).strip("-")[:80]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _url(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text.startswith("https://") or len(text) > 300 or any(c.isspace() for c in text):
        return None
    return text


def repo_key(url: Any) -> Optional[str]:
    """``host/owner/name`` (lower case) for a GitHub or Codeberg project address, else None."""
    m = _REPO_URL_RE.match(str(url or "").strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}".lower()


def write_json_gz(obj: Any, out_path: str) -> str:
    """Deterministic gzip JSON (no timestamp, sorted keys). Returns the path."""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
        gz.write(raw)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, out_path)
    return out_path


# ---------------------------------------------------------------------------
# awesome-selfhosted
# ---------------------------------------------------------------------------

def _members(tar: tarfile.TarFile) -> dict[str, bytes]:
    """Regular files we read, keyed by their path under the archive's top folder."""
    out: dict[str, bytes] = {}
    for m in tar:
        if not m.isreg() or m.size > MEMBER_MAX:
            continue
        parts = m.name.replace("\\", "/").split("/")
        if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
            continue
        rel = "/".join(parts[1:])
        wanted = (rel.startswith(("software/", "tags/")) and rel.endswith(".yml")
                  and rel.count("/") == 1) or rel in ("licenses.yml", "licenses-nonfree.yml")
        if not wanted:
            continue
        fh = tar.extractfile(m)
        if fh is not None:
            out[rel] = fh.read(MEMBER_MAX + 1)[:MEMBER_MAX]
    return out


def build_awesome_selfhosted(tar_path: str, out_path: str, *, generated: str) -> dict[str, Any]:
    import yaml   # build time only

    with tarfile.open(tar_path, "r:gz") as tar:
        commit = str((tar.pax_headers or {}).get("comment") or "")
        files = _members(tar)
    if not _SHA_RE.match(commit):
        raise ValueError("the archive does not name its source commit")

    def load(rel: str) -> Any:
        return yaml.safe_load(files[rel].decode("utf-8"))

    nonfree = {str(x.get("identifier")) for x in (load("licenses-nonfree.yml") or [])
               if isinstance(x, dict)} if "licenses-nonfree.yml" in files else set()
    tags = {}
    for rel in sorted(k for k in files if k.startswith("tags/")):
        data = load(rel) or {}
        if isinstance(data, dict) and data.get("name"):
            tags[str(data["name"])] = slug(data["name"])
    rows = []
    for rel in sorted(k for k in files if k.startswith("software/")):
        data = load(rel)
        if not isinstance(data, dict) or not data.get("name"):
            continue
        licenses = [str(x)[:60] for x in data.get("licenses") or [] if isinstance(x, str)][:6]
        row = {
            "id": "awesome_selfhosted:" + slug(rel[len("software/"):-len(".yml")]),
            "name": _text(data.get("name"), 100),
            "description": _text(data.get("description"), 400),
            "tags": [str(t)[:60] for t in data.get("tags") or [] if isinstance(t, str)][:12],
            "licenses": licenses,
            "nonfree": any(x in nonfree for x in licenses),
            "platforms": [str(p)[:40] for p in data.get("platforms") or []
                          if isinstance(p, str)][:12],
            "website_url": _url(data.get("website_url")),
            "source_code_url": _url(data.get("source_code_url")),
            "demo_url": _url(data.get("demo_url")),
            "stars": data.get("stargazers_count") if isinstance(data.get("stargazers_count"), int)
            else None,
            "updated_at": str(data.get("updated_at"))[:10] if data.get("updated_at") else None,
            "archived": data.get("archived") is True,
            "depends_3rdparty": data.get("depends_3rdparty") is True,
        }
        rows.append(row)
    snap = {
        "schema": AWESOME_SCHEMA, "generated": generated, "source": AWESOME_SOURCE,
        "source_commit": commit, "license": "CC-BY-SA-3.0",
        "attribution": ("Data from awesome-selfhosted-data by the awesome-selfhosted "
                        "contributors, CC BY-SA 3.0, "
                        "https://github.com/awesome-selfhosted/awesome-selfhosted-data. "
                        "Changed: fields selected and reshaped; shared under CC BY-SA 3.0."),
        "tag_slugs": dict(sorted(tags.items())),
        "rows": rows,
    }
    write_json_gz(snap, out_path)
    return snap


# ---------------------------------------------------------------------------
# HACS
# ---------------------------------------------------------------------------

HACS_CATEGORIES = ("integration", "plugin", "theme")


def build_hacs(folder: str, out_path: str, *, generated: str, commit: str) -> dict[str, Any]:
    if not _SHA_RE.match(commit or ""):
        raise ValueError("name the hacs/default commit the lists came from")
    rows = []
    for cat in HACS_CATEGORIES:
        with open(os.path.join(folder, f"default_{cat}.json"), encoding="utf-8") as fh:
            listed = {str(x).lower() for x in json.load(fh) if isinstance(x, str)}
        with open(os.path.join(folder, f"data_{cat}.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        for rec in (data.values() if isinstance(data, dict) else []):
            if not isinstance(rec, dict):
                continue
            full = str(rec.get("full_name") or "")
            if full.lower() not in listed or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full):
                continue
            rows.append({
                "id": f"hacs:{cat}:{full.lower()}",
                "category": cat,
                "full_name": full,
                "name": _text(rec.get("manifest_name") or full.split("/", 1)[1], 100),
                "description": _text(rec.get("description"), 400),
                "topics": [str(t)[:50] for t in rec.get("topics") or [] if isinstance(t, str)][:15],
                "stars": rec.get("stargazers_count") if isinstance(rec.get("stargazers_count"), int)
                else None,
                "updated_at": str(rec.get("last_updated") or "")[:10] or None,
                "url": f"https://github.com/{full}",
            })
    rows.sort(key=lambda r: r["id"])
    snap = {
        "schema": HACS_SCHEMA, "generated": generated, "sources": list(HACS_SOURCES),
        "source_commit": commit, "license": "MIT (the hacs/default lists)",
        "attribution": ("Lists from hacs/default (MIT, Copyright (c) 2019 HACS) and "
                        "descriptions from HACS's published data (data-v2.hacs.xyz), which "
                        "copies each project's own GitHub description. Look-only rows."),
        "rows": rows,
    }
    write_json_gz(snap, out_path)
    return snap


# ---------------------------------------------------------------------------
# the long licences, word by word
# ---------------------------------------------------------------------------
#
# A long licence (Apache-2.0, the GNU licences, MPL-2.0, CC0, OFL, the Creative
# Commons 4.0 licences) is recognised only when EVERY word of it is the
# licence's own word, in order. A reader that looks only at a few anchor
# sentences and not at what stands between them reads "You may not use the
# Work for commercial purposes." in the middle of the Apache licence as
# Apache-2.0.
#
# The words come from the licence texts kept under ``tests/fixtures/recorded_web``
# (the publishers' own texts, and the texts GitHub's "add a licence" button
# writes into projects), read into words by the same function the reader uses
# (``where_to_look._lic_norm``), so the words and the reader always agree.
#
# Real copies differ from each other in a few known places only. Those places
# are listed below, one by one, as exact alternatives (the Free Software
# Foundation's old and new postal addresses, the sample signer's name, "&" or
# "and"). The one place that is not a fixed text is the owner line of the
# Apache appendix ("Copyright [yyyy] [name of copyright owner]", which projects
# fill in): there, only whole lines that the reader's own copyright-line check
# accepts may stand (names only). Nothing else is a gap: there is no place
# where free words are allowed.
#
# Element shapes (``puffpai_licence_words/v1``):
#
# * ``{"w": "words ..."}`` - exactly these words;
# * ``{"alt": [[...], [...]]}`` - exactly one of these element lists;
# * ``{"opt": [...]}`` - this element list, or nothing (an appendix left out);
# * ``{"cline": n}`` - one to *n* whole copyright lines.
#
# The words live under the ``licence_words`` key of ``config/where_to_look.json``
# (the registry file), not in a file of their own.

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
LICENCE_FIXTURES = os.path.join(_ROOT, "tests", "fixtures", "recorded_web")
#: The Free Software Foundation's addresses as the GNU licences have carried
#: them over the years (the newest texts carry none, only a web address).
FSF_ADDRESSES = (
    "51 franklin street fifth floor boston ma 02110 1301 usa",
    "59 temple place suite 330 boston ma 02111 1307 usa",
    "675 mass ave cambridge ma 02139 usa",
)
#: The owner line of the Apache appendix, as the licence ships it.
APACHE_OWNER = "copyright yyyy name of copyright owner"


def _lw_words(name: str, fixtures: str) -> str:
    from puffpai.catalogs import where_to_look as w
    with open(os.path.join(fixtures, name), encoding="utf-8") as fh:
        return " ".join(w._lic_norm(fh.read()).split())


def _W(words: str) -> dict:
    return {"w": " ".join(words.split())}


def _ALT(*choices: list) -> dict:
    return {"alt": [list(c) for c in choices]}


def _lw_split(elements: list, key: str, make, *, _count: Optional[list] = None) -> list:
    """Every ``{"w"}`` element with *key* in it (inside optional parts too), cut
    there, with ``make()`` in its place. *key* must be found at least once."""
    key = " ".join(key.split())
    top = _count is None
    count = [0] if top else _count
    out: list = []
    for el in elements:
        if "opt" in el:
            out.append({"opt": _lw_split(el["opt"], key, make, _count=count)})
            continue
        if "w" not in el:
            out.append(el)
            continue
        text = " " + el["w"] + " "
        parts = text.split(" " + key + " ")
        count[0] += len(parts) - 1
        for i, part in enumerate(parts):
            if part.strip():
                out.append(_W(part))
            if i < len(parts) - 1:
                out.extend(make())
    if top and not count[0]:
        raise ValueError(f"not in the text: {key!r}")
    return out


def _lw_body_and_closing(words: str, closing_starts: Optional[str]) -> list:
    """The licence, with everything from *closing_starts* on as an optional part."""
    if closing_starts is None:
        return [_W(words)]
    key = " ".join(closing_starts.split())
    at = (" " + words + " ").find(" " + key + " ")
    if at < 0 or (" " + words + " ").count(" " + key + " ") != 1:
        raise ValueError(f"the closing part must start once: {key!r}")
    return [_W(words[:at].strip()), {"opt": [_W(words[at:].strip())]}]


def _lw_gnu_v2(elements: list) -> list:
    """The GPL-2.0 / LGPL-2.1 places where real copies differ."""
    els = _lw_split(elements, "free software foundation inc everyone is permitted",
                    lambda: [_W("free software foundation inc"),
                             _ALT([], *[[_W(a)] for a in FSF_ADDRESSES]),
                             _W("everyone is permitted")])
    els = _lw_split(els, "if not see", lambda: [_W("if not"), _ALT(
        [_W("see")], *[[_W("write to the free software foundation inc " + a)]
                       for a in FSF_ADDRESSES])])
    return _lw_split(els, "moe ghoul", lambda: [_ALT([_W("moe ghoul")], [_W("ty coon")])])


def licence_editions(fixtures: str = LICENCE_FIXTURES) -> list[dict]:
    """Every long licence edition, from the licence texts in *fixtures*."""
    out: list[dict] = []

    def add(spdx: str, elements: list) -> None:
        out.append({"id": spdx, "elements": elements})

    def words(name: str) -> str:
        return _lw_words(name, fixtures)

    apache = _lw_body_and_closing(words("license_Apache-2.0.txt"),
                                  "appendix how to apply the apache license to your work")
    apache = _lw_split(apache, APACHE_OWNER,
                       lambda: [_ALT([_W(APACHE_OWNER)], [{"cline": 3}])])
    add("Apache-2.0", apache)
    add("MPL-2.0", _lw_body_and_closing(words("license_MPL-2.0.txt"),
                                        "exhibit b incompatible with secondary licenses notice"))
    add("AGPL-3.0", _lw_body_and_closing(words("license_AGPL-3.0.txt"),
                                         "how to apply these terms to your new programs"))
    add("LGPL-3.0", _lw_body_and_closing(words("license_LGPL-3.0.txt"), None))
    gpl3 = _lw_body_and_closing(words("license_GPL-3.0.txt"),
                                "how to apply these terms to your new programs")
    # The GPL-3.0 that GitHub's "add a licence"
    # template writes has "{project} Copyright (C) {year} {fullname}" for the
    # appendix's interactive notice ("<program> Copyright (C) <year> <name of
    # author>"); real projects carry it as written. Exactly those words, no other.
    gpl3 = _lw_split(gpl3, "program copyright c year name of author this program comes",
                     lambda: [_ALT([_W("program copyright c year name of author")],
                                   [_W("project copyright c year fullname")]),
                              _W("this program comes")])
    add("GPL-3.0", gpl3)
    add("LGPL-2.1", _lw_gnu_v2(_lw_body_and_closing(
        words("license_LGPL-2.1.txt"), "how to apply these terms to your new libraries")))
    add("GPL-2.0", _lw_gnu_v2(_lw_body_and_closing(
        words("license_GPL-2.0.txt"), "how to apply these terms to your new programs")))
    add("CC0-1.0", _lw_body_and_closing(words("license_CC0-1.0.txt"),
                                        "for more information please see"))
    add("CC0-1.0", [_W(words("license_CC0-1.0_github.txt"))])
    ofl = words("license_OFL-1.1.txt")
    preamble = ("this font software is licensed under the sil open font license version 1 1 "
                "this license is copied below and is also available with a faq at")
    if not ofl.startswith(preamble + " "):
        raise ValueError("the OFL text does not start with its two naming lines")
    ofl_rest = _lw_split([_W(ofl[len(preamble):])], "permission conditions permission is hereby",
                         lambda: [_W("permission"), _ALT([], [_W("and")]),
                                  _W("conditions permission is hereby")])
    add("OFL-1.1", [{"opt": [_W(preamble)]}] + ofl_rest)
    for spdx in ("CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
                 "CC-BY-NC-ND-4.0", "CC-BY-ND-4.0"):
        add(spdx, [_W(words(f"license_{spdx}.txt"))])
    return out


def build_licence_words(fixtures: str = LICENCE_FIXTURES) -> dict:
    """The ``licence_words`` part of ``config/where_to_look.json``."""
    from puffpai.catalogs import where_to_look as w
    return {"schema": w.LICENCE_WORDS_SCHEMA,
            "generated_by": "python -m puffpai.catalogs.snapshot_build_code licence",
            "editions": licence_editions(fixtures)}


def registry_text(registry: dict) -> str:
    """``config/where_to_look.json`` as it is written (one-space indent, ASCII)."""
    return json.dumps(registry, indent=1, ensure_ascii=True) + "\n"


def with_licence_words(registry: dict, fixtures: str = LICENCE_FIXTURES) -> dict:
    """*registry* with its ``licence_words`` part built again (every other key kept, in order)."""
    from puffpai.catalogs import where_to_look as w
    out = dict(registry)
    out[w.LICENCE_WORDS_KEY] = build_licence_words(fixtures)
    return out


def write_licence_words(*, check: bool = False, path: Optional[str] = None) -> int:
    """Write the ``licence_words`` part into the registry file; with *check*,
    only say whether it is up to date (0) or not (1)."""
    from puffpai.catalogs import where_to_look as w
    path = path or w.registry_path()
    with open(path, encoding="utf-8") as fh:
        now = fh.read()
    text = registry_text(with_licence_words(json.loads(now)))
    if check:
        if now != text:
            print(f"{path}: licence_words is out of date (run: licence)")
            return 1
        print("up to date")
        return 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {path} ({len(text)} bytes)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "licence":
        return write_licence_words(check="--check" in argv[1:])
    if len(argv) >= 3 and argv[0] == "awesome":
        snap = build_awesome_selfhosted(argv[1], argv[2], generated=argv[3] if len(argv) > 3
                                        else "2026-09-27")
    elif len(argv) >= 4 and argv[0] == "hacs":
        snap = build_hacs(argv[1], argv[2], commit=argv[3],
                          generated=argv[4] if len(argv) > 4 else "2026-09-27")
    else:
        print(__doc__)
        return 2
    print(f"{len(snap['rows'])} rows written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
