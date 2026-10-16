"""A skill's own command line program, installed by BuildBot, may get the
skill's key for one run.

Some skills use a program that reads its key from its environment by itself
(``runcomfy`` reads ``RUNCOMFY_TOKEN``). Handing a key to whatever answers to
that name would hand it to anything planted under the name or on the import
path, so:

* At install: when "Install what's missing" installs the npm or pip package
  that provides a program a skill declares, :func:`record_installed` installs
  a SECOND copy into a folder of that skill's own
  (``<state>/tools/keycli/<skill>/<kind>/<package>``) and records, in the
  vault, a manifest of EVERY file in that folder (relative path and sha256),
  plus which program maps to which entry. The folder is self contained
  (``pip --target`` / ``npm --prefix`` put the package and its dependencies
  there), so the manifest covers the whole import root: the package's own
  files, its dependencies, any ``__pycache__``, any top-level module. The
  record lives in the vault (encrypted and authenticated), keyed by skill and
  program together, so one skill's program can never take over another's.
  With the vault locked nothing is recorded and the program never gets a key.

* At run (:func:`plan`): the command must be ONE plain call of a program the
  skill declares, with no shell features (else it runs keyless in the shell).
  The whole folder is re-walked and must match the manifest exactly (any
  added, removed or changed file, including a planted ``__init__.py``, a
  ``.pyc`` or a top-level module, means no key and nothing runs), checked
  again right before it starts.

* It never runs from that folder: :func:`materialize` reads each recorded
  file once into memory, checks its hash on those bytes, and writes the same
  bytes into a fresh run copy (random name), which is what runs and is
  deleted afterwards. pip: BuildBot's own Python with ``-I -S -B`` (no
  ``site`` import, so no site-packages and no ``.pth``) and ``sys.path`` set
  to the stdlib plus the run copy, appended last; a program needing anything
  else fails with a plain line. npm: the trusted ``node``
  (:func:`host_programs.path`) with the copied entry, no ``NODE_PATH`` or
  ``NODE_OPTIONS``. The environment is the small fixed set plus that skill's
  own declared keys only.

Not covered (SECURITY.md "Known risks"): the program itself gets the key by
design and can send it anywhere; and, as for every key path, other programs
already running with the owner's permissions are outside the boundary.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import configparser
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")
_MAX_FILE = 40 * 1024 * 1024
_MAX_FILES = 5000


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class ProgramRefused(ValueError):
    """The skill's program may not get its key. ``reason`` is plain words."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── where an isolated copy lives, and its manifest ───────────────────────────

def _safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("_")[:80] or "x"


def iso_root(skill: str, kind: str, package: str) -> str:
    """``<state>/tools/keycli/<skill>/<kind>/<package>`` (absolute)."""
    from puffpai import deps
    return os.path.abspath(deps.tools_dir("keycli", _safe_part(skill), kind,
                                          _safe_part(package)))


def manifest(root: str) -> dict[str, str]:
    """``{relative path: sha256}`` of EVERY file under *root* (symlinks and
    oversize trees refuse). Raises :class:`ProgramRefused` on trouble."""
    root = os.path.realpath(root)
    out: dict[str, str] = {}
    for here, dirs, files in sorted(os.walk(root)):
        for name in sorted(dirs):
            if os.path.islink(os.path.join(here, name)):
                raise ProgramRefused("the program folder has a symbolic link")
        for name in sorted(files):
            path = os.path.join(here, name)
            if os.path.islink(path):
                raise ProgramRefused("the program folder has a symbolic link")
            try:
                if os.path.getsize(path) > _MAX_FILE:
                    raise ProgramRefused("the program folder has a file that is too large")
            except OSError as exc:
                raise ProgramRefused("the program folder could not be read") from exc
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            out[rel] = _sha256(path)
            if len(out) > _MAX_FILES:
                raise ProgramRefused("the program folder has too many files")
    return out


# ── reading what an isolated copy provides ───────────────────────────────────

def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pip_entries(root: str, package: str) -> list[dict]:
    """``console_scripts`` of *package* in the isolated pip *root*:
    ``[{bin, module, attr}]``."""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for folder in names:
        if not folder.endswith(".dist-info"):
            continue
        dist = folder[:-len(".dist-info")].rsplit("-", 1)[0]
        if _norm(dist) != _norm(package):
            continue
        ep = os.path.join(root, folder, "entry_points.txt")
        if not os.path.isfile(ep):
            continue
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        try:
            cp.read(ep, encoding="utf-8")
        except (configparser.Error, OSError):
            continue
        if not cp.has_section("console_scripts"):
            continue
        for b, spec in cp.items("console_scripts"):
            module, _, attr = spec.split("[", 1)[0].strip().partition(":")
            module, attr = module.strip(), attr.strip()
            if _BIN_RE.match(b) and _MODULE_RE.match(module) and _MODULE_RE.match(attr):
                out.append({"bin": b, "module": module, "attr": attr})
    return out


def _npm_entries(root: str, package: str) -> list[dict]:
    """``bin`` of *package* in the isolated npm *root*: ``[{bin, entry}]``
    (``entry`` relative to *root*)."""
    pkg_dir = os.path.join(root, "node_modules", *package.split("/"))
    manifest_path = os.path.join(pkg_dir, "package.json")
    if not os.path.isfile(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    bins = data.get("bin") if isinstance(data, dict) else None
    if isinstance(bins, str):
        bins = {package.rsplit("/", 1)[-1]: bins}
    if not isinstance(bins, dict):
        return []
    out = []
    for b, rel in bins.items():
        if not (isinstance(b, str) and _BIN_RE.match(b) and isinstance(rel, str)):
            continue
        entry = os.path.join(pkg_dir, *rel.replace("\\", "/").split("/"))
        if os.path.isfile(entry):
            out.append({"bin": b, "entry": os.path.relpath(entry, root).replace(os.sep, "/")})
    return out


# ── installing an isolated copy and recording it ─────────────────────────────

def declared_programs(skill: str) -> set[str]:
    """The program names *skill* declares (``requires.bins``, ``allowed-tools``,
    its install specs' ``bins``)."""
    try:
        from puffpai import deps
        needs = deps.read_skill(skill)
    except Exception:  # noqa: BLE001
        return set()
    names = {p["name"] for p in needs.get("programs") or [] if isinstance(p.get("name"), str)}
    names.update(p for p in needs.get("any_programs") or [] if isinstance(p, str))
    for pkg in needs.get("packages") or []:
        names.update(b for b in pkg.get("bins") or [] if isinstance(b, str))
    return {n for n in names if _BIN_RE.match(n)}


def record_installed(step: dict, version: str, skill: str = "",
                     say: Callable[[str], None] = lambda _l: None, *,
                     vault: Any = None) -> list[str]:
    """Install a skill-private copy of package *step* and record its programs
    in the vault. Only for a program *skill* declares and only when *skill*
    has a kept key (else there is no key to protect). Returns the program
    names recorded. Never raises."""
    from puffpai import deps
    from puffpai.security import keys
    if step.get("kind") not in ("npm", "pip") or not (isinstance(skill, str)
                                                       and _SKILL_RE.match(skill)):
        return []
    try:
        if not keys.skill_keys_set(skill):
            return []
        declared = declared_programs(skill)
        package, kind = str(step.get("name") or ""), step["kind"]
        root = iso_root(skill, kind, package)
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        argv = deps.installer_argv(step, version, target=root)
        if argv is None:
            return []
        got = deps._run_installer(argv)  # noqa: SLF001 - the install seam
        if got.get("code") != 0:
            say(f"{package}: BuildBot could not make a private copy to run with your key.")
            return []
        entries = (_pip_entries if kind == "pip" else _npm_entries)(root, package)
        entries = [e for e in entries if e["bin"] in declared]
        if not entries:
            return []
        man = manifest(root)
    except ProgramRefused as exc:
        say(f"{step.get('name')}: not recorded ({exc.reason}).")
        return []
    except Exception as exc:  # noqa: BLE001 - nothing recorded means no key
        logger.info("skill_programs: could not record %s (%s)", step.get("name"),
                    type(exc).__name__)
        return []
    done = []
    for e in entries:
        rec = {"kind": kind, "package": package, "version": str(version or ""),
               "root": root, "manifest": man, **e}
        if keys.put_program_record(skill, e["bin"], rec, vault=vault):
            done.append(e["bin"])
    if entries and not done:
        names = ", ".join(e["bin"] for e in entries)
        say(f"{names}: installed, but your vault was locked, so BuildBot could not record "
            f"it. It runs without your key until it is installed again with the vault open.")
    return done


# ── at run time ──────────────────────────────────────────────────────────────

def candidate(command: str, skill: str) -> Optional[tuple[str, list[str]]]:
    """``(program, args)`` when *command* is ONE plain call of a program
    *skill* declares, else None (then the command is an ordinary keyless one).
    Any shell feature, variable or folder in the program name gives None."""
    from puffpai.security import skill_key_use
    try:
        words = skill_key_use.split_words(command.strip(), set())
    except skill_key_use.KeyUseRefused:
        return None
    if not words:
        return None
    program = words[0]
    if not _BIN_RE.match(program) or program not in declared_programs(skill):
        return None
    return program, words[1:]


def verify(record: dict) -> None:
    """Re-walk the program's folder and require it to match the recorded
    manifest exactly. Raises :class:`ProgramRefused`."""
    root = record.get("root")
    saved = record.get("manifest")
    if not (isinstance(root, str) and isinstance(saved, dict)):
        raise ProgramRefused("BuildBot's record of the program is incomplete")
    if not os.path.isdir(root):
        raise ProgramRefused("the program is no longer where BuildBot installed it")
    now = manifest(root)
    added = sorted(set(now) - set(saved))
    removed = sorted(set(saved) - set(now))
    changed = sorted(r for r in now if r in saved and not hmac.compare_digest(now[r], saved[r]))
    if added or removed or changed:
        what = (added or removed or changed)[0]
        raise ProgramRefused(f"the program's files changed since BuildBot installed it "
                             f"(for example {what})")
    if record.get("kind") == "pip" and not (_MODULE_RE.match(str(record.get("module") or ""))
                                            and _MODULE_RE.match(str(record.get("attr") or ""))):
        raise ProgramRefused("BuildBot's record of the program is not usable")
    if record.get("kind") == "npm":
        entry = os.path.join(root, *str(record.get("entry") or "").split("/"))
        if not os.path.isfile(entry):
            raise ProgramRefused("the program's start file is missing")


def plan(command: str, skill: str, *, vault: Any = None) -> Optional[dict]:
    """A key run of the skill's own installed program, or None (no such
    program in the command, or it was not recorded: it then runs keyless).
    Raises :class:`ProgramRefused` when the program WAS recorded but no longer
    matches (nothing runs). Needs the vault open (the record is in it)."""
    found = candidate(command, skill)
    if found is None:
        return None
    program, args = found
    from puffpai.security import keys
    record = keys.program_record(skill, program, vault=vault)
    if record is None:
        return None
    verify(record)
    return {"kind": "program", "program": program, "package": record.get("package", ""),
            "version": record.get("version", ""), "args": args, "record": record}


#: Exit code and line when a pip program needs something outside its own copy
#: (site-packages are never loaded for a key run).
NEEDS_SITE_EXIT = 97
NEEDS_SITE_LINE = ("This program needs Python packages outside its own folder, so it "
                   "cannot run with your key.")


def _pip_boot(record: dict, run_dir: str) -> str:
    """Code for ``python -I -S -B -c``: no ``site`` import at all (so no
    site-packages and no ``.pth`` file ever load), ``sys.path`` set to the
    stdlib entries Python starts with plus the verified run copy, then the
    recorded entry is called."""
    return ("import sys\n"
            "sys.path[:]=[p for p in sys.path if p and 'site-packages' not in p.lower()"
            " and 'dist-packages' not in p.lower()]"
            f"+[{run_dir!r}]\n"
            f"sys.argv[0]={record['bin']!r}\n"
            "import importlib\n"
            "try:\n"
            f" f=importlib.import_module({record['module']!r})\n"
            "except ImportError:\n"
            f" print({NEEDS_SITE_LINE!r},file=sys.stderr);sys.exit({NEEDS_SITE_EXIT})\n"
            f"for _p in {record['attr']!r}.split('.'):f=getattr(f,_p)\n"
            "r=f()\n"
            "sys.exit(r if isinstance(r,int) else 0)")


def _read_nofollow(path: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_FILE:
                raise ProgramRefused("the program folder has a file that is too large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def materialize(record: dict) -> str:
    """Copy the recorded files into a fresh run folder and return it. Each
    file's bytes are read ONCE into memory, hashed, compared with the
    manifest, and those same bytes are written to the copy, so what was
    checked is exactly what runs. The original folder is never executed from.
    Raises :class:`ProgramRefused` (nothing runs) on any mismatch."""
    import tempfile
    from puffpai import deps
    root, saved = record.get("root"), record.get("manifest")
    if not (isinstance(root, str) and isinstance(saved, dict) and saved):
        raise ProgramRefused("BuildBot's record of the program is incomplete")
    real_root = os.path.realpath(root)
    base = deps.tools_dir("keycli-run")
    os.makedirs(base, exist_ok=True)
    run_dir = tempfile.mkdtemp(prefix="run-", dir=base)    # random name, atomic, 0700
    try:
        for rel, want in saved.items():
            parts = [p for p in str(rel).split("/") if p]
            if not parts or any(p in (".", "..") for p in parts) or not isinstance(want, str):
                raise ProgramRefused("BuildBot's record of the program is not usable")
            src = os.path.join(real_root, *parts)
            if os.path.normcase(os.path.realpath(src)) != os.path.normcase(src):
                raise ProgramRefused("the program folder has a symbolic link")
            try:
                data = _read_nofollow(src)
            except OSError:
                raise ProgramRefused(f"the program's files changed since BuildBot installed "
                                     f"it (for example {rel})") from None
            if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), want):
                raise ProgramRefused(f"the program's files changed since BuildBot installed "
                                     f"it (for example {rel})")
            dst = os.path.join(run_dir, *parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                         0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
    except BaseException:
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return run_dir


def argv_for(run_plan: dict) -> list[str]:
    """The exact argv, run from a verified copy (:func:`materialize`). The
    copy's folder is kept in ``run_plan["run_dir"]``; the caller deletes it."""
    record = run_plan["record"]
    kind = record.get("kind")
    if kind == "npm":
        from puffpai.security import host_programs
        node = host_programs.path("node")
        if not node:
            raise ProgramRefused("this computer has no trusted node to run it with")
    elif kind == "pip":
        python = sys.executable
        if not python or not os.path.isabs(python):
            raise ProgramRefused("BuildBot's own Python could not be found")
        if not (_MODULE_RE.match(str(record.get("module") or ""))
                and _MODULE_RE.match(str(record.get("attr") or ""))):
            raise ProgramRefused("BuildBot's record of the program is not usable")
    else:
        raise ProgramRefused("BuildBot does not know how to start that program")
    run_dir = materialize(record)
    run_plan["run_dir"] = run_dir
    if kind == "npm":
        entry = os.path.join(run_dir, *str(record["entry"]).split("/"))
        if not os.path.isfile(entry):
            raise ProgramRefused("the program's start file is missing")
        return [node, entry, *run_plan["args"]]
    return [python, "-I", "-S", "-B", "-c", _pip_boot(record, run_dir), *run_plan["args"]]


def run_folder(skill: str) -> str:
    """A fresh folder under ``workspace/made/<skill>`` for one run."""
    from puffpai import brain_commands
    base = brain_commands.made_dir(skill, create=True)
    folder = os.path.join(base, time.strftime("run-%Y%m%d-%H%M%S-") + secrets.token_hex(3))
    os.makedirs(folder)
    return folder


__all__ = ["ProgramRefused", "argv_for", "candidate", "declared_programs", "iso_root",
           "manifest", "plan", "record_installed", "run_folder", "verify"]
