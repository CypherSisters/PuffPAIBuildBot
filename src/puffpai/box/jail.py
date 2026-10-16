"""The one audited bubblewrap builder for outside code.

This file is standard-library only on purpose: setup copies it into the locked
box as ``/opt/puffpai/jail.py`` and the launcher there (``box_run.py``)
imports it, so the jail the box builds on Windows (inside WSL) and the jail a
Linux host builds are the same code, line for line.

What a run gets (measured in the proof transcripts):

* every namespace unshared, including the network (only ``lo`` inside), and
  no way to make new user namespaces (``--disable-userns``);
* a fresh, read-only root: ``/usr``, ``/lib``, ``/bin`` read-only, ``/etc`` only
  ``passwd``, ``group``, ``hosts`` and ``ssl``; the Windows driver share and
  kernel modules hidden; no ``/mnt``, no ``/run``, no ``/srv``;
* ``/work`` = the project (read-write), ``/tmp`` fresh, ``/data/<name>`` = the
  owner's folder when one was given (read-only unless the grant says ``rw``);
* the egress socket only when the run has an approved outside list, bridged
  by ``box-entry`` to ``127.0.0.1:3128`` inside the empty network namespace;
* no capabilities, a new session (no terminal injection), and the seccomp
  filter read from file descriptor :data:`SECCOMP_FD`, which the caller must
  open (``box.bpf``) and pass to bubblewrap.

The caller adds its own prefix (``setpriv`` to the runner user with
no-new-privileges, ``prlimit``) and appends the program's argv after the
returned list, which ends with ``--`` and the entry script.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import re

#: Where the box's own trusted files live (``/opt/puffpai`` in the box).
OPT = "/opt/puffpai"
#: The file descriptor the seccomp filter is read from. The caller dup2()s the
#: opened ``box.bpf`` onto it and passes it to bubblewrap.
SECCOMP_FD = 99
ENTRY = "/opt/box-entry"
PROXY_PORT = 3128

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
#: Names the caller may not set: the jail sets them itself.
RESERVED_ENV = frozenset({
    "PATH", "HOME", "TMPDIR", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "npm_config_https_proxy", "npm_config_proxy", "NODE_USE_ENV_PROXY", "LD_PRELOAD",
    "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS",
    "BASH_ENV", "ENV", "WSL_INTEROP", "WSLENV", "WSL_DISTRO_NAME"})
MAX_ENV = 32
MAX_VALUE = 4096


def _abs(path: str, what: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path \
            or "/../" in path + "/" or "/./" in path + "/":
        raise ValueError(f"{what} must be a plain absolute path")
    return path


def _optional_ro(path: str) -> list:
    """Hide *path* with an empty tmpfs when it exists on this system."""
    return ["--tmpfs", path] if os.path.isdir(path) else []


def bwrap_argv(project_dir: str, *, folder: dict | None = None,
               egress_sock: str | None = None, env: dict | None = None) -> list[str]:
    """The bubblewrap argv for one run, ending with ``--`` and the entry script.

    *folder*: ``{"path": <already mounted, checked folder>, "mode": "ro"|"rw",
    "name": <short name>}`` or None. *egress_sock*: the run's proxy socket or
    None (no internet at all). *env*: extra variables for the program (at most
    32; names the jail sets itself are refused). Raises ValueError on anything
    that is not of that shape: nothing is passed through unchecked.
    """
    _abs(project_dir, "the project folder")
    argv = [
        "bwrap", "--unshare-all", "--unshare-user", "--disable-userns",
        "--die-with-parent", "--new-session", "--clearenv", "--hostname", "box",
        "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/bin", "/bin",
    ]
    # The Windows driver share (9p, GPU libraries) and kernel modules: hidden.
    argv += _optional_ro("/usr/lib/wsl") + _optional_ro("/lib/modules")
    argv += [
        "--ro-bind", "/etc/ssl", "/etc/ssl",
        "--ro-bind", f"{OPT}/etc/passwd", "/etc/passwd",
        "--ro-bind", f"{OPT}/etc/group", "/etc/group",
        "--ro-bind", f"{OPT}/etc/hosts", "/etc/hosts",
        "--ro-bind", f"{OPT}/box-entry", ENTRY,
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", project_dir, "/work", "--chdir", "/work",
        "--setenv", "PATH", "/work/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/work/.home", "--setenv", "TMPDIR", "/tmp",
        "--setenv", "npm_config_cache", "/work/.home/.npm",
        "--setenv", "npm_config_update_notifier", "false",
        "--setenv", "npm_config_fund", "false", "--setenv", "npm_config_audit", "false",
        "--setenv", "PIP_DISABLE_PIP_VERSION_CHECK", "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    ]
    if folder is not None:
        if not isinstance(folder, dict) or set(folder) - {"path", "mode", "name"}:
            raise ValueError("a folder is {path, mode, name}")
        mode = folder.get("mode")
        name = str(folder.get("name") or "files")
        if mode not in ("ro", "rw") or not NAME_RE.match(name):
            raise ValueError("a folder's mode is ro or rw and its name is a short word")
        path = _abs(str(folder.get("path") or ""), "the folder")
        argv += ["--ro-bind" if mode == "ro" else "--bind", path, "/data/" + name]
    if egress_sock is not None:
        _abs(egress_sock, "the outside socket")
        argv += ["--ro-bind", egress_sock, "/run/egress.sock"]
        proxy = f"http://127.0.0.1:{PROXY_PORT}"
        for key in ("HTTPS_PROXY", "https_proxy", "npm_config_https_proxy", "npm_config_proxy"):
            argv += ["--setenv", key, proxy]
        argv += ["--setenv", "NODE_USE_ENV_PROXY", "1"]
    extra = env or {}
    if not isinstance(extra, dict) or len(extra) > MAX_ENV:
        raise ValueError("env is a small mapping")
    for key, value in extra.items():
        if not isinstance(key, str) or not ENV_NAME_RE.match(key) or key in RESERVED_ENV \
                or key.startswith(("LD_", "BWRAP")):
            raise ValueError("that environment variable name is not allowed")
        if not isinstance(value, str) or len(value) > MAX_VALUE or "\x00" in value:
            raise ValueError("an environment value is a short string")
        argv += ["--setenv", key, value]
    argv += ["--remount-ro", "/", "--seccomp", str(SECCOMP_FD), "--", ENTRY]
    return argv


__all__ = ["ENTRY", "OPT", "SECCOMP_FD", "bwrap_argv"]
