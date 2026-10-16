"""Shared test fixtures.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import os
import subprocess
import sys
import tempfile
import types

import pytest

# ── Test isolation from the developer's real machine ───────────────────────
# Importing puffpai.api_server resolves the app API key at import time. Left
# alone that reads — and on a fresh machine WRITES — the developer's real OS
# keyring, and would drop a generated key in their real per-user state
# directory. Neither belongs in a test run, so before any test module is
# imported we:
#   1. pin PUFFPAI_STATE_DIR at a throwaway directory,
#   2. seed a test-only PUFFPAI_API_KEY so resolution never needs to mint one,
#   3. install an inert `keyring` stub whose set_password refuses, so nothing
#      under test can reach the OS credential store.
# Each is set only if the environment has not already chosen a value.
os.environ.setdefault(
    "PUFFPAI_STATE_DIR", tempfile.mkdtemp(prefix="puffpai_test_state_"))
# A bot with every first app on (see turn_on_first_apps below), from the very
# first import: the router reads the apps' words once, at import time.
os.makedirs(os.environ["PUFFPAI_STATE_DIR"], exist_ok=True)
if not os.path.exists(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "apps_on.json")):
    with open(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "apps_on.json"), "w",
              encoding="utf-8") as _fh:
        _fh.write('{"on": ["alarmflow", "calendar", "fitfam", "freshtable", "trivia"]}')
# The pairing code is per build now (<state>/pairing/pairing.json), but a
# test must never find, claim or rotate the checkout's own
# dashboard/data/pairing.json either. Tests about the per-build rules clear
# this themselves (tests/test_per_build_secrets.py).
os.environ.setdefault(
    "PUFF_PAIRING_STATE",
    os.path.join(os.environ["PUFFPAI_STATE_DIR"], "pairing", "pairing.json"))
# Not a credential for anything: 64 lowercase hex chars, shaped like a real
# key so validate_api_key (and anything asserting hex-ness) passes.
os.environ.setdefault("PUFFPAI_API_KEY", "0123456789abcdef" * 4)
# The setup wizard checks whether the internet answers (one TCP connect to the
# model registry). A test never does: the check is recorded as "not checked",
# which also means nothing a test runs is ever downloaded by default. A test
# that is about the check replaces wizard.netcheck.internet_reachable itself.
os.environ.setdefault("PUFFPAI_WIZARD_NET_PROBE", "off")
# "Looks good" now builds what it approved (the factory step): a live card is
# made from one sample look at the service. A test never makes that look; a
# test about the look switches it back on itself.
os.environ.setdefault("PUFFPAI_BUILD_PROBES", "off")
# The owner's chat goes through the tool loop
# (puffpai.brain_loop). The many older chat tests are about the shortcut chain
# and the no-tools model call, which are PARKED (kept for voice, keyless
# callers and a model that cannot call tools), so they run with the loop off.
# The loop's own tests switch it on (tests/test_brain_loop_chat.py).
os.environ.setdefault("PUFFPAI_TOOL_LOOP", "off")
# A thing's page recipe asks the brain once, in the background, for friendlier
# words (puffpai.things). A test never reaches a real model that way; the
# things tests switch it on with a fake brain.
os.environ.setdefault("PUFFPAI_THINGS_BRAIN", "off")
# The find step also asks the free libraries for books (puffpai.finding.books).
# The older find tests are about the shelves and code sites, so books
# are off; the books tests switch them on with recorded library answers.
os.environ.setdefault("PUFFPAI_FIND_BOOKS", "off")

# ── No test ever reaches the real OS password store ───────────────────────
# Tests used to leave real Credential Manager entries named
# PUFFPAI_API_KEY.state.<id> ("state" is the pinned test state folder's name).
# The old guard installed a stand-in only if nothing had imported the real
# ``keyring`` first, and did nothing for child processes a test starts. Now:
#
# 1. every process this run starts, this one included, is told to use
#    keyring's "fail" backend (PYTHON_KEYRING_BACKEND), so even the real
#    ``keyring`` package cannot reach the OS store from a test;
# 2. the Windows calls that write or delete a stored credential are replaced
#    by traps that record the attempt (the entry's name only);
# 3. every test gets a fresh stand-in ``keyring`` module (the
#    ``_keyring_guard`` fixture below): reads find nothing and writes raise.
#    A test about keyring behaviour opts in with
#    ``@pytest.mark.keyring_stub`` and gets an in-memory one that keeps what
#    is written (the ``keyring_entries`` fixture);
# 4. a test that reached the real store, or put its own keyring in place
#    without the marker, FAILS, naming itself.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"

#: What reached the real OS password store during the current test.
REAL_KEYRING_TOUCHES: list = []

_STUB_MARK = "__puffpai_test_keyring__"


def _keyring_stub(entries=None):
    """A stand-in ``keyring`` module. *entries* (a dict) makes it keep writes;
    without it, reads find nothing and writes raise."""
    stub = types.ModuleType("keyring")
    setattr(stub, _STUB_MARK, True)

    def _get_password(service, name):  # noqa: D103 - stub
        return None if entries is None else entries.get((service, name))

    def _set_password(service, name, value):  # noqa: D103 - stub
        if entries is None:
            raise RuntimeError(
                "tests must not write to the OS keyring "
                f"(service={service!r}, entry={name!r}); a test about keyring "
                "behaviour is marked @pytest.mark.keyring_stub")
        entries[(service, name)] = value

    def _delete_password(service, name):  # noqa: D103 - stub
        if entries is None:
            raise RuntimeError(
                "tests must not delete from the OS keyring; a test about "
                "keyring behaviour is marked @pytest.mark.keyring_stub")
        entries.pop((service, name), None)

    stub.get_password = _get_password           # type: ignore[attr-defined]
    stub.set_password = _set_password           # type: ignore[attr-defined]
    stub.delete_password = _delete_password     # type: ignore[attr-defined]
    stub.errors = types.SimpleNamespace(        # type: ignore[attr-defined]
        KeyringError=RuntimeError, PasswordDeleteError=RuntimeError)
    return stub


def _real_store_trap(what: str):
    def _trap(*args, **kwargs):
        target = kwargs.get("TargetName")
        if target is None and args and isinstance(args[0], dict):
            target = args[0].get("TargetName")
        REAL_KEYRING_TOUCHES.append(f"{what} ({target or 'unnamed entry'})")
        raise RuntimeError(f"tests must not {what} the real OS password store")
    return _trap


def _trap_real_password_store() -> None:
    import importlib
    for name in ("win32ctypes.pywin32.win32cred", "win32cred"):
        try:
            module = importlib.import_module(name)
        except Exception:  # not installed on this computer
            continue
        for call, what in (("CredWrite", "write to"), ("CredDelete", "delete from")):
            if hasattr(module, call):
                setattr(module, call, _real_store_trap(f"{what}, {name}.{call},"))
    real = sys.modules.get("keyring")
    if real is not None and not getattr(real, _STUB_MARK, False):
        # Something imported the real package before this file ran.
        for module in (real, sys.modules.get("keyring.core")):
            if module is None:
                continue
            module.set_password = _real_store_trap("write to")
            module.delete_password = _real_store_trap("delete from")


_trap_real_password_store()
sys.modules["keyring"] = _keyring_stub()

from puffpai import config as _puffpai_config  # noqa: E402
from puffpai import config_health  # noqa: E402

#: The real resolver, captured before anything replaces it, for the one test
#: that is allowed to ask what the per-user directory would be.
_REAL_USER_STATE_ROOT = _puffpai_config._user_state_root


# ── Every root pinned, for every test, with the real one booby-trapped ──────
# The module-level PUFFPAI_STATE_DIR above is not enough. A test that clears the
# environment on purpose (``test_install_pointer``) makes
# ``config.get_state_dir`` fall through to the developer's real per-user
# directory, and a rescan there LEFT TWO EMPTY FOLDERS in
# ``%LOCALAPPDATA%\PuffPAI`` on every run. Empty folders are the harmless
# version; the same fall-through is how a test could read, or overwrite, the
# owner's live install.
#
# So: both roots are pinned per test (so one test's writes are not another's
# fixtures), and ``config._user_state_root`` - the ONE function that names the
# real per-user directory - is replaced by one that raises. A test that reaches
# for the real machine now fails, loudly, naming itself, instead of quietly
# succeeding against the developer's data.

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "keyring_stub: the test is about keyring behaviour; it "
        "gets an in-memory stand-in that keeps what is written "
        "(keyring_entries), and may put its own stand-in in place")
    config.addinivalue_line(
        "markers", "real_pairing: the test is about the paired-device check; "
        "X-Puff-Device-Id is checked against a real pairing store")


@pytest.fixture(autouse=True)
def _paired_devices_stand_in(request, monkeypatch):
    """Most route tests send a made-up ``X-Puff-Device-Id`` and are about
    something else. For them any well-formed id counts as a paired device,
    exactly as before the check existed. A test marked ``real_pairing`` gets
    the real check against the pairing store (tests/test_paired_device_gate.py).
    Production code has no such switch."""
    from puffpai.security import paired_devices
    paired_devices.forget()
    if request.node.get_closest_marker("real_pairing") is not None:
        yield
        paired_devices.forget()
        return

    def _any(session_id):
        sid = str(session_id or "")
        if not sid:
            return None
        return {"id": "test-device1", "name": "", "label": f"test device {sid[:6]}"}

    monkeypatch.setattr(paired_devices, "lookup", _any)
    yield


@pytest.fixture(autouse=True)
def _keyring_guard(request, monkeypatch):
    """A fresh stand-in ``keyring`` for every test; the real store never.

    See the note at the top of this file. Fails the test (in teardown, so it
    cannot be swallowed by an ``except``) if anything reached the real OS
    password store, or if a test without ``@pytest.mark.keyring_stub`` put
    its own keyring in place.
    """
    marked = request.node.get_closest_marker("keyring_stub") is not None
    entries: dict = {}
    stub = _keyring_stub(entries if marked else None)
    request.node.puffpai_keyring_entries = entries if marked else None
    monkeypatch.setitem(sys.modules, "keyring", stub)
    provider = sys.modules.get("puffpai.security.secrets_provider")
    if provider is not None:
        monkeypatch.setattr(provider, "keyring", stub, raising=False)
    before = len(REAL_KEYRING_TOUCHES)
    yield
    touched = REAL_KEYRING_TOUCHES[before:]
    del REAL_KEYRING_TOUCHES[before:]
    problems = []
    if touched:
        problems.append("reached the REAL OS password store: " + "; ".join(touched))
    if not marked:
        provider = sys.modules.get("puffpai.security.secrets_provider")
        in_place = [sys.modules.get("keyring")]
        if provider is not None and getattr(provider, "_HAS_KEYRING", False):
            in_place.append(getattr(provider, "keyring", stub))
        # One of this file's stand-ins (a test that calls monkeypatch.undo()
        # puts the first one back): fine. Anything else is the test's own.
        if any(ring is not stub and not getattr(ring, _STUB_MARK, False)
               for ring in in_place):
            problems.append("put its own keyring in place without "
                            "@pytest.mark.keyring_stub")
    if problems:
        pytest.fail(f"{request.node.nodeid} " + " and ".join(problems),
                    pytrace=False)


@pytest.fixture
def keyring_entries(request):
    """What a ``@pytest.mark.keyring_stub`` test's stand-in keyring holds:
    ``{(service, entry): value}``."""
    entries = getattr(request.node, "puffpai_keyring_entries", None)
    if entries is None:
        pytest.fail("keyring_entries needs @pytest.mark.keyring_stub", pytrace=False)
    return entries


#: The five shipped apps are first apps: off on a new bot, turned on per bot
#: (puffpai.first_apps). The older tests are about those apps working, so the
#: pinned test state is a bot that has all five on; the first-apps tests start
#: from a state with no on-set (tests/test_first_apps.py).
FIRST_APPS = ("alarmflow", "calendar", "fitfam", "freshtable", "trivia")


def turn_on_first_apps(state_dir: str) -> None:
    """Make *state_dir* a bot that has every first app turned on."""
    import json
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "apps_on.json"), "w", encoding="utf-8") as fh:
        json.dump({"on": list(FIRST_APPS)}, fh)


@pytest.fixture(autouse=True)
def _router_app_words_kept():
    """Turning a first app on or off reloads the router's app words
    (router.reload_app_words). Each test gets back the words it started with,
    so one test's bot does not route the next test's sentences."""
    router = sys.modules.get("puffpai.router")
    saved = ({n: getattr(router, n) for n in ("_PACKS", "_APP_KEYWORDS", "_ALL_AGENT_KEYWORDS")}
             if router is not None else None)
    yield
    if router is not None and saved is not None:
        for name, value in saved.items():
            setattr(router, name, value)


@pytest.fixture(autouse=True)
def _pin_state_roots(tmp_path_factory, monkeypatch, _no_real_ollama):
    """Pin the state, data and config roots at tmp; forbid the real one.

    The pinned roots live in their OWN temp directory rather than in the test's
    ``tmp_path``, because a dozen tests assert that ``tmp_path`` is empty (a
    refused download must leave nothing behind) and a fixture's bookkeeping is
    not the writer's leftovers.
    """
    from puffpai import bot_identity, config

    root = tmp_path_factory.mktemp("pinned_roots")
    state = root / "state"
    data = root / "data"
    state.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    turn_on_first_apps(str(state))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(data))

    def _forbidden() -> str:
        raise AssertionError(
            "a test resolved the REAL per-user state directory "
            "(config._user_state_root). Pin PUFFPAI_STATE_DIR, or pass an "
            "explicit path - nothing in the suite may touch the developer's "
            "own install.")

    monkeypatch.setattr(config, "_user_state_root", _forbidden)
    config.get_config(_force_reload=True)
    bot_identity.invalidate()
    # The installed-apps list is cached per process. Read under the last
    # test's roots, it would show that test's installed apps to this one
    # (test_self_knowledge failed after test_drawbridge_checks installed a
    # card): drop it whenever the roots move.
    _forget_installed_apps()
    try:
        yield
    finally:
        bot_identity.invalidate()
        _forget_installed_apps()
        config._instance = None


def _forget_installed_apps() -> None:
    from puffpai import app_registry
    with app_registry._cache_lock:
        app_registry._cached_manifests = None
    # The same for what the bot may reach: the run setting and door switches
    # are cached per process too. Read under the last test's build record,
    # an "only the services you said yes to" setting leaked into a test whose
    # build has none (test_routines_cli's home-town test failed after
    # test_factory_wiring): drop them whenever the roots move.
    from puffpai.security import egress_doors, outside_switches
    egress_doors.reset_cache()
    outside_switches.forget()


@pytest.fixture
def allow_real_user_state_root(monkeypatch):
    """Undo the booby trap, for the one test whose subject IS that resolver.

    ``test_the_default_state_dir_is_outside_any_checkout`` exists to prove the
    default lands outside the checkout, which it cannot do without asking for the
    default. It only READS the path. Any other test asking for this fixture
    should be asked why.
    """
    from puffpai import config
    monkeypatch.setattr(config, "_user_state_root", _REAL_USER_STATE_ROOT)
    yield


@pytest.fixture(autouse=True)
def _reset_cli_stdin_latch():
    """Start every test with the wizard's end-of-file latch cleared.

    ``cli._read_line`` remembers that stdin ran out so a redirected run stops
    prompting (see its docstring). That latch belongs to one wizard run; in a
    test process, one test reaching end of input must not make every later
    test's prompt come back empty.
    """
    from puffpai import cli
    cli._reset_stdin_state()
    yield
    cli._reset_stdin_state()


@pytest.fixture(autouse=True)
def _fresh_request_budget():
    """Start every test with the web server's per-address request count
    cleared. Every test client is 127.0.0.1, so on a fast computer the whole
    run shares one 600-a-minute budget and a later test gets "Rate limit
    exceeded" for requests it never made (Linux CI, Python 3.11). The limit
    itself is unchanged; a test about it makes its own requests."""
    api = sys.modules.get("puffpai.api_server")
    if api is not None and isinstance(getattr(api, "_rate_limit_store", None), dict):
        api._rate_limit_store.clear()
    yield


@pytest.fixture(autouse=True)
def _no_real_downloads(monkeypatch):
    """No test downloads a book or re-reads an outside list for real.

    The download and list-refresh code reach the network through one seam
    each; a test that is about a download replaces that seam with its own
    stand-in (its monkeypatch runs after this one and wins).
    """
    from puffpai import source_fetch
    from puffpai.catalogs import live

    def _refuse(*_a, **_k):
        raise OSError("tests never go online")

    monkeypatch.setattr(source_fetch, "_default_transport", _refuse)
    monkeypatch.setattr(live, "_default_transport", _refuse)
    yield


@pytest.fixture(autouse=True)
def first_fetches(monkeypatch):
    """Switching a live card on starts no background fetch in a test.

    ``feeds.start_first_fetch`` normally starts a thread; a thread that outlives
    its test would write into the next test's state folder. Here it only
    records the app id. A test about the first fetch reads this list and runs
    ``feeds._run_first_fetch`` itself.
    """
    from puffpai import feeds
    started: list[str] = []
    monkeypatch.setattr(feeds, "_first_fetch_starter",
                        lambda app_id: started.append(app_id) or True)
    yield started


@pytest.fixture(autouse=True)
def _clean_config_health():
    """Start every test with an empty config-health record.

    The record is process-wide by design (a loader deep in the call graph has to
    be able to report without threading a context object through every caller),
    so tests that deliberately feed a broken config would otherwise leak a
    degradation into every test that ran after them.
    """
    config_health.clear()
    yield
    config_health.clear()


#: Ports a local Ollama listens on (the default, and a second one a developer
#: may run beside it).
_OLLAMA_PORTS = (11434, 11435)


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Every test sees the default Ollama address and never reaches a real one.

    A developer who points PUFFPAI_OLLAMA_URL (or OLLAMA_HOST) at their own
    Ollama was changing what a dozen address tests saw; a test that needs an
    address sets its own. And no test may talk to a real Ollama: a connect
    to its ports is refused, so a probe reads as "nothing answered".
    """
    import socket

    monkeypatch.delenv("PUFFPAI_OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    real_connect = socket.socket.connect

    real_connect_ex = socket.socket.connect_ex
    real_execute = subprocess.Popen._execute_child

    def _connect(sock, address):
        if (isinstance(address, tuple) and len(address) >= 2
                and address[1] in _OLLAMA_PORTS):
            raise ConnectionRefusedError("tests never talk to a real Ollama")
        _outside_exit_guard(address)
        return real_connect(sock, address)

    def _connect_ex(sock, address):
        _outside_exit_guard(address)
        return real_connect_ex(sock, address)

    def _execute_child(self, args, *rest, **kw):
        words = [args] if isinstance(args, (str, bytes)) else list(args or [])
        if any(_NET_PROGRAM_RE.search(str(w)) for w in words[:3]):
            _outside_exit_guard(("a program: " + " ".join(map(str, words[:2])), 0))
        return real_execute(self, args, *rest, **kw)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(subprocess.Popen, "_execute_child", _execute_child)
    yield


# ── The single-exit rule, at run time ──────────────────────────────────────
# tests/test_outside_single_exit.py reads the source; this watches what the
# suite actually does. Any connection to somewhere other than this computer,
# or any fetching program started, that comes from BuildBot's own code must be
# opened by one of the listed exits (which log and pin it) or by a line that
# says it is a home-network call (``# lan-egress: <why>``). Anything else
# raises OutsideExitViolation - a BaseException, so an ``except Exception``
# that swallows network errors cannot hide it - and is kept for the summary.

_SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "src", "puffpai")
_EXIT_FILES = tuple(os.path.normcase(os.path.join(_SRC_ROOT, *rel.split("/")))
                    for rel in ("security/egress_doors.py",
                                "security/pinned_net.py", "source_fetch.py",
                                "wizard/netcheck.py"))
_NET_PROGRAM_RE = __import__("re").compile(
    r"(?i)(?:^|[\\/\s])(?:curl|wget|invoke-webrequest|invoke-restmethod|bitsadmin"
    r"|ncat|telnet|sftp|scp|ssh|rsync)(?:\.exe)?(?:$|\s)")
OUTSIDE_EXIT_VIOLATIONS: list = []


class OutsideExitViolation(BaseException):
    """BuildBot's code reached outside this computer past the listed exits."""


def _is_this_computer(address) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    if not isinstance(host, str):
        return True  # a Unix socket path or similar: not the network
    host = host.lower().strip("[]")
    return (host in ("localhost", "::1", "0.0.0.0", "")
            or host.startswith("127.") or host.startswith("::ffff:127."))


def _outside_exit_guard(address) -> None:
    import linecache
    if _is_this_computer(address) and not str(address[0]).startswith("a program"):
        return
    frame = sys._getframe(2)
    site = None
    src = os.path.normcase(_SRC_ROOT)
    while frame is not None:
        name = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if name in _EXIT_FILES:
            return
        if site is None and name.startswith(src):
            site = frame
        frame = frame.f_back
    if site is None:
        return  # the test's own code, or a library a test drives directly
    number = site.f_lineno
    near = [linecache.getline(site.f_code.co_filename, n)
            for n in range(max(1, number - 3), number + 1)]
    if any("# lan-egress:" in line for line in near):
        # The marker says "home network": hold it to that. A marked call to
        # an internet address is a violation too (security.lan_guard refuses
        # it in the running app).
        from puffpai.security import lan_guard
        if lan_guard.is_home(address):
            return
        where = f"{os.path.relpath(site.f_code.co_filename, _SRC_ROOT)}:{number}"
        OUTSIDE_EXIT_VIOLATIONS.append(f"{where} -> {address!r} (marked LAN)")
        raise OutsideExitViolation(
            f"{where} is marked '# lan-egress:' but reached {address!r}, "
            f"which is not this computer or a home-network address")
    where = (f"{os.path.relpath(site.f_code.co_filename, _SRC_ROOT)}:{number}")
    OUTSIDE_EXIT_VIOLATIONS.append(f"{where} -> {address!r}")
    raise OutsideExitViolation(
        f"{where} reached {address!r} without a listed exit (egress_doors, "
        f"source_fetch, netcheck) or a '# lan-egress: <why>' marker")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if OUTSIDE_EXIT_VIOLATIONS:
        terminalreporter.section("outside traffic past the listed exits")
        for row in dict.fromkeys(OUTSIDE_EXIT_VIOLATIONS):
            terminalreporter.write_line(row)


def pytest_sessionfinish(session, exitstatus):
    if OUTSIDE_EXIT_VIOLATIONS and session.exitstatus == 0:
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _no_real_installs(request, monkeypatch):
    """Tests never install a program on this computer, never start Docker
    Desktop and never wait for it (Docker and app runtimes are
    installed after the one Yes). A test of the installer replaces these
    seams itself."""
    from puffpai import deps
    monkeypatch.setattr(deps, "_run_installer",
                        lambda argv, extra_env=None: {"code": 1, "output": "tests never install"})
    monkeypatch.setattr(deps, "_start_docker", lambda: None)
    monkeypatch.setattr(deps, "_sleep", lambda s: None)
    monkeypatch.setattr(deps, "DOCKER_WAIT_S", 0)
    deps._FAILED.clear()
    yield
    deps._FAILED.clear()
