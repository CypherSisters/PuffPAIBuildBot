"""Find keys, tokens and passwords in text, so they go to the vault, not the index.

Every path that puts text into what the bot knows (an upload, a file dropped
in the data folder, a linked folder, a downloaded book) runs :func:`find`
before anything is indexed. A hit is replaced in the searchable copy by
:data:`HELD` and its value is kept only in the vault
(``puffpai.ingestion.screen_text_for_pii``).

The shapes are the common, well-known ones (each has a ``kind`` name):
``openai_style_key`` (``sk-...``), ``stripe_style_key`` (``sk_live_...``),
``github_token`` (``ghp_...``, ``github_pat_...``), ``aws_access_key``
(``AKIA...``), ``slack_token`` (``xoxb-...``), ``google_api_key``
(``AIza...``), ``private_key`` (a PEM block), ``jwt``, ``url_password``
(``https://user:pass@host``), ``env_line`` (``API_TOKEN=...`` in a ``.env``
style line) and ``secret_assignment`` (``password = ...``, ``"token": "..."``
in config-like text). For an assignment only the value is held, so the
searchable copy still says which setting it was.

What this does not catch: a key with no known shape and no telling name next
to it. Values are never logged; :func:`summary` names kinds and counts only.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What the searchable copy shows where a key or password was.
HELD = "[held in vault]"

# (kind, pattern, group holding the secret value; 0 = the whole match)
_SHAPES: list[tuple[str, re.Pattern[str], int]] = [
    ("private_key", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]+?"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"), 0),
    ("github_token", re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"), 0),
    ("stripe_style_key", re.compile(
        r"(?<![A-Za-z0-9_])(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"), 0),
    ("openai_style_key", re.compile(
        r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9][A-Za-z0-9_\-]{19,}"), 0),
    ("aws_access_key", re.compile(
        r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"), 0),
    ("slack_token", re.compile(
        r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9\-]{10,}"), 0),
    ("google_api_key", re.compile(
        r"(?<![A-Za-z0-9_\-])AIza[0-9A-Za-z_\-]{35}"), 0),
    ("jwt", re.compile(
        r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}"
        r"\.[A-Za-z0-9_\-]{8,}"), 0),
    ("url_password", re.compile(
        r"\b[a-z][a-z0-9+.\-]{1,15}://[^\s:/@]{1,64}:([^\s@/]{3,128})@"), 1),
]

_SECRET_NAME = (r"[A-Za-z0-9_.\-]*(?:password|passwd|passphrase|pwd|secret|token|"
                r"api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|"
                r"client[_\-]?secret|auth[_\-]?key|credentials?)[A-Za-z0-9_.\-]*")

# NAME=value on its own line, the .env way (also "export NAME=value").
_ENV_LINE = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(" + _SECRET_NAME + r")[ \t]*=[ \t]*"
    r"(\"[^\"\r\n]{4,}\"|'[^'\r\n]{4,}'|[^\s#'\"]{4,})")

# name: value / name = value / "name": "value" anywhere in config-like text.
_ASSIGNMENT = re.compile(
    r"(?i)[\"']?(" + _SECRET_NAME + r")[\"']?[ \t]*(?::|=|=>)[ \t]*"
    r"(\"[^\"\r\n]{4,}\"|'[^'\r\n]{4,}'|[^\s,;#'\"<>]{6,})")

# Values that are a placeholder, not a secret.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:true|false|null|none|nil|undefined|required|optional|string|"
    r"changeme|change_me|your[_\-\s].*|<.*>|\$\{?.*|\{\{.*|%\(.*|\*+|x+|\.+|"
    r"\[held in vault\])$")


@dataclass
class SecretHit:
    kind: str
    start: int
    end: int
    line: int
    value: str

    def pointer(self) -> dict:
        """Where it was, never what it was."""
        return {"kind": self.kind, "line": self.line}


def _strip_quotes(value: str) -> tuple[str, int]:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1], 1
    return value, 0


def find(text: str) -> list[SecretHit]:
    """Every key, token or password in ``text``, in order, never overlapping."""
    if not text:
        return []
    spans: list[tuple[int, int, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _k in spans)

    for kind, pattern, group in _SHAPES:
        for m in pattern.finditer(text):
            a, b = m.span(group)
            if b > a and free(a, b):
                spans.append((a, b, kind))
    for kind, pattern in (("env_line", _ENV_LINE), ("secret_assignment", _ASSIGNMENT)):
        for m in pattern.finditer(text):
            raw = m.group(2)
            value, q = _strip_quotes(raw)
            if len(value) < 4 or _PLACEHOLDER.match(value.strip()):
                continue
            if HELD in value:
                continue
            a = m.start(2) + q
            b = a + len(value)
            if free(a, b):
                spans.append((a, b, kind))
    spans.sort()
    return [SecretHit(kind=k, start=a, end=b, line=text.count("\n", 0, a) + 1,
                      value=text[a:b]) for a, b, k in spans]


def redact(text: str, hits: list[SecretHit] | None = None) -> tuple[str, list[SecretHit]]:
    """``(text with every hit replaced by HELD, the hits)``."""
    hits = find(text) if hits is None else hits
    if not hits:
        return text, []
    out, at = [], 0
    for h in hits:
        out.append(text[at:h.start])
        out.append(HELD)
        at = h.end
    out.append(text[at:])
    return "".join(out), hits


def redact_only(text: str) -> str:
    """The searchable copy, for text whose values are not the owner's to keep
    (a downloaded book): the shapes are taken out and nothing is stored."""
    return redact(text)[0]


def summary(hits: list[SecretHit]) -> str:
    """Kinds and counts, never values: safe for a log line."""
    counts: dict[str, int] = {}
    for h in hits:
        counts[h.kind] = counts.get(h.kind, 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items())) or "none"


__all__ = ["HELD", "SecretHit", "find", "redact", "redact_only", "summary"]
