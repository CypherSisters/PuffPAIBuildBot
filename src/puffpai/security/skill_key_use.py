"""A skill's own command may borrow its vault key for one run.

Keys always live in the vault. A skill's own
command line may NAME its key (``$BLUECOLUMN_API_KEY``, ``${X}``, ``%X%``,
``$env:X``) only when this module can show that the key goes nowhere but
the skill's own outside service. This is a known risk, recorded in
SECURITY.md under "Known risks": the skill and its service get the key by
design.

What must hold (anything this module cannot read for certain is refused,
with a plain reason):

* The command is ONE simple ``curl``, ``wget`` or ``httpie`` command. No
  pipes, redirects, ``;``/``&&``, command substitution, backticks, ``eval``,
  other variables, braces, globs, comments, ``NAME=value`` prefixes.
* Every address in the command (``https://`` only) is on the skill's own
  service: the same registrable domain as the homepage or API base the skill
  declares in its frontmatter, or a host that appears in one of the skill's
  own code examples beside the key variable. A prose link elsewhere in the
  SKILL.md does NOT count. The SKILL.md must still be byte for byte the copy
  the install pinned (``finding.install`` lock), so an edit after install
  cannot add an address. Shared hosting (``*.supabase.co`` ...) counts the
  whole project host, so another project on the same platform is not the same
  domain. Fail closed when the skill declares no service at all.
* The key never goes into the address before its path, never into a file,
  and the program never follows redirects, uses a proxy, reads settings from
  a file, skips certificate checks or sets a ``Host`` header.

The allowed command is then run DIRECTLY, never through a shell: the program
is resolved to a trusted absolute path (``host_programs.web_program`` for
``curl``/``wget``/``httpie``, a fixed system place, never ``PATH``, never the
"Install what's missing" tools folder, never the skill's folder), and the
key value is put into that one argument of the child's argv by us. So no
planted program and no shell trick can reach the key. This module sees key
names, never values, except :func:`build_argv` (which places the value into
the argv for the run) and :func:`key_forms` (the forms the scrubber hides).

A skill's own installed CLI that reads the key from its environment (for
example ``runcomfy``) is the other key path, handled by
:mod:`puffpai.security.skill_programs` (recorded and hash-checked at install).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote, quote_plus

#: What stands in for a skill's key (and its encodings) in any output.
HIDDEN_KEY = "[hidden key]"

_NAME_CHARS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DNS_HOST = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}\Z")
_URL_IN_TEXT = re.compile(r"(?i)\bhttps?://[^\s\"'<>()\[\]{}`\\|^]+")
_SENT = "\x00"

#: Hosts shared by many unrelated owners: the "registrable domain" is the
#: project host one level under these (like the public suffix list does).
SHARED_SUFFIXES = (
    "supabase.co", "supabase.in", "vercel.app", "now.sh", "netlify.app", "netlify.com",
    "herokuapp.com", "github.io", "gitlab.io", "pages.dev", "workers.dev", "web.app",
    "firebaseapp.com", "firebaseio.com", "appspot.com", "cloudfunctions.net", "run.app",
    "azurewebsites.net", "azure-api.net", "azureedge.net", "cloudapp.net",
    "blob.core.windows.net", "windows.net", "cloudfront.net", "amazonaws.com",
    "elasticbeanstalk.com", "onrender.com", "fly.dev", "railway.app", "glitch.me",
    "repl.co", "replit.dev", "replit.app", "ngrok.io", "ngrok.app", "ngrok-free.app",
    "deno.dev", "surge.sh", "gitbook.io", "notion.site", "readthedocs.io",
    "trycloudflare.com", "loca.lt", "pipedream.net", "modal.run", "hf.space",
    "googleapis.com", "googleusercontent.com", "r2.dev", "r2.cloudflarestorage.com",
    "digitaloceanspaces.com", "ondigitalocean.app", "koyeb.app", "wixsite.com",
    "blogspot.com", "wordpress.com", "myshopify.com", "webflow.io", "framer.app",
    "bubbleapps.io", "val.run", "deta.dev", "render.com", "cloudflareworkers.com",
)
#: Second-level labels under a two-letter country code that are suffixes
#: (``co.uk``, ``com.au`` ...), so ``a.co.uk`` and ``b.co.uk`` differ.
_CC_SECOND = frozenset(("co", "com", "net", "org", "ac", "gov", "edu", "ne", "or", "go",
                        "gv", "mil", "nom", "ltd", "plc", "sch", "info", "biz"))
#: Never a skill's "own service": code hosts, registries, link shorteners,
#: catch-all request bins, big shared sites and example names.
GENERIC_DOMAINS = frozenset((
    "github.com", "githubusercontent.com", "gitlab.com", "bitbucket.org", "codeberg.org",
    "sourceforge.net", "npmjs.com", "npmjs.org", "yarnpkg.com", "pypi.org",
    "pythonhosted.org", "crates.io", "rubygems.org", "skills.sh", "clawhub.ai",
    "clawhub.com", "google.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
    "discord.com", "discord.gg", "reddit.com", "medium.com", "wikipedia.org",
    "shields.io", "pastebin.com", "hastebin.com", "paste.ee", "webhook.site",
    "requestbin.com", "beeceptor.com", "bit.ly", "tinyurl.com", "t.co", "goo.gl",
    "notion.so", "example.com", "example.org", "example.net", "dropbox.com",
    "transfer.sh", "file.io", "linktr.ee", "facebook.com", "instagram.com",
))
_RESERVED_TLDS = frozenset(("local", "localhost", "internal", "test", "example", "invalid",
                            "lan", "home", "arpa", "onion", "corp", "intranet"))
#: Headers that pick who answers on a shared server: never set with a key.
_ROUTING_HEADERS = frozenset(("host", ":authority", "x-forwarded-host", "x-host",
                              "forwarded", "x-original-host", "x-original-url",
                              "x-rewrite-url", "x-forwarded-server"))


class KeyUseRefused(ValueError):
    """The command names the skill's key and may not run. ``reason`` is
    plain words for her and for the model."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── where the skill's own service is ─────────────────────────────────────────

def registrable(host: str) -> str:
    """The part of *host* one owner controls: ``api.x.com`` -> ``x.com``,
    ``proj.supabase.co`` -> ``proj.supabase.co``, ``a.b.co.uk`` -> ``b.co.uk``."""
    host = host.lower().rstrip(".")
    labels = host.split(".")
    for suffix in sorted(SHARED_SUFFIXES, key=len, reverse=True):
        if host == suffix:
            return host
        if host.endswith("." + suffix):
            n = suffix.count(".") + 1
            return ".".join(labels[-(n + 1):])
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _CC_SECOND:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _usable_host(host: str) -> bool:
    if not host or not _DNS_HOST.match(host):
        return False                        # IP addresses, localhost, odd forms
    if host.rsplit(".", 1)[-1] in _RESERVED_TLDS:
        return False
    return registrable(host) not in GENERIC_DOMAINS


def _hosts_in(text: str) -> list[str]:
    out: list[str] = []
    for m in _URL_IN_TEXT.finditer(text or ""):
        rest = m.group(0).split("://", 1)[1]
        auth = re.split(r"[/?#]", rest, maxsplit=1)[0]
        if "@" in auth or "%" in auth:
            continue
        host = auth.rsplit(":", 1)[0] if re.search(r":\d+\Z", auth) else auth
        host = host.lower()
        if _usable_host(host) and host not in out:
            out.append(host)
    return out


#: Frontmatter fields that name a skill's own service.
_HOME_FIELDS = ("homepage", "home_page", "home", "url", "website", "site", "api",
                "api_base", "apibase", "apiurl", "api_url", "base", "base_url",
                "baseurl", "endpoint", "server", "host", "documentation", "docs")
_FENCE = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)", re.S)


def _pinned_skill_md(skill: str) -> str:
    """The skill's SKILL.md text, but only if it is byte for byte the copy the
    install pinned. Raises :class:`KeyUseRefused` otherwise (fail closed)."""
    from puffpai import skills as skills_mod
    from puffpai.finding import install
    entry = install._read_lock().get(skill)  # noqa: SLF001
    pinned = ((entry or {}).get("files") or {}).get(skills_mod.SKILL_FILE) \
        if isinstance(entry, dict) else None
    if not isinstance(pinned, str):
        raise KeyUseRefused(f"BuildBot does not know which outside service the skill {skill} "
                            f"uses, because it was not installed through Find more")
    path = os.path.join(skills_mod.skills_dir(), skill, skills_mod.SKILL_FILE)
    try:
        with open(path, "rb") as fh:
            data = fh.read(1_000_000)
    except OSError:
        raise KeyUseRefused(f"the skill {skill} has no SKILL.md to say which outside "
                            f"service it uses") from None
    if hashlib.sha256(data).hexdigest() != pinned:
        raise KeyUseRefused(f"the skill {skill}'s SKILL.md was changed after it was "
                            f"installed, so BuildBot no longer knows its own service")
    return data.decode("utf-8", "replace")


def _declared_service_values(meta: Any) -> list[str]:
    """Every string under a key that names a service (``homepage``, ``api`` ...)
    anywhere in the frontmatter mapping."""
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.strip().lower() in _HOME_FIELDS \
                        and isinstance(v, str):
                    out.append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(meta if isinstance(meta, dict) else {})
    return out


def own_hosts(skill: str, names: Any = ()) -> list[str]:
    """The hosts that are the skill's own service: the homepage or API base it
    declares in frontmatter, plus hosts in its own code examples that also
    name one of its keys *names*. A prose link does not count. Raises
    :class:`KeyUseRefused` when none can be found (fail closed)."""
    from puffpai import skills as skills_mod
    text = _pinned_skill_md(skill)
    meta, body = skills_mod.parse_frontmatter(text)
    hosts: list[str] = []
    for value in _declared_service_values(meta):
        for h in _hosts_in(value):
            if h not in hosts:
                hosts.append(h)
    for value in _declared_service_values(meta):
        bare = value.strip().split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].lower()
        if "://" not in value and _usable_host(bare) and bare not in hosts:
            hosts.append(bare)
    wanted = [n for n in (names or ()) if isinstance(n, str)]
    for _fence, block in _FENCE.findall(body):
        if any(n and n in block for n in wanted):
            for h in _hosts_in(block):
                if h not in hosts:
                    hosts.append(h)
    if not hosts:
        raise KeyUseRefused(f"the skill {skill} does not declare a service of its own "
                            f"(a homepage or an API base, or a host in a code example "
                            f"next to the key), so BuildBot cannot tell where the key "
                            f"may go")
    return hosts


# ── reading the command (a small, strict bash reader) ─────────────────────────

_OPERATOR_WORDS = {
    "|": "it pipes output into another program",
    "&": "it runs more than one command",
    ";": "it runs more than one command",
    "<": "it reads from or writes to a file with < or >",
    ">": "it reads from or writes to a file with < or >",
    "(": "it uses shell grouping",
    ")": "it uses shell grouping",
    "`": "it runs a hidden command inside it",
    "\n": "it runs more than one command",
    "\r": "it runs more than one command",
    "{": "it uses shell braces, which can make more than one address",
    "}": "it uses shell braces, which can make more than one address",
    "*": "it has a wildcard outside quotes (put the address in quotes)",
    "?": "it has a wildcard outside quotes (put the address in quotes)",
    "[": "it has a wildcard outside quotes (put the address in quotes)",
    "]": "it has a wildcard outside quotes (put the address in quotes)",
    "!": "it uses ! outside quotes",
}


def _var(s: str, i: int, names: set[str]) -> tuple[str, int]:
    """Read a ``$`` expansion at ``s[i] == "$"``: the sentinel text and the
    next index. Only the skill's own key names are allowed."""
    nxt = s[i + 1] if i + 1 < len(s) else ""
    if nxt == "{":
        m = _NAME_CHARS.match(s, i + 2)
        if not m or s[m.end():m.end() + 1] != "}":
            raise KeyUseRefused("it uses a ${...} form that changes a value")
        name, end = m.group(0), m.end() + 1
    elif nxt == "(":
        raise KeyUseRefused("it runs a hidden command inside it ($(...))")
    else:
        m = _NAME_CHARS.match(s, i + 1)
        if not m:
            raise KeyUseRefused("it uses a $ form BuildBot cannot check")
        name, end = m.group(0), m.end()
        if name == "env" and s[end:end + 1] == ":":
            m2 = _NAME_CHARS.match(s, end + 1)
            if m2 and m2.group(0) in names:
                return _SENT + m2.group(0) + _SENT, m2.end()
    if name not in names:
        raise KeyUseRefused(f"it uses the variable {name}, and only the skill's own key "
                            f"may be named, so the address cannot be built from a variable")
    return _SENT + name + _SENT, end


_PERCENT = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _percent(s: str, i: int, names: set[str]) -> Optional[tuple[str, int]]:
    """``%NAME%`` at ``s[i]`` when NAME is a skill key (else None, so ``%`` is
    a literal, as in a ``curl -w "%{http_code}"`` format)."""
    m = _PERCENT.match(s, i)
    if m and m.group(1) in names:
        return _SENT + m.group(1) + _SENT, m.end()
    return None


def split_words(command: str, names: set[str]) -> list[str]:
    """The words bash would see, each key reference as a sentinel. Raises
    :class:`KeyUseRefused` for anything that hides what runs or where."""
    s, i, n = command, 0, len(command)
    words: list[str] = []
    cur: Optional[list[str]] = None

    def add(text: str) -> None:
        nonlocal cur
        if cur is None:
            cur = []
        cur.append(text)

    while i < n:
        c = s[i]
        if c in " \t":
            if cur is not None:
                words.append("".join(cur))
                cur = None
            i += 1
        elif c == "\\":
            if s[i + 1:i + 2] == "\n":
                i += 2
            elif s[i + 1:i + 3] == "\r\n":
                i += 3
            elif i + 1 < n and s[i + 1] not in "\r\n":
                add(s[i + 1])
                i += 2
            else:
                raise KeyUseRefused("BuildBot could not read where this command ends")
        elif c == "'":
            end = s.find("'", i + 1)
            if end < 0:
                raise KeyUseRefused("BuildBot could not read it (a quote is not closed)")
            part = s[i + 1:end]
            if "\n" in part or "\r" in part:
                raise KeyUseRefused("it has a line break inside quotes")
            add(part)
            i = end + 1
        elif c == '"':
            i += 1
            add("")
            while True:
                if i >= n:
                    raise KeyUseRefused("BuildBot could not read it (a quote is not closed)")
                d = s[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\":
                    if s[i + 1:i + 2] == "\n":
                        i += 2
                    elif s[i + 1:i + 3] == "\r\n":
                        i += 3
                    elif s[i + 1:i + 2] in ('"', "\\", "$", "`"):
                        add(s[i + 1])
                        i += 2
                    else:
                        add("\\")
                        i += 1
                elif d == "$":
                    text, i = _var(s, i, names)
                    add(text)
                elif d == "`":
                    raise KeyUseRefused("it runs a hidden command inside it (backticks)")
                elif d in "\r\n":
                    raise KeyUseRefused("it has a line break inside quotes")
                elif d == "%" and (hit := _percent(s, i, names)):
                    text, i = hit
                    add(text)
                else:
                    add(d)
                    i += 1
        elif c == "$":
            if s[i + 1:i + 2] in ("'", '"'):
                raise KeyUseRefused("it uses $'...' quoting, which can hide letters")
            text, i = _var(s, i, names)
            add(text)
        elif c == "%" and (hit := _percent(s, i, names)):
            text, i = hit
            add(text)
        elif c == "#" and cur is None:
            raise KeyUseRefused("it has a # comment")
        elif c in _OPERATOR_WORDS:
            raise KeyUseRefused(_OPERATOR_WORDS[c])
        else:
            add(c)
            i += 1
    if cur is not None:
        words.append("".join(cur))
    return words


# ── the programs ─────────────────────────────────────────────────────────────

class _Parsed:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.headers: list[str] = []


def _no_key(word: str, what: str) -> None:
    if _SENT in word:
        raise KeyUseRefused(f"the key is put in {what}, where BuildBot cannot check it")


_CURL_FLAGS = frozenset("sSfiIvgGNq46#jlRVM")
_CURL_VALUE = frozenset("HdXuAeFmTbwrCYy")
_CURL_KEY_OK_SHORT = frozenset("HduAeFb")
_CURL_SHORT_NO = {
    "o": "it writes the answer to a file", "O": "it writes the answer to a file",
    "J": "it writes the answer to a file", "D": "it writes headers to a file",
    "c": "it writes cookies to a file", "K": "it reads curl settings from a file",
    "x": "it sends through a proxy", "p": "it sends through a proxy",
    "U": "it sends through a proxy", "k": "it skips the certificate check",
    "L": "it follows redirects, which can carry the key to another address",
    "n": "it reads passwords from a file", "E": "it uses a certificate file",
    "Z": "it runs requests in parallel", ":": "it runs more than one request",
}
_CURL_LONG_VALUE = {
    "--header": True, "--data": True, "--data-raw": True, "--data-binary": True,
    "--data-ascii": True, "--data-urlencode": True, "--json": True, "--request": False,
    "--user": True, "--user-agent": True, "--referer": True, "--form": True,
    "--form-string": True, "--max-time": False, "--connect-timeout": False,
    "--retry": False, "--retry-delay": False, "--retry-max-time": False,
    "--upload-file": False, "--oauth2-bearer": True, "--cookie": True,
    "--write-out": False, "--range": False, "--url": False, "--url-query": True,
    "--proto": False, "--proto-redir": False, "--max-filesize": False,
    "--limit-rate": False, "--continue-at": False, "--expect100-timeout": False,
    "--keepalive-time": False, "--tls-max": False,
}
_CURL_LONG_FLAGS = frozenset((
    "--silent", "--show-error", "--fail", "--fail-with-body", "--fail-early", "--include",
    "--head", "--verbose", "--globoff", "--get", "--no-buffer", "--compressed",
    "--http1.0", "--http1.1", "--http2", "--http2-prior-knowledge", "--http3",
    "--ipv4", "--ipv6", "--no-progress-meter", "--disable", "--tlsv1.2", "--tlsv1.3",
    "--tr-encoding", "--raw", "--path-as-is", "--no-keepalive", "--retry-connrefused",
    "--retry-all-errors", "--show-headers", "--styled-output", "--no-styled-output",
    "--remote-time", "--ssl-reqd",
))
_CURL_LONG_NO = {
    "--output": "it writes the answer to a file", "--remote-name": "it writes the answer to a file",
    "--remote-name-all": "it writes the answer to a file",
    "--remote-header-name": "it writes the answer to a file",
    "--output-dir": "it writes the answer to a file", "--dump-header": "it writes headers to a file",
    "--cookie-jar": "it writes cookies to a file",
    "--trace": "it writes what it sends (with the key) to a file",
    "--trace-ascii": "it writes what it sends (with the key) to a file",
    "--libcurl": "it writes what it sends (with the key) to a file",
    "--stderr": "it writes messages to a file", "--config": "it reads curl settings from a file",
    "--proxy": "it sends through a proxy", "--preproxy": "it sends through a proxy",
    "--resolve": "it points the address at another server",
    "--connect-to": "it points the address at another server",
    "--doh-url": "it looks addresses up somewhere else",
    "--insecure": "it skips the certificate check",
    "--location": "it follows redirects, which can carry the key to another address",
    "--location-trusted": "it follows redirects, which can carry the key to another address",
    "--next": "it runs more than one request", "--parallel": "it runs requests in parallel",
    "--variable": "it builds values from variables",
}


def _curl(words: list[str], out: _Parsed) -> None:
    i, rest_urls = 1, False
    while i < len(words):
        w = words[i]
        i += 1
        if rest_urls or not w.startswith("-") or w == "-":
            out.urls.append(w)
            continue
        if w == "--":
            rest_urls = True
            continue
        _no_key(w, "a curl setting name")
        if w.startswith("--"):
            if w in _CURL_LONG_FLAGS:
                continue
            if w in _CURL_LONG_NO or w.startswith(("--proxy", "--socks", "--expand-",
                                                   "--netrc", "--trace")):
                raise KeyUseRefused(_CURL_LONG_NO.get(w, f"it uses the curl setting {w}"))
            if w not in _CURL_LONG_VALUE:
                raise KeyUseRefused(f"it uses the curl setting {w}, which BuildBot cannot check")
            if i >= len(words):
                raise KeyUseRefused(f"the curl setting {w} has no value")
            v = words[i]
            i += 1
            _curl_value(w, v, _CURL_LONG_VALUE[w], out)
            continue
        chars = w[1:]
        j = 0
        while j < len(chars):
            ch = chars[j]
            j += 1
            if ch in _CURL_FLAGS:
                continue
            if ch in _CURL_SHORT_NO:
                raise KeyUseRefused(_CURL_SHORT_NO[ch])
            if ch not in _CURL_VALUE:
                raise KeyUseRefused(f"it uses the curl setting -{ch}, which BuildBot "
                                    f"cannot check")
            if j < len(chars):
                v = chars[j:]
            elif i < len(words):
                v = words[i]
                i += 1
            else:
                raise KeyUseRefused(f"the curl setting -{ch} has no value")
            long = {"H": "--header", "w": "--write-out"}.get(ch, "-" + ch)
            _curl_value(long, v, ch in _CURL_KEY_OK_SHORT, out)
            break


def _curl_value(opt: str, v: str, key_ok: bool, out: _Parsed) -> None:
    if not key_ok:
        _no_key(v, f"the curl setting {opt}")
    if opt == "--header":
        if v.startswith("@"):
            raise KeyUseRefused("it reads headers from a file")
        out.headers.append(v)
    elif opt == "--url":
        if v.startswith("@"):
            raise KeyUseRefused("it reads addresses from a file")
        out.urls.append(v)
    elif opt == "--write-out" and ("output" in v.lower() or v.startswith("@")):
        raise KeyUseRefused("its --write-out writes to a file")


_WGET_FLAGS = {"-q", "--quiet", "-S", "--server-response", "-nv", "--no-verbose",
               "--content-on-error", "--no-config", "--https-only", "--no-cookies",
               "--retry-connrefused", "-v", "--verbose"}
_WGET_VALUE = {"--header": True, "--post-data": True, "--body-data": True, "--method": False,
               "--timeout": False, "-T": False, "--tries": False, "-t": False,
               "--max-redirect": False, "--output-document": False, "-O": False,
               "--user": True, "--password": True, "--http-user": True,
               "--http-password": True, "--user-agent": True, "-U": True, "--referer": True,
               "--read-timeout": False, "--connect-timeout": False, "--dns-timeout": False,
               "--waitretry": False, "--secure-protocol": False, "--post-file": False,
               "--body-file": False}


def _wget(words: list[str], out: _Parsed) -> None:
    redirects_off = to_stdout = False
    i = 1
    while i < len(words):
        w = words[i]
        i += 1
        if not w.startswith("-") or w == "-":
            out.urls.append(w)
            continue
        _no_key(w.split("=", 1)[0], "a wget setting name")
        if w in _WGET_FLAGS:
            continue
        # -qO- / -qO - (quiet, answer to the screen)
        m = re.fullmatch(r"-(q?)O(-?)", w)
        if m and w not in ("-O",):
            if m.group(2) == "-":
                to_stdout = True
                continue
            if i < len(words) and words[i] == "-":
                i += 1
                to_stdout = True
                continue
            raise KeyUseRefused("wget would save the answer to a file (use -O -)")
        name, eq, v = w.partition("=")
        if name not in _WGET_VALUE:
            if name in ("--no-check-certificate",):
                raise KeyUseRefused("it skips the certificate check")
            raise KeyUseRefused(f"it uses the wget setting {name}, which BuildBot cannot check")
        if not eq:
            if i >= len(words):
                raise KeyUseRefused(f"the wget setting {name} has no value")
            v = words[i]
            i += 1
        if not _WGET_VALUE[name]:
            _no_key(v, f"the wget setting {name}")
        if name == "--header":
            out.headers.append(v)
        elif name == "--max-redirect":
            if v != "0":
                raise KeyUseRefused("wget would follow redirects (use --max-redirect=0)")
            redirects_off = True
        elif name in ("--output-document", "-O"):
            if v != "-":
                raise KeyUseRefused("wget would save the answer to a file (use -O -)")
            to_stdout = True
    if not redirects_off:
        raise KeyUseRefused("wget follows redirects by itself, which can carry the key to "
                            "another address (add --max-redirect=0)")
    if not to_stdout:
        raise KeyUseRefused("wget would save the answer to a file (add -O -)")


_HTTPIE_FLAGS = {"--json", "-j", "--form", "-f", "--headers", "-h", "--body", "-b",
                 "--verbose", "-v", "--check-status", "--ignore-stdin", "-I", "--quiet",
                 "-q", "--stream", "-S", "--compress", "-x", "--all", "--offline",
                 "--chunked", "--multipart", "--path-as-is"}
_HTTPIE_VALUE = {"--print": False, "-p": False, "--pretty": False, "--style": False,
                 "-s": False, "--timeout": False, "--auth": True, "-a": True,
                 "--auth-type": False, "-A": False, "--raw": True, "--boundary": False,
                 "--max-redirects": False, "--response-charset": False,
                 "--response-mime": False, "--format-options": False, "--sorted": False}
_HTTPIE_NO = {"--follow": "it follows redirects, which can carry the key to another address",
              "-F": "it follows redirects, which can carry the key to another address",
              "--download": "it writes the answer to a file",
              "-d": "it writes the answer to a file", "--output": "it writes the answer to a file",
              "-o": "it writes the answer to a file", "--continue": "it writes to a file",
              "-c": "it writes to a file", "--session": "it saves the key in a session file",
              "--session-read-only": "it reads a session file",
              "--proxy": "it sends through a proxy", "--verify": "it changes the certificate check",
              "--cert": "it uses a certificate file", "--cert-key": "it uses a certificate file"}
_HTTPIE_SEPS = (":=@", "=@", "==", ":=", "@", "=", ":")


def _httpie_item(item: str, out: _Parsed) -> None:
    best: Optional[tuple[int, str]] = None
    for sep in _HTTPIE_SEPS:
        k = item.find(sep)
        if k > 0 and (best is None or k < best[0]):
            best = (k, sep)
    if best is None:
        raise KeyUseRefused(f"BuildBot could not read the httpie item {item[:40]!r}")
    k, sep = best
    _no_key(item[:k], "an httpie field name")
    if sep in (":=@", "=@", "@"):
        _no_key(item, "an httpie file field")
    if sep == ":":
        out.headers.append(item)


def _httpie(words: list[str], out: _Parsed) -> None:
    positional: list[str] = []
    i = 1
    while i < len(words):
        w = words[i]
        i += 1
        if not w.startswith("-") or w == "-":
            positional.append(w)
            continue
        name, eq, v = w.partition("=")
        _no_key(name, "an httpie setting name")
        if name in _HTTPIE_FLAGS and not eq:
            continue
        if name in _HTTPIE_NO:
            raise KeyUseRefused(_HTTPIE_NO[name])
        if name not in _HTTPIE_VALUE:
            raise KeyUseRefused(f"it uses the httpie setting {name}, which BuildBot "
                                f"cannot check")
        if not eq:
            if i >= len(words):
                raise KeyUseRefused(f"the httpie setting {name} has no value")
            v = words[i]
            i += 1
        if not _HTTPIE_VALUE[name]:
            _no_key(v, f"the httpie setting {name}")
        if name == "--max-redirects" and v != "0":
            raise KeyUseRefused("it changes how redirects are followed")
    if positional and re.fullmatch(r"[A-Za-z]+", positional[0]) and len(positional) > 1:
        if not positional[0].isupper():
            raise KeyUseRefused("write the httpie method in capitals (GET, POST)")
        positional = positional[1:]
    if not positional:
        raise KeyUseRefused("it has no address")
    out.urls.append(positional[0])
    for item in positional[1:]:
        _httpie_item(item, out)


_PROGRAMS = {"curl": _curl, "curl.exe": _curl, "wget": _wget, "wget.exe": _wget,
             "http": _httpie, "https": _httpie}
_SCRIPT_RUNNERS = frozenset(("python", "python3", "py", "python.exe", "node", "node.exe",
                             "deno", "bun", "ruby", "perl", "php"))
_SHOWS_IT = frozenset(("echo", "printf", "write-output", "write-host", "tee", "out-file",
                       "set-content", "add-content", "cat", "print"))
_DUMPS_ENV = frozenset(("env", "printenv", "set", "export", "declare", "typeset", "compgen",
                        "get-childitem", "gci", "dir", "ls"))
_HIDES_IT = frozenset(("eval", "exec", "source", ".", "base64", "xxd", "openssl", "iex",
                       "invoke-expression", "bash", "sh", "zsh", "dash", "cmd", "cmd.exe",
                       "powershell", "powershell.exe", "pwsh", "xargs", "nohup", "time",
                       "command", "builtin", "sudo", "nc", "ncat", "socat", "telnet", "ssh"))
_POWERSHELL_WEB = ("invoke-webrequest", "iwr", "invoke-restmethod", "irm")  # names only


def _program_reason(prog: str, words: list[str]) -> str:
    low = prog.lower()
    if low in _SCRIPT_RUNNERS:
        return ("a script given on the command line can build any address and follow "
                "redirects, so BuildBot cannot be sure where the key goes. The skill's own "
                "script gets the key by itself without naming it")
    if low in _SHOWS_IT:
        return f"{prog} would print or write the key"
    if low in _DUMPS_ENV:
        return f"{prog} would list the key with the other settings"
    if low in _HIDES_IT:
        return f"{prog} can hide what really runs or where it goes"
    if low in _POWERSHELL_WEB:
        return "commands run in bash here, where PowerShell commands do not run"
    return (f"BuildBot can only check where the key goes for one curl, wget or httpie "
            f"command, and this runs {prog}")


def _check_url(url: str, own: set[str]) -> str:
    """The host of *url*, or :class:`KeyUseRefused`."""
    m = re.match(r"(?i)^([a-z][a-z0-9+.-]*)://([^/?#]*)(.*)\Z", url)
    if not m:
        raise KeyUseRefused(f"{_shown(url)} is not a full https:// address")
    scheme, auth, rest = m.group(1).lower(), m.group(2), m.group(3)
    _no_key(auth, "the server part of the address")
    _no_key(scheme, "the address")
    if scheme != "https":
        raise KeyUseRefused(f"{_shown(url)} is not https, so the key would travel unprotected")
    if "@" in auth or "\\" in url or "%" in auth:
        raise KeyUseRefused(f"{_shown(url)} has a form that can hide the real server")
    if any(ch in url for ch in "{}[] \t"):
        raise KeyUseRefused(f"{_shown(url)} has braces or brackets, which curl turns "
                            f"into more than one address")
    pm = re.fullmatch(r"(.*?)(?::(\d{1,5}))?", auth)
    host = (pm.group(1) if pm else auth).lower()
    if not _usable_host(host):
        raise KeyUseRefused(f"{_shown(url)} is not an outside service BuildBot can check")
    if registrable(host) not in own:
        raise KeyUseRefused(f"it would send to {host}, which is not the skill's own service")
    return host


def _shown(text: str) -> str:
    text = text.replace(_SENT, "$")
    return text if len(text) <= 80 else text[:77] + "..."


def references(command: str, names: Any) -> list[str]:
    """The kept key names *command* mentions at all (any case, any form)."""
    out = []
    for name in names or ():
        if re.search(r"(?i)(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                     command or ""):
            out.append(name)
    return out


#: ``curl``/``wget``/``http`` spellings -> the :mod:`host_programs` name.
_PROGRAM_NAME = {"curl": "curl", "curl.exe": "curl", "wget": "wget", "wget.exe": "wget",
                 "http": "httpie", "https": "httpie"}


def check(command: str, skill: str, names: list[str]) -> dict[str, Any]:
    """May *command*, run as *skill*'s command, name the kept keys *names*?

    Returns a plan for a direct (no shell) run:
    ``{"names": [...], "hosts": [...], "program": "curl"|"wget"|"httpie",
    "argv": [...]}``. The ``argv`` is the literal argument list after the
    program, each key reference kept as ``\\x00NAME\\x00``; the runner swaps in
    the value with :func:`build_argv`. Raises :class:`KeyUseRefused` with a
    plain reason."""
    allowed = set(names)
    words = split_words(command.strip(), allowed)
    if not words:
        raise KeyUseRefused("there is no command")
    used = sorted({m for w in words for m in re.findall(_SENT + r"([A-Za-z0-9_]+)" + _SENT, w)})
    if not used:
        raise KeyUseRefused("BuildBot could not tell how the key is used")
    first = words[0]
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", first):
        raise KeyUseRefused("it sets a variable before the program")
    _no_key(first, "the program name")
    if "/" in first or "\\" in first:
        raise KeyUseRefused("name the program (curl, wget) without a folder, so a "
                            "program with the same name in another folder never runs")
    program = _PROGRAM_NAME.get(first.lower())
    if program is None:
        raise KeyUseRefused(_program_reason(first, words))
    own = {registrable(h) for h in own_hosts(skill, names)}
    # Every address anywhere in the command must be the skill's own.
    for w in words:
        for m in _URL_IN_TEXT.finditer(w.replace(_SENT, " ")):
            if m.group(0).lower().startswith("https://"):
                _check_url(m.group(0), own)
            else:
                _plain_http(m.group(0), own)
    out = _Parsed()
    _PROGRAMS[first.lower()](words, out)
    if not out.urls:
        raise KeyUseRefused("it has no address")
    hosts = [_check_url(u, own) for u in out.urls]
    for h in out.headers:
        name = h.split(":", 1)[0].strip().lower()
        if name in _ROUTING_HEADERS:
            raise KeyUseRefused(f"it sets the {name} header, which could hand the key to "
                                f"someone else on the same server")
    return {"names": used, "hosts": sorted(set(hosts)), "program": program,
            "argv": list(words[1:])}


def trusted_program(program: str) -> str:
    """The absolute path of ``curl``/``wget``/``httpie`` from a fixed system
    place, or raise :class:`KeyUseRefused`. Never a ``PATH`` lookup, never the
    tools or skill folder, so a planted copy can never run with the key."""
    from puffpai.security import host_programs
    try:
        found = host_programs.web_program(program)
    except Exception:  # noqa: BLE001 - unknown program: refuse
        found = None
    if not found:
        raise KeyUseRefused(f"this computer has no trusted {program} to run the request "
                            f"with (it is not where the system keeps it)")
    return found


def build_argv(plan: dict, values: dict[str, str]) -> list[str]:
    """``[trusted program path, *arguments]`` for a checked *plan*, with each
    key reference replaced by its value from *values*. The value lands only in
    this argv (in memory), never in a shell string, the chat, the card or a
    log."""
    exe = trusted_program(plan["program"])

    def fill(word: str) -> str:
        return re.sub(_SENT + r"([A-Za-z0-9_]+)" + _SENT,
                      lambda m: values.get(m.group(1), ""), word)
    return [exe] + [fill(w) for w in plan["argv"]]


def _plain_http(url: str, own: set[str]) -> None:
    """An http:// address: refused, naming the host when it is someone else's."""
    _check_url("https" + url[4:], own)
    raise KeyUseRefused(f"{_shown(url)} is not https, so the key would travel unprotected")


# ── scrubbing ────────────────────────────────────────────────────────────────

def _json_escape(raw: str) -> str:
    """The inside of a JSON string, with ``/`` escaped as ``\\/`` too (many
    encoders, PHP's default among them, write it that way)."""
    return json.dumps(raw)[1:-1].replace("/", "\\/")


def _json_uescape(raw: str) -> str:
    r"""Every character as a ``\uXXXX`` escape (a cautious encoder's form)."""
    return "".join(f"\\u{ord(c):04x}" for c in raw)


def _wrapped(text: str) -> list[str]:
    """*text* with a line break put in at the usual base64 widths (MIME 64,
    PEM 76), so a wrapped encoding is caught as well as the unwrapped one."""
    out = []
    for width in (64, 76):
        if len(text) > width:
            out.append("\n".join(text[i:i + width] for i in range(0, len(text), width)))
    return out


def key_forms(value: str) -> list[str]:
    """*value* and the forms a program or service may print it in: base64
    (standard and url-safe, at any offset inside a longer base64 run such as
    ``Basic`` auth, and wrapped at MIME/PEM widths), url-encoded (once and
    twice), hex, and JSON escaped (``\\/`` and ``\\uXXXX``). Longest first.

    Not covered (residuals, see SECURITY.md): a value split across lines by
    the service, reversed, or in an encoding we do not list."""
    raw = str(value or "")
    if not raw:
        return []
    forms = {raw}
    data = raw.encode("utf-8")
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        for lead in range(3):
            text = enc(b"\0" * lead + data).decode("ascii").rstrip("=")
            start = -(-8 * lead // 6)
            end = (8 * (lead + len(data)) - 6) // 6
            frag = text[start:end + 1]
            if len(frag) >= 8:
                forms.add(frag)
        forms.update(_wrapped(enc(data).decode("ascii").rstrip("=")))
    once = quote(raw, safe="")
    for form in (once, quote(once, safe=""), quote_plus(raw), quote_plus(quote_plus(raw)),
                 _json_escape(raw), json.dumps(raw)[1:-1], _json_uescape(raw), data.hex()):
        if len(form) >= 4:
            forms.add(form)
    return sorted(forms, key=len, reverse=True)


def _loose_base64(value: str) -> list[str]:
    """Whitespace-tolerant patterns for *value*'s base64 (standard and
    url-safe), at every byte offset (0, 1, 2) so a ``Basic`` auth encoding is
    covered too. A line break or space at ANY position between characters is
    allowed, so a wrapped encoding is caught however the service folded it."""
    out = []
    data = value.encode("utf-8")
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        for lead in range(3):
            text = enc(b"\0" * lead + data).decode("ascii").rstrip("=")
            start = -(-8 * lead // 6)
            end = (8 * (lead + len(data)) - 6) // 6
            frag = text[start:end + 1]
            if len(frag) >= 8:
                out.append(r"\s*".join(re.escape(c) for c in frag))
    return out


def scrub(text: str, values: Any) -> str:
    """*text* with every value in *values*, in any of its :func:`key_forms`,
    replaced by :data:`HIDDEN_KEY`. Case is ignored (hex and percent forms
    come in both cases). Scrub BEFORE any trimming, so a key cut across the
    trim boundary cannot leak."""
    vals = [str(v) for v in (values or ()) if len(str(v or "")) >= 4]
    if not vals or not text:
        return text
    parts: list[str] = []
    for v in vals:
        parts.extend(_loose_base64(v))          # most tolerant first
    forms: set[str] = set()
    for v in vals:
        forms.update(key_forms(v))
    parts.extend(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    pattern = re.compile("|".join(parts), re.IGNORECASE)
    return pattern.sub(HIDDEN_KEY, text)


def host_for_log(host: str) -> str:
    """*host* as a security log line may show it: a project address that
    looks like a key (``xkjk...supabase.co``) is shown by its platform."""
    try:
        from puffpai import web_search
        if not web_search.id_looks_like_key(host):
            return host
    except Exception:  # noqa: BLE001 - fail closed: show the platform only
        pass
    parent = host.split(".", 1)[1] if "." in host else ""
    return f"a project address on {parent}" if parent else "a project address"


__all__ = ["GENERIC_DOMAINS", "HIDDEN_KEY", "KeyUseRefused", "SHARED_SUFFIXES", "build_argv",
           "check", "host_for_log", "key_forms", "own_hosts", "references", "registrable",
           "scrub", "split_words", "trusted_program"]
