"""Drawbridge's static check for a page that hides encoded code and runs it.

Found 2026-10-14 (NEXT_FIXES item 15): a project's only ``index.html``
(titled "Site") held large encrypted blobs and a small obfuscated loader that
decoded them and wrote them into the page. Cloud Toolsmith refused it; this
check makes Drawbridge stop it too, before any command runs.

:func:`found` reads one HTML or JavaScript text (never runs anything) and is
True when ALL of these hold:

1. **A large encoded blob**: one base64, base64url or hex run of 4 KB or
   more, a run of ``\\xNN`` / ``\\uNNNN`` escapes or a number array that
   long, or many long runs (10 or more of 200+ characters, 4 KB in all).
   Not counted: ``data:`` URLs of pictures, fonts, sound, video, WebAssembly
   or plain bytes (they are shown or loaded, not run), and base64 that starts
   with a picture, font or WebAssembly file's own header. A ``data:`` URL of
   HTML or script IS counted.
2. **A runtime decoder**: ``atob``, ``decodeURIComponent``, ``unescape`` or
   ``escape``, ``Buffer.from(.., "base64"|"hex")``, ``TextDecoder``,
   ``crypto.subtle.decrypt`` or a ``.decrypt(`` call, a hex ``parseInt(.., 16)``
   loop, or a hand-made XOR loop (``charCodeAt`` with ``^`` and
   ``fromCharCode``).
3. **A sink that runs or writes code**: ``eval``, ``Function(``,
   ``setTimeout``/``setInterval`` with a string or a decoded value,
   ``document.write``, ``innerHTML``/``outerHTML`` assignment,
   ``insertAdjacentHTML``, ``createContextualFragment``, ``srcdoc``.

When the code left after taking the blobs out is small (a loader page, under
64 KB), 2 and 3 may be anywhere in it. In a big file (a real bundle) the
decoder and the sink must sit within 1,500 characters of each other, so a
bundle that uses ``innerHTML`` in one place and ``atob`` in another does not
trip it. Bundles in ``dist/`` or ``build/`` are checked the same way on
purpose: a folder name is not proof of anything (a page served from
``dist/index.html`` is still a page); what keeps normal bundles clear is that
they carry no large non-media encoded blob.

Residual, stated plainly: this is a pattern check. It does not see a blob
fetched at runtime from elsewhere, text past the first 1 MB of a file, or a
loader split across files with the blob in a non-web file. It can be beaten
by a determined author; it stops the plain kind seen in the wild.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re

#: File types the check reads.
WEB_EXTS = frozenset({".html", ".htm", ".js", ".mjs", ".cjs"})
REASON = "its page hides encoded code that it unpacks and runs"

BIG_BLOB = 4096
LONG_RUN = 200
MANY_RUNS = 10
SMALL_CODE = 64 * 1024
NEAR = 1500

_RUN_RE = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}" % LONG_RUN)
_ESCAPES_RE = re.compile(r"(?:\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}){%d,}" % (BIG_BLOB // 4))
_NUMBERS_RE = re.compile(r"(?:\d{1,5}\s*,\s*){%d,}\d{1,5}" % (BIG_BLOB // 4))
#: ``data:<type>;base64,`` just before a run: the type decides if it counts.
_DATA_URL_RE = re.compile(r"data:([a-z0-9.+/-]*)(?:;[a-z0-9=._-]+)*;base64,\s*$", re.I)
_INERT_TYPES_RE = re.compile(
    r"^(?:image/(?!svg)[a-z0-9.+-]+|font/[a-z0-9.+-]+|application/(?:x-)?font-[a-z0-9.+-]+"
    r"|application/vnd\.ms-fontobject|audio/[a-z0-9.+-]+|video/[a-z0-9.+-]+"
    r"|application/wasm|application/octet-stream)$", re.I)
#: base64 that opens with a picture, font or WebAssembly file's own header.
_MEDIA_HEADS = ("iVBORw0KGgo", "/9j/", "R0lGOD", "UklGR", "d09GMg", "d09GRg", "AAEAAA",
                "T1RUTw", "AGFzbQ", "Qk0", "AAAAIGZ0eXA", "AAAAHGZ0eXA", "SUQz", "T2dnUw")
_DECODER_RE = re.compile(
    r"\batob\s*\(|\bdecodeURIComponent\s*\(|\bunescape\s*\(|\bescape\s*\("
    r"|\bBuffer\.from\s*\([^)]{0,200}['\"](?:base64|hex)['\"]"
    r"|\bTextDecoder\b|\bsubtle\.decrypt\s*\(|\.decrypt\s*\("
    r"|\bparseInt\s*\((?:[^()]|\([^()]{0,40}\)){0,80},\s*16\s*\)")
_XOR_RE = re.compile(r"charCodeAt\s*\([^)]{0,40}\)\s*\^|\^\s*[^;\n]{0,80}charCodeAt\s*\(")
_FROMCHAR_RE = re.compile(r"fromCharCode\b")
_SINK_RE = re.compile(
    r"\beval\s*\(|\[\s*['\"]eval['\"]\s*\]|\bFunction\s*\("
    r"|\bset(?:Timeout|Interval)\s*\(\s*(?:['\"`]|(?:atob|unescape|decodeURIComponent)\s*\()"
    r"|\bdocument\s*\.\s*write(?:ln)?\s*\(|\bdocument\s*\[\s*['\"]write(?:ln)?['\"]\s*\]"
    r"|\.\s*(?:inner|outer)HTML\s*\+?=(?!=)|\[\s*['\"](?:inner|outer)HTML['\"]\s*\]\s*\+?=(?!=)"
    r"|\binsertAdjacentHTML\s*\(|\bcreateContextualFragment\s*\(|\.\s*srcdoc\s*=(?!=)")


def _varied(run: str) -> bool:
    """A real blob mixes letters and digits (not a line of dashes)."""
    kinds = (any(c.isdigit() for c in run) + any(c.islower() for c in run)
             + any(c.isupper() for c in run))
    return kinds >= 2


def _inert(text: str, start: int, run: str) -> bool:
    m = _DATA_URL_RE.search(text[max(0, start - 120):start])
    if m:
        return bool(_INERT_TYPES_RE.match(m.group(1) or "application/octet-stream"))
    return run.startswith(_MEDIA_HEADS)


def _blobs(text: str) -> list[tuple[int, int]]:
    """Spans of encoded blobs that count (see rule 1), or [] when too few."""
    big: list[tuple[int, int]] = []
    long_runs: list[tuple[int, int]] = []
    for m in _RUN_RE.finditer(text):
        run = m.group(0)
        if not _varied(run) or _inert(text, m.start(), run):
            continue
        long_runs.append(m.span())
        if len(run) >= BIG_BLOB:
            big.append(m.span())
    for rx in (_ESCAPES_RE, _NUMBERS_RE):
        for m in rx.finditer(text):
            if m.end() - m.start() >= BIG_BLOB:
                big.append(m.span())
    if big:
        return big + [s for s in long_runs if s not in big]
    if len(long_runs) >= MANY_RUNS and sum(b - a for a, b in long_runs) >= BIG_BLOB:
        return long_runs
    return []


def _without(text: str, spans: list[tuple[int, int]]) -> str:
    out, at = [], 0
    for a, b in sorted(spans):
        if a < at:
            continue
        out.append(text[at:a])
        out.append('""')
        at = b
    out.append(text[at:])
    return "".join(out)


def _decoders(code: str) -> list[int]:
    hits = [m.start() for m in _DECODER_RE.finditer(code)]
    if _FROMCHAR_RE.search(code):
        hits += [m.start() for m in _XOR_RE.finditer(code)]
    return sorted(hits)


def found(text: str) -> bool:
    """True when *text* (HTML or JavaScript) hides a large encoded blob and
    decodes it at runtime into code that runs or is written into the page."""
    if not text or len(text) < BIG_BLOB:
        return False
    spans = _blobs(text)
    if not spans:
        return False
    code = _without(text, spans)
    decoders = _decoders(code)
    if not decoders:
        return False
    sinks = [m.start() for m in _SINK_RE.finditer(code)]
    if not sinks:
        return False
    if len(code) <= SMALL_CODE:
        return True
    j = 0
    for d in decoders:
        while j < len(sinks) and sinks[j] < d - NEAR:
            j += 1
        if j < len(sinks) and abs(sinks[j] - d) <= NEAR:
            return True
    return False


def in_file(rel: str, text: str) -> bool:
    """:func:`found` for a web file of the tree (by its extension)."""
    name = rel.lower().rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return dot >= 0 and name[dot:] in WEB_EXTS and found(text)


__all__ = ["REASON", "WEB_EXTS", "found", "in_file"]
