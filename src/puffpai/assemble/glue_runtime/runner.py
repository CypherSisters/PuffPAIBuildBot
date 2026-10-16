"""Run ONE glue function in its own process and print its rows.

Started by :func:`puffpai.assemble.toolsmith_loop.run_glue` as
``python -I -B runner.py <request file>``, inside a Job Object (Windows) or
its own process group, with a 30 second limit, a tiny environment and no
key from the vault. The request file (written by BuildBot outside the glue
folder) says which file and function to run, with what arguments, and this
run's own fetch token; it is read and deleted first.

Before the glue file is loaded this installs a Python audit hook, which
cannot be removed once added. It refuses, for anything running in this
process (glue and the packages it imports):

* starting programs (``subprocess``, ``os.system``, ``os.exec*``,
  ``os.spawn*``, ``os.startfile``, ``CreateProcess``) and ending processes;
* any ``ctypes`` use, ``pickle`` loading classes, and changing the
  registry (loading code from bytes with ``marshal`` cannot be refused: the
  import system itself uses it, so the checklist refuses it in glue);
* every connection except one: to the literal address 127.0.0.1 (or ::1)
  on the port of BuildBot's own callback (the gateway ``puff_glue`` asks).
  "Local" is never decided from a name: a name like
  ``127.x.example.com`` is refused before anything looks it up, and so is
  every other name look-up (no answer leaks out over DNS). Python's own
  ``connect`` looks a name up in C before the audit hook hears of it, so the
  runner also puts a checking socket class in place (:func:`guard_sockets`)
  before anything else is imported: a name handed to ``connect``,
  ``connect_ex``, ``sendto`` or ``sendmsg`` is refused before any look-up. Ollama and every
  other service on 127.0.0.1 are refused too, and so is any listening
  socket. Even a planned package cannot reach the internet itself: it has
  to ask BuildBot through ``puff_glue``;
* writing, renaming or deleting files outside this run's own scratch
  folder, and reading files outside Python's own folders, the app's
  folders and this run's folders.

What this is and is not: a set of refusals inside one ordinary process. It is
NOT a sandbox. Native code in a planned package can do what it likes, and so
can a planned package that goes around Python's ``socket.socket`` (to the C
``_socket`` type) on purpose; the
Job Object (everything ends at once, nothing outlives the run), the time
limit, the small environment and the absence of any vault key are what
limit that. Glue files themselves are also read first by Drawbridge
(:mod:`.check`).

Prints ``<nonce> <json>`` on success, where the JSON is ``{"rows": [...]}``.
The request is read and deleted before anything else; its token and nonce
are then held only inside closures (never a dict or a module value glue
could name), and the request itself is dropped before the glue is loaded.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
import json
import os
import sys

#: The only addresses glue may connect to, as literal strings (never names).
_CALLBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})
_BLOCKED_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.startfile", "os.fork", "os.forkpty",
    "os.kill", "os.killpg", "_winapi.CreateProcess", "_winapi.TerminateProcess",
    "_winapi.OpenProcess", "pickle.find_class", "winreg.ConnectRegistry",
    "winreg.CreateKey", "winreg.DeleteKey", "winreg.DeleteValue",
    "winreg.SetValue", "winreg.SaveKey", "winreg.LoadKey", "webbrowser.open",
    "socket.sethostname", "os.putenv", "os.unsetenv", "sys.remote_exec",
    "cpython.remote_debugger_script", "_posixsubprocess.fork_exec",
})
_BLOCKED_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn", "ctypes.", "_ctypes.")
_PATH_EVENTS = frozenset({
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod",
    "os.chown", "os.link", "os.symlink", "os.utime", "shutil.rmtree", "shutil.move",
    "shutil.copyfile", "shutil.copytree", "shutil.chown", "os.chflags",
})


class Refused(PermissionError):
    pass


def _norm(path):
    try:
        if isinstance(path, int):
            return None
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))
    except Exception:  # noqa: BLE001 - an unreadable path is outside
        return ""


def _inside(path, roots):
    if path is None:
        return True          # an open file number, already allowed
    if not path:
        return False
    for root in roots:
        if path == root or path.startswith(root.rstrip("\\/") + os.sep):
            return True
    return False


def _literal(host):
    """The host as text, or "" (bytes are read as ASCII)."""
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return ""
    return host if isinstance(host, str) else ""


def _callback_ok(address, port):
    """True only for ``("127.0.0.1" | "::1", <the callback port>, ...)``.

    No name is ever read as local: ``"localhost"``, ``"127.x.nip.io"`` and
    every other name are refused, so nothing is looked up to decide."""
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host, got = _literal(address[0]), address[1]
    if host not in _CALLBACK_ADDRESSES:
        return False
    return isinstance(got, int) and not isinstance(got, bool) and port > 0 and got == port


def guard_sockets(module, callback_port):
    """Put a checking class in place of ``module.socket`` (the ``socket``
    module): every address handed to it must be the callback, as a literal,
    BEFORE the C code sees it (C looks names up before the audit hook runs).
    Returns the new class."""
    port = int(callback_port or 0)
    base = module.socket
    talk = ("glue may only talk to BuildBot on this computer; ask BuildBot to fetch "
            "pages with puff_glue.fetch_json or fetch_text")

    def check(address):
        if not _callback_ok(address, port):
            raise Refused(talk)

    class CheckedSocket(base):
        __slots__ = ()

        def connect(self, address):
            check(address)
            return super().connect(address)  # lan-egress: BuildBot's callback on 127.0.0.1 only, checked above

        def connect_ex(self, address):
            check(address)
            return super().connect_ex(address)  # lan-egress: BuildBot's callback on 127.0.0.1 only, checked above

        def sendto(self, data, *args):
            check(args[-1] if args else None)
            return super().sendto(data, *args)

        def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
            if address is not None:
                check(address)
                return super().sendmsg(buffers, ancdata, flags, address)
            return super().sendmsg(buffers, ancdata, flags)

        def bind(self, address):
            raise Refused("glue may not listen for connections")

    CheckedSocket.__name__ = CheckedSocket.__qualname__ = "socket"
    CheckedSocket.__module__ = getattr(base, "__module__", "socket")
    module.socket = CheckedSocket
    if getattr(module, "SocketType", None) is base:
        module.SocketType = CheckedSocket
    return CheckedSocket


def make_hook(write_root, read_roots, callback_port=0):
    """The audit hook for one run (a function, so tests can call it).

    *callback_port*: the port of BuildBot's callback (the gateway) on
    127.0.0.1, the one place glue may connect to. 0 means nowhere."""
    write_roots = [_norm(write_root)]
    reads = [r for r in (_norm(p) for p in read_roots) if r] + write_roots
    port = int(callback_port or 0)
    talk = ("glue may only talk to BuildBot on this computer; ask BuildBot to fetch "
            "pages with puff_glue.fetch_json or fetch_text")

    def hook(event, args):
        if event in _BLOCKED_EVENTS or event.startswith(_BLOCKED_PREFIXES):
            raise Refused(f"glue may not do this here ({event})")
        if event in ("socket.connect", "socket.sendto"):
            address = args[1] if len(args) > 1 else None
            if not _callback_ok(address, port):
                raise Refused(talk)
            return
        if event == "socket.sendmsg":
            address = args[1] if len(args) > 1 else None
            # None: a socket that is already connected (and was checked then).
            if address is not None and not _callback_ok(address, port):
                raise Refused(talk)
            return
        if event == "socket.bind":
            raise Refused("glue may not listen for connections")
        if event in ("socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyname_ex"):
            host = args[0] if args else ""
            if _literal(host) not in _CALLBACK_ADDRESSES:
                raise Refused("glue may not look up names; ask BuildBot to fetch "
                              "pages with puff_glue")
            return
        if event in ("socket.gethostbyaddr", "socket.getnameinfo"):
            raise Refused("glue may not look up names; ask BuildBot to fetch "
                          "pages with puff_glue")
        if event == "open":
            path = args[0] if args else None
            mode = str(args[1] if len(args) > 1 and args[1] is not None else "r")
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(c in mode for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR
                                                                    | os.O_CREAT
                                                                    | os.O_APPEND))
            where = _norm(path)
            if writing and not _inside(where, write_roots):
                raise Refused("glue may not write files here")
            if not writing and not _inside(where, reads):
                raise Refused("glue may not read files here")
            return
        if event in _PATH_EVENTS:
            for value in args[:2]:
                if isinstance(value, (str, bytes, os.PathLike)) \
                        and not _inside(_norm(value), write_roots):
                    raise Refused("glue may not change files here")
    return hook


def _clean_rows(value, limit=500):
    if not isinstance(value, list):
        raise TypeError(f"the function returned {type(value).__name__}, not a list of rows")
    rows = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            raise TypeError(f"a row is {type(item).__name__}, not a dict")
        rows.append({str(k)[:80]: v for k, v in item.items()})
    return json.loads(json.dumps(rows, default=str))


def _emitter(nonce):
    """Write the result line once. The nonce lives only in this closure."""
    used = []

    def emit(rows):
        if used:
            return
        used.append(1)
        sys.stdout.write(nonce + " " + json.dumps({"rows": rows}) + "\n")
        sys.stdout.flush()
    return emit


def _gateway_port(url):
    from urllib.parse import urlsplit
    parts = urlsplit(str(url or ""))
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
        raise SystemExit("runner: the callback is not on this computer")
    return int(parts.port)


def main(argv):
    if len(argv) != 2:
        print("runner: one request file is needed", file=sys.stderr)
        return 2
    req_path = argv[1]
    with open(req_path, "r", encoding="utf-8") as fh:
        req = json.load(fh)
    try:
        os.remove(req_path)
    except OSError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    glue_dir = os.path.abspath(req["glue_dir"])
    scratch = os.path.abspath(req["scratch"])
    venv = os.path.abspath(req["venv"]) if req.get("venv") else ""
    module_name, fn_name = str(req["module"]), str(req["fn"])
    args = dict(req.get("args") or {})
    gateway = str(req["gateway"])
    port = _gateway_port(gateway)
    emit = _emitter(str(req.pop("nonce")))
    token = str(req.pop("token"))
    req.clear()
    del req
    # Only Python's own folders, the app's venv, this helper and the glue.
    keep = []
    for entry in sys.path:
        low = entry.lower()
        if "site-packages" in low and not (venv and _inside(_norm(entry), [_norm(venv)])):
            continue
        keep.append(entry)
    # The glue folder last: a glue file can never stand in for a real module.
    sys.path[:] = [here] + keep + [glue_dir]
    read_roots = [sys.prefix, sys.base_prefix, sys.exec_prefix, here, glue_dir]
    if venv:
        read_roots.append(venv)
    import socket
    guard_sockets(socket, port)      # before ssl, urllib or any package loads
    sys.addaudithook(make_hook(scratch, read_roots, port))
    os.chdir(scratch)

    import importlib
    import puff_glue
    puff_glue._configure(gateway, token)
    del token
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name)
    emit(_clean_rows(fn(**args)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
