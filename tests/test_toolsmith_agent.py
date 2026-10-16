"""The Toolsmith — the seventh master agent — and the gates in front of it.

Three claims are under test here, and each one is the kind that reads as true
in a docstring and turns out to be false in code:

1. **The six agents that were here before are untouched.** Same ids, same
   display names, same order, same reviewer. Adding an agent to a roster is an
   easy way to silently rename one.
2. **``web_fetch`` refuses before a socket.** Loopback, private, link-local and
   the cloud-metadata address; names that resolve to any of those; names that
   are addresses in disguise; non-https schemes; ports off the allow-list;
   redirects that leave the host originally named; bodies larger than the cap;
   content that is not text. Every one of these is asserted with a transport
   that FAILS the test if it is ever called for a request that should have been
   refused, so "refused" means "no socket", not "nice error message".
3. **The shell tool is absent where it cannot be isolated.** On Windows and on
   any box without bubblewrap it is not in the registered tool list, the health
   block says one plain sentence, and asking for it anyway refuses instead of
   running the command on the host.

Nothing here touches the network, the real state dir, or ``%LOCALAPPDATA%``.

Run:
    pytest tests/test_toolsmith_agent.py
"""
from __future__ import annotations

import os
import sys

import pytest

from puffpai.agents import registry
from puffpai.agents.envelope import Budget, Envelope
from puffpai.agents.toolsmith import (SHELL_UNAVAILABLE_NOTE, WEB_FETCH_DOOR,
                                      ToolsmithAgent, extract_text,
                                      sandbox_available)
from puffpai.security import egress_doors
from puffpai.security.secrets_provider import AgentScope

# ── the six that were already here ───────────────────────────────────────────

#: The roster as it shipped before WF-7, in order. Hard-coded on purpose: the
#: whole point is to notice if porting an agent in quietly moved one of these.
SIX = (
    ("concierge", "Concierge"),
    ("steward", "Steward"),
    ("sentinel", "Drawbridge"),
    ("archivist", "Archivist"),
    ("majordomo", "Majordomo"),
    ("caretaker", "Caretaker"),
)


def test_the_original_six_are_untouched():
    """Same ids, same display names, same order, still first on the roster."""
    front = registry.ROSTER[:len(SIX)]
    assert tuple((e.name, e.display_name) for e in front) == SIX


def test_the_reviewer_is_still_drawbridge():
    assert registry.REVIEWER_NAME == "sentinel"
    assert registry.display_name("sentinel") == "Drawbridge"


def test_the_seventh_agent_is_on_the_roster():
    entry = registry.ROSTER[-1]
    assert entry.name == "toolsmith"
    assert entry.display_name == "Toolsmith"
    assert entry.cls is ToolsmithAgent
    assert entry.scope is AgentScope.TOOLSMITH
    assert len(registry.ROSTER) == 7


def test_the_seventh_agent_is_in_the_serialised_roster():
    """``roster_summary`` is what /agents/status and the dashboard render."""
    rows = {row["name"]: row for row in registry.roster_summary()}
    assert len(rows) == 7
    assert rows["toolsmith"]["display_name"] == "Toolsmith"
    assert rows["toolsmith"]["scope"] == "toolsmith"


def test_build_team_registers_the_seventh_on_the_bus(tmp_path):
    from puffpai.security.audit_log import AuditLog
    bus = registry.build_team(audit=AuditLog(log_file=str(tmp_path / "a.jsonl")))
    assert "toolsmith" in bus.agents()
    assert bus.get("toolsmith") is not None


def test_the_toolsmith_holds_no_credentials():
    """The separation is the whole design: an empty allow-list, not a short one."""
    from puffpai.security import secrets_provider
    assert secrets_provider._SCOPE_ALLOW[AgentScope.TOOLSMITH] == set()


def test_every_roster_entry_still_carries_the_faq_copy():
    for name, role, blurb in registry.roster_blurbs():
        assert name and role and blurb.endswith("."), name


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def agent(tmp_path):
    """A Toolsmith whose workspace is a fresh temp dir and whose resolver and
    transport are both injected, so no test can resolve or connect."""
    def _never_resolve(host, port):  # pragma: no cover - overridden per test
        raise AssertionError(f"a DNS lookup happened for {host!r}")

    def _never_fetch(*args, **kwargs):
        raise AssertionError(f"a socket was opened: {args!r}")

    return ToolsmithAgent(workspace_root=str(tmp_path / "ws"),
                          resolver=_never_resolve, fetcher=_never_fetch)


@pytest.fixture
def public_agent(tmp_path):
    """Same, but every name resolves to a public address."""
    return ToolsmithAgent(workspace_root=str(tmp_path / "ws"),
                          resolver=lambda host, port: ["93.184.216.34"],
                          fetcher=None)


@pytest.fixture(autouse=True)
def open_door(monkeypatch):
    """Open the ``web_fetch`` door for the fetch tests.

    Monkeypatched rather than configured, so these tests say nothing about how
    the door resolves -- ``tests/test_dashboard_weather_egress_doors.py`` owns that
    -- and everything about what happens once it is open.
    """
    monkeypatch.setattr(egress_doors, "egress_allowed",
                        lambda door_id: (True, f"{door_id} forced open in test"))


def _envelope(intent, payload, *, egress=True, state_changing=False):
    return Envelope(intent=intent, payload=payload, requester="owner",
                    budget=Budget(max_actions=8, allow_egress=egress),
                    state_changing=state_changing)


def _fetch(agent, url, **payload):
    payload["url"] = url
    return agent.handle(_envelope("web_fetch", payload))


class _Resp:
    """A response object shaped like the streamed one ``egress_get`` returns."""

    def __init__(self, body=b"<html><body>hello</body></html>",
                 status=200, content_type="text/html"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._body = body

    def iter_content(self, chunk_size=16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        return None


# ── the door and the budget: two consents, both required ─────────────────────

def test_a_shut_door_refuses_without_a_socket(agent, monkeypatch):
    monkeypatch.setattr(
        egress_doors, "egress_allowed",
        lambda door_id: (False, f"egress door {door_id!r} is shut (posture: island)"))
    out = _fetch(agent, "https://example.com/")
    assert out["ok"] is False and out["egress_denied"] is True
    assert "Outside service switched off" in out["error"]


def test_an_unreadable_door_registry_reads_as_shut(agent, monkeypatch):
    def _boom(door_id):
        raise RuntimeError("registry on fire")
    monkeypatch.setattr(egress_doors, "egress_allowed", _boom)
    out = _fetch(agent, "https://example.com/")
    assert out["ok"] is False and out["egress_denied"] is True


def test_an_open_door_is_not_enough_without_budget_egress(agent):
    out = agent.handle(_envelope("web_fetch", {"url": "https://example.com/"},
                                 egress=False))
    assert out["ok"] is False and out["egress_denied"] is True
    assert "allow_egress" in out["error"]


def test_the_door_exists_in_the_registry_and_ships_shut():
    d = egress_doors.door(WEB_FETCH_DOOR)
    assert d is not None
    assert d.default is False
    assert d.dynamic_hosts is True
    assert d.posture_switch is None, (
        "the wizard's egress table must not be able to open a general fetch")


# ── SSRF: addresses, names, schemes, ports ───────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://127.0.0.1/",           # loopback
    "https://[::1]/",               # loopback, v6
    "https://10.0.0.5/",            # RFC 1918
    "https://192.168.1.1/",         # the household's own router
    "https://172.16.4.4/",
    "https://169.254.169.254/",     # cloud metadata
    "https://[::ffff:127.0.0.1]/",  # loopback in a costume
    "https://0.0.0.0/",             # unspecified
    "https://100.64.0.1/",          # CGNAT, not globally routable
])
def test_web_fetch_refuses_an_internal_literal(agent, url):
    """No resolver call and no socket: the fixtures fail the test if either."""
    out = _fetch(agent, url)
    assert out["ok"] is False, url
    assert "web_fetch" in out["error"]


@pytest.mark.parametrize("url", [
    "https://localhost/",
    "https://printer.local/",
    "https://nas.lan/",
    "https://svc.internal/",
    "https://box.home.arpa/",
    "https://intranet/",            # single label
    "https://127.1/",               # abbreviated address
    "https://2130706433/",          # decimal address
    "https://0x7f000001/",          # hex address
])
def test_web_fetch_refuses_a_local_or_disguised_name(agent, url):
    out = _fetch(agent, url)
    assert out["ok"] is False, url


def test_web_fetch_refuses_a_public_name_that_resolves_inside(tmp_path):
    """The check that matters: the NAME is fine and the ANSWER is not."""
    def _resolver(host, port):
        return ["93.184.216.34", "192.168.1.50"]   # one public, one not

    def _never_fetch(*a, **k):
        raise AssertionError("a socket was opened for a name resolving inside")

    agent = ToolsmithAgent(workspace_root=str(tmp_path / "ws"),
                           resolver=_resolver, fetcher=_never_fetch)
    out = _fetch(agent, "https://totally-normal.example/")
    assert out["ok"] is False
    assert "totally-normal.example" in out["error"]


def test_web_fetch_refuses_a_name_that_resolves_to_nothing(tmp_path):
    agent = ToolsmithAgent(workspace_root=str(tmp_path / "ws"),
                           resolver=lambda h, p: [],
                           fetcher=lambda *a, **k: pytest.fail("socket opened"))
    out = _fetch(agent, "https://example.com/")
    assert out["ok"] is False and "resolves to nothing" in out["error"]


@pytest.mark.parametrize("url", [
    "file:///etc/shadow",
    "file://C:/Windows/win.ini",
    "data:text/html,<b>hi</b>",
    "gopher://example.com/",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "http://example.com/",          # plaintext is refused too, on purpose
])
def test_web_fetch_refuses_a_non_https_scheme(agent, url):
    out = _fetch(agent, url)
    assert out["ok"] is False, url


@pytest.mark.parametrize("url", [
    "https://example.com:22/",
    "https://example.com:6379/",
    "https://example.com:11434/",   # a local model runtime
    "https://example.com:8000/",
    "https://example.com:80/",
])
def test_web_fetch_refuses_a_port_off_the_allow_list(public_agent, url,
                                                     monkeypatch):
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: pytest.fail("socket opened"))
    out = _fetch(public_agent, url)
    assert out["ok"] is False and "port" in out["error"], url


def test_web_fetch_refuses_credentials_in_the_url(agent):
    out = _fetch(agent, "https://user:secret@example.com/")
    assert out["ok"] is False
    assert "credentials" in out["error"]
    assert "secret" not in out["error"], "the refusal echoed the credential"


def test_web_fetch_refuses_an_oversize_url(agent):
    out = _fetch(agent, "https://example.com/" + "a" * 4000)
    assert out["ok"] is False and "exceeds" in out["error"]


def test_web_fetch_refuses_whitespace_and_control_characters(agent):
    assert _fetch(agent, "https://example.com/\n\rHost: evil")["ok"] is False


# ── the caller's host scope ──────────────────────────────────────────────────

def test_allowed_hosts_refuses_a_host_the_caller_never_approved(public_agent,
                                                                monkeypatch):
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: pytest.fail("socket opened"))
    out = _fetch(public_agent, "https://attacker.example/",
                 allowed_hosts=["example.com"])
    assert out["ok"] is False and "allowed_hosts" in out["error"]


def test_an_empty_allowed_hosts_list_permits_nothing(public_agent, monkeypatch):
    """`[]` is the most restrictive scope there is, not 'no scope'."""
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: pytest.fail("socket opened"))
    out = _fetch(public_agent, "https://example.com/", allowed_hosts=[])
    assert out["ok"] is False and "allowed_hosts" in out["error"]


def test_a_non_list_allowed_hosts_refuses_rather_than_being_ignored(public_agent):
    out = _fetch(public_agent, "https://example.com/", allowed_hosts="example.com")
    assert out["ok"] is False and "allowed_hosts" in out["error"]


def test_absent_allowed_hosts_is_unrestricted(public_agent, monkeypatch):
    monkeypatch.setattr(public_agent, "_fetcher", lambda *a, **k: _Resp())
    assert _fetch(public_agent, "https://example.com/")["ok"] is True


# ── redirects are the door's job, and the door is in the path ────────────────

def test_a_redirect_off_the_named_host_is_refused_by_the_door(public_agent,
                                                              monkeypatch):
    """The Location header is attacker-chosen; the door refuses the hop.

    Asserted here rather than only in the door's own tests because the claim
    being made is about ``web_fetch``: that its wire path is the door and not a
    private copy of one.
    """
    def _door_says_no(*args, **kwargs):
        raise egress_doors.EgressBlocked(
            WEB_FETCH_DOOR, "redirect to 'attacker.example' leaves the "
                            "requested host 'example.com'",
            "That site redirected to attacker.example, which is not where you "
            "pointed it, so nothing was fetched.")
    monkeypatch.setattr(egress_doors, "egress_get", _door_says_no)
    monkeypatch.setattr(public_agent, "_fetcher", None)
    out = _fetch(public_agent, "https://example.com/")
    assert out["ok"] is False and "attacker.example" in out["error"]


def test_the_wire_path_is_the_door(public_agent, monkeypatch):
    """``web_fetch`` must call ``egress_get`` with this door's id, streamed."""
    seen = {}

    def _spy(door_id, url, **kwargs):
        seen.update({"door": door_id, "url": url, **kwargs})
        return _Resp()

    monkeypatch.setattr(egress_doors, "egress_get", _spy)
    monkeypatch.setattr(public_agent, "_fetcher", None)
    assert _fetch(public_agent, "https://example.com/")["ok"] is True
    assert seen["door"] == WEB_FETCH_DOOR
    assert seen["stream"] is True
    assert seen["resolver"] is not None


def _toolsmith_code() -> str:
    """The module's executable source, with comments and docstrings removed.

    Scanning the raw file would match the prose that explains why these things
    are absent -- the module says ``shell=True`` and ``docker run`` in the
    course of saying it never does either. The tokenizer is what tells the
    difference between a claim and a call.
    """
    import io
    import tokenize
    from pathlib import Path
    import puffpai.agents.toolsmith as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(tok.string)
    return " ".join(kept)


def test_the_module_never_imports_requests():
    """The package-wide scan already forbids it; assert it for this file too."""
    code = _toolsmith_code()
    assert "requests" not in code
    assert "httpx" not in code
    assert "urlopen" not in code
    assert "shell = True" not in code and "shell=True" not in code


# ── response handling: size, type, untrusted ─────────────────────────────────

def test_an_oversize_body_is_truncated_not_swallowed(public_agent, monkeypatch):
    from puffpai.agents import toolsmith
    body = b"x" * (toolsmith.MAX_RESPONSE_BYTES + 50_000)
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: _Resp(body, content_type="text/plain"))
    out = _fetch(public_agent, "https://example.com/")
    assert out["ok"] is True
    assert out["bytes_read"] <= toolsmith.MAX_RESPONSE_BYTES
    assert out["truncated"] is True


def test_a_lying_content_length_cannot_stream_us_to_death(public_agent,
                                                          monkeypatch):
    """The cap is on bytes actually read, not on what the server claims."""
    from puffpai.agents import toolsmith

    class _Liar(_Resp):
        def __init__(self):
            super().__init__(b"", content_type="text/plain")
            self.headers["Content-Length"] = "10"

        def iter_content(self, chunk_size=16384):
            while True:            # an endless stream
                yield b"y" * chunk_size

    monkeypatch.setattr(public_agent, "_fetcher", lambda *a, **k: _Liar())
    out = _fetch(public_agent, "https://example.com/")
    assert out["bytes_read"] <= toolsmith.MAX_RESPONSE_BYTES


@pytest.mark.parametrize("ctype", [
    "image/png", "application/octet-stream", "application/zip",
    "application/pdf", "audio/mpeg",
])
def test_non_text_content_is_refused(public_agent, monkeypatch, ctype):
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: _Resp(b"\x00\x01", content_type=ctype))
    out = _fetch(public_agent, "https://example.com/")
    assert out["ok"] is False and "not text" in out["error"], ctype


def test_a_page_comes_back_as_text_marked_untrusted(public_agent, monkeypatch):
    page = (b"<html><head><title>Notes</title>"
            b"<script>steal()</script></head>"
            b"<body><p>Hello there.</p></body></html>")
    monkeypatch.setattr(public_agent, "_fetcher", lambda *a, **k: _Resp(page))
    out = _fetch(public_agent, "https://example.com/")
    assert out["ok"] is True
    assert out["untrusted"] is True, "web content was not labelled as data"
    assert "Hello there." in out["text"]
    assert "steal()" not in out["text"], "a script body reached the prose"
    assert "<p>" not in out["text"]


def test_an_http_error_is_a_result_not_an_exception(public_agent, monkeypatch):
    monkeypatch.setattr(public_agent, "_fetcher",
                        lambda *a, **k: _Resp(b"nope", status=503))
    out = _fetch(public_agent, "https://example.com/")
    assert out["ok"] is False and out["status"] == 503


def test_extract_text_drops_an_unclosed_script_tail():
    assert "evil" not in extract_text("<p>ok</p><script>evil(", "text/html")


# ── workspace containment ────────────────────────────────────────────────────

def _write(agent, path, content="note"):
    return agent.handle(_envelope("workspace_write",
                                  {"path": path, "content": content},
                                  state_changing=True))


def test_a_write_without_review_is_refused(agent):
    out = agent.handle(_envelope("workspace_write",
                                 {"path": "a.txt", "content": "x"},
                                 state_changing=False))
    assert out["ok"] is False and out["requires_review"] is True


def test_a_note_round_trips(agent):
    assert _write(agent, "notes/day.txt", "remember this")["ok"] is True
    out = agent.handle(_envelope("workspace_read", {"path": "notes/day.txt"}))
    assert out["ok"] is True and out["content"] == "remember this"
    assert out["untrusted"] is True, (
        "a workspace file is very often something web_fetch wrote")


@pytest.mark.parametrize("path", [
    "../escape.txt", "../../etc/passwd", "notes/../../out.txt",
    "/etc/passwd", "C:/Windows/win.ini", "\\\\server\\share\\x.txt",
    "nul", "NUL.txt", "com1", "a/b/c/d/e/f/g/h/i/j.txt",
])
def test_a_path_that_leaves_the_workspace_is_refused(agent, path):
    out = _write(agent, path)
    assert out["ok"] is False, path


def test_a_listing_cannot_be_pointed_outside(agent):
    out = agent.handle(_envelope("workspace_list", {"subdir": "../.."}))
    assert out["ok"] is False


def test_the_workspace_is_under_the_state_dir(tmp_path, monkeypatch):
    """Never package-relative: a packaged install puts the code read-only."""
    state = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    root = ToolsmithAgent().workspace_root()
    assert os.path.realpath(root).startswith(os.path.realpath(str(state)))


# ── the shell tool: present only where it can be isolated ────────────────────

def test_the_shell_tool_is_absent_without_bubblewrap(agent, monkeypatch):
    monkeypatch.setattr("puffpai.agents.toolsmith.sandbox_available",
                        lambda: False)
    assert "workspace_exec" not in agent.tools()
    assert agent.capability_notes() == [SHELL_UNAVAILABLE_NOTE]
    health = agent.health()
    assert "workspace_exec" not in health["tools"]
    assert health["shell"] == SHELL_UNAVAILABLE_NOTE


def test_asking_for_the_shell_tool_anyway_refuses_rather_than_running(
        agent, monkeypatch):
    """Losing isolation must cost the capability, never the isolation."""
    monkeypatch.setattr("puffpai.agents.toolsmith.sandbox_available",
                        lambda: False)

    def _no_subprocess(*args, **kwargs):
        raise AssertionError("a command ran on the host with no sandbox")
    monkeypatch.setattr("puffpai.agents.toolsmith.subprocess.run", _no_subprocess)

    out = agent.handle(_envelope("workspace_exec", {"command": "id"},
                                 state_changing=True))
    assert out["ok"] is False and out["sandbox_unavailable"] is True
    assert out["error"] == f"toolsmith: {SHELL_UNAVAILABLE_NOTE}"


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows claim")
def test_on_windows_the_shell_tool_is_simply_not_there(agent):
    """No probe patching: the real answer on this machine."""
    assert sandbox_available() is False
    # The Assemble step's glue tools are here on Windows too (no bubblewrap).
    assert agent.tools() == ["run_glue", "web_fetch", "workspace_list", "workspace_read",
                             "workspace_write", "write_glue"]
    assert agent.health()["shell"] == SHELL_UNAVAILABLE_NOTE


def test_sandbox_availability_is_probed_not_configured(monkeypatch):
    """A config flag cannot conjure a sandbox, and nothing here reads one."""
    monkeypatch.setenv("PUFFPAI_SANDBOX", "1")
    monkeypatch.setenv("PUFFPAI_WORKSPACE_EXEC", "true")
    monkeypatch.setattr("puffpai.agents.toolsmith.os.path.isfile",
                        lambda p: False)
    assert sandbox_available() is False


def test_the_jail_has_no_network_and_one_writable_path(tmp_path):
    argv = ToolsmithAgent._jail_argv(str(tmp_path), "echo hi", "/usr/bin/bwrap")
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv          # the network namespace goes too
    assert "--die-with-parent" in argv
    assert "--clearenv" in argv
    assert argv.count("--bind") == 1        # exactly one writable host path
    assert argv[argv.index("--bind") + 1] == str(tmp_path)
    assert "/etc" not in argv
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]


def test_the_jail_is_never_docker_backed():
    """No docker in the CODE. The comments explain at length why not."""
    code = _toolsmith_code().lower()
    for forbidden in ("docker", "podman", "containerd", "nsenter"):
        assert forbidden not in code, forbidden
    # ...and the prose that says so is still there, because deleting it is how
    # someone re-introduces the Docker backend in good faith.
    from pathlib import Path
    import puffpai.agents.toolsmith as mod
    assert "root-equivalent" in Path(mod.__file__).read_text(encoding="utf-8")


# ── unknown intents ──────────────────────────────────────────────────────────

def test_an_unknown_intent_is_refused_plainly(agent):
    out = agent.handle(_envelope("exfiltrate", {}))
    assert out["ok"] is False and "unknown intent" in out["error"]
