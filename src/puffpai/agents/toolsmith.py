"""Toolsmith — WF-7, the generic tool tier. The seventh master agent.

Every other capability on this team is a hand-coded intent: someone decided in
advance that "turn the lamp off" is a thing a build can do, and wrote the
branch. That does not scale to "read this page and keep a note of it". This
agent supplies the small set of *universal* tools a plan can compose into new
behaviour without anyone shipping a new intent — reach one page on the outside
world, and keep notes between steps.

**Why this is not the Steward.** The Steward uses the Home Assistant token and
the media key (kept in the vault, read for it by the key broker). Web content is
attacker-controlled text: a page can carry instructions
aimed at whatever model reads it next. Executing that fetch inside the
component that uses the house credentials would put a prompt-injection surface
and the ability to unlock the front door in the same privilege domain. The
Toolsmith is therefore a *separate* domain and holds **no** credential at all
(:data:`AgentScope.TOOLSMITH` maps to an empty allow-list, like the Concierge).
It can be talked into fetching a bad page; it cannot be talked into opening a
door, because it has never held the key that would.

Everything this agent returns from outside — a fetched page, a workspace file,
a command's output — is stamped ``untrusted: True`` so the layer that assembles
a prompt labels it as data rather than instruction. That label is the only
honest thing this agent can say about a web page.

Four structural gates, none of them advisory:

* **Egress** — two independent consents, both required, both refusing before a
  socket opens. The owner's standing **egress door** (``web_fetch`` in
  :mod:`puffpai.security.egress_doors`, default shut, and only openable at all
  under the ``enumerated`` run posture) says whether this build may make that
  class of request; ``envelope.budget.allow_egress`` says whether THIS request
  was granted egress by its caller. Budgets default egress **off** and doors
  default shut, so the default is "cannot reach the network" twice over.
* **SSRF** — refusal is by *resolved address*, not by name, so a public name
  whose A record points into the house LAN is rejected too; and the wire path
  is :func:`puffpai.security.egress_doors.egress_get`, which re-validates every
  redirect hop, re-checks the socket's real peer, and refuses a hop that leaves
  the host originally named. Reaching the network any other way is a test
  failure: ``tests/test_dashboard_weather_egress_doors.py`` scans the whole package
  for ungated ``requests``/``httpx``/``urllib`` calls.
* **Caller host scope** — an optional ``allowed_hosts`` payload entry the
  *caller* sets. Absent means unrestricted; present means enforced, **including
  an empty list, which permits nothing**.
* **Path containment** — the workspace is one directory under the state dir
  (:func:`puffpai.config.get_state_dir`, which honours ``$PUFFPAI_STATE_DIR``
  and ``$PUFFPAI_DATA_DIR``). Every path is realpath'd and proven to be inside
  the realpath'd root, which is what defeats ``../``, absolute paths, drive
  letters and symlinks in one check rather than four string tests.

A fifth gate governs ``workspace_exec``:

* **Isolation** — bubblewrap, **not Docker**. On an appliance the service
  account is typically in the ``docker`` group, and that group is
  root-equivalent: a Docker-backed "sandbox" would upgrade a prompt injection
  into root. It also **fails closed** — no sandbox means no execution, never
  execution on the host.

  Availability is decided by *probing for the bubblewrap binary at runtime*
  (:func:`sandbox_available`), never by a config flag: a flag is a claim, and a
  build whose owner sets it on a box with no ``bwrap`` would be claiming an
  isolation it does not have. On Windows, or on any Linux box without
  bubblewrap, ``workspace_exec`` is **not registered at all** — it is absent
  from :meth:`ToolsmithAgent.tools`, absent from ``health()["tools"]``, and the
  capability list says so in one plain sentence
  (:data:`SHELL_UNAVAILABLE_NOTE`).

``workspace_exec`` is the only tool here that is state-changing *and* has no
egress: ``--unshare-all`` removes the network namespace, so egress is not gated
for it, it is absent. Reaching the network still means ``web_fetch``.

The glue path (every platform, Windows included): ``write_glue`` and
``run_glue`` are the Assemble step's coding tools
(:mod:`puffpai.assemble.toolsmith_loop`). ``write_glue`` writes one small
Python file into an assembled app's own glue folder only after Drawbridge's
``glue`` checklist passes it; ``run_glue`` runs its one function with
``python -I -B`` in its own process, in a Job Object (or process group), for
30 seconds, with an audit hook that refuses programs, ``ctypes``, file
writes outside its scratch folder and any connection or name look-up beyond
127.0.0.1. That is NOT the bubblewrap jail and is not called a sandbox:
native code in a planned package is not stopped by it. Both are
state-changing (Sentinel reviews the envelope) and both work only on an app
the owner said yes to (its folder exists). Neither holds a key.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import html
import ipaddress
import os
import re
import subprocess

from puffpai import procflags
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger
from puffpai.security import egress_doors
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope

logger = get_logger(__name__)


# ── intent classification ───────────────────────────────────────────
#
# Declared here so the envelope *minter* can look up what needs
# ``state_changing=True``, and ``handle`` can refuse anything that arrived
# without it — i.e. anything that skipped Sentinel's veto in ``bus.send``.
STATE_CHANGING_INTENTS = frozenset({"workspace_write", "workspace_exec",
                                    "write_glue", "run_glue"})

#: Intents that leave the box. Kept separate from the state-changing set
#: because they are orthogonal questions: a fetch mutates nothing here and
#: still must not happen without an egress-carrying budget.
EGRESS_INTENTS = frozenset({"web_fetch"})

#: Everything this agent can do *at most*. What it can do on THIS machine is
#: :meth:`ToolsmithAgent.tools`, which is this set minus anything whose
#: prerequisite is missing. Anything not here is an unknown intent.
TOOL_INTENTS = frozenset({"web_fetch", "workspace_read", "workspace_write",
                          "workspace_list", "workspace_exec", "write_glue", "run_glue"})

#: The Assemble step's coding tools: present on every platform (no bubblewrap
#: needed; see the module doc for what limits a run).
GLUE_INTENTS = frozenset({"write_glue", "run_glue"})

#: What ``health()["glue"]`` says.
GLUE_NOTE = ("connecting code: checked by Drawbridge, run in its own process for "
             "30 s, 127.0.0.1 only, no keys (not a sandbox)")

#: Intents whose prerequisite is the sandbox. Absent from the registered tool
#: list when :func:`sandbox_available` is False.
SANDBOXED_INTENTS = frozenset({"workspace_exec"})

#: The egress door this agent's one outbound tool rides on.
WEB_FETCH_DOOR = "web_fetch"


# ── web_fetch policy ────────────────────────────────────────────────

#: Only this scheme ever opens a socket. ``http`` is refused as well as
#: ``file``, ``data``, ``gopher``, ``ftp`` and ``javascript``: the egress-door
#: registry is https-only by construction, and a build that cannot be
#: downgraded to plaintext by a redirect or a hand-edited config is the more
#: restrictive of the two available answers. ``file:///etc/shadow`` is the
#: exact bug this agent exists not to repeat.
ALLOWED_URL_SCHEMES = frozenset({"https"})

#: Ports a fetch may use. The address checks are the real gate, so this is
#: belt-and-braces against pivoting at an unusual port on a public host.
ALLOWED_PORTS = frozenset({443, 8443})

MAX_URL_LEN = 2048
#: Entries a caller's ``allowed_hosts`` may carry. Bounds the work per hop and
#: the size of the refusal message.
MAX_ALLOWED_HOSTS = 64
#: Raw bytes read off the wire before the connection is dropped, whatever the
#: server claims in Content-Length. A hostile endpoint cannot stream us to
#: death.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
#: Extracted-text ceilings.
DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 100_000
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 30.0

#: Content types worth extracting text from. Anything else (images, archives,
#: ``application/octet-stream``) is refused rather than fed to a text extractor
#: that would emit binary noise into a prompt.
ALLOWED_CONTENT_PREFIXES = ("text/",)
ALLOWED_CONTENT_TYPES = frozenset({
    "application/xhtml+xml", "application/xml", "application/json",
    "application/rss+xml", "application/atom+xml",
})

#: Name forms that can only ever mean "inside this house". Refused by name as
#: well as by address, so a resolver that answers oddly cannot open a hole.
LOCAL_HOST_NAMES = frozenset({"localhost", "localhost.localdomain"})
LOCAL_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa",
                       ".lan", ".arpa")

#: A final DNS label that is really a number — decimal, octal (leading zero) or
#: hex. ``https://127.1/``, ``https://2130706433/`` and ``https://0x7f000001/``
#: are all loopback to a permissive HTTP client. Most resolvers hand these back
#: as loopback and the address check catches them; refusing by shape as well
#: means the refusal does not depend on the resolver behaving.
_NUMERIC_LABEL_RE = re.compile(r"\A(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")

_UA = "PuffPAI-Toolsmith/1.0 (+local appliance; text extraction only)"


# ── workspace policy ────────────────────────────────────────────────

#: Directory name under the state dir. One root, so containment is one check.
WORKSPACE_DIRNAME = "toolsmith_workspace"

MAX_FILE_BYTES = 1024 * 1024
MAX_WORKSPACE_FILES = 500
MAX_REL_PATH_LEN = 255
MAX_PATH_DEPTH = 8
MAX_LIST_ENTRIES = 500

#: Path components that are never a legitimate relative path inside a sandbox.
#: The realpath containment check is the authority; these are refused early so
#: the *reason* returned to a caller says what was wrong.
_REJECTED_COMPONENTS = frozenset({"", ".", ".."})

#: Windows reserved device names — writing ``NUL`` or ``COM1`` opens a device,
#: not a file.
_WINDOWS_DEVICES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


# ── workspace_exec policy ───────────────────────────────────────────
#
# THREAT MODEL, answered explicitly:
#
# *What may the code open?*  Only the workspace. The sandbox root is built from
# scratch — read-only binds of the interpreter/runtime, a private ``/tmp``, and
# a single read-write bind of the Toolsmith workspace at :data:`EXEC_WORKDIR`.
# ``/etc``, ``/opt``, ``/data``, ``$HOME`` and every credential on the box
# simply do not exist inside.
#
# *What may it reach?*  Nothing. ``--unshare-all`` includes the network
# namespace, so there is no interface to talk to and no DNS to resolve with.
# This is why ``workspace_exec`` is deliberately NOT in :data:`EGRESS_INTENTS`:
# egress is not gated here, it is *absent*.
#
# *How is it killed?*  A wall-clock timeout kills the process group, and
# ``--die-with-parent`` means a sandbox cannot outlive the agent even if the
# timeout path is skipped. Output is capped.
#
# *What does a compromised sandbox reach?*  The workspace, and nothing else.
#
# WHY NOT DOCKER. Docker is the obvious backend and it is the wrong one: on an
# appliance the service account is in the ``docker`` group, and Docker group
# membership is root-equivalent — anything that can reach the socket can run
# ``docker run -v /:/host --privileged`` and own the host. A Docker-backed
# "sandbox" would convert a prompt-injection foothold into root, which is
# strictly worse than having no exec tool at all. Bubblewrap needs no daemon,
# no socket and no privileged group.
#
# FAIL CLOSED. If ``bwrap`` is missing the tool is not offered and, if asked
# for anyway, refuses. It never degrades to running the command on the host —
# an isolation failure must cost a capability, never silently cost the
# isolation.

#: The sandbox helper. Absolute on purpose: resolving via ``$PATH`` would let a
#: poisoned environment choose the "sandbox" that is supposed to contain it.
#: Where it is looked for; the program itself always comes from
#: ``host_programs.path("bwrap")`` (this path on Linux, nothing on Windows,
#: where "/usr/bin" would be a folder on the drive root anyone may create).
BWRAP = "/usr/bin/bwrap"

#: Where the workspace is mounted inside the jail, and the command's cwd.
EXEC_WORKDIR = "/work"

#: Read-only binds that make up the runtime. Deliberately does NOT include
#: ``/etc``: nothing here needs it, and it is where the interesting secrets
#: are. Missing sources are skipped rather than fatal — ``/lib64`` does not
#: exist on an arm64 userland.
EXEC_RO_BINDS = ("/usr", "/bin", "/sbin", "/lib", "/lib64")

EXEC_DEFAULT_TIMEOUT = 30.0
EXEC_MAX_TIMEOUT = 300.0
#: Output cap. Truncated rather than refused: a command that legitimately
#: prints a lot should still return its first page of output.
EXEC_MAX_OUTPUT_BYTES = 256 * 1024

#: The one sentence a person is shown where the shell tool would have been.
#: Deliberately plain and deliberately specific about the prerequisite, so
#: nobody reads "unavailable" as "broken" or goes looking for a setting.
SHELL_UNAVAILABLE_NOTE = "shell: not on this machine (needs Linux + bubblewrap)"

#: What ``health()["shell"]`` says when it IS there.
SHELL_AVAILABLE_NOTE = ("shell: bubblewrap jail: no network, workspace only, "
                        "fails closed")


def sandbox_available() -> bool:
    """Is the bubblewrap helper present and executable *right now*?

    Probed, never configured. A config flag is a claim about the machine; this
    is the machine. False on Windows, on macOS, and on any Linux box where
    ``bwrap`` is not installed — and in every one of those cases the shell tool
    is simply not offered rather than being offered and then failing, or worse,
    falling back to the host.
    """
    try:
        from puffpai.security import host_programs
        bwrap = host_programs.path("bwrap")
        return bool(bwrap) and os.access(bwrap, os.X_OK)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


class ToolsmithInputError(ValueError):
    """Bad payload — rejected before any socket opens or any file is touched."""


class EgressDenied(PermissionError):
    """A tool that must leave the box ran on a budget or door that forbids it."""


# ── HTML → text (dependency-free) ───────────────────────────────────
#
# Deliberately not a parser. A parser would be better at fidelity and worse at
# safety review: this is ~15 lines whose failure mode is "kept too much text",
# which the untrusted label already covers. What it must not do is emit script
# bodies or CSS into something a model will read as prose.

_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_DROP_ELEMENT_RE = re.compile(
    r"(?is)<(script|style|noscript|template|svg|canvas|head)\b[^>]*>.*?</\1\s*>")
_UNCLOSED_DROP_RE = re.compile(r"(?is)<(script|style)\b[^>]*>.*\Z")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_BLOCK_BREAK_RE = re.compile(
    r"(?is)</(?:p|div|li|tr|section|article|h[1-6]|blockquote|pre)\s*>"
    r"|<(?:br|hr)\s*/?>")
_TAG_RE = re.compile(r"(?s)<[^>]*>")
#: Any horizontal whitespace, including the non-breaking space HTML is full of.
_SPACES_RE = re.compile("[^\\S\\n]+")
_BLANKS_RE = re.compile(r"\n\s*\n\s*\n+")


def extract_text(body: str, content_type: str = "") -> str:
    """Best-effort readable text from a fetched body.

    Non-markup bodies (JSON, plain text) come back whitespace-normalised only —
    reformatting them would corrupt data the caller asked for verbatim.
    """
    lowered = (content_type or "").lower()
    is_markup = ("html" in lowered or "xml" in lowered
                 or bool(re.search(r"(?i)<\s*(?:html|body|div|p)\b", body[:4096])))
    if not is_markup:
        return _normalise_whitespace(body)

    title_match = _TITLE_RE.search(body)
    title = _normalise_whitespace(_TAG_RE.sub(" ", title_match.group(1))) \
        if title_match else ""

    text = _COMMENT_RE.sub(" ", body)
    text = _DROP_ELEMENT_RE.sub(" ", text)
    # A truncated body can leave a <script> open; everything after it is code,
    # not prose, so drop the tail rather than let it through detagged.
    text = _UNCLOSED_DROP_RE.sub(" ", text)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _normalise_whitespace(text)
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}" if text else title
    return text


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of spaces/blank lines and drop non-printables.

    Control characters are stripped because this text ends up in a prompt and
    in the logs; an ANSI escape or a lone ``\\r`` there is a rendering bug at
    best and log injection at worst.
    """
    cleaned = "".join(
        ch if (ch.isprintable() or ch in "\n\t") else " " for ch in text)
    cleaned = cleaned.replace("\t", " ")
    cleaned = _SPACES_RE.sub(" ", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.splitlines())
    return _BLANKS_RE.sub("\n\n", cleaned).strip()


# ── address policy ──────────────────────────────────────────────────

def reject_internal_address(text: str, *, host: str = "") -> None:
    """Refuse any address that is not a globally routable public one.

    The policy itself lives in
    :func:`puffpai.security.egress_doors.internal_address_reason` and is shared
    with the door registry, so the pre-flight refusal here and the refusal the
    door makes a moment later cannot drift apart. Judged with :mod:`ipaddress`
    on the *resolved* address rather than against a list of strings, because a
    blocklist of ``127.0.0.1``/``10.*`` never covers ``0.0.0.0``, ``[::1]``,
    ``::ffff:127.0.0.1``, ``100.64.0.0/10`` or the next range someone invents.

    The message names the HOST and the class, never the address it resolved to:
    this string goes back to the caller and into the logs, and a household's
    internal addressing is not something to write down there.
    """
    reason = egress_doors.internal_address_reason(text)
    if reason:
        where = host or str(text)
        raise ToolsmithInputError(f"web_fetch: {where} {reason}")


def _default_resolver(host: str, port: int) -> list[str]:
    """Every address *host* resolves to. Injected in tests; never called there."""
    try:
        return egress_doors.resolve_addresses(host, port)
    except OSError as exc:
        raise ToolsmithInputError(
            f"web_fetch: {host} could not be looked up "
            f"({type(exc).__name__})") from None


def _read_capped(response, limit: int) -> tuple[bytes, bool]:
    """Read at most *limit* bytes off *response*. Returns ``(body, hit_cap)``.

    Streams where the transport can, because ``Content-Length`` is a claim by
    the server and a hostile endpoint will happily lie about it. Falls back to
    ``.content``/``.text`` for a transport that cannot stream, and truncates
    that too — a cap that only applies on the fast path is not a cap.
    """
    chunks: list[bytes] = []
    total = 0
    iter_content = getattr(response, "iter_content", None)
    if callable(iter_content):
        try:
            for chunk in iter_content(chunk_size=16384):
                if not chunk:
                    continue
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(chunk)
                total += len(chunk)
                if total >= limit:
                    body = b"".join(chunks)[:limit]
                    return body, True
            return b"".join(chunks), False
        except (AttributeError, TypeError):  # pragma: no cover - odd transport
            pass
    raw = getattr(response, "content", None)
    if raw is None:
        raw = getattr(response, "text", "") or ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    return raw[:limit], len(raw) > limit


class ToolsmithAgent(BaseAgent):
    """Universal composable tools: read one page, keep notes. No keys at all.

    Args:
        secrets: shared provider (scope enforcement is identical either way).
            :data:`AgentScope.TOOLSMITH` resolves nothing, on purpose.
        resolver: ``(host, port) -> [address, ...]``. Injected so a test can
            prove a *name* resolving into the LAN is refused without owning a
            domain that does.
        workspace_root: absolute sandbox root. Injected by tests; otherwise
            resolved under :func:`puffpai.config.get_state_dir`.
        fetcher: ``(url, *, timeout, headers, resolver) -> response``. The one
            wire seam, defaulting to
            :func:`puffpai.security.egress_doors.egress_get` — which is where
            the door check, the per-hop redirect re-validation, the
            resolved-address check and the peer check live. Injected ONLY by
            tests, so that no test in this repo opens a socket.
    """

    name = "toolsmith"
    scope = AgentScope.TOOLSMITH

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 resolver=None, workspace_root: str | None = None,
                 fetcher=None) -> None:
        super().__init__(secrets=secrets)
        self._resolve = resolver or _default_resolver
        self._workspace_root = (os.path.realpath(workspace_root)
                                if workspace_root else None)
        self._fetcher = fetcher

    # ── what this machine can actually do ───────────────────────────
    def tools(self) -> list[str]:
        """The intents that are REGISTERED here, sorted.

        Not the same thing as :data:`TOOL_INTENTS`. A tool whose prerequisite
        is missing is left out rather than advertised and then refused: the
        list a person or a planner reads is the list of things that work.
        """
        available = set(TOOL_INTENTS)
        if not sandbox_available():
            available -= SANDBOXED_INTENTS
        return sorted(available)

    def capability_notes(self) -> list[str]:
        """One line per capability that is *absent*, and why. Plain sentences.

        Empty on a machine where everything is present. The point is that a
        missing capability is stated rather than silently missing: an owner
        reading the crew card should be able to see that the shell tool is not
        here and what it would take.
        """
        if sandbox_available():
            return []
        return [SHELL_UNAVAILABLE_NOTE]

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}

        # A state-changing intent that arrived on a read-only envelope never
        # went through Sentinel's veto in ``bus.send``. Fail closed.
        if intent in STATE_CHANGING_INTENTS and not envelope.state_changing:
            return {
                "ok": False,
                "requires_review": True,
                "error": f"toolsmith: {intent!r} is state-changing; the envelope "
                         "must set state_changing=True so Drawbridge reviews it",
            }

        # An intent that is real but not registered on THIS machine gets the
        # plain sentence, not "unknown intent" — the difference between "this
        # build cannot do that" and "you asked for something that isn't a
        # thing" is the difference between an honest answer and a confusing
        # one.
        if intent in SANDBOXED_INTENTS and not sandbox_available():
            return {"ok": False, "sandbox_unavailable": True,
                    "error": f"toolsmith: {SHELL_UNAVAILABLE_NOTE}"}

        try:
            if intent == "web_fetch":
                return self._web_fetch(envelope, p)
            if intent == "workspace_write":
                return self._workspace_write(envelope, p)
            if intent == "workspace_read":
                return self._workspace_read(envelope, p)
            if intent == "workspace_list":
                return self._workspace_list(envelope, p)
            if intent == "workspace_exec":
                return self._workspace_exec(envelope, p)
            if intent in GLUE_INTENTS:
                return self._glue(intent, p)
            return {"ok": False, "error": f"toolsmith: unknown intent {intent!r}"}
        except EgressDenied as exc:
            return {"ok": False, "egress_denied": True,
                    "error": f"toolsmith: {exc}"}
        except KeyError as exc:
            return {"ok": False, "error": f"toolsmith: missing payload key {exc}"}
        except ToolsmithInputError as exc:
            return {"ok": False, "error": f"toolsmith: {exc}"}
        except OSError as exc:
            # Disk full, permission denied, a workspace that will not create.
            # Reported, never raised: a failing tool must not abort the bus.
            from puffpai.web_search import log_preview
            logger.warning("toolsmith %s failed: %s", intent,
                           log_preview(str(exc), 200))
            return {"ok": False, "error": f"toolsmith: {intent} failed ({exc})"}

    # ── web_fetch ───────────────────────────────────────────────────
    def _web_fetch(self, envelope: Envelope, p: dict) -> dict:
        """Fetch one public page and return its **text**, marked untrusted.

        The order of operations is the security property. Everything that can
        refuse does so before a socket opens:

        0. the owner's ``web_fetch`` egress door must be open — the standing
           consent, checked before anything else so a shut door says so plainly
           rather than producing a URL error that explains nothing;
        1. the request's budget must carry egress at all;
        2. the URL is shape-checked — scheme, length, printability;
        3. the host must be in the caller's ``allowed_hosts``, when the caller
           supplied one. A *scope* check, not a safety check: the caller is
           saying which sites this particular request was ever authorised to
           read;
        4. the host is refused if it is a literal internal address, a
           local-network name, a single-label intranet name or a numeric label
           that is an address in disguise;
        5. every address the host resolves to must be globally routable;
        6. the port must be on the allow-list;
        7. the hop is charged to the budget as an egress action;
        8. only then does the request go to
           :func:`puffpai.security.egress_doors.egress_get`, which re-checks the
           door, re-validates the URL, re-resolves and re-checks every address,
           re-checks the socket's actual peer, and re-validates every redirect
           — refusing any hop that leaves the host originally named. That last
           rule is stricter than a caller's ``allowed_hosts`` and is what stops
           the "``302`` to attacker.example" pivot regardless of scope;
        9. the response's content type must be text-ish, and at most
           :data:`MAX_RESPONSE_BYTES` are read whatever it claims.

        Returns raw *text*, never HTML: the caller gets prose, not a document
        something downstream might render. ``untrusted: True`` is always set.
        """
        # The owner's switch, before the caller's grant and before the URL is
        # even shape-checked.
        self._require_door()

        budget = getattr(envelope, "budget", None)
        if budget is None or not getattr(budget, "allow_egress", False):
            # Explicit and legible. ``budget.consume(egress=True)`` below would
            # also stop this and deliberately still runs — one gate that returns
            # a good error, one primitive that cannot be edited out of the path.
            raise EgressDenied(
                "web_fetch needs an envelope whose budget sets "
                "allow_egress=True; budgets default to no egress")

        url = self._clean_url(p.get("url"))
        allowed_hosts = self._allowed_hosts(p.get("allowed_hosts"))
        max_chars = self._max_chars(p.get("max_chars"))
        timeout = self._timeout(p.get("timeout"))

        self._validate_url(url, allowed_hosts)
        budget.consume(1, egress=True)

        response = self._fetch(url, timeout=timeout)
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if status >= 400 or status == 0:
                return {"ok": False, "url": url, "status": status,
                        "error": f"toolsmith: web_fetch: HTTP {status}"}
            content_type = self._content_type(response)
            raw, over_cap = _read_capped(response, MAX_RESPONSE_BYTES)
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # pragma: no cover - transport specific
                    pass

        body = raw.decode(_charset(content_type), errors="replace")
        text = extract_text(body, content_type)
        truncated = over_cap or len(text) > max_chars
        if len(text) > max_chars:
            text = text[:max_chars]
        return {
            "ok": True,
            "url": url,
            "status": status,
            "content_type": content_type,
            "bytes_read": len(raw),
            "text": text,
            "truncated": truncated,
            # The whole point of this agent's separation: whatever came back is
            # DATA. Downstream prompt assembly must label it, never splice it in
            # as instruction.
            "untrusted": True,
        }

    def _require_door(self) -> None:
        """The household's standing consent for this class of egress. No socket.

        Resolved from :mod:`puffpai.security.egress_doors` — env > config >
        posture > shut — and only openable at all under the ``enumerated`` run
        posture. Fails closed on anything unexpected: a registry that will not
        import or resolve is not consent.
        """
        try:
            egress_doors.require_open(WEB_FETCH_DOOR)
        except egress_doors.EgressBlocked as blocked:
            raise EgressDenied(
                f"[Outside service switched off] {blocked.user_message}") from blocked
        except Exception as exc:  # a broken registry must not read as "open"
            logger.warning("toolsmith: egress door %s unreadable: %s",
                           WEB_FETCH_DOOR, exc)
            raise EgressDenied(
                f"[Outside service switched off] The {WEB_FETCH_DOOR!r} switch "
                f"could not be read, so I am treating it as off.") from exc

    def _fetch(self, url: str, *, timeout: float):
        """The one wire seam. Everything outbound goes through a door.

        There is deliberately no ``requests`` import in this module: the
        package-wide scan in ``tests/test_dashboard_weather_egress_doors.py`` fails
        the build on an outbound call that is not routed through the registry,
        and an agent that reads attacker-supplied URLs is the last place that
        rule should be bent.
        """
        fetcher = self._fetcher or self._door_get
        return fetcher(url, timeout=timeout,
                       headers={"User-Agent": _UA,
                                "Accept": "text/html,text/plain,*/*"},
                       resolver=self._resolve)

    @staticmethod
    def _door_get(url: str, *, timeout: float, headers: dict, resolver):
        """``GET`` through the ``web_fetch`` door, streamed so the cap applies.

        ``egress_get`` owns redirect policy: it re-validates each ``Location``,
        re-resolves it, re-checks the peer, and refuses any hop that leaves the
        host this request originally named.
        """
        try:
            return egress_doors.egress_get(
                WEB_FETCH_DOOR, url, timeout=timeout, headers=headers,
                stream=True, resolver=resolver)
        except egress_doors.EgressBlocked as blocked:
            # The door's own refusals — a private resolved address, a redirect
            # off the named host, an unencrypted hop — come back as this
            # agent's refusal so a caller has one shape to handle.
            raise ToolsmithInputError(
                f"web_fetch: {blocked.user_message}") from None

    # ── web_fetch input validation ──────────────────────────────────
    @staticmethod
    def _clean_url(value) -> str:
        """Shape checks that need no network: type, length, printability."""
        if not isinstance(value, str):
            raise ToolsmithInputError("web_fetch: url must be a string")
        url = value.strip()
        if not url:
            raise ToolsmithInputError("web_fetch: url is empty")
        if len(url) > MAX_URL_LEN:
            raise ToolsmithInputError(
                f"web_fetch: url exceeds {MAX_URL_LEN} characters")
        if any(ch.isspace() or not ch.isprintable() for ch in url):
            raise ToolsmithInputError(
                "web_fetch: url contains whitespace or control characters")
        return url

    @staticmethod
    def _allowed_hosts(value) -> frozenset[str] | None:
        """Normalise a caller's host scope. ``None`` means **unrestricted**.

        The absent/empty distinction is the whole contract and must not be
        collapsed into a truthiness test:

        * **absent or ``None``** → unrestricted;
        * **present** → enforced, and that includes ``[]``, which permits
          nothing at all. ``if allowed_hosts:`` would silently turn the most
          restrictive possible scope into no scope at all.

        A value that is not a list or tuple **refuses the fetch** rather than
        being ignored: a caller that meant to pass a scope and passed a string
        must not be quietly promoted to unrestricted.
        """
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ToolsmithInputError(
                "web_fetch: allowed_hosts must be a list of hostnames "
                f"(got {type(value).__name__})")
        hosts = set()
        for entry in list(value)[:MAX_ALLOWED_HOSTS]:
            if not isinstance(entry, str):
                # Dropped, not fatal: one junk entry narrows the scope, which
                # is the safe direction. A junk *container* is fatal above.
                continue
            # Normalised exactly as ``_validate_url`` normalises the host it
            # compares against, or the comparison is unsound in both directions.
            host = entry.strip().lower().rstrip(".")
            if host:
                hosts.add(host)
        return frozenset(hosts)

    def _validate_url(self, url: str, allowed_hosts: frozenset[str] | None = None):
        """Full pre-flight validation. Raises unless this URL is safe to open."""
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port   # .port validates the port
        except ValueError as exc:
            raise ToolsmithInputError(f"web_fetch: unusable url ({exc})") from None

        scheme = (parts.scheme or "").lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise ToolsmithInputError(
                f"web_fetch: url scheme must be one of "
                f"{sorted(ALLOWED_URL_SCHEMES)}, not {scheme!r}")
        if parts.username or parts.password or "@" in parts.netloc:
            # Credentials in a URL are handed to whichever host actually
            # answers, which after a redirect need not be the one the caller
            # meant. The message must not echo them back into a log.
            raise ToolsmithInputError("web_fetch: url must not carry credentials")
        if not host:
            raise ToolsmithInputError("web_fetch: url has no host")

        host = host.lower().rstrip(".")

        # The caller's scope, ahead of the address checks on purpose: an
        # out-of-scope host must not even be looked up, because a DNS query for
        # an attacker-chosen name is itself a signal leaving the box.
        if allowed_hosts is not None and host not in allowed_hosts:
            raise ToolsmithInputError(
                f"web_fetch: {host} is not in the caller's allowed_hosts")

        # A literal address is checked directly as well as through the
        # resolver, so the refusal does not depend on the resolver at all — and
        # it is checked FIRST, because the name heuristics below would
        # otherwise answer "127.0.0.1 is an obfuscated address" when the
        # truthful refusal is "that is loopback".
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            reject_internal_address(str(literal), host=host)
        else:
            self._reject_local_name(host)

        effective_port = port or 443
        if effective_port not in ALLOWED_PORTS:
            raise ToolsmithInputError(
                f"web_fetch: port {effective_port} is not in "
                f"{sorted(ALLOWED_PORTS)}")
        addresses = self._resolve(host, effective_port) or []
        if not addresses:
            raise ToolsmithInputError(f"web_fetch: {host} resolves to nothing")
        for address in addresses:
            reject_internal_address(address, host=host)
        return parts

    @staticmethod
    def _reject_local_name(host: str) -> None:
        """Refuse names that can only ever mean 'inside this house'."""
        if host in LOCAL_HOST_NAMES or host.endswith(LOCAL_HOST_SUFFIXES):
            raise ToolsmithInputError(
                f"web_fetch: {host} is a local-network name")
        # Strict parsing failed, but a host whose last label is numeric is an
        # address in disguise: ``127.1``, ``2130706433`` and ``0x7f000001`` are
        # all loopback to a permissive client. A real DNS name never ends in an
        # all-numeric label, so this cannot reject a legitimate host.
        if _NUMERIC_LABEL_RE.match(host.rsplit(".", 1)[-1]):
            raise ToolsmithInputError(
                f"web_fetch: {host} is an abbreviated or obfuscated IP address")
        if "." not in host:
            raise ToolsmithInputError(
                f"web_fetch: {host} is a single-label intranet name")

    @staticmethod
    def _content_type(response) -> str:
        headers = getattr(response, "headers", {}) or {}
        raw = str(headers.get("Content-Type") or headers.get("content-type") or "")
        content_type = raw.split(";", 1)[0].strip().lower()
        if not content_type:
            # No declared type: treat it as plain text rather than guess. The
            # size cap and the text extractor both hold regardless.
            return "text/plain"
        if (content_type in ALLOWED_CONTENT_TYPES
                or content_type.startswith(ALLOWED_CONTENT_PREFIXES)):
            return content_type
        raise ToolsmithInputError(
            f"web_fetch: content-type {content_type!r} is not text; this tool "
            "extracts prose and will not hand back binary")

    @staticmethod
    def _max_chars(value) -> int:
        if value is None:
            return DEFAULT_MAX_CHARS
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolsmithInputError("web_fetch: max_chars must be a number")
        number = int(value)
        if number < 1:
            raise ToolsmithInputError("web_fetch: max_chars must be positive")
        return min(number, HARD_MAX_CHARS)

    @staticmethod
    def _timeout(value) -> float:
        if value is None:
            return DEFAULT_TIMEOUT
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolsmithInputError("web_fetch: timeout must be a number")
        number = float(value)
        if number <= 0:
            raise ToolsmithInputError("web_fetch: timeout must be positive")
        return min(number, MAX_TIMEOUT)

    # ── workspace ───────────────────────────────────────────────────
    def workspace_root(self) -> str:
        """Absolute sandbox root, resolved once per agent and created on use.

        Under the STATE dir, never a package-relative path: a packaged install
        puts the code somewhere read-only, and a package-relative workspace
        there fails at the worst moment. :func:`puffpai.config.get_state_dir`
        is the one helper that already honours ``$PUFFPAI_STATE_DIR`` and
        ``$PUFFPAI_DATA_DIR``, so this cannot drift from where everything else
        writes.
        """
        if self._workspace_root is None:
            try:
                from puffpai.config import get_state_dir
                base = get_state_dir(create=True)
            except Exception as exc:  # config must never break a tool
                logger.warning("config unavailable for toolsmith workspace: %s",
                               exc)
                base = os.path.abspath(os.environ.get("PUFFPAI_STATE_DIR")
                                       or os.getcwd())
            self._workspace_root = os.path.realpath(
                os.path.join(base, WORKSPACE_DIRNAME))
        os.makedirs(self._workspace_root, exist_ok=True)
        return self._workspace_root

    def _safe_path(self, value, *, field: str = "path",
                   allow_root: bool = False) -> str:
        """Resolve *value* inside the workspace, or refuse.

        The containment proof is ``realpath(root/rel)`` starting with
        ``realpath(root) + sep``. That one test defeats ``../``, an absolute
        path, a Windows drive letter, a UNC path **and** a symlink planted
        inside the workspace that points out of it — three of which a
        string-prefix check on the un-resolved path would let through.

        The component checks above it exist for the error message, not for the
        security property; the realpath check would refuse all of them anyway.
        """
        if not isinstance(value, str):
            if value is None and allow_root:
                return self.workspace_root()
            raise ToolsmithInputError(f"{field} must be a string")
        raw = value.strip()
        if not raw:
            if allow_root:
                return self.workspace_root()
            raise ToolsmithInputError(f"{field} is empty")
        if "\x00" in raw:
            raise ToolsmithInputError(f"{field} contains a null byte")
        if len(raw) > MAX_REL_PATH_LEN:
            raise ToolsmithInputError(
                f"{field} exceeds {MAX_REL_PATH_LEN} characters")

        normalised = raw.replace("\\", "/")
        if normalised.startswith("/") or os.path.isabs(raw) or ":" in normalised:
            raise ToolsmithInputError(
                f"{field} must be relative to the workspace, not an absolute path")

        components = normalised.split("/")
        if len(components) > MAX_PATH_DEPTH:
            raise ToolsmithInputError(
                f"{field} is nested deeper than {MAX_PATH_DEPTH} levels")
        for component in components:
            if component in _REJECTED_COMPONENTS:
                raise ToolsmithInputError(
                    f"{field} may not contain empty, '.' or '..' components")
            if component.split(".", 1)[0].lower() in _WINDOWS_DEVICES:
                raise ToolsmithInputError(
                    f"{field} names a reserved device ({component!r})")

        root = self.workspace_root()
        resolved = os.path.realpath(os.path.join(root, *components))
        if resolved != root and not resolved.startswith(root + os.sep):
            raise ToolsmithInputError(f"{field} escapes the workspace sandbox")
        if resolved == root and not allow_root:
            raise ToolsmithInputError(f"{field} is the workspace root itself")
        return resolved

    def _workspace_write(self, envelope: Envelope, p: dict) -> dict:
        """Write UTF-8 text into the sandbox. Text only, and capped.

        This is a notes drawer for a plan in progress — not a place to stage a
        binary, and not a place a runaway loop can fill a disk from.
        """
        path = self._safe_path(p.get("path"))
        content = p.get("content")
        if content is None:
            raise ToolsmithInputError("content is required")
        if not isinstance(content, str):
            raise ToolsmithInputError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ToolsmithInputError(f"content exceeds {MAX_FILE_BYTES} bytes")

        existed = os.path.exists(path)
        if not existed and self._count_files() >= MAX_WORKSPACE_FILES:
            raise ToolsmithInputError(
                f"workspace already holds {MAX_WORKSPACE_FILES} files; delete "
                "some before writing more")

        envelope.budget.consume(1)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return {"ok": True, "path": self._relative(path),
                "bytes": len(encoded), "created": not existed}

    def _workspace_read(self, envelope: Envelope, p: dict) -> dict:
        """Read a sandbox file back as text.

        Marked ``untrusted`` like a fetch: the most likely author of a workspace
        file is a previous ``web_fetch``, so its content carries exactly the
        same prompt-injection risk one step later.
        """
        path = self._safe_path(p.get("path"))
        if not os.path.isfile(path):
            return {"ok": False,
                    "error": f"toolsmith: no such workspace file: "
                             f"{self._relative(path)}"}
        envelope.budget.consume(1)
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_FILE_BYTES)
        return {"ok": True, "path": self._relative(path), "content": content,
                "bytes": size, "truncated": size > len(content.encode("utf-8")),
                "untrusted": True}

    def _workspace_list(self, envelope: Envelope, p: dict) -> dict:
        """List sandbox files, optionally under *subdir*.

        ``subdir`` goes through the same containment check as any other path,
        so ``workspace_list`` cannot be used to enumerate ``/etc`` — a listing
        is a read, and a read of the wrong directory is still a disclosure.
        """
        root = self.workspace_root()
        base = self._safe_path(p.get("subdir"), field="subdir", allow_root=True)
        envelope.budget.consume(1)
        if not os.path.isdir(base):
            return {"ok": True, "root": root, "subdir": self._relative(base),
                    "files": [], "count": 0}

        files = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                # ``os.walk`` does not follow directory symlinks, but a
                # symlinked FILE still reads through: skip anything whose
                # realpath leaves the root, so a listing can never advertise
                # /etc/passwd.
                real = os.path.realpath(full)
                if real != root and not real.startswith(root + os.sep):
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                files.append({"path": self._relative(full), "bytes": size})
                if len(files) >= MAX_LIST_ENTRIES:
                    return {"ok": True, "root": root,
                            "subdir": self._relative(base), "files": files,
                            "count": len(files), "truncated": True}
        return {"ok": True, "root": root, "subdir": self._relative(base),
                "files": files, "count": len(files)}

    def _relative(self, path: str) -> str:
        """Workspace-relative form, with forward slashes on every platform."""
        root = self.workspace_root()
        try:
            rel = os.path.relpath(path, root)
        except ValueError:  # pragma: no cover - different drive on Windows
            return path
        return "." if rel == "." else rel.replace(os.sep, "/")

    def _count_files(self) -> int:
        total = 0
        for _dirpath, _dirnames, filenames in os.walk(self.workspace_root()):
            total += len(filenames)
            if total >= MAX_WORKSPACE_FILES:
                break
        return total

    # ── workspace_exec ──────────────────────────────────────────────
    def _workspace_exec(self, envelope: Envelope, p: dict) -> dict:
        """Run a command against the workspace, inside a bubblewrap jail.

        This is the generic primitive the enumerated-capability tier cannot
        provide: it is what lets a plan do a thing nobody wrote an intent for.
        The whole reason it is safe to offer is the jail, so the jail is built
        here rather than trusted from configuration — see the threat model
        above :data:`BWRAP` for what it does and does not contain, and for why
        it is NOT Docker-backed.

        Three refusals, in order, none of them advisory:

        1. **Reviewed** — ``workspace_exec`` is in
           :data:`STATE_CHANGING_INTENTS`, so ``handle`` has already refused any
           envelope that did not carry ``state_changing=True``.
        2. **Isolable** — without :data:`BWRAP` the tool is not registered, and
           if asked for anyway it refuses. It does **not** fall back to host
           execution; losing isolation must cost the capability, not the
           isolation. There is no ``shell=True`` anywhere on this path: the
           argv is a list and the only shell involved is ``/bin/sh`` *inside*
           the jail.
        3. **Bounded** — wall-clock timeout, killed as a process group, output
           truncated to :data:`EXEC_MAX_OUTPUT_BYTES`.

        The result is stamped ``untrusted: True`` for the same reason a fetch
        is: a command's output is very often a file some earlier ``web_fetch``
        wrote. Exit status is reported rather than raised — a command that
        fails is a *result*.
        """
        command = p.get("command")
        if command is None:
            raise ToolsmithInputError("command is required")
        if not isinstance(command, str):
            raise ToolsmithInputError("command must be a string")
        if not command.strip():
            raise ToolsmithInputError("command must not be empty")

        timeout = self._exec_timeout(p.get("timeout"))

        if not sandbox_available():
            # Fail closed. See the threat model: never degrade to the host.
            return {"ok": False, "sandbox_unavailable": True,
                    "error": f"toolsmith: {SHELL_UNAVAILABLE_NOTE}"}

        from puffpai.security import host_programs
        bwrap = host_programs.path("bwrap")
        if not bwrap:
            return {"ok": False, "sandbox_unavailable": True,
                    "error": f"toolsmith: {SHELL_UNAVAILABLE_NOTE}"}
        root = self.workspace_root()
        envelope.budget.consume(1)
        argv = self._jail_argv(root, command, bwrap)

        try:
            proc = subprocess.run(
                argv, capture_output=True, timeout=timeout,
                start_new_session=True,   # own process group, so the kill lands
                **procflags.hidden(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "timed_out": True, "untrusted": True,
                    "timeout": timeout,
                    "error": f"toolsmith: command exceeded {timeout:g}s and was "
                             "killed"}
        except OSError as exc:
            return {"ok": False, "sandbox_unavailable": True,
                    "error": f"toolsmith: sandbox failed to start ({exc}); "
                             "refusing to run the command unsandboxed"}

        stdout, out_trunc = self._cap_output(proc.stdout)
        stderr, err_trunc = self._cap_output(proc.stderr)
        return {
            "ok": proc.returncode == 0,
            "untrusted": True,
            "sandboxed": True,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
        }

    @staticmethod
    def _jail_argv(root: str, command: str, bwrap: str) -> list:
        """Build the bubblewrap argv the shell tool runs inside.

        One builder, so there is a single place to audit what a jail contains
        and a single place a mistake could be made. Nothing here is derived
        from a payload except the command string itself, which is passed as a
        positional argument to ``/bin/sh -c`` *inside* the jail — not through a
        host shell.
        """
        argv = [bwrap,
                "--unshare-all",      # net namespace included: no egress, at all
                "--die-with-parent",  # cannot outlive the agent
                "--new-session",      # no shared tty -> no TIOCSTI injection
                "--clearenv"]
        for src in EXEC_RO_BINDS:
            # Skipped when absent: /lib64 has no arm64 counterpart, and a bind
            # list that assumes it would refuse every command on that hardware.
            if os.path.exists(src):
                argv += ["--ro-bind", src, src]
        argv += ["--proc", "/proc",
                 "--dev", "/dev",
                 "--tmpfs", "/tmp",
                 "--bind", root, EXEC_WORKDIR,   # the ONLY writable host path
                 "--chdir", EXEC_WORKDIR,
                 "--setenv", "PATH", "/usr/bin:/bin",
                 "--setenv", "HOME", EXEC_WORKDIR,
                 "--setenv", "TMPDIR", "/tmp",
                 "--", "/bin/sh", "-c", command]
        return argv

    @staticmethod
    def _exec_timeout(raw, *, default: float = EXEC_DEFAULT_TIMEOUT) -> float:
        """Clamp a caller's timeout into ``(0, EXEC_MAX_TIMEOUT]``."""
        if raw is None:
            return min(default, EXEC_MAX_TIMEOUT)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ToolsmithInputError("timeout must be a number") from None
        if value != value or value <= 0:          # NaN or non-positive
            raise ToolsmithInputError("timeout must be positive")
        return min(value, EXEC_MAX_TIMEOUT)

    @staticmethod
    def _cap_output(raw: bytes) -> tuple[str, bool]:
        """Decode captured output, truncating past the cap.

        ``errors="replace"`` because a sandboxed command may emit arbitrary
        bytes, and a tool that raises on a stray byte is a tool that fails on
        the day it matters.
        """
        data = raw or b""
        truncated = len(data) > EXEC_MAX_OUTPUT_BYTES
        if truncated:
            data = data[:EXEC_MAX_OUTPUT_BYTES]
        return data.decode("utf-8", errors="replace"), truncated

    # ── the glue path (every platform) ─────────────────────────────
    @staticmethod
    def _glue(intent: str, p: dict) -> dict:
        """``write_glue`` {app_id, name, code} / ``run_glue`` {app_id, name, args}.

        Only for an assembled app that exists (the owner said yes to it)."""
        from puffpai.assemble import toolsmith_loop
        from puffpai.assemble.server import host
        try:
            folder = host.app_folder(str(p.get("app_id") or ""))
        except host.AssembleError as exc:
            raise ToolsmithInputError(f"{intent}: {exc.line}") from None
        if not os.path.isfile(os.path.join(folder, "spec.json")):
            raise ToolsmithInputError(f"{intent}: that app has not been put together")
        if intent == "write_glue":
            got = toolsmith_loop.write_glue(str(p["app_id"]), str(p.get("name") or ""),
                                            str(p.get("code") or ""))
        else:
            args = p.get("args") if isinstance(p.get("args"), dict) else {}
            got = toolsmith_loop.run_glue(str(p["app_id"]), str(p.get("name") or ""), args)
            got["untrusted"] = True
        return got

    # ── health ──────────────────────────────────────────────────────
    def health(self) -> dict:
        base = super().health()
        # The REGISTERED list, not the aspirational one. The Agent Team page
        # renders this verbatim, so listing a tool that is not here would make
        # the product describe a capability it does not have.
        base["tools"] = self.tools()
        base["shell"] = (SHELL_AVAILABLE_NOTE if sandbox_available()
                         else SHELL_UNAVAILABLE_NOTE)
        base["notes"] = self.capability_notes()
        base["glue"] = GLUE_NOTE
        base["holds_no_credentials"] = True
        try:
            root = self.workspace_root()
            base["workspace"] = root
            base["workspace_writable"] = os.access(root, os.W_OK)
        except Exception as exc:
            base["workspace"] = None
            base["workspace_writable"] = False
            base["error"] = str(exc)
        return base


def _charset(content_type: str) -> str:
    """Charset for a content type. UTF-8 unless the type is clearly otherwise.

    Deliberately narrow: this decodes with ``errors="replace"``, so a wrong
    guess costs a few mangled characters, never an exception.
    """
    lowered = (content_type or "").lower()
    if "iso-8859-1" in lowered or "latin-1" in lowered:
        return "latin-1"
    return "utf-8"


__all__ = [
    "BWRAP",
    "EGRESS_INTENTS",
    "EgressDenied",
    "GLUE_INTENTS",
    "GLUE_NOTE",
    "SANDBOXED_INTENTS",
    "SHELL_AVAILABLE_NOTE",
    "SHELL_UNAVAILABLE_NOTE",
    "STATE_CHANGING_INTENTS",
    "TOOL_INTENTS",
    "WEB_FETCH_DOOR",
    "ToolsmithAgent",
    "ToolsmithInputError",
    "extract_text",
    "reject_internal_address",
    "sandbox_available",
]
