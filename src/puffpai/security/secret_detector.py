"""Find keys, tokens and passwords in text, so they go to the vault, not the index.

Every path that puts text into what the bot knows (an upload, a file dropped
in the data folder, a linked folder, a downloaded book) runs :func:`find`
before anything is indexed. A hit is replaced in the searchable copy by
:data:`HELD` and its value is kept only in the vault
(``puffpai.ingestion.screen_text_for_pii``). Text going to a cloud model is
cleaned with :func:`redact_only` (``anywhere=True``).

The shapes are the common, well-known ones (each has a ``kind`` name):
``openai_style_key`` (``sk-...``), ``stripe_style_key`` (``sk_live_...``),
``github_token`` (``ghp_...``, ``github_pat_...``), ``aws_access_key``
(``AKIA...``), ``slack_token`` (``xoxb-...``), ``google_api_key``
(``AIza...``), ``private_key`` (a PEM block), ``jwt``, ``url_password``
(``https://user:pass@host``, ``redis://:pass@host``), ``auth_header``
(``Authorization: Bearer ...``), ``cli_password`` (``curl -u user:pass``,
``mysql -pPASS``), ``env_line`` (``API_TOKEN=...`` in a ``.env`` style
line), ``secret_assignment`` (``password = ...``, ``"token": "..."`` in
config-like text), ``secret_setting`` (a setting, flag or tag whose name
says secret: ``DB_PASS``, ``SESSION_SALT``, ``AUTH``, ``ENCRYPTION_KEY``,
``--token VALUE``, ``<password>..</password>``; the value is held whatever
its shape or length) and ``generated_key`` (24+ hex or base64 characters on
a setting line, under any name but a hash's). For a setting only the value
is held, so the searchable copy still says which setting it was.

Not held: a placeholder (``${X}``, ``changeme``, ``<your key>``), a yes/no,
a path, URL or host under a name that is about where a secret is
(``POSTGRES_PASSWORD_FILE: /run/secrets/x``, ``TOKEN_URL``), a number under
a name that measures it (``TOKEN_TTL: 3600``), and a capitalised word in a
book's sentences (``Salt: a pinch``).

What this does not catch: a key with no known shape and no telling name next
to it. Values are never logged; :func:`summary` names kinds and counts only.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import functools
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
    # A password in a connection string, any length, with or without a user.
    # (The scheme starts at a word start, so it is read once: linear; up to 64
    # characters, so postgresql+asyncpg:// and x_postgres:// count.)
    ("url_password", re.compile(
        r"(?<![A-Za-z0-9+.\-_])[A-Za-z][A-Za-z0-9+.\-_]{0,63}://[^\s:/@]{0,64}:"
        r"([^\s@/]{1,128})@"), 1),
    # Authorization: Bearer <token> (also in curl -H '...', and as a setting).
    ("auth_header", re.compile(
        r"(?i)(?<![A-Za-z0-9_\-])(?:proxy-)?authorization[\"']?[ \t]*[:=][ \t]*[\"']?"
        r"(?:(?:bearer|basic|token|digest|negotiate|apikey|key)[ \t]+)?([^\s\"',;]{3,})"), 1),
    # curl -u user:pass, -uuser:pass, --user=user:pass (not -u 1000:1000, a user
    # and group id).
    ("cli_password", re.compile(
        r"(?:(?<=\s)|^)(?:-u[ \t]*|--user(?:[ \t]+|=))[\"']?(?![0-9]*:[0-9]+(?:[\s\"']|$))"
        r"[^\s:\"'@]*:([^\s\"'@]+)", re.M), 1),
    # mysql -pPASS (the password written onto -p).
    ("cli_password", re.compile(
        r"(?<![A-Za-z0-9_])(?:mysql|mysqldump|mysqladmin|mysqlimport|mysqlshow|mariadb)"
        r"(?:\.exe)?(?![A-Za-z0-9_])[^\n|;&]{0,300}?[ \t]-p[ \t]*[\"']?([^\s\"'-][^\s\"']*)"),
     1),                        # (-p VALUE: a database name to mysql, held all the same)
    # "my password is: hunter22" (with the colon; "the password is required" stays).
    ("secret_assignment", re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:password|passwd|passphrase|passcode|pwd)[ \t]+(?:is|was)"
        r"[ \t]*:[ \t]*[\"']?([^\s\"']{1,128})"), 1),
]

# (Bounded on both sides: a long word full of "token" is read in linear time.)
_SECRET_NAME = (r"[A-Za-z0-9_.\-]{0,256}?(?:password|passwd|passphrase|pwd|secret|token|"
                r"api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|"
                r"client[_\-]?secret|auth[_\-]?key|credentials?)[A-Za-z0-9_.\-]{0,256}")

# NAME=value on its own line, the .env way (also "export NAME=value").
_ENV_LINE = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(" + _SECRET_NAME + r")[ \t]*=[ \t]*"
    r"(\"[^\"\r\n]{4,}\"|'[^'\r\n]{4,}'|[^\s#'\"]{4,})")

# name: value / name = value / "name": "value" anywhere in config-like text.
# The name starts at the start of a word (else a long word is tried from
# every letter of it: quadratic).
_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\-])[\"']?(" + _SECRET_NAME + r")[\"']?[ \t]*(?::|=|=>)[ \t]*"
    r"(\"[^\"\r\n]{4,}\"|'[^'\r\n]{4,}'|[^\s,;#'\"<>]{6,})")

# Values that are a placeholder, not a secret.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:true|false|null|none|nil|undefined|required|optional|string|"
    r"changeme|change_me|your[_\-\s].*|<.*>|\$\{?.*|\{\{.*|%\(.*|\*+|x{3,}|\.+|"
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
    return _find(text, anywhere=False)


def _find(text: str, *, anywhere: bool) -> list[SecretHit]:
    if not text:
        return []
    # Collect every candidate, then merge once (overlaps are joined, so no
    # part of a value is left out; the earliest pass names the kind).
    cands: list[tuple[int, int, int, str]] = []
    for prio, (kind, pattern, group) in enumerate(_SHAPES):
        for m in pattern.finditer(text):
            a, b = m.span(group)
            if b > a and not _HIDDEN.match(text, a):
                cands.append((a, b, prio, kind))
    prio = len(_SHAPES)
    for kind, pattern in (("env_line", _ENV_LINE), ("secret_assignment", _ASSIGNMENT)):
        for m in pattern.finditer(text):
            value, q = _strip_quotes(m.group(2))
            if len(value) < 4 or _PLACEHOLDER.match(value.strip()):
                continue
            a = m.start(2) + q
            if HELD in value or _HIDDEN.match(text, a):
                continue                # already hidden (the settings pass below holds
                #                         anything written after the mark)
            cands.append((a, a + len(value), prio, kind))
        prio += 1
    for a, b, kind in _setting_spans(text, anywhere=anywhere):
        cands.append((a, b, prio, kind))
    if not cands:
        return []
    cands.sort()
    merged: list[list] = []
    for a, b, p, k in cands:
        if merged and a < merged[-1][1]:
            last = merged[-1]
            last[1] = max(last[1], b)
            if p < last[2]:
                last[2], last[3] = p, k
        else:
            merged.append([a, b, p, k])
    hits: list[SecretHit] = []
    line, at = 1, 0
    for a, b, _p, k in merged:
        line += text.count("\n", at, a)
        at = a
        hits.append(SecretHit(kind=k, start=a, end=b, line=line, value=text[a:b]))
    return hits


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


def redact_only(text: str, *, anywhere: bool = False) -> str:
    """The searchable copy, for text whose values are not the owner's to keep
    (a downloaded book, text going to a cloud model): the shapes are taken
    out and nothing is stored. With *anywhere* (text going to a cloud model)
    a generated-looking value is taken out of any ``NAME=value`` too, not
    only out of a setting line (``docker run -e X=<key>``)."""
    return redact(text, _find(text, anywhere=anywhere))[0]


# ── settings: NAME=value, NAME: value, --name value, <name>value</name> ─────

#: The head of a setting: ``NAME=``, ``NAME:``, ``"name":``, ``u"name":``,
#: ``\"name\":`` (escaped JSON), ``--name=``, ``NAME =>``. The value is read
#: after it, so a head that is not a secret never swallows the next one
#: (``$env:API_KEY=...``). A head starts only at a word start (with its
#: dashes and quote), so a long run is tried once: linear.
_HEAD = re.compile(r"(?<![A-Za-z0-9_.\-])-{0,2}(?:\\?[\"'])?([A-Za-z_][A-Za-z0-9_.\-]{0,255})"
                   r"(?:\\?[\"'])?[ \t]*(=>|=|:)[ \t]*")
_UNQUOTED = re.compile(r"[^\s,;#'\"<>{}()\[\]]+")
_QUERY_VALUE = re.compile(r"[^\s&#\"'<>]+")
_TO_LINE_END = re.compile(r"[^\r\n]*")
_COMMENT = re.compile(r"[ \t]+#")
_LOOK = 4096                            # the most of one value looked at
#: What may come before a setting at the start of its line (YAML, .env, compose lists).
_LINE_START = re.compile(r"[ \t]*(?:-[ \t]+)?(?:export[ \t]+|set[ \t]+)?")
#: A value already hidden: this module's mark or the private-details marks.
_HIDDEN = re.compile(r"\[(?:held in vault|[a-z][a-z ]{0,40} kept private)\]")
#: ``--token VALUE`` (a flag named for a secret, its value after a space).
_FLAG = re.compile(r"(?:(?<=[\s\"'])|^)--([A-Za-z][A-Za-z0-9_\-]{0,60})[ \t]+([\"']?)"
                   r"([^\s\"'\-][^\s\"']*)", re.M)
#: ``<password>value</password>``.
_TAG = re.compile(r"<([A-Za-z_][A-Za-z0-9_.\-]{0,60})>([^<\r\n]{1,256})</\1>")

#: Name words that mean secret anywhere in the name ...
_SECRET_WORDS = re.compile(r"(?i)secret|token|password|passwd|passphrase|credential|"
                           r"api[_\-]?key|apikey|private[_\-]?key|access[_\-]?key|"
                           r"auth[_\-]?key|webhook|dsn")
#: ... and ones that only count as a whole part (DB_PASS, not COMPASS or AUTHOR).
_SECRET_PARTS = frozenset({"pass", "pwd", "pw", "salt", "auth", "private"})
#: ``<x>_KEY`` is a key, except these (a database's or a map's key, a public key).
_NOT_A_SECRET_KEY = frozenset({
    "primary", "foreign", "sort", "partition", "cache", "routing", "public", "pub", "hash",
    "range", "lookup", "unique", "composite", "map", "group", "label", "shard", "sharding",
    "idempotency", "translation", "i18n", "object", "bucket", "registry", "row", "column",
    "index", "dict", "react", "list", "hot", "short", "shortcut"})
#: A flag takes a secret value only when it is named for one (``--password``,
#: ``--token``, ``--api-key``), not ``--max-tokens``, ``--tokenizer``,
#: ``--password-stdin``, ``--auth`` or ``--auth-type``.
_FLAG_NOUNS = frozenset({"password", "passwd", "pass", "pwd", "token", "secret", "passphrase",
                         "apikey", "credential", "credentials"})
_FLAG_KEY_KINDS = frozenset({"api", "access", "secret", "private", "auth", "license", "signing",
                             "encryption", "master", "client"})
#: A last name part that MEASURES something: a number there is not the secret.
_MEASURE_PARTS = frozenset({
    "ttl", "timeout", "length", "len", "expiry", "expires", "expire", "lifetime", "age", "min",
    "max", "size", "count", "rounds", "iterations", "cost", "bits", "version", "days", "hours",
    "minutes", "seconds", "ms", "enabled", "disabled", "required", "port", "retries",
    "attempts", "interval", "limit"})
#: Name parts of public check values (a hash is not a secret).
_HASH_PARTS = frozenset({"sha", "sha1", "sha256", "sha512", "hash", "digest", "checksum",
                         "integrity", "commit", "rev", "revision", "etag", "ref", "id", "uuid",
                         "guid", "version", "image"})
#: Option words: the KIND of sign-in, not a secret (``AUTH=password`` for
#: code-server, ``--auth none``, ``POSTGRES_HOST_AUTH_METHOD=trust``).
_OPTION_WORDS = frozenset({
    "yes", "no", "on", "off", "enabled", "disabled", "-", "none", "password", "passwd",
    "oauth", "oauth2", "oidc", "basic", "bearer", "token", "digest", "jwt", "saml", "ldap",
    "plain", "md5", "scram-sha-256", "trust", "peer", "ident", "cookie", "session", "header",
    "apikey", "api-key", "api_key", "env", "file", "stdin", "prompt", "keyring"})
#: URL query parameters that carry a secret (``?key=...``, ``&access_token=...``).
_QUERY_SECRETS = frozenset({
    "key", "api_key", "apikey", "api-key", "token", "access_token", "refresh_token",
    "id_token", "auth", "auth_token", "secret", "client_secret", "password", "pass", "pwd",
    "sig", "signature", "x-amz-signature", "x-amz-security-token", "x-amz-credential",
    "x-goog-signature", "x-goog-credential", "private_token"})
_NUMBER = re.compile(r"^\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y)?$")
_GENERATED = re.compile(r"^(?:[A-Za-z0-9]{2,10}:)?([A-Za-z0-9+/_\-]{24,}={0,2})$")


def _parts(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)         # dbPass -> db_Pass
    return [p for p in re.split(r"[_.\-]+", spaced.lower()) if p]


def _secret_name(name: str, parts: list[str]) -> bool:
    if not parts:
        return False
    if _SECRET_WORDS.search(name) or any(p in _SECRET_PARTS for p in parts):
        return True
    if parts[-1] == "key":
        return (len(parts) > 1 and parts[-2] not in _NOT_A_SECRET_KEY) or name == "KEY"
    return False


def _flag_takes_secret(parts: list[str]) -> bool:
    if not parts:
        return False
    last = parts[-1]
    return last in _FLAG_NOUNS or (last == "key" and len(parts) > 1
                                   and parts[-2] in _FLAG_KEY_KINDS)


_CAPITAL_WORD = re.compile(r"[A-Z][a-z]+")
_CODE_NAME = re.compile(r"[_\-.\d]|[a-z][A-Z]")


@functools.lru_cache(maxsize=4096)
def _name_info(name: str) -> tuple[list[str], bool, bool, bool]:
    """``(parts, says secret, a capitalised word, written like code)`` for a
    setting name (names repeat: read once)."""
    parts = _parts(name)
    return (parts, _secret_name(name, parts), _CAPITAL_WORD.fullmatch(name) is not None,
            bool(_CODE_NAME.search(name)) or (len(name) > 1 and name.isupper()))


def _no_secret(parts: list[str], v: str) -> bool:
    """A value under a secret-named setting that is no secret: empty, a
    placeholder, an option word, or a number under a name that measures."""
    return (not v or HELD in v or bool(_PLACEHOLDER.match(v)) or v.lower() in _OPTION_WORDS
            or (bool(parts) and parts[-1] in _MEASURE_PARTS and bool(_NUMBER.match(v))))


def _generated(value: str) -> bool:
    """24+ hex or base64 characters that read like a made key, not a word."""
    v = value.strip()
    if v.startswith(("/", "./", "../", "~")) or "://" in v:
        return False
    m = _GENERATED.match(v)
    if not m:
        return False
    body = m.group(1)
    if re.fullmatch(r"[0-9a-fA-F]+", body):
        return True
    return bool(re.search(r"\d", body)) or (bool(re.search(r"[a-z]", body))
                                            and bool(re.search(r"[A-Z]", body)))


def _past_marks(text: str, pos: int, *, spaces: bool) -> int:
    """*pos* moved past the hidden marks there (and, with *spaces*, the
    spaces between them: a setting line's value runs on past a space)."""
    while True:
        m = _HIDDEN.match(text, pos)
        if not m:
            return pos
        pos = m.end()
        while spaces and pos < len(text) and text[pos] in " \t":
            pos += 1


def _setting_spans(text: str, *, anywhere: bool) -> list[tuple[int, int, str]]:
    """``[(start, end, kind)]`` of the setting values to hold.

    ``secret_setting``: a name that says secret, whatever the value's shape
    (a URL, a path, a host: a secret-named setting is held), unless it is a
    placeholder, an option word or a measuring number. On a setting line any
    setting-style name counts, never a capitalised word (``Salt: a pinch``);
    in a sentence only a name written like a setting (``DB_PASS``, ``dbPass``,
    a quoted key) or one set with ``=``. A URL's secret query parameters
    (``?key=``, ``&access_token=``) too. ``generated_key``: 24+ hex/base64
    characters under any name but a hash's, on a setting line (or anywhere,
    with *anywhere*). A value that is already a hidden mark is left; text
    written after a mark is held with it."""
    out: list[tuple[int, int, str]] = []
    if not text:
        return out
    n = len(text)
    line_at, seen = 0, 0
    run = (0, 0)                        # the last unquoted run read (start, end)
    next_at = {"\"": -1, "'": -1, "\n": -1}     # the next quote / line end seen

    def next_of(ch: str, pos: int) -> int:
        if next_at[ch] < pos:
            found = text.find(ch, pos)
            next_at[ch] = n if found < 0 else found
        return next_at[ch]

    def line_rest(pos: int) -> int:
        """The end of a setting line's value from *pos* (a comment left out)."""
        rest = _TO_LINE_END.match(text, pos).group(0)  # type: ignore[union-attr]
        c = _COMMENT.search(rest)
        return pos + len((rest[:c.start()] if c else rest).rstrip())

    for m in _HEAD.finditer(text):
        name, sep = m.group(1), m.group(2)
        start = m.start(1)
        nl = text.rfind("\n", seen, start)
        if nl >= 0:
            line_at = nl + 1
        seen = start
        parts, secret, capital, code = _name_info(name)
        before = text[m.start() - 1:m.start()] if m.start() else ""
        if before in ("?", "&") and sep == "=":
            # A URL query parameter: only a secret one is held.
            v = _QUERY_VALUE.match(text, m.end())
            if v and name.lower() in _QUERY_SECRETS and not _HIDDEN.match(text, m.end()):
                out.append((v.start(), v.end(), "secret_setting"))
            continue
        quoted_name = start > 0 and text[start - 1] in "\"'"
        line = _LINE_START.fullmatch(text, line_at, m.start()) is not None
        capital_word = capital and not quoted_name
        code_name = quoted_name or code
        e = m.end()
        if e < n and text[e] in "uUbBrRfF" and text[e + 1:e + 2] in ("\"", "'"):
            e += 1                                          # u'value', b"value"
        if _HIDDEN.match(text, e):
            # Already hidden; anything written onto the mark is held with it
            # (on a setting line, the rest of the line).
            p = _past_marks(text, e, spaces=line)
            if line:
                b = line_rest(p)
            else:
                v = _UNQUOTED.match(text, p)
                b = v.end() if v else p
            if b > p and secret:
                out.append((e, b, "secret_setting"))
            continue
        quoted_value = True
        if text.startswith(("\\\"", "\\'"), e):             # {\"password\":\"x\"}
            close = text.find(text[e:e + 2], e + 2, min(next_of("\n", e), e + 2 + _LOOK))
            if close < 0:
                continue
            a, b = e + 2, close
        elif text[e:e + 1] in ("\"", "'"):
            close = next_of(text[e], e + 1)
            if close >= n or next_of("\n", e + 1) < close:
                continue
            a, b = e + 1, close
        else:
            if not run[0] <= e < run[1]:        # one read per run, not per head in it
                v = _UNQUOTED.match(text, e)
                if not v:
                    continue
                run = (e, v.end())
            a, b, quoted_value = e, run[1], False
        if quoted_value and _HIDDEN.match(text, a):
            if _past_marks(text, a, spaces=True) < b and secret:   # "[held in vault]more"
                out.append((a, b, "secret_setting"))
            continue
        first_end = b
        # What is looked at stays short (a long run is read once, not copied per head).
        first = text[a:min(b, a + _LOOK)]
        if line and not quoted_value:
            b = max(b, line_rest(b))            # NAME: two words: the rest of the line
        value = text[a:min(b, a + _LOOK)].strip()
        if secret:
            if _no_secret(parts, value):
                continue
            if (line and (not capital_word or sep != ":")) or code_name or sep != ":":
                out.append((a, b, "secret_setting"))
            continue
        if (anywhere or (line and not capital_word)) and first_end - a <= _LOOK and \
                _generated(first) and not any(p in _HASH_PARTS for p in parts):
            out.append((a, first_end, "generated_key"))
    for m in _FLAG.finditer(text):
        name, value = m.group(1), m.group(3)
        parts = _parts(name)
        if _flag_takes_secret(parts) and not _no_secret(parts, value) \
                and not _NUMBER.match(value) and not _HIDDEN.match(text, m.start(3)):
            out.append((m.start(3), m.end(3), "secret_setting"))
    for m in _TAG.finditer(text):
        name, value = m.group(1), m.group(2).strip()
        parts = _parts(name)
        if _secret_name(name, parts) and not _no_secret(parts, value) \
                and not _HIDDEN.match(text, m.start(2)):
            out.append((m.start(2), m.end(2), "secret_setting"))
    return out


def summary(hits: list[SecretHit]) -> str:
    """Kinds and counts, never values: safe for a log line."""
    counts: dict[str, int] = {}
    for h in hits:
        counts[h.kind] = counts.get(h.kind, 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items())) or "none"


__all__ = ["HELD", "SecretHit", "find", "redact", "redact_only", "summary"]
