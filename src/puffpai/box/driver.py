"""The locked box driver: the one place BuildBot talks to ``wsl.exe``.

A listed network exit for outside code: every visit the box's proxy makes is
copied into the "What leaves your computer" log by :func:`ingest_outside`,
which re-checks each line through ``security.egress_ledger.record``.

What is here: the box's name, who has claimed it, its status in every
state, removing it through the unregister guard; setting
it up (lock first, then provision), running one request in it, putting a
project in and copying files out, and the things waiting for Windows' Linux
feature. The trusted launcher that runs inside the box is
``assets/box_run.py``; the jail it builds is :mod:`puffpai.box.jail`.

Rules pinned by the design (2.9):

* One box per install, safely shared. Box root ``%LOCALAPPDATA%\\PuffPAI\\box``
  (``PUFFPAI_BOX_ROOT`` overrides). Each distro lives in ``<root>\\<distro>``;
  claims in ``<root>\\claims\\<distro>\\<install id>.json``; an owner marker
  ``/etc/puffpai-box.json`` inside the distro.
* Unregister guard: ``wsl --terminate`` / ``--unregister`` only for a name
  matching ``^PuffPAI-sandbox(-[A-Za-z0-9-]+)?$``, whose Lxss ``BasePath``
  (HKCU) is this install's ``<root>\\<distro>``, whose marker names it, and
  never ``Ubuntu``, ``jetson-flash`` or ``docker-desktop``. ``box.json`` is a
  hint, never the authority. ``wsl --shutdown`` is never run: :func:`_wsl`
  refuses it outright.
* ``wsl.exe`` only by absolute path (``security.host_programs`` when present,
  else ``%SystemRoot%\\System32\\wsl.exe``, which must exist), working
  directory System32, never ``shutil.which``, never a bare name.
* Networking: refuse to run (``not_nat``) unless ``%USERPROFILE%\\.wslconfig``
  is absent or leaves ``networkingMode`` absent or ``nat``, and does not set
  ``hostAddressLoopback=true``. Re-read on every status.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess

from puffpai import procflags
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from puffpai.box import wording as W

DEFAULT_NAME = "PuffPAI-sandbox"
NAME_RE = re.compile(r"^PuffPAI-sandbox(-[A-Za-z0-9-]+)?\Z")
#: Her own distros. The name rule already keeps them out; this is the belt
#: to that brace, compared case-insensitively.
NEVER_TOUCH = frozenset({"ubuntu", "jetson-flash", "docker-desktop"})
MARKER_PATH = "/etc/puffpai-box.json"
LAUNCHER = "/opt/puffpai/box-run"
LXSS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
STATES = ("ready", "no_wsl", "wsl_off", "no_box", "box_v1", "box_broken",
          "not_nat", "orphan", "taken", "unsupported")
SELFTEST_SCHEMA = "puffpai_box_selftest/v1"
_GUID_RE = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}\Z")
_REMOVE_FLAGS = frozenset({"--terminate", "-t", "--unregister"})
_BOX_SERVICE_RE = re.compile(r"^box_[0-9a-f]{24}\Z")
_BOX_WHAT = frozenset({"box_install", "box_traffic"})


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


@dataclass
class BoxStatus:
    state: str                 # one of STATES
    message: str               # plain words for the owner
    offer: str = ""            # install_wsl | set_up_box | share_box | repair_box | remove_orphan | ""
    distro: str = ""
    orphans: list = field(default_factory=list)   # [{"distro","bytes"}]

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_dict(self) -> dict:
        return {"state": self.state, "message": self.message, "offer": self.offer,
                "distro": self.distro,
                "orphans": [dict(o) for o in self.orphans]}


class GuardRefused(RuntimeError):
    """A ``wsl --terminate/--unregister`` the guard did not allow."""


# ── where things are ─────────────────────────────────────────────────────────

def box_root() -> str:
    """The folder every box of this user lives under. Not created here."""
    env = os.environ.get("PUFFPAI_BOX_ROOT", "").strip()
    if env:
        return os.path.abspath(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PuffPAI", "box")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "puffpai", "box")


def install_root() -> str:
    """This install's folder: the installer's ``PUFFPAI_CHECKOUT``, else the
    checkout this code runs from."""
    env = os.environ.get("PUFFPAI_CHECKOUT", "").strip()
    if env:
        return os.path.abspath(env)
    from puffpai.config import get_config
    return os.path.abspath(get_config().REPO_ROOT)


def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def install_id() -> str:
    """This install's claim id: its install folder and its state folder."""
    text = _canon(install_root()) + "\n" + _canon(_state_dir())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _suffixed_name() -> str:
    digest = hashlib.sha256(_canon(install_root()).encode("utf-8")).hexdigest()
    return f"{DEFAULT_NAME}-{digest[:8]}"


def _configured_name() -> str:
    env = os.environ.get("PUFFPAI_BOX_DISTRO", "").strip()
    if env:
        return env
    try:
        from puffpai.config import get_config
        box = getattr(get_config(), "box", None)
    except Exception:
        box = None
    if isinstance(box, dict):
        value = box.get("distro")
    else:
        value = getattr(box, "distro", None) if box is not None else None
    return str(value).strip() if value else ""


def distro_name() -> str:
    """The box distro this install uses.

    ``PUFFPAI_BOX_DISTRO`` or config ``box.distro`` when set; else
    ``PuffPAI-sandbox`` when it does not exist, this install claimed it, or
    it is a real shared box (one box per computer, adopted by
    adding a claim after the owner's yes); else
    ``PuffPAI-sandbox-<first 8 hex of sha256(install root)>`` (the "taken"
    and "orphan" cases).
    """
    named = _configured_name()
    if named:
        return named
    if os.name != "nt":
        return DEFAULT_NAME
    if _has_my_claim(DEFAULT_NAME):
        return DEFAULT_NAME
    suffixed = _suffixed_name()
    if _has_my_claim(suffixed):
        return suffixed         # this install's own box, even if the default name is free now
    exists = any(e["name"].casefold() == DEFAULT_NAME.casefold() for e in _lxss())
    if not exists or _is_shareable(DEFAULT_NAME):
        return DEFAULT_NAME
    return suffixed


def _is_shareable(distro: str) -> bool:
    """True only for a box another live BuildBot of this user set up: exactly
    one registry entry, its disk exactly in ``<box root>\\<distro>``, another
    live claim, and the owner marker inside naming it. Anything less is
    "taken" or "orphan" and this install keeps to its own suffixed name.
    Never writes a claim (sharing needs the owner's yes, see ``_do_setup``).
    """
    entries = _entries_named(distro)
    if len(entries) != 1 or not base_path_is_ours(distro, entries[0]["base_path"]):
        return False
    if not other_live_claims(distro):
        return False
    return _marker_names(distro)


# ── host programs ────────────────────────────────────────────────────────────

def wsl_exe() -> Optional[str]:
    """Absolute path of ``wsl.exe``, or None when this computer has none."""
    hp = _optional("puffpai.security.host_programs")
    if hp is not None:
        return hp.path("wsl")
    root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
    if not root:
        return None
    path = os.path.join(root, "System32", "wsl.exe")
    return path if os.path.isfile(path) else None


def _run_kwargs() -> dict:
    hp = _optional("puffpai.security.host_programs")
    if hp is not None:
        return dict(hp.run_kwargs())
    root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or ""
    return {"cwd": os.path.join(root, "System32")} if root else {}


def _spawn(argv: list, *, timeout: float, input_bytes: Optional[bytes] = None,
           **kwargs: Any) -> subprocess.CompletedProcess:
    """Run one program. The single subprocess call in this module (tests fake it)."""
    if input_bytes is None and "stdin" not in kwargs:
        # wsl.exe reads whatever it inherits: never the owner's typed answers.
        kwargs["stdin"] = subprocess.DEVNULL
    return subprocess.run(argv, input=input_bytes, capture_output=True,
                          timeout=timeout, **procflags.hidden(), **kwargs)


def _wsl(args: list, *, timeout: float = 30, input_bytes: Optional[bytes] = None,
         _guarded: bool = False) -> subprocess.CompletedProcess:
    """``wsl.exe <args>``. Refuses ``--shutdown`` always, and ``--terminate`` /
    ``--unregister`` unless the unregister guard sent it."""
    words = [str(a) for a in args]
    low = [w.casefold() for w in words]
    if any(w in ("--shutdown", "-shutdown") for w in low):
        raise GuardRefused("wsl --shutdown is never run")
    if any(w in _REMOVE_FLAGS for w in low):
        if not _guarded:
            raise GuardRefused("terminate and unregister go through the guard")
        if (len(words) != 2 or low[0] not in _REMOVE_FLAGS
                or not NAME_RE.match(words[1])
                or words[1].casefold() in NEVER_TOUCH):
            raise GuardRefused("that distro is not one BuildBot may stop or remove")
    exe = wsl_exe()
    if not exe:
        raise FileNotFoundError("wsl.exe")
    return _spawn([exe, *words], timeout=timeout, input_bytes=input_bytes,
                  **_run_kwargs())


def _text(raw: Optional[bytes]) -> str:
    """wsl.exe's own messages are UTF-16LE; programs run inside print UTF-8."""
    raw = raw or b""
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le", "replace").replace("\x00", "")
    if len(raw) > 1 and raw[1:2] == b"\x00":
        return raw.decode("utf-16-le", "replace").replace("\x00", "")
    return raw.decode("utf-8", "replace").lstrip("﻿")


# ── the Lxss registry (read only) ────────────────────────────────────────────

def _lxss() -> list:
    """Every registered distro: ``[{"guid","name","base_path","version"}]``.

    Read from ``HKCU\\...\\Lxss``: the authority for which distros exist and
    where their disks live. Never written.
    """
    if os.name != "nt":
        return []
    import winreg
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LXSS_KEY)
    except OSError:
        return []
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not _GUID_RE.match(sub):
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    name = winreg.QueryValueEx(key, "DistributionName")[0]
                    base = winreg.QueryValueEx(key, "BasePath")[0]
                    try:
                        version = winreg.QueryValueEx(key, "Version")[0]
                    except OSError:
                        version = None
            except OSError:
                continue
            out.append({"guid": sub, "name": str(name), "base_path": str(base),
                        "version": version})
    return out


def _entries_named(name: str) -> list:
    return [e for e in _lxss() if e["name"].casefold() == name.casefold()]


def _norm_base(path: str) -> Optional[str]:
    """A registry BasePath as a comparable path, or None when it is not a
    plain local folder."""
    p = str(path or "")
    if p.startswith("\\\\?\\"):
        p = p[4:]
        if p.upper().startswith("UNC\\"):
            return None
    if not p or p.startswith("\\\\"):
        return None
    return _canon(p)


def _home_of(distro: str) -> str:
    return os.path.join(box_root(), distro)


def base_path_is_ours(distro: str, base_path: str) -> bool:
    """True only when the disk lives exactly in ``<box root>\\<distro>``."""
    got = _norm_base(base_path)
    return got is not None and got == _canon(_home_of(distro))


# ── claims ───────────────────────────────────────────────────────────────────

def _claims_dir(distro: str) -> str:
    if not NAME_RE.match(distro or ""):
        raise ValueError("not a box name")
    return os.path.join(box_root(), "claims", distro)


def claims(distro: str) -> list:
    """Every claim on *distro*: ``[{"id","install_root","state_dir","created","live"}]``.

    An unreadable claim counts as LIVE: when in doubt, the box stays.
    """
    try:
        folder = _claims_dir(distro)
        names = sorted(os.listdir(folder))
    except (OSError, ValueError):
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            root = str(data.get("install_root") or "")
            live = bool(root) and os.path.isdir(root)
        except (OSError, ValueError):
            data, root, live = {}, "", True
        out.append({"id": name[:-5], "install_root": root,
                    "state_dir": str(data.get("state_dir") or ""),
                    "created": str(data.get("created") or ""), "live": live})
    return out


def _has_my_claim(distro: str) -> bool:
    me = install_id()
    return any(c["id"] == me for c in claims(distro))


def add_claim(distro: str) -> str:
    """Write this install's claim on *distro*; returns its path."""
    folder = _claims_dir(distro)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, install_id() + ".json")
    body = {"install_root": os.path.abspath(install_root()),
            "state_dir": os.path.abspath(_state_dir()),
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
    os.replace(tmp, path)
    return path


def drop_claim(distro: str) -> bool:
    try:
        os.unlink(os.path.join(_claims_dir(distro), install_id() + ".json"))
        return True
    except (OSError, ValueError):
        return False


def other_live_claims(distro: str) -> list:
    me = install_id()
    return [c for c in claims(distro) if c["id"] != me and c["live"]]


def known_install_roots() -> list:
    """Install folders of every live BuildBot this computer's box claims know."""
    out = []
    try:
        names = os.listdir(os.path.join(box_root(), "claims"))
    except OSError:
        return out
    for name in names:
        if not NAME_RE.match(name):
            continue
        for c in claims(name):
            if c["live"] and c["install_root"] and c["install_root"] not in out:
                out.append(c["install_root"])
    return out


# ── inside the distro (only ever for a distro whose disk is ours) ───────────

def _read_marker(distro: str) -> Optional[dict]:
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "cat", MARKER_PATH],
                 timeout=60)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(_text(r.stdout).strip() or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _marker_names(distro: str) -> bool:
    marker = _read_marker(distro)
    return bool(marker) and marker.get("distro") == distro


def _launcher_present(distro: str) -> bool:
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "test", "-x", LAUNCHER],
                 timeout=60)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    return r.returncode == 0


#: BuildBot's own trusted programs inside the box: (path in the box, the
#: packaged file it is a copy of, its mode). A box set up by an older
#: BuildBot keeps older copies until they are put back (without this, a box
#: can run a launcher that never reads the per-install deny lists, while the
#: code says it does).
LAUNCHER_FILES = (("/opt/puffpai/box-run", "box_run.py", "755"),
                  ("/opt/puffpai/jail.py", "jail.py", "644"),
                  ("/opt/puffpai/egress_proxy.py", "egress_proxy.py", "644"),
                  ("/opt/puffpai/box-entry", "box-entry", "755"))
_SHA_LINE_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(/opt/puffpai/[A-Za-z0-9._-]+)\s*\Z")


def _launcher_bytes(name: str) -> bytes:
    return _jail_bytes() if name == "jail.py" else _asset_bytes(name)


def launcher_shas() -> dict:
    """``{path in the box: sha256}`` of the programs this BuildBot ships."""
    return {path: hashlib.sha256(_launcher_bytes(name)).hexdigest()
            for path, name, _mode in LAUNCHER_FILES}


def launcher_fingerprint() -> str:
    text = "\n".join(f"{p} {s}" for p, s in sorted(launcher_shas().items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def installed_launcher(distro: str) -> Optional[dict]:
    """``{path: sha256}`` of the copies in the box, or None when one is
    missing or the box did not answer."""
    paths = [p for p, _n, _m in LAUNCHER_FILES]
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "sha256sum", *paths], timeout=60)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return None
    if r.returncode != 0:
        return None
    out = {}
    for line in _text(r.stdout).splitlines():
        m = _SHA_LINE_RE.match(line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out if set(out) == set(paths) else None


def refresh_launcher(distro: str) -> bool:
    """Put this BuildBot's own copies of its trusted programs into its box
    (only a box with this install's claim, its disk in our root and the
    owner marker inside). Each file is written next to its place and moved
    over it, so a run starting meanwhile gets the old or the new file, never
    half of one. True when the box then holds exactly these copies."""
    if not _box_is_mine(distro) or not _marker_names(distro):
        return False
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for _path, name, _mode in LAUNCHER_FILES:
            data = _launcher_bytes(name)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            tf.addfile(info, io.BytesIO(data))
    steps = ["set -e", "umask 022", "d=$(mktemp -d /tmp/puffpai-refresh.XXXXXX)",
             'tar -x -C "$d" -f -']
    for path, name, mode in LAUNCHER_FILES:
        steps.append(f'install -m {mode} -o root -g root "$d/{name}" {path}.new')
        steps.append(f"mv -f {path}.new {path}")
    steps.append('rm -rf "$d"')
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c", " && ".join(steps)],
                 input_bytes=buf.getvalue(), timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    return r.returncode == 0 and installed_launcher(distro) == launcher_shas()


def _launcher_current(distro: str) -> bool:
    """The box runs exactly this BuildBot's trusted programs: checked, and
    put back (then self-tested) when they differ. False fails closed."""
    if installed_launcher(distro) == launcher_shas():
        return True
    return refresh_launcher(distro) and self_test(distro)


def self_test(distro: str) -> bool:
    """The launcher's own check after Windows' Linux feature changed: a
    Windows program is refused, ``/mnt`` is absent and only ``lo`` exists
    inside a run. Every answer must be exactly true."""
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", LAUNCHER, "--self-test"],
                 timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    if r.returncode != 0:
        return False
    try:
        lines = _text(r.stdout).strip().splitlines()
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        return False
    if not isinstance(data, dict) or data.get("schema") != SELFTEST_SCHEMA:
        return False
    return all(data.get(k) is True
               for k in ("interop_refused", "mnt_absent", "only_lo"))


# ── .wslconfig ───────────────────────────────────────────────────────────────

def _wslconfig_path() -> str:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".wslconfig")


def wslconfig_problem(path: Optional[str] = None) -> Optional[str]:
    """None when the box may run; else "mirrored"/"loopback"/"unreadable".

    ``networkingMode`` must be absent or ``nat`` and ``hostAddressLoopback``
    must not be ``true``, in ``[wsl2]`` and in the older ``[experimental]``
    section alike (either one saying otherwise is enough to refuse).
    """
    path = path or _wslconfig_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read(1_000_000)
    except OSError:
        return "unreadable"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 1 and raw[1:2] == b"\x00"):
        text = raw.decode("utf-16", "replace")
    else:
        text = raw.decode("utf-8", "replace").lstrip("﻿")
    section = ""
    values: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if "=" not in s or section not in ("wsl2", "experimental"):
            continue
        key, _, value = s.partition("=")
        value = re.split(r"\s[#;]", " " + value.strip(), maxsplit=1)[0]
        value = value.strip().strip('"').strip("'").strip().lower()
        values[(section, key.strip().lower())] = value
    for sec in ("wsl2", "experimental"):
        mode = values.get((sec, "networkingmode"), "")
        if mode not in ("", "nat"):
            return "mirrored"
        if values.get((sec, "hostaddressloopback"), "") == "true":
            return "loopback"
    return None


# ── box.json (a hint) and the wsl --version check ───────────────────────────

def _box_json_path() -> str:
    return os.path.join(_state_dir(), "box", "box.json")


def _read_box_json() -> dict:
    try:
        with open(_box_json_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_box_json(data: dict) -> None:
    path = _box_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def wsl_version_sha() -> str:
    """sha256 of ``wsl --version``; "" when it cannot be read (an old WSL):
    then the self-test runs before every job."""
    try:
        r = _wsl(["--version"], timeout=30)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return ""
    if r.returncode != 0:
        return ""
    text = _text(r.stdout).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


# ── listing ──────────────────────────────────────────────────────────────────

_NO_WSL_MARKS = ("0x8007019e", "0x80070490", "not installed", "--install")
_VIRT_MARKS = ("0x80370102", "virtualization", "virtual machine platform",
               "hyper-v", "bios")


def _list_distros() -> tuple:
    """``({name: version}, None)`` or ``(None, BoxStatus)`` when WSL is
    missing or does not answer."""
    try:
        r = _wsl(["-l", "-v"], timeout=30)
    except FileNotFoundError:
        return None, BoxStatus("no_wsl", W.NO_WSL, "install_wsl")
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return None, BoxStatus("wsl_off", W.WSL_OFF, "")
    out = _text(r.stdout) + "\n" + _text(r.stderr)
    low = out.lower()
    if r.returncode != 0:
        if "wsl_e_default_distro_not_found" in low or "no installed distributions" in low:
            return {}, None
        if any(m in low for m in _VIRT_MARKS):
            return None, BoxStatus("wsl_off", W.WSL_NO_VIRTUALIZATION, "")
        if any(m in low for m in _NO_WSL_MARKS):
            return None, BoxStatus("no_wsl", W.NO_WSL, "install_wsl")
        return None, BoxStatus("wsl_off", W.WSL_OFF, "")
    rows: dict = {}
    lines = [ln for ln in _text(r.stdout).splitlines() if ln.strip()]
    for line in lines[1:]:                       # the first line is the header
        parts = line.replace("*", " ", 1).split() if line.lstrip().startswith("*") \
            else line.split()
        if len(parts) >= 3:
            rows[parts[0]] = parts[-1]
    return rows, None


# ── orphans ──────────────────────────────────────────────────────────────────

def _dir_bytes(path: str) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += st.st_size
    return total


def _is_orphan(distro: str) -> bool:
    cl = claims(distro)
    return not any(c["live"] for c in cl)


def find_orphans(listing: Optional[dict] = None) -> list:
    """Boxes left over from a BuildBot that was deleted: ours by disk place
    and marker, with no live claim. ``[{"distro","bytes"}]``."""
    out = []
    names = set(listing) if listing is not None else None
    for entry in _lxss():
        name = entry["name"]
        if not NAME_RE.match(name) or name.casefold() in NEVER_TOUCH:
            continue
        if names is not None and name not in names:
            continue
        if not base_path_is_ours(name, entry["base_path"]):
            continue
        if not _is_orphan(name):
            continue
        if not _marker_names(name):
            continue
        out.append({"distro": name, "bytes": _dir_bytes(_home_of(name))})
    return out


# ── status ───────────────────────────────────────────────────────────────────

def status() -> BoxStatus:
    """Where the locked box stands, in plain words. Never raises."""
    try:
        return _status()
    except Exception:  # a status check must never break a page
        return BoxStatus("box_broken", W.BOX_BROKEN, "repair_box")


def _platform() -> str:
    """"windows", "linux" or "other" (macOS and the rest: no box)."""
    if os.name == "nt":
        return "windows"
    return "linux" if sys.platform.startswith("linux") else "other"


def _bwrap() -> Optional[str]:
    for path in ("/usr/bin/bwrap", "/bin/bwrap", "/usr/local/bin/bwrap"):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _status() -> BoxStatus:
    where = _platform()
    if where == "linux":
        # Bubblewrap is there. The run-time checks happen when code runs.
        if _bwrap():
            return BoxStatus("ready", W.READY, "", DEFAULT_NAME)
        return BoxStatus("no_box", W.LINUX_NO_BWRAP, "", DEFAULT_NAME)
    if where != "windows":
        return BoxStatus("unsupported", W.UNSUPPORTED, "")
    if not wsl_exe():
        return BoxStatus("no_wsl", W.NO_WSL, "install_wsl")
    listing, problem = _list_distros()
    if problem is not None:
        return problem
    if wslconfig_problem() is not None:
        return BoxStatus("not_nat", W.NOT_NAT, "")
    distro = distro_name()
    orphans = [o for o in find_orphans(listing) if o["distro"] != distro]

    def _with(st: BoxStatus) -> BoxStatus:
        st.distro = distro
        st.orphans = orphans
        return st

    if not NAME_RE.match(distro) or distro.casefold() in NEVER_TOUCH:
        return _with(BoxStatus("taken", W.TAKEN.format(distro=distro), ""))
    entries = _entries_named(distro)
    if distro not in listing and not entries:
        if orphans:
            size = W.size_words(orphans[0]["bytes"])
            return _with(BoxStatus("orphan", W.ORPHAN.format(size=size),
                                   "remove_orphan"))
        return _with(BoxStatus("no_box", W.NO_BOX, "set_up_box"))
    if len(entries) != 1 or not base_path_is_ours(distro, entries[0]["base_path"]):
        return _with(BoxStatus("taken", W.TAKEN.format(distro=distro), ""))
    if not _marker_names(distro):
        return _with(BoxStatus("taken", W.TAKEN.format(distro=distro), ""))
    if not _has_my_claim(distro):
        if other_live_claims(distro):
            return _with(BoxStatus("no_box", W.SHARED, "share_box"))
        size = W.size_words(_dir_bytes(_home_of(distro)))
        return _with(BoxStatus("orphan", W.ORPHAN.format(size=size), "remove_orphan"))
    if str(listing.get(distro, entries[0].get("version") or "")) != "2":
        return _with(BoxStatus("box_v1", W.BOX_V1, "repair_box"))
    if not _launcher_present(distro):
        return _with(BoxStatus("box_broken", W.BOX_BROKEN, "repair_box"))
    hint = _read_box_json()
    if not _launcher_current(distro):
        if hint.get("launcher"):
            hint.pop("launcher", None)
            _write_box_json(hint)
        return _with(BoxStatus("box_broken", W.LAUNCHER_OLD, "repair_box"))
    fresh = hint.get("launcher") != launcher_fingerprint()
    hint["launcher"] = launcher_fingerprint()
    sha = wsl_version_sha()
    if not sha or hint.get("wsl_version_sha") != sha or hint.get("distro") != distro:
        if not self_test(distro):
            return _with(BoxStatus("box_broken", W.BOX_BROKEN, "repair_box"))
        if sha:
            hint.update({"wsl_version_sha": sha, "distro": distro,
                         "self_test": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                    time.gmtime())})
            _write_box_json(hint)
            fresh = False
    if fresh:
        _write_box_json(hint)
    return _with(BoxStatus("ready", W.READY, ""))


# ── the unregister guard ─────────────────────────────────────────────────────

def guard(distro: str) -> tuple:
    """``(True, "")`` when *distro* may be terminated and unregistered.

    Every condition must hold, checked fresh each time: the name rule, not one
    of her distros, exactly one Lxss entry, its BasePath is
    ``<box root>\\<distro>``, and the marker inside names it.
    """
    if not isinstance(distro, str) or not NAME_RE.match(distro):
        return False, "name"
    if distro.casefold() in NEVER_TOUCH:
        return False, "never"
    entries = _entries_named(distro)
    if len(entries) != 1:
        return False, "not_registered"
    if not base_path_is_ours(distro, entries[0]["base_path"]):
        return False, "base_path"
    if not _marker_names(distro):
        return False, "marker"
    return True, ""


def _remove_home(distro: str) -> None:
    home = _home_of(distro)
    if not os.path.isdir(home):
        return
    from puffpai import remove_cli
    remove_cli.delete_tree(home, allowed_roots=[box_root()], keeps=[])


def _unregister(distro: str) -> tuple:
    ok, why = guard(distro)
    if not ok:
        return False, why
    _wsl(["--terminate", distro], timeout=60, _guarded=True)
    r = _wsl(["--unregister", distro], timeout=300, _guarded=True)
    if r.returncode != 0 or _entries_named(distro):
        return False, "unregister_failed"
    try:
        _remove_home(distro)
    except Exception:
        pass
    return True, ""


def _tidy_claims(distro: str) -> None:
    folder = _claims_dir(distro)
    try:
        for name in os.listdir(folder):
            if name.endswith(".json"):
                os.unlink(os.path.join(folder, name))
        os.rmdir(folder)
    except OSError:
        pass


def remove_box(*, orphan: bool = False) -> dict:
    """Drop this install's claim; unregister the box only when no live claim
    remains and the guard agrees. ``orphan=True`` removes boxes left over
    from deleted BuildBots instead. ``{"removed": bool, "why": str}``."""
    if os.name != "nt":
        return {"removed": False, "why": ""}
    if orphan:
        removed, why = False, ""
        for o in find_orphans():
            ok, reason = _unregister(o["distro"])
            if ok:
                _tidy_claims(o["distro"])
                removed = True
            else:
                why = why or reason
        return {"removed": removed, "why": why}
    distro = distro_name()
    if not NAME_RE.match(distro):
        return {"removed": False, "why": "name"}
    if not _has_my_claim(distro):
        # Never set up (or never shared) by this install: not ours to remove.
        return {"removed": False, "why": "not_claimed"}
    drop_claim(distro)
    if other_live_claims(distro):
        return {"removed": False, "why": W.SHARED_STAYS}
    if not _entries_named(distro):
        _tidy_claims(distro)
        return {"removed": False, "why": "not_set_up"}
    ok, why = _unregister(distro)
    if ok:
        _tidy_claims(distro)
    return {"removed": ok, "why": why}


def plan_for_removal() -> Optional[dict]:
    """What Remove BuildBot would do with the box: ``{"distro","bytes","last"}``
    for a registered box this install may remove, else None. ``last`` is
    False when another BuildBot still uses it (the box then stays)."""
    if os.name != "nt" or not wsl_exe():
        return None
    distro = distro_name()
    if not NAME_RE.match(distro) or not _has_my_claim(distro):
        return None
    ok, _why = guard(distro)
    if not ok:
        return None
    return {"distro": distro, "bytes": _dir_bytes(_home_of(distro)),
            "last": not other_live_claims(distro)}


# ── outside visits ───────────────────────────────────────────────────────────

def ingest_outside(lines: list) -> int:
    """Copy the box proxy's visits into "What leaves your computer".

    Every line is rebuilt and re-checked by ``egress_ledger.record``; only
    box services (``box_<sha24>``) and box kinds of visit are accepted.
    """
    from puffpai.security import egress_ledger
    n = 0
    for rec in lines or []:
        if not isinstance(rec, dict):
            continue
        service = str(rec.get("service", ""))
        what = str(rec.get("what", ""))
        if not _BOX_SERVICE_RE.match(service) or what not in _BOX_WHAT:
            continue
        fields = dict(host=str(rec.get("host", "")), outcome=str(rec.get("outcome", "")),
                      method=str(rec.get("method", "CONNECT")), path="",
                      code=str(rec.get("code", "")), bytes_in=rec.get("bytes_in"),
                      bytes_out=rec.get("bytes_out"), what=what)
        # Install traffic is logged under the pinned service "box_install"
        # (as the box's own download is). Run-time traffic under the project's
        # switch id; the log drops names that look like a pasted key (the
        # switch id is 24 hex), so then under "box_traffic".
        label = "box_install" if what == "box_install" else service
        ok = egress_ledger.record(label, **fields)
        if not ok and label != "box_install":
            ok = egress_ledger.record("box_traffic", **fields)
        n += bool(ok)
    return n


# ═════════════════════════════════════════════════════════════════════════════
# Running outside code in the box
# ═════════════════════════════════════════════════════════════════════════════

RESULT_SCHEMA = "puffpai_box_result/v1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
HOST_RE = re.compile(r"^(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)"
                     r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z][a-z0-9-]*[a-z0-9]\Z")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
WHATS = ("box_install", "box_traffic")
LIMIT_MAX = {"cpu_s": 3600, "mem_mb": 8192, "wall_s": 3600, "pids": 1024,
             "fsize_mb": 4096, "disk_mb": 20480}
LIMIT_DEF = {"cpu_s": 600, "mem_mb": 2048, "wall_s": 900, "pids": 256,
             "fsize_mb": 512, "disk_mb": 4096}
#: The only hosts an install phase may reach (npm and PyPI). Run-time hosts are
#: empty unless the owner switched the project's outside service on.
INSTALL_HOSTS = frozenset({"registry.npmjs.org", "pypi.org", "files.pythonhosted.org"})
#: Raw output (a built app streamed out) is cut off here.
RAW_MAX_BYTES = 300_000_000
CAPTURE_MAX_BYTES = 1_000_000

#: Alpine minirootfs 3.24.2, pinned. The signature is checked once at release
#: build time (tools/verify_rootfs.py, key 0482D84022F52DF1C4E7CD43293ACD0907D9495A);
#: at run time only this sha256 is trusted.
ROOTFS_URL = ("https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/"
              "alpine-minirootfs-3.24.2-x86_64.tar.gz")
ROOTFS_HOST = "dl-cdn.alpinelinux.org"
ROOTFS_SHA256 = "c5ca053cfe1d85c5b96dff8b9bc57045f7f184a30ffb6b65776409ca90388677"
ROOTFS_MAX_BYTES = 16 * 1024 * 1024
ASSET_FILES = ("box_run.py", "egress_proxy.py", "box-entry", "setup_box.sh", "wsl.conf",
               "make_seccomp.py")
MARKER_SCHEMA = "puffpai_box_marker/v1"


class BoxRefused(ValueError):
    """A request the box will not run. ``code`` is short; ``message`` plain."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class BoxError(RuntimeError):
    """Something in the box did not work (the message is plain words)."""


@dataclass
class BoxRequest:
    """One run in the box. Field names are the launcher's wire format."""
    run_id: str
    project: str
    argv: list
    allow_hosts: list = field(default_factory=list)
    service: str = ""
    what: str = "box_traffic"
    folder: Optional[dict] = None
    limits: dict = field(default_factory=dict)
    stdin: str = "none"
    stdout_mode: str = "capture"
    env: dict = field(default_factory=dict)

    def wire(self) -> dict:
        out = {"run_id": self.run_id, "project": self.project, "argv": list(self.argv),
               "allow_hosts": list(self.allow_hosts), "service": self.service,
               "what": self.what, "limits": dict(self.limits), "stdin": self.stdin,
               "stdout_mode": self.stdout_mode, "env": dict(self.env)}
        if self.folder is not None:
            out["folder"] = dict(self.folder)
        return out


@dataclass
class BoxResult:
    """``puffpai_box_result/v1`` (the launcher's result line)."""
    ok: bool = False
    run_id: str = ""
    exit_code: Optional[int] = None
    timed_out: bool = False
    oom_killed: bool = False
    wall_s: float = 0.0
    cpu_s: float = 0.0
    mem_peak_mb: float = 0.0
    pids_peak: int = 0
    project_disk_used_mb: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    outside: list = field(default_factory=list)
    refused: str = ""
    error: str = ""
    removed_new_links: int = 0
    schema: str = RESULT_SCHEMA

    @classmethod
    def from_dict(cls, data: Any) -> "BoxResult":
        if not isinstance(data, dict) or data.get("schema") != RESULT_SCHEMA:
            return cls(ok=False, error="no result from the locked box")
        res = cls()
        for name in ("run_id", "stdout_tail", "stderr_tail", "refused", "error"):
            value = data.get(name)
            if isinstance(value, str):
                setattr(res, name, value[:8192])
        for name in ("ok", "timed_out", "oom_killed"):
            setattr(res, name, data.get(name) is True)
        for name in ("wall_s", "cpu_s", "mem_peak_mb", "project_disk_used_mb"):
            value = data.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(res, name, float(value))
        for name in ("pids_peak", "removed_new_links", "exit_code"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(res, name, value)
        outside = data.get("outside")
        res.outside = [r for r in outside[:500] if isinstance(r, dict)] \
            if isinstance(outside, list) else []
        if res.refused or res.error:
            res.ok = False
        return res

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def service_for(project: str) -> str:
    """``box_<sha256(project)[:24]>``: the project's outside switch id, which is
    also the service name its log lines carry."""
    sw = _optional("puffpai.security.outside_switches")
    fn = getattr(sw, "switch_id_box", None) if sw is not None else None
    if callable(fn):
        sid = str(fn(project))
        if _BOX_SERVICE_RE.match(sid):
            return sid
    return "box_" + hashlib.sha256(project.encode("utf-8")).hexdigest()[:24]


def new_run_id() -> str:
    import secrets
    return "r" + secrets.token_hex(8)


# ── why a web build in the box stopped ──────────────────────────────────────

#: The steps of a web build in the box, in order.
BUILD_STEPS = ("put", "install", "build", "export")
#: (cause, pieces that must all be in the step's own error output). Read in
#: order; the first match wins. Only these fixed causes come out.
_BUILD_MARKS = (
    ("old_node", ("node sass does not yet support your current environment",)),
    ("old_node", ("node-sass", "unsupported runtime")),
    ("old_node", ("err_ossl_evp_unsupported",)),
    ("old_node", ("0308010c", "digital envelope routines")),
    ("own_install_steps", ("could not load the \"sharp\" module",)),
    ("own_install_steps", ("node-gyp",)),
    ("own_install_steps", ("prebuild-install",)),
    ("own_install_steps", ("was compiled against a different node.js version",)),
    ("lock_mismatch", ("npm ci", "can only install packages when your package.json and")),
    ("lock_mismatch", ("eusage", "package-lock.json")),
    ("part_changed", ("eintegrity",)),
    ("part_gone", ("etarget",)),
    ("part_gone", ("e404", "not found")),
    ("no_room", ("enospc",)),
    ("no_build_step", ("missing script", "build")),
)
_STEP_DEFAULT = {"put": "put_failed", "install": "install_failed",
                 "build": "build_failed", "export": "export_failed"}


def build_failure(step: str, result: Any = None, *, no_page: bool = False) -> dict:
    """Why a web build in the box stopped, for the owner.

    *step* is one of :data:`BUILD_STEPS` (``put``: the project copied in,
    ``install``: ``npm ci``, ``build``: ``npm run build``, ``export``: the
    built files copied out). *result* is that step's :class:`BoxResult` (or
    a dict of the same fields), or None. *no_page*: the build made files but
    no ``index.html`` (a library or a card for another program).

    Returns ``{"step", "cause", "line"}``. ``cause`` is a short code from
    :data:`wording.BUILD_CAUSE_WORDS` and ``line`` one fixed sentence. The
    project's own output is untrusted: it is only searched for the known
    causes above, never copied into the words."""
    step = step if step in BUILD_STEPS else "build"
    if no_page:
        return {"step": "export", "cause": "no_page", "line": W.BUILD_NO_PAGE}

    def _get(name: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    cause = ""
    if result is not None:
        if _get("timed_out") is True:
            cause = "too_long"
        elif _get("oom_killed") is True:
            cause = "memory"
        elif _get("refused"):
            cause = "no_room" if str(_get("refused")) == "too_big" else "box"
        else:
            text = (str(_get("stderr_tail") or "") + "\n"
                    + str(_get("stdout_tail") or "")).lower()[-16384:]
            for code, pieces in _BUILD_MARKS:
                if code == "no_build_step" and step != "build":
                    continue
                if all(p in text for p in pieces):
                    cause = code
                    break
    cause = cause or _STEP_DEFAULT[step]
    line = W.BUILD_STOPPED.format(step=W.BUILD_STEP_WORDS[step],
                                  cause=W.BUILD_CAUSE_WORDS[cause])
    return {"step": step, "cause": cause, "line": line}


def validate(req: BoxRequest) -> BoxRequest:
    """The same rules as the launcher, checked here first (fail closed).
    Returns a cleaned copy; raises :class:`BoxRefused`."""
    if not isinstance(req, BoxRequest):
        raise BoxRefused("bad_request")
    for name in ("run_id", "project"):
        value = getattr(req, name)
        if not isinstance(value, str) or not ID_RE.match(value):
            raise BoxRefused("bad_" + name)
    argv = req.argv
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 64 or not all(
            isinstance(a, str) and len(a) <= 4096 and "\x00" not in a for a in argv)):
        raise BoxRefused("bad_argv")
    hosts = req.allow_hosts
    if not isinstance(hosts, list) or len(hosts) > 32:
        raise BoxRefused("bad_hosts")
    clean_hosts = []
    for h in hosts:
        if not isinstance(h, str):
            raise BoxRefused("bad_hosts")
        name, _, port = h.strip().lower().partition(":")
        if not HOST_RE.match(name) or port not in ("", "443"):
            raise BoxRefused("bad_hosts")
        if name not in clean_hosts:
            clean_hosts.append(name)
    service = req.service or service_for(req.project)
    if not _BOX_SERVICE_RE.match(service):
        raise BoxRefused("bad_service")
    if req.what not in WHATS:
        raise BoxRefused("bad_what")
    if req.stdin not in ("none", "pass") or req.stdout_mode not in ("capture", "raw"):
        raise BoxRefused("bad_streams")
    env = req.env or {}
    if not isinstance(env, dict) or len(env) > 32 or not all(
            isinstance(k, str) and ENV_RE.match(k) and isinstance(v, str)
            and len(v) <= 4096 and "\x00" not in v for k, v in env.items()):
        raise BoxRefused("bad_env")
    limits = req.limits or {}
    if not isinstance(limits, dict):
        raise BoxRefused("bad_limits")
    for k, v in limits.items():
        if k not in LIMIT_MAX or not isinstance(v, int) or isinstance(v, bool) \
                or not 1 <= v <= LIMIT_MAX[k]:
            raise BoxRefused("bad_limits")
    folder = req.folder
    if folder is not None:
        if (not isinstance(folder, dict) or set(folder) - {"windows_path", "mode", "name"}
                or folder.get("mode") not in ("ro", "rw")
                or not ID_RE.match(str(folder.get("name") or "files"))
                or not isinstance(folder.get("windows_path"), str)):
            raise BoxRefused("bad_folder")
        if req.what == "box_install":
            # Build and install phases never get an owner folder (S8).
            raise BoxRefused("folder_while_installing")
    if req.what == "box_install":
        off = [h for h in clean_hosts if h not in INSTALL_HOSTS]
        if off:
            raise BoxRefused("install_host")
    return BoxRequest(run_id=req.run_id, project=req.project, argv=list(argv),
                      allow_hosts=clean_hosts, service=service, what=req.what,
                      folder=dict(folder) if folder is not None else None,
                      limits=dict(limits), stdin=req.stdin, stdout_mode=req.stdout_mode,
                      env=dict(env))


def _runtime_hosts_allowed(req: BoxRequest) -> bool:
    """Run-time hosts only when the owner switched this project's outside
    service on (the switch id is the project's service name).

    Install-time hosts (the code parts a build fetches) follow the Bridge
    (``bridge_guard.switch_allows("box_install", ...)``): on as built, but
    with the Bridge up only once "Downloading an app or code you picked" is
    switched on, and never on a build that keeps everything on this computer.
    Run-time hosts need the project's own ON and the Bridge's yes as well.
    Fails closed: no switches or no Bridge to ask means no hosts.
    """
    if not req.allow_hosts:
        return True
    sw = _optional("puffpai.security.outside_switches")
    get = getattr(sw, "get", None) if sw is not None else None
    bridge = _optional("puffpai.security.bridge_guard")
    allows = getattr(bridge, "switch_allows", None) if bridge is not None else None
    if not callable(get) or not callable(allows):
        return False
    try:
        if req.what != "box_traffic":
            return bool(allows("box_install", get))
        return get(req.service) is True and bool(allows(req.service, get))
    except Exception:
        return False


def _check_folder(req: BoxRequest) -> Optional[str]:
    """The owner folder, resolved and checked on this side (REQUIRED: the
    launcher alone would mount a junction to the vault, proof 11/16)."""
    if req.folder is None:
        return None
    from puffpai.security import owner_folders
    real, why = owner_folders.check(req.folder["windows_path"], req.folder["mode"])
    if not real:
        raise BoxRefused("folder", why)
    return real


def _popen(argv: list, **kwargs: Any) -> subprocess.Popen:
    """Start one program with pipes. The single Popen in this module (tests fake it)."""
    return subprocess.Popen(argv, **procflags.hidden(), **kwargs)


def _box_ready_for_run(distro: str) -> None:
    """Cheap checks before every run: the box is ours (registry and claim),
    the network setting is still NAT, and the self-test ran since Windows'
    Linux feature last changed. Raises BoxRefused."""
    if wslconfig_problem() is not None:
        raise BoxRefused("not_nat", W.NOT_NAT)
    entries = _entries_named(distro)
    if len(entries) != 1 or not base_path_is_ours(distro, entries[0]["base_path"]):
        raise BoxRefused("not_ours", W.TAKEN.format(distro=distro))
    if not _has_my_claim(distro):
        raise BoxRefused("not_claimed", W.NO_BOX)
    hint = _read_box_json()
    if hint.get("broken"):
        raise BoxRefused("box_broken", W.BOX_BROKEN)
    if hint.get("launcher") != launcher_fingerprint():
        # Not checked since this BuildBot's programs changed: check (and put
        # back) before anything runs; an older launcher never runs a job.
        if not _launcher_current(distro):
            raise BoxRefused("box_broken", W.LAUNCHER_OLD)
        hint["launcher"] = launcher_fingerprint()
        _write_box_json(hint)
    sha = wsl_version_sha()
    if not sha or hint.get("wsl_version_sha") != sha or hint.get("distro") != distro:
        if not self_test(distro):
            hint.update({"broken": True, "distro": distro})
            _write_box_json(hint)
            raise BoxRefused("box_broken", W.BOX_BROKEN)
        if sha:
            hint.update({"wsl_version_sha": sha, "distro": distro, "broken": False,
                         "self_test": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            _write_box_json(hint)


def run(req: BoxRequest, *, stdin_path: Optional[str] = None,
        stdout_path: Optional[str] = None) -> BoxResult:
    """Run one request in the locked box. Never raises for the box's own
    refusals: they come back as ``BoxResult(ok=False, refused=<code>)``.

    *stdin_path*: a file streamed to the program (``stdin="pass"``).
    *stdout_path*: where raw output goes (``stdout_mode="raw"``), capped at
    :data:`RAW_MAX_BYTES`. Every outside visit the run made is copied into
    the owner's outside log (:func:`ingest_outside`).
    """
    try:
        req = validate(req)
        if req.stdin == "pass" and not stdin_path:
            raise BoxRefused("bad_streams")
        if req.stdout_mode == "raw" and not stdout_path:
            raise BoxRefused("bad_streams")
        if not _runtime_hosts_allowed(req):
            raise BoxRefused("switched_off")
        real = _check_folder(req)
        if real is not None:
            req.folder["windows_path"] = real
    except BoxRefused as exc:
        return BoxResult(ok=False, refused=exc.code, error=exc.message if exc.message != exc.code else "")
    if _platform() != "windows":
        return _run_linux(req, stdin_path=stdin_path, stdout_path=stdout_path)
    distro = distro_name()
    try:
        _box_ready_for_run(distro)
        if req.folder is not None and not sync_deny_list(distro):
            # The box's own second check must know this BuildBot's private
            # folders before any owner folder is mounted (fail closed).
            raise BoxRefused("deny_list", W.DENY_LIST_NOT_SET)
    except BoxRefused as exc:
        return BoxResult(ok=False, refused=exc.code, error=exc.message)
    res = _run_wsl(distro, req, stdin_path=stdin_path, stdout_path=stdout_path)
    if res.outside:
        try:
            ingest_outside([dict(r, service=req.service, what=req.what) for r in res.outside])
        except Exception:
            pass
    if real is not None and req.folder and req.folder.get("mode") == "rw":
        from puffpai.security import owner_folders
        try:
            owner_folders.after_write_sweep(real)
        except PermissionError:
            res.ok = False
            res.error = "sweep"
    return res


def _feed(proc: subprocess.Popen, head: bytes, stdin_path: Optional[str]) -> None:
    try:
        proc.stdin.write(head)
        if stdin_path:
            with open(stdin_path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass


def _drain(stream, sink, cap: int, flag: dict) -> None:
    total = 0
    try:
        while True:
            chunk = stream.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                flag["over"] = True
                break
            sink(chunk)
    except (OSError, ValueError):
        pass


def _run_wsl(distro: str, req: BoxRequest, *, stdin_path: Optional[str],
             stdout_path: Optional[str]) -> BoxResult:
    import threading
    exe = wsl_exe()
    if not exe:
        return BoxResult(ok=False, refused="no_wsl", error=W.NO_WSL)
    if stdin_path and (not os.path.isfile(stdin_path) or os.path.islink(stdin_path)):
        return BoxResult(ok=False, refused="bad_streams")
    head = (json.dumps(req.wire(), separators=(",", ":")) + "\n").encode("utf-8")
    if len(head) > 65536:
        return BoxResult(ok=False, refused="bad_request")
    wall = int((req.limits or {}).get("wall_s", LIMIT_DEF["wall_s"]))
    raw = req.stdout_mode == "raw"
    try:
        proc = _popen([exe, "-d", distro, "-u", "root", "--exec", LAUNCHER],
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      **_run_kwargs())
    except OSError:
        return BoxResult(ok=False, error="the locked box did not start")
    out_buf: list = []
    err_buf: list = []
    flags = {"over": False}
    out_fh = open(stdout_path, "wb") if raw else None
    try:
        feeder = threading.Thread(target=_feed, args=(proc, head, stdin_path), daemon=True)
        out_t = threading.Thread(target=_drain, args=(
            proc.stdout, out_fh.write if out_fh else out_buf.append,
            RAW_MAX_BYTES if raw else CAPTURE_MAX_BYTES, flags), daemon=True)
        err_t = threading.Thread(target=_drain, args=(proc.stderr, err_buf.append,
                                                      CAPTURE_MAX_BYTES, {}), daemon=True)
        for t in (feeder, out_t, err_t):
            t.start()
        deadline = time.monotonic() + wall + 60
        while proc.poll() is None:
            if flags["over"] or time.monotonic() > deadline:
                proc.kill()          # the launcher traps the hangup and cleans up
                break
            time.sleep(0.05)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        for t in (feeder, out_t, err_t):
            t.join(timeout=10)
    finally:
        if out_fh:
            out_fh.close()
    if flags["over"]:
        return BoxResult(ok=False, error="too_big")
    if raw:
        lines = [ln for ln in _text(b"".join(err_buf)).splitlines()
                 if ln.startswith("PUFFBOX-RESULT ")]
        text = lines[-1][len("PUFFBOX-RESULT "):] if lines else ""
    else:
        lines = [ln for ln in _text(b"".join(out_buf)).strip().splitlines() if ln.strip()]
        text = lines[-1] if lines else ""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    return BoxResult.from_dict(data)


def _run_linux(req: BoxRequest, *, stdin_path: Optional[str],
               stdout_path: Optional[str]) -> BoxResult:
    """Linux host: bubblewrap directly, from the same builder. No cgroup, no
    per-project disk image and no outside list here (fail closed: a run with
    hosts is refused). Not proven on hardware."""
    from puffpai.box import jail
    if _platform() != "linux":
        return BoxResult(ok=False, refused="unsupported", error=W.UNSUPPORTED)
    bwrap = _bwrap()
    if not bwrap:
        return BoxResult(ok=False, refused="no_box", error=W.LINUX_NO_BWRAP)
    if req.allow_hosts:
        return BoxResult(ok=False, refused="not_in_build")
    bpf = os.path.join(box_root(), "box.bpf")
    if not os.path.isfile(bpf):
        return BoxResult(ok=False, refused="box_broken", error=W.BOX_BROKEN)
    proj = os.path.join(box_root(), "projects", req.project)
    os.makedirs(proj, mode=0o700, exist_ok=True)
    folder = None
    if req.folder:
        folder = {"path": req.folder["windows_path"], "mode": req.folder["mode"],
                  "name": req.folder.get("name") or "files"}
    lim = dict(LIMIT_DEF, **(req.limits or {}))
    argv = jail.bwrap_argv(proj, folder=folder, env=req.env or None) + list(req.argv)
    argv[0] = bwrap
    prefix = ["/usr/bin/prlimit", f"--cpu={lim['cpu_s']}", "--core=0", "--nofile=4096",
              f"--fsize={lim['fsize_mb'] * 1024 * 1024}", "--"]
    fd = os.open(bpf, os.O_RDONLY)
    try:
        if fd != jail.SECCOMP_FD:
            os.dup2(fd, jail.SECCOMP_FD)
        started = time.monotonic()
        stdin = open(stdin_path, "rb") if req.stdin == "pass" and stdin_path else subprocess.DEVNULL
        out = open(stdout_path, "wb") if req.stdout_mode == "raw" and stdout_path else subprocess.PIPE
        try:
            proc = subprocess.run(prefix + argv, stdin=stdin, stdout=out, stderr=subprocess.PIPE,
                                  pass_fds=(jail.SECCOMP_FD,), timeout=lim["wall_s"],
                                  env={"PATH": "/usr/bin:/bin"}, cwd="/")
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            proc, timed_out = exc, True
        finally:
            for fh in (stdin, out):
                if hasattr(fh, "close"):
                    fh.close()
    finally:
        os.close(fd)
        if fd != jail.SECCOMP_FD:
            try:
                os.close(jail.SECCOMP_FD)
            except OSError:
                pass
    rc = None if timed_out else getattr(proc, "returncode", None)

    def _tail(data: Any) -> str:
        return data[-4096:].decode("utf-8", "replace") if isinstance(data, bytes) else ""
    return BoxResult(ok=(rc == 0 and not timed_out), exit_code=rc, timed_out=timed_out,
                     wall_s=round(time.monotonic() - started, 1),
                     stdout_tail=_tail(getattr(proc, "stdout", b"")),
                     stderr_tail=_tail(getattr(proc, "stderr", b"")))


# ── projects ─────────────────────────────────────────────────────────────────

def _single_top(tar_path: str) -> Optional[str]:
    """The one top folder every member of *tar_path* sits in, else None."""
    import tarfile
    tops = set()
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            for i, member in enumerate(tf):
                if i > 200_000:
                    return None
                name = member.name.lstrip("./")
                if not name:
                    continue
                head = name.split("/", 1)[0]
                tops.add(head)
                if len(tops) > 1:
                    return None
                if "/" not in name and not member.isdir():
                    return None
    except (tarfile.TarError, OSError, EOFError):
        return None
    return next(iter(tops)) if len(tops) == 1 else None


def _compression_flag(tar_path: str) -> Optional[str]:
    """GNU tar cannot tell compression from a pipe: the flag for this file
    ("" for a plain tar, None for something that is not an archive)."""
    try:
        with open(tar_path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if head[:2] == b"\x1f\x8b":
        return "-z"
    if head[:3] == b"BZh":
        return "-j"
    if head[:6] == b"\xfd7zXZ\x00":
        return "-J"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "--zstd"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return ""
    return None


def put_project(project: str, tar_path: str, *, disk_mb: int = 4096) -> BoxResult:
    """Unpack *tar_path* (a project archive) as the whole of box project
    *project*. The archive is unpacked INSIDE the jail with no network, so a
    hostile archive only lands in its own ``/work``. A single top folder is
    stripped (GitHub archives have one)."""
    if not isinstance(project, str) or not ID_RE.match(project):
        return BoxResult(ok=False, refused="bad_project")
    if not isinstance(tar_path, str) or not os.path.isfile(tar_path) or os.path.islink(tar_path):
        return BoxResult(ok=False, refused="bad_streams")
    if os.path.getsize(tar_path) > disk_mb * 1024 * 1024:
        return BoxResult(ok=False, refused="too_big")
    flag = _compression_flag(tar_path)
    if flag is None:
        return BoxResult(ok=False, refused="bad_streams")
    remove_project(project)
    argv = ["tar", "-x", "-f", "-", "-C", "/work", "--no-same-owner", "--no-same-permissions"]
    if flag:
        argv.insert(1, flag)
    if _single_top(tar_path):
        argv.append("--strip-components=1")
    req = BoxRequest(run_id=new_run_id(), project=project, argv=argv, what="box_install",
                     limits={"disk_mb": int(disk_mb), "wall_s": 600, "mem_mb": 512},
                     stdin="pass")
    return run(req, stdin_path=tar_path)


_REL_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}\Z")


def _rel_ok(rel: str) -> bool:
    if not isinstance(rel, str) or not _REL_RE.match(rel) or rel.startswith("-"):
        return False
    return all(part not in ("", ".", "..") for part in rel.split("/"))


def _extract_regular(archive: str, dest: str, max_bytes: int) -> None:
    """Fallback extractor: regular files and folders only, no absolute or
    ``..`` names, a byte cap, a single top folder stripped."""
    import tarfile
    total = 0
    top = _single_top(archive)
    with tarfile.open(archive, "r:*") as tf:
        for member in tf:
            name = member.name.lstrip("./")
            if top and (name == top or name.startswith(top + "/")):
                name = name[len(top) + 1:]
            if not name:
                continue
            parts = name.split("/")
            if any(p in ("", ".", "..") for p in parts) or ":" in name or "\\" in name:
                continue
            target = os.path.join(dest, *parts)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            if not member.isfile():
                continue                         # links, devices: never
            total += member.size
            if total > max_bytes:
                raise BoxError("too_big")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(member)
            with open(target, "xb") as out:
                _copy_stream(src, out)


def _copy_stream(src, out) -> None:
    while True:
        chunk = src.read(1 << 20)
        if not chunk:
            break
        out.write(chunk)


def export(project: str, rel_path: str, dest_dir: str, *,
           max_bytes: int = 200_000_000) -> list[str]:
    """Copy ``/work/<rel_path>`` out of box project *project* into *dest_dir*
    (which must be new or empty, inside the apps folder). Only regular files
    and folders arrive; the total is capped. Returns the files written."""
    if not isinstance(project, str) or not ID_RE.match(project) or not _rel_ok(rel_path):
        raise BoxError("bad_request")
    from puffpai.security import owner_folders
    dest = os.path.abspath(dest_dir)
    apps = _canon(owner_folders.apps_root())
    if not (_canon(dest) + os.sep).startswith(apps.rstrip("\\/") + os.sep) or _canon(dest) == apps:
        raise BoxError("not_in_apps")
    if os.path.lexists(dest) and (owner_folders.is_link(dest) or os.listdir(dest)):
        raise BoxError("not_empty")
    os.makedirs(dest, exist_ok=True)
    import secrets as _secrets
    tmp = os.path.join(os.path.dirname(dest), f".export-{_secrets.token_hex(6)}.tar")
    try:
        req = BoxRequest(run_id=new_run_id(), project=project,
                         argv=["tar", "-c", "-f", "-", "--", rel_path], what="box_traffic",
                         limits={"wall_s": 600, "mem_mb": 512}, stdout_mode="raw")
        res = run(req, stdout_path=tmp)
        if not res.ok or not os.path.isfile(tmp):
            raise BoxError(res.refused or res.error or "export_failed")
        if os.path.getsize(tmp) > max_bytes + (max_bytes // 10) + 10_000_000:
            raise BoxError("too_big")
        sa = _optional("puffpai.security.safe_archive")
        if sa is not None and callable(getattr(sa, "extract", None)):
            sa.extract(tmp, dest, max_total_bytes=int(max_bytes), strip_top=True)
        else:
            _extract_regular(tmp, dest, int(max_bytes))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    out = []
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        dirnames[:] = [d for d in dirnames if not owner_folders.is_link(os.path.join(dirpath, d))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not owner_folders.is_link(path):
                out.append(path)
    return sorted(out)


def remove_project(project: str) -> None:
    """Drop box project *project* (its disk image goes)."""
    if not isinstance(project, str) or not ID_RE.match(project):
        raise BoxError("bad_project")
    if _platform() == "linux":
        path = os.path.join(box_root(), "projects", project)
        if os.path.isdir(path):
            from puffpai import remove_cli
            remove_cli.delete_tree(path, allowed_roots=[os.path.join(box_root(), "projects")])
        return
    if _platform() != "windows":
        return
    distro = distro_name()
    entries = _entries_named(distro)
    if len(entries) != 1 or not base_path_is_ours(distro, entries[0]["base_path"]):
        return
    try:
        _wsl(["-d", distro, "-u", "root", "--exec", LAUNCHER, "--remove-project", project],
             timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        pass


def _box_is_mine(distro: str) -> bool:
    """This install's own claim, one registry entry, its disk in our root."""
    if not NAME_RE.match(distro or "") or distro.casefold() in NEVER_TOUCH:
        return False
    if not _has_my_claim(distro):
        return False
    entries = _entries_named(distro)
    return len(entries) == 1 and base_path_is_ours(distro, entries[0]["base_path"])


def projects_in_box() -> Optional[list]:
    """The project names in this install's box (a project folder or its disk
    image), sorted. ``[]`` when there is no box of this install's; None when
    its box is there but could not be looked at."""
    where = _platform()
    if where == "linux":
        path = os.path.join(box_root(), "projects")
        try:
            names = os.listdir(path)
        except FileNotFoundError:
            return []
        except OSError:
            return None
        return sorted(n for n in names if ID_RE.match(n))
    if where != "windows" or not wsl_exe():
        return []
    distro = distro_name()
    if not _box_is_mine(distro):
        return []
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
                  f"ls -1 {PROJ_DIR}; ls -1 {IMG_DIR}"], timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return None
    names = set()
    for line in _text(r.stdout).splitlines():
        name = line.strip()
        if name.endswith(".img"):
            name = name[:-4]
        if ID_RE.match(name):
            names.add(name)
    return sorted(names)


def remove_projects(names: list) -> list:
    """Remove each of *names* (this install's own projects, picked by the
    caller from its own records) from the box. Returns the ones still there
    afterwards (``[]`` when all went, or there is no box to look in)."""
    wanted = [n for n in (names or []) if isinstance(n, str) and ID_RE.match(n)]
    for name in wanted:
        try:
            remove_project(name)
        except BoxError:
            pass
    left = projects_in_box()
    if left is None:
        return list(wanted)
    return [n for n in wanted if n in left]


#: Where the box keeps each BuildBot's own private folders for its second
#: check (one file per install, named by its claim id).
DENY_DIR = "/etc/puffpai/deny.d"
PROJ_DIR = "/srv/box/projects"
IMG_DIR = "/srv/box/images"


def sync_deny_list(distro: str) -> bool:
    """Write this install's private folders (vault, state, apps, box and
    install folders, plus the fixed system places) into the box, so the
    launcher's own check refuses them even when another BuildBot set the box
    up, or the vault moved since. True when the box holds the current list."""
    if not _box_is_mine(distro):
        return False
    body = json.dumps(deny_list_for_box(), indent=1).encode("utf-8")
    name = install_id()
    if not re.fullmatch(r"[0-9a-f]{16}", name):
        return False
    target = f"{DENY_DIR}/{name}.json"
    script = (f"umask 022 && mkdir -p {DENY_DIR} && chmod 755 {DENY_DIR} && "
              f"cat > {target}.tmp && mv -f {target}.tmp {target}")
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c", script],
                 input_bytes=body, timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    return r.returncode == 0


def drop_deny_list() -> bool:
    """Remove BuildBot: take this install's own deny list file out of its box
    (only while the box still carries this install's claim)."""
    if _platform() != "windows" or not wsl_exe():
        return False
    distro = distro_name()
    name = install_id()
    if not _box_is_mine(distro) or not re.fullmatch(r"[0-9a-f]{16}", name):
        return False
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/rm", "-f",
                  f"{DENY_DIR}/{name}.json"], timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    return r.returncode == 0


# ── setting the box up ───────────────────────────────────────────────────────

def _asset_bytes(name: str) -> bytes:
    """A packaged asset with Windows line endings taken out (a text file
    checked out on Windows may have them; the box's shell would choke)."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)
    with open(here, "rb") as fh:
        data = fh.read()
    return data.replace(b"\r\n", b"\n")


def _jail_bytes() -> bytes:
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jail.py")
    with open(here, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")


def deny_list_for_box() -> list:
    """The deny list the launcher checks a second time (Windows paths): the
    fixed system places of ``assets/deny_windows.json`` plus this install's
    own private folders (:func:`owner_folders.deny_roots`: the vault and its
    inbox, the state, data, apps, box and install folders). Written into the
    box at setup and again before every run with an owner folder
    (:func:`sync_deny_list`)."""
    from puffpai.security import owner_folders
    fixed = json.loads(_asset_bytes("deny_windows.json").decode("utf-8"))
    out = []
    for p in list(fixed) + list(owner_folders.deny_roots()):
        text = os.path.abspath(p) if os.path.isabs(p) else p
        if re.match(r"^[A-Za-z]:\\", text) and text not in out:
            out.append(text)
    return out


def _marker(distro: str) -> dict:
    return {"schema": MARKER_SCHEMA, "distro": distro, "install_id": install_id(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def assets_tar(distro: str) -> bytes:
    """Everything setup streams into ``/tmp/boxsrc`` (a tar in memory)."""
    import io
    import tarfile
    files = {name: _asset_bytes(name) for name in ASSET_FILES}
    files["jail.py"] = _jail_bytes()
    files["deny_windows.json"] = json.dumps(deny_list_for_box(), indent=1).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in ("box_run.py", "box-entry", "setup_box.sh") else 0o644
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def box_setup_rows(draft: dict) -> list:
    """Drawbridge checklist for ``box_setup``: the downloaded
    base is exactly the pinned one."""
    got = str((draft or {}).get("rootfs_sha256") or "")
    ok = got == ROOTFS_SHA256
    return [{"check": "The download is the one BuildBot expects", "result": "pass" if ok else "fail",
             "why": "It matches the fingerprint BuildBot ships with." if ok
             else W.CLI_REASONS["rootfs_changed"], "code": "rootfs_changed"}]


BOX_SETUP_BLOCK = frozenset({"rootfs_changed"})


def _say(progress: Optional[Callable[[str], None]], line: str) -> None:
    if progress is not None:
        try:
            progress(line)
        except Exception:
            pass


def _ledger_setup(outcome: str) -> None:
    """The box's package download at setup (Alpine's own signed packages).
    It runs before the box has its proxy, so it is logged here as one line."""
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record("box_install", host="dl-cdn.alpinelinux.org", outcome=outcome,
                             method="GET", path="", code="" if outcome == "done" else "no_answer",
                             bytes_in=None, bytes_out=None, what="box_install")
    except Exception:
        pass


def setup(*, ticket: str, device_id: str, progress: Callable[[str], None]) -> BoxStatus:
    """Set up (or adopt) this install's locked box after the owner's yes.

    The yes is a ``box_setup`` ticket for ``target_box(distro)`` minted on the
    owner's device. Returns the box's status afterwards."""
    ct = _optional("puffpai.security.consent_tickets")
    target_fn = getattr(ct, "target_box", None) if ct is not None else None
    if ct is None or target_fn is None or "box_setup" not in getattr(ct, "ACTIONS", ()):
        return BoxStatus("no_box", W.SETUP_NOT_IN_BUILD, "")
    distro = distro_name()
    try:
        target = target_fn(distro)
    except ValueError:
        return BoxStatus("taken", W.TAKEN.format(distro=distro), "", distro)
    ok, verdict = ct.redeem(ticket, device_id, "box_setup", target)
    if not ok:
        return BoxStatus("no_box", ct.refusal_words(verdict).replace(
            "Nothing was switched on", "Nothing was set up"), "set_up_box", distro)
    return _do_setup(distro, progress)


def _do_setup(distro: str, progress: Optional[Callable[[str], None]]) -> BoxStatus:
    """The setup itself; callers have checked the owner's yes."""
    started = time.monotonic()
    st = status()
    if st.state == "ready":
        _say(progress, W.SETUP_ALREADY)
        return st
    if st.offer == "share_box":
        add_claim(distro)
        _say(progress, W.SETUP_SHARED)
        return status()
    if _platform() != "windows":
        return st
    if st.state in ("box_broken", "box_v1") and _has_my_claim(distro):
        ok, _why = _unregister(distro)
        if not ok:
            return st
    elif st.state != "no_box" or st.offer not in ("set_up_box", ""):
        return st
    if not NAME_RE.match(distro) or distro.casefold() in NEVER_TOUCH or _entries_named(distro):
        return BoxStatus("taken", W.TAKEN.format(distro=distro), "", distro)
    home = _home_of(distro)
    if os.path.lexists(home) and (os.path.islink(home) or os.listdir(home)):
        return BoxStatus("no_box", W.SETUP_FOLDER_IN_USE, "", distro)
    sf = _optional("puffpai.source_fetch")
    if sf is None or not callable(getattr(sf, "get_archive", None)):
        return BoxStatus("no_box", W.SETUP_NOT_IN_BUILD, "", distro)
    _say(progress, W.SETUP_DOWNLOADING)
    dl = os.path.join(box_root(), "dl", "alpine-minirootfs-3.24.2-x86_64.tar.gz")
    os.makedirs(os.path.dirname(dl), exist_ok=True)
    try:
        got = sf.get_archive(ROOTFS_URL, allowed_hosts={ROOTFS_HOST},
                             max_bytes=ROOTFS_MAX_BYTES, dest_path=dl, service="box_install",
                             expect_sha256=ROOTFS_SHA256)
    except Exception as exc:  # FetchError and friends: plain words
        code = str(getattr(exc, "code", "") or "")
        msg = W.SETUP_NOT_EXPECTED if code == "sha_mismatch" else W.SETUP_DOWNLOAD_FAILED
        return BoxStatus("no_box", msg, "set_up_box", distro)
    from puffpai import drawbridge
    verdict = drawbridge.check_addition(
        {"kind": "box_setup", "id": "box:setup:locked-box", "name": "The locked box",
         "rootfs_sha256": str(got.get("sha256") or "")}, by="toolsmith")
    if verdict.get("verdict") != "allow":
        if "unknown_kind" in (verdict.get("reasons") or []):
            return BoxStatus("no_box", W.SETUP_NOT_IN_BUILD, "", distro)
        return BoxStatus("no_box", W.SETUP_NOT_EXPECTED, "set_up_box", distro)
    _say(progress, W.SETUP_UNPACKING)
    os.makedirs(home, exist_ok=True)
    try:
        r = _wsl(["--import", distro, home, got["path"], "--version", "2"], timeout=900)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        r = None
    entries = _entries_named(distro)
    if r is None or r.returncode != 0 or len(entries) != 1 \
            or not base_path_is_ours(distro, entries[0]["base_path"]):
        if len(entries) == 1 and base_path_is_ours(distro, entries[0]["base_path"]):
            _undo_import(distro)
        return BoxStatus("no_box", W.SETUP_BROKE, "set_up_box", distro)
    try:
        # Boot 1 does exactly two things, neither of which touches /mnt: the
        # owner marker (from here on the guard recognises the box, so a setup
        # that breaks later can still be taken back out) and the lock
        # (/etc/wsl.conf: no Windows drives, no Windows programs).
        mk = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
                   f"umask 022 && cat > {MARKER_PATH}"],
                  input_bytes=json.dumps(_marker(distro)).encode("utf-8"), timeout=120)
        if mk.returncode != 0 or not _marker_names(distro):
            raise BoxError("marker")
        lock = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
                     "umask 022 && cat > /etc/wsl.conf"],
                    input_bytes=_asset_bytes("wsl.conf"), timeout=120)
        if lock.returncode != 0:
            raise BoxError("lock")
        ok, _why = guard(distro)
        if not ok:
            raise BoxError("guard")
        _wsl(["--terminate", distro], timeout=120, _guarded=True)
        # Boot 2: the lock is in force. Nothing more runs unless no Windows
        # drive is mounted (setup_box.sh checks again itself).
        if not _locked_mounts(distro):
            raise BoxError("not_locked")
        put = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
                    "rm -rf /tmp/boxsrc && mkdir -m 700 /tmp/boxsrc && tar -x -C /tmp/boxsrc -f -"],
                   input_bytes=assets_tar(distro), timeout=120)
        if put.returncode != 0:
            raise BoxError("assets")
        _say(progress, W.SETUP_TOOLS)
        sh = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/sh", "/tmp/boxsrc/setup_box.sh"],
                  timeout=1200)
        _ledger_setup("done" if sh.returncode == 0 else "failed")
        if sh.returncode != 0 or "provisioned" not in _text(sh.stdout):
            raise BoxError("setup_box")
        add_claim(distro)
        ok, _why = guard(distro)
        if not ok:
            raise BoxError("guard")
        _wsl(["--terminate", distro], timeout=120, _guarded=True)
    except (BoxError, OSError, subprocess.TimeoutExpired, GuardRefused):
        _undo_import(distro)
        drop_claim(distro)
        return BoxStatus("no_box", W.SETUP_BROKE, "set_up_box", distro)
    hint = _read_box_json()
    hint.update({"distro": distro, "wsl_version_sha": "", "broken": False,
                 "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _write_box_json(hint)
    _say(progress, W.SETUP_CHECKING)
    after = status()
    hint = _read_box_json()
    hint["setup_seconds"] = round(time.monotonic() - started, 1)
    _write_box_json(hint)
    if after.ready:
        _say(progress, W.SETUP_DONE)
    return after


def mounts_are_locked(proc_mounts: str) -> bool:
    """True when nothing is mounted under /mnt except WSL's own /mnt/wsl and
    /mnt/wslg (so no Windows drive), read from /proc/mounts."""
    for line in (proc_mounts or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("/mnt/") \
                and not parts[1].startswith("/mnt/wsl"):
            return False
    return bool((proc_mounts or "").strip())


def _locked_mounts(distro: str) -> bool:
    try:
        r = _wsl(["-d", distro, "-u", "root", "--exec", "/bin/cat", "/proc/mounts"], timeout=120)
    except (OSError, subprocess.TimeoutExpired, GuardRefused):
        return False
    return r.returncode == 0 and mounts_are_locked(_text(r.stdout))


def _undo_import(distro: str) -> None:
    """Take a half-made box back out (only through the guard)."""
    try:
        _unregister(distro)
    except Exception:
        pass


# ── Windows' Linux feature missing, and things waiting for the box ──────────

def _waiting_path() -> str:
    return os.path.join(_state_dir(), "box", "waiting.json")


def waiting() -> list[dict]:
    """Things that were ticked and need the box, waiting for it."""
    try:
        with open(_waiting_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [dict(i) for i in items or [] if isinstance(i, dict) and isinstance(i.get("item"), dict)
            and not _integration_gone(i["item"])]


def _integration_gone(line: dict) -> bool:
    """An item for an integration whose record is gone (she removed it, maybe
    while it was being added) is no longer waiting."""
    iid = str(line.get("integration") or "")
    if not iid:
        return False
    records = _optional("puffpai.integrate.records")
    get = getattr(records, "get", None) if records is not None else None
    if get is None:
        return False
    try:
        return get(iid) is None
    except Exception:  # noqa: BLE001 - an unreadable record still waits
        return False


def remove_waiting(item_id: str) -> bool:
    """Take *item_id* (an item or the integration it adds) off the waiting
    list. True when something was taken off."""
    want = str(item_id or "")
    try:
        with open(_waiting_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    items = [i for i in (data.get("items") if isinstance(data, dict) else None) or []
             if isinstance(i, dict) and isinstance(i.get("item"), dict)]
    left = [i for i in items if want not in (str(i["item"].get("id") or ""),
                                             str(i["item"].get("integration") or ""))]
    if len(left) == len(items):
        return False
    _write_waiting(left)
    return True


def _write_waiting(items: list) -> None:
    path = _waiting_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"items": items[-50:]}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def add_waiting(item: dict) -> None:
    """Keep one box-needing plan item until the box exists.

    *item* is ``{"item": <approved plan line>, "found": <its found/v1 record,
    as the server found it>}`` (or just the plan line). The found record is
    kept because the finder's memory does not survive a restart."""
    if not isinstance(item, dict):
        raise ValueError("a waiting item is a mapping")
    entry = dict(item) if isinstance(item.get("item"), dict) else {"item": dict(item)}
    line = entry["item"]
    kind = str((line.get("apply") or {}).get("kind") or "")
    if not re.fullmatch(r"[a-z][a-z0-9_.]{0,39}", kind) or not str(line.get("id") or ""):
        raise ValueError("a waiting item needs an id and an apply kind")
    entry["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    items = [w for w in waiting() if (w.get("item") or {}).get("id") != line.get("id")]
    items.append(entry)
    _write_waiting(items)


def _wsl_install_path() -> str:
    return os.path.join(_state_dir(), "box", "wsl_install.json")


def install_wsl(*, ticket: str, device_id: str) -> dict:
    """Ask Windows to turn its Linux feature on (it shows its own permission
    prompt). Needs the owner's ``box_setup`` yes. ``{"started", "message"}``."""
    ct = _optional("puffpai.security.consent_tickets")
    target_fn = getattr(ct, "target_box", None) if ct is not None else None
    if ct is None or target_fn is None:
        return {"started": False, "message": W.SETUP_NOT_IN_BUILD}
    distro = distro_name()
    try:
        target = target_fn(distro)
    except ValueError:
        return {"started": False, "message": W.TAKEN.format(distro=distro)}
    ok, verdict = ct.redeem(ticket, device_id, "box_setup", target)
    if not ok:
        return {"started": False, "message": ct.refusal_words(verdict).replace(
            "Nothing was switched on", "Nothing was changed")}
    if _platform() != "windows":
        return {"started": False, "message": W.UNSUPPORTED}
    hp = _optional("puffpai.security.host_programs")
    exe = hp.path("wsl") if hp is not None else None
    if not exe:
        root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or ""
        candidate = os.path.join(root, "System32", "wsl.exe") if root else ""
        exe = candidate if candidate and os.path.isfile(candidate) else None
    if not exe:
        return {"started": False, "message": W.NO_WSL}
    import threading

    def _wait(proc: subprocess.Popen) -> None:
        try:
            code = proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            code = None
        record = {"finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "exit_code": code, "declined": code not in (0, None)}
        try:
            path = _wsl_install_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
        except OSError:
            pass
    try:
        proc = _popen([exe, "--install", "--no-distribution"], stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_run_kwargs())
    except OSError:
        return {"started": False, "message": W.NO_WSL}
    threading.Thread(target=_wait, args=(proc,), daemon=True).start()
    return {"started": True, "message": W.WSL_TURNING_ON}


def wsl_install_outcome() -> dict:
    """What the last "turn it on" ended with: ``{"declined": bool, "message"}``."""
    try:
        with open(_wsl_install_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"declined": False, "message": ""}
    if not isinstance(data, dict) or not data.get("declined"):
        return {"declined": False, "message": ""}
    n = len(waiting())
    return {"declined": True,
            "message": W.WSL_DECLINED_ONE if n == 1 else W.WSL_DECLINED.format(n=n)}


def waiting_lines() -> list[str]:
    n = len(waiting())
    if not n:
        return []
    return [W.FINISH_CARD_ONE if n == 1 else W.FINISH_CARD.format(n=n)]


def finish_waiting(*, progress: Callable[[str], None]) -> list[dict]:
    """After Windows' Linux feature is on: set the box up (the plan's yes
    already covered it; its facts are re-checked by setup and by each item's
    own Drawbridge check) and add the waiting items. Callers (the Finish
    button) check the owner's yes first. Returns one result per item."""
    items = waiting()
    if not items:
        _say(progress, W.FINISH_NOTHING)
        return []
    st = status()
    if st.state in ("no_wsl", "wsl_off"):
        _say(progress, W.FINISH_STILL_NO_WSL)
        return [{"id": (w.get("item") or {}).get("id"), "status": "waiting",
                 "line": W.FINISH_STILL_NO_WSL} for w in items]
    if not st.ready:
        st = _do_setup(distro_name(), progress)
    if not st.ready:
        line = W.SETUP_FAILED.format(message=st.message)
        _say(progress, line)
        return [{"id": (w.get("item") or {}).get("id"), "status": "failed", "line": line}
                for w in items]
    out = []
    left = []
    for entry in items:
        got = _apply_waiting(entry, progress)
        out.append(got)
        if got.get("status") == "waiting":
            left.append(entry)
    _write_waiting(left)
    return out


#: apply kind -> fn(entry, progress) -> {"status","line"}; filled by wiring
#: (the command-line tool applier) and by other modules through
#: :func:`register_waiting_applier`.
_WAITING_APPLIERS: dict = {}


def register_waiting_applier(kind: str, fn: Callable[[dict, Callable[[str], None]], dict]) -> None:
    """How a waiting item of *kind* is finished after the box is ready."""
    if not re.fullmatch(r"[a-z][a-z0-9_.]{0,39}", str(kind)) or not callable(fn):
        raise ValueError("a waiting applier is a kind and a function")
    _WAITING_APPLIERS[str(kind)] = fn


def _apply_waiting(entry: dict, progress: Callable[[str], None]) -> dict:
    line = entry.get("item") or {}
    kind = str((line.get("apply") or {}).get("kind") or "")
    fn = _WAITING_APPLIERS.get(kind)
    if fn is None:
        return {"id": line.get("id"), "status": "failed",
                "line": f"{line.get('label') or 'This'}: {W.SETUP_NOT_IN_BUILD}"}
    try:
        got = fn(entry, progress) or {}
    except Exception:
        got = {"status": "failed", "line": "Something went wrong adding this. Nothing else changed."}
    return {"id": line.get("id"), "status": str(got.get("status") or "failed"),
            "line": str(got.get("line") or "")}


__all__ = ["BUILD_STEPS", "BoxError", "BoxRefused", "BoxRequest", "BoxResult", "BoxStatus",
           "GuardRefused", "INSTALL_HOSTS", "ROOTFS_SHA256", "STATES", "add_claim",
           "build_failure",
           "add_waiting", "remove_waiting", "assets_tar", "box_root", "box_setup_rows", "claims",
           "deny_list_for_box", "distro_name", "drop_claim", "export", "find_orphans",
           "finish_waiting", "guard", "ingest_outside", "install_id", "install_root",
           "install_wsl", "known_install_roots", "new_run_id", "other_live_claims",
           "plan_for_removal", "put_project", "register_waiting_applier", "remove_box",
           "remove_project", "remove_projects", "projects_in_box", "sync_deny_list",
           "drop_deny_list",
           "run", "self_test", "service_for", "setup", "status",
           "validate", "waiting", "waiting_lines", "wsl_exe", "wsl_install_outcome",
           "wsl_version_sha", "wslconfig_problem"]
