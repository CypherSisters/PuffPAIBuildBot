"""``run_command``: the tool loop runs a command, after her yes for each one.

Many skills run small scripts. Like Hermes Agent's default approval mode
(``tools/approval.py``, MIT, see THIRD_PARTY_NOTICES.md), every command is
shown to the owner exactly as it will run, with the folder it runs in, and
runs only after she says yes. There is no allow list and no "yes to all".

Where it runs:

* a skill's own folder, ``<state>/skills/<name>``, when the model names the
  skill, or when the command names a file that only that skill's folder has
  (``python3 scripts/add_spool.py``); or
* the work folder ``<state>/workspace`` (made on first use).

A skill's folder holds what was installed and nothing else: a file a command
makes or changes there (that the install did not pin) is moved afterwards to
``<state>/workspace/made/<skill>/``, where the skill's page shows it
(:func:`move_made`). Its caches (``__pycache__``, ``node_modules``, dot
folders) stay.

Never in the vault folder, and a command that names the vault folder or a
key file is refused before she is asked: by name, by a wildcard that fits a
key file's name, or by a path (relative to the folder it runs in, ``~``
being the work folder) that leads into the vault, to a key file, or to a
folder that holds them (``..`` from the work folder is the state folder). The command gets this computer's
environment without anything whose name says key, token, secret, password
or PIN. It stops after :data:`COMMAND_SECONDS`.

How it runs (copied from Hermes Agent ``tools/environments/local.py``):
skills are written for bash, so on Windows a command runs in Git for
Windows' bash (never ``System32\\bash.exe``, which starts Linux under WSL),
with Git's own tools first on the path. ``python3`` and ``python`` start the
bot's own Python (on Windows ``python3`` is otherwise a Store stub). ``HOME``
is the work folder, so a script that writes to ``~/...`` stays there. Known
secrets (the bot's API key, the machine key, tool passwords, anything shaped
like a Home Assistant token) are hidden in what a command prints before the
model sees it.

A skill's own key (a known risk recorded in SECURITY.md): keys always live in the vault, and a key reaches a command only
through ONE checked web request. A plain command in a skill's folder gets NO
key, even the skill's own scripts. No command in a skill folder is handed the
skill's keys just for being there.

When the command NAMES the skill's key (``$X``, ``${X}``, ``%X%``, ``$env:X``)
and :mod:`puffpai.security.skill_key_use` shows it is one curl, wget or httpie
request whose every address is the skill's own service (its declared homepage
or API base, or a host in its own code example next to the key), the request
is run DIRECTLY, never through a shell: the program is ``curl``/``wget``/
``httpie`` at a fixed system path (``host_programs.web_program``, never ``PATH``,
never the tools or skill folder), and the key value is placed by us into the
one argument it belongs in. So no planted program and no shell trick can take
it. The Yes card says which key and which host. The value, and its base64,
url-encoded, hex and JSON forms, is hidden as ``[hidden key]`` in the output
(scrubbed before any trimming). With the vault locked nothing runs; she is
asked to open it. One security log line names the skill, the key name and the
host. Anything else that names the key is refused with a plain reason.

The second key path: a skill's own command line program that reads its key
from its environment (for example ``runcomfy``). When the command is ONE
plain call of a program the skill declares (no shell features at all), and
BuildBot's installer recorded that program in the vault (entry script path
and sha256, :mod:`puffpai.security.skill_programs`), it runs directly (argv,
no shell) as ``node <entry>`` or BuildBot's own Python loading the entry,
the hash checked again right before it starts, with a small fixed
environment plus that skill's own keys, in a fresh folder under
``workspace/made/<skill>``. A changed program gets no key and does not run;
a program BuildBot did not record runs keyless. The Developer terminal never
gets these keys.

A command that fails because a program, a package or a key is missing
("command not found", "is not recognized", ``ModuleNotFoundError``) comes
back with ``missing``: the "Install what's missing" card (:func:`missing_card`,
:mod:`puffpai.deps`), shown to her with one Yes, never run by the model.
What that card installed is first on a command's path (``<state>/tools``).

The WSL locked box (``puffpai.box``) is parked: commands run here, as her.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import shutil
import subprocess

from puffpai import procflags
import sys
import tempfile
from typing import Any, Optional

from puffpai import brain_tools
from puffpai.logging_config import get_logger
from puffpai.security import skill_key_use
from puffpai.security.skill_key_use import HIDDEN_KEY

logger = get_logger(__name__)

COMMAND_SECONDS = 120
MAX_COMMAND = 2000
MAX_OUTPUT = 4000
WORKSPACE = "workspace"
SKILLS = "skills"
SHIMS = "command_bin"
#: Where files a command makes in a skill's folder are moved:
#: ``<state>/workspace/made/<skill>`` (its page's gallery reads it).
MADE = "made"
#: Folders a skill keeps for itself (caches); what lands there stays.
_KEEP_DIRS = frozenset(("__pycache__", "node_modules", ".venv", "venv"))

_SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PIN|CREDENTIAL|AUTH)")
#: File names a command may never name (the vault's files and key files).
_SECRET_FILES = ("vault_data", "api_key.txt", "voice_key", ".vault", "vault.meta",
                 "vault.salt", "vault.enc", "keyring", "machine_key", "service_key",
                 # her own programs and their Always allow switch: changed only
                 # from a paired device (programs_api), never by a command
                 "programs.json")
#: What stands in for a secret in a command's output.
HIDDEN = "[a secret, hidden]"
#: Home Assistant tokens and other JSON web tokens.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
#: First words that start Linux (WSL) on Windows when Git's bash is missing.
_WSL_WORDS = ("bash", "bash.exe", "sh", "wsl", "wsl.exe")
#: Words for a command that did not work, so the model never says it was done.
FAILED_NOTE = ("The command did not work (exit code {code}). Nothing it was meant to do "
               "is done. Tell the owner plainly, with the reason from the output.")

DESCRIPTION = (
    "Run one command on this computer, for example a script a skill tells you "
    "to run. The owner sees the exact command and must say yes first, every "
    "time. Set skill to the skill's name to run it in that skill's own folder "
    "(always do this for a skill's scripts); otherwise it runs in a work "
    "folder. A file it makes in a skill's folder is moved to the work folder "
    "afterwards (the skill's page shows it); write new files to ~/ to keep "
    "them there. Commands run in bash and python3 works. When one job needs "
    "several commands, join them with && in one command. The output and "
    "whether it worked come back to you.")
SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string",
                    "description": "The exact command line to run."},
        "skill": {"type": "string",
                  "description": "The skill whose folder to run it in (optional)."},
    },
    "required": ["command"],
    "additionalProperties": False,
}


def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir(create=True)


def _vault_dir() -> str:
    try:
        from puffpai.security.vault import _default_vault_dir
        return os.path.abspath(_default_vault_dir())
    except Exception:  # noqa: BLE001
        return ""


def _inside(path: str, folder: str) -> bool:
    if not folder:
        return False
    path = os.path.normcase(os.path.abspath(path))
    folder = os.path.normcase(os.path.abspath(folder))
    return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)


def _workspace() -> str:
    folder = os.path.join(_state_dir(), WORKSPACE)
    os.makedirs(folder, exist_ok=True)
    return os.path.abspath(folder)


# -- which folder ---------------------------------------------------------------

def _skill_folder(name: str) -> Optional[str]:
    """The folder of the installed skill *name* (its folder or its own name)."""
    try:
        from puffpai import skills
        rec = skills.find(name)
    except Exception:  # noqa: BLE001
        rec = None
    if rec is not None:
        return os.path.dirname(rec["path"])
    if _SKILL_RE.match(name):
        folder = os.path.join(_state_dir(), SKILLS, name)
        if os.path.isdir(folder):
            return folder
    return None


def _skill_for_command(command: str, workspace: str) -> Optional[str]:
    """The one installed skill whose folder holds a file the command names
    (``scripts/add_spool.py``) when the work folder does not. Small models
    often forget to name the skill; its script says which one it is."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    rels = [w.replace("\\", "/") for w in words
            if ("/" in w or "\\" in w) and not w.startswith(("/", "~", "-", "$"))
            and ":" not in w and ".." not in w]
    if not rels:
        return None
    try:
        from puffpai import skills
        folders = [os.path.dirname(r["path"]) for r in skills.installed()]
    except Exception:  # noqa: BLE001
        return None
    hits = []
    for folder in folders:
        for rel in rels:
            parts = rel.split("/")
            if os.path.exists(os.path.join(folder, *parts)) and \
                    not os.path.exists(os.path.join(workspace, *parts)):
                hits.append(folder)
                break
    return hits[0] if len(hits) == 1 else None


def folder_for(args: dict) -> str:
    """The folder this command runs in. Raises ValueError with plain words."""
    skill = str(args.get("skill") or "").strip()
    if skill:
        if len(skill) > 80 or ".." in skill or "/" in skill or "\\" in skill:
            raise ValueError(f"{skill!r} is not a skill name")
        folder = _skill_folder(skill)
        if folder is None:
            raise ValueError(f"no skill called {skill} is installed")
    else:
        folder = _workspace()
        command = args.get("command")
        if isinstance(command, str):
            folder = _skill_for_command(command, folder) or folder
    if _inside(folder, _vault_dir()) or _reaches(folder, _protected()):
        raise ValueError("commands never run in the vault folder")
    return os.path.abspath(folder)


NO_SECRETS_LINE = "commands never touch the vault or key files"
_GLOB_CHARS = re.compile(r"[*?\[]")
#: Where one word of a command ends (shell operators, assignments, quotes).
_WORD_SPLIT = re.compile(r"[\s;&|<>()`=,{}]+|\$\(")


def _protected() -> list[str]:
    """The vault folder and the key files, as full paths."""
    found = [_vault_dir()]
    state = _state_dir()
    found += [os.path.join(state, name) for name in _SECRET_FILES]
    try:
        from puffpai.security import api_key_store
        found.append(api_key_store.key_file())
    except Exception:  # noqa: BLE001
        pass
    try:
        from puffpai.security import vault
        found += [str(path) for _label, path in vault.service_key_candidates()]
    except Exception:  # noqa: BLE001
        pass
    return [os.path.normcase(os.path.abspath(p)) for p in found if p]


def _reaches(path: str, protected: list[str]) -> bool:
    """Is *path* a protected one, inside one, or a folder that holds one?"""
    path = os.path.normcase(os.path.abspath(path))
    for p in protected:
        if path == p or p.startswith(path.rstrip("\\/") + os.sep) or \
                path.startswith(p.rstrip("\\/") + os.sep):
            return True
    return False


def _as_path(word: str, folder: str) -> Optional[str]:
    """*word* as a full path when it looks like one, else None."""
    if not word or "://" in word or word.startswith("-") or word == "/":
        return None          # a bare / is far more often a division than a path
    if not (word in (".", "..", "~") or "/" in word or word.startswith("~")):
        return None
    if word.startswith("~"):
        word = _workspace() + word[1:]
    elif os.name == "nt" and re.match(r"^/[A-Za-z](/|$)", word):
        word = word[1] + ":/" + word[3:]          # Git Bash's /c/Users/...
    return os.path.join(folder, word)


def check_command(command: Any, folder: Optional[str] = None) -> str:
    """The command, or ValueError when it may not run at all. With *folder*
    (where it runs), the paths it names are checked as well."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("there is no command to run")
    command = command.strip()
    if len(command) > MAX_COMMAND or "\x00" in command:
        raise ValueError("that command is too long")
    low = command.lower().replace("\\", "/")
    vault = _vault_dir().lower().replace("\\", "/")
    if (vault and vault in low) or any(name in low for name in _SECRET_FILES):
        raise ValueError(NO_SECRETS_LINE)
    if folder is None:
        return command
    protected = _protected()
    names = {os.path.basename(p) for p in protected}
    for word in _WORD_SPLIT.split(command.replace("\\", "/")):
        word = word.strip("\"'")
        if _GLOB_CHARS.search(word):
            base = word.rsplit("/", 1)[-1].lower()
            literal = _GLOB_CHARS.sub("", base)
            if len(literal) >= 3 and any(fnmatch.fnmatch(n, base) for n in names):
                raise ValueError(NO_SECRETS_LINE)      # api_key.tx? and the like
        path = _as_path(word, folder)
        if path is None:
            continue
        hits = glob.glob(path) if _GLOB_CHARS.search(word) else []
        for one in [path, *hits]:
            if _reaches(one, protected):
                raise ValueError(NO_SECRETS_LINE)
    return command


# -- the shell ------------------------------------------------------------------

def _is_wsl_bash(path: str) -> bool:
    low = os.path.normcase(os.path.abspath(path))
    return any(part in low for part in ("system32", "sysnative", "windowsapps"))


def git_bash() -> Optional[str]:
    """Git for Windows' ``bash.exe`` from where its installer puts it, or None
    (and always None off Windows). Never looked up by name on the path (the
    trusted program rule). ``PUFFPAI_BASH`` names another full path."""
    if os.name != "nt":
        return None
    wanted = os.environ.get("PUFFPAI_BASH", "").strip()
    if wanted:
        return wanted if os.path.isfile(wanted) and not _is_wsl_bash(wanted) else None
    found: list[str] = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                 os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base and os.path.isabs(base):
            found.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for path in found:
        if os.path.isfile(path) and not _is_wsl_bash(path):
            return os.path.abspath(path)
    return None


def _posix_bash() -> Optional[str]:
    for path in ("/bin/bash", "/usr/bin/bash"):
        if os.path.isfile(path):
            return path
    return None


def shell_name() -> str:
    """The shell commands run in, in plain words."""
    if os.name != "nt":
        return "bash" if _posix_bash() else "the system shell"
    return "Git Bash" if git_bash() else "the Windows command prompt"


def _git_bin_dirs(bash: str) -> list[str]:
    """Git's tool folders, mingw first, so ``bash``, ``cat`` and friends are
    Git's own and never the System32 look-alikes."""
    parent = os.path.dirname(os.path.dirname(bash))
    root = os.path.dirname(parent) if os.path.basename(parent).lower() == "usr" else parent
    dirs = (os.path.join(root, *sub.split("/"))
            for sub in ("mingw64/bin", "usr/local/bin", "usr/bin", "bin"))
    return list(dict.fromkeys(d for d in dirs if os.path.isdir(d)))


def shim_dir() -> str:
    """A folder holding ``python3`` and ``python`` that start the bot's own
    Python, and ``puffpai`` that starts this bot's own command line, with
    this bot's state folder whatever ``HOME`` the command runs with."""
    folder = os.path.join(_state_dir(), SHIMS)
    os.makedirs(folder, exist_ok=True)
    exe = sys.executable or "python"
    texts: dict[str, str] = {}
    for name in ("python3", "python"):
        texts[name] = '#!/bin/sh\nexec "{}" "$@"\n'.format(exe.replace("\\", "/"))
        if os.name == "nt":
            texts[name + ".cmd"] = '@"{}" %*\r\n'.format(exe)
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state = _state_dir()
    texts["puffpai"] = (
        '#!/bin/sh\nPUFFPAI_STATE_DIR="{}" PYTHONPATH="{}" exec "{}" -m puffpai "$@"\n'
        .format(state.replace("\\", "/"), package_root.replace("\\", "/"),
                exe.replace("\\", "/")))
    if os.name == "nt":
        texts["puffpai.cmd"] = ('@set "PUFFPAI_STATE_DIR={}"\r\n@set "PYTHONPATH={}"\r\n'
                                '@"{}" -m puffpai %*\r\n'.format(state, package_root, exe))
    for name, text in texts.items():
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if os.name != "nt":
            os.chmod(path, 0o755)
    return folder


def _first_word(command: str) -> str:
    word = command.strip().split(None, 1)[0] if command.strip() else ""
    return os.path.basename(word.strip("\"'")).lower()


def _check_shell(command: str) -> None:
    """On Windows without Git's bash, ``bash`` would start Linux (WSL)."""
    if os.name == "nt" and not git_bash() and _first_word(command) in _WSL_WORDS:
        raise ValueError("this command needs bash, and this computer has no Git Bash. "
                         "Here bash would start Linux (WSL), which BuildBot does not use. "
                         "Installing Git for Windows adds Git Bash")


#: Why a remote skill or a JSON tool call never goes to the shell.
NOT_A_COMMAND_JSON = ("that is a tool call written as JSON, not a command. Nothing was run. "
                      "Use the tool it belongs to if one is listed; otherwise tell the owner "
                      "it needs to be connected first")


_CMD_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://\[?([A-Za-z0-9.-]+)")


def _build_remote_skills() -> list[tuple[str, list[str]]]:
    """``[(skill, hosts)]`` for each installed skill the build uses as a
    remote API (a TOOL or DATA part that runs as an outside service). A skill
    whose own single declared request is the approved key path is not
    one: chat keeps that path. Never raises."""
    try:
        from puffpai import skills
        from puffpai.build import schema as S
        from puffpai.build import sources
        build = S.load_build()
    except Exception:  # noqa: BLE001 - no build: nothing classified
        return []
    out: list[tuple[str, list[str]]] = []
    for part in (build.get("parts") or {}).values():
        try:
            if not isinstance(part, dict) or part.get("type") != "skill" \
                    or part.get("class") not in (S.TOOL, S.DATA):
                continue
            name = str(part.get("folder") or part.get("skill") or "")
            rec = skills.find(name) if name else None
            if not rec:
                continue
            path = str(rec["path"])
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(512 * 1024)
            here = os.path.dirname(path)
            if skills.key_request_service(text, here):
                continue                       # the approved single request
            how = skills.how_it_runs_text(text, here)
            if how["kind"] != "remote":
                continue
            hosts = list(dict.fromkeys([str(h).lower() for h in how.get("hosts") or []]
                                       + sources.skill_calls(text, here)))
            if hosts:
                out.append((str(rec.get("name") or name), hosts))
        except Exception:  # noqa: BLE001 - an unreadable skill is not one
            continue
    return out


def remote_api_owner(command: str) -> str:
    """The skill a command's address belongs to, when that skill is a remote
    API the build uses (:func:`_build_remote_skills`), else "". An address in
    the command (any scheme), or the skill's host written bare, on the same
    site (registrable domain) as one of the skill's hosts counts."""
    owners = _build_remote_skills()
    if not owners:
        return ""
    text = str(command or "")
    reg = skill_key_use.registrable
    named = {reg(m.group(1).lower().rstrip(".")) for m in _CMD_URL.finditer(text)
             if "." in m.group(1)}
    for skill, hosts in owners:
        for h in hosts:
            if reg(h) in named or re.search(r"(?i)(?<![A-Za-z0-9.-])" + re.escape(h)
                                            + r"(?![A-Za-z0-9-])", text):
                return skill
    return ""


def check_not_remote(command: str, args: dict, folder: str) -> None:
    """Refuse (ValueError) a command that is not one: JSON (a tool call
    copied from a skill's text) or a command for a skill that is an outside
    service (:func:`puffpai.skills.is_remote`). Such a skill is a TOOL or a
    DATA source, never a shell command."""
    text = command.strip()
    if text[:1] in ("{", "["):
        import json
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            raise ValueError(NOT_A_COMMAND_JSON)
    owner = remote_api_owner(command)
    if owner:
        raise ValueError(f"that address belongs to the skill {owner}, an outside service your "
                         f"bot already uses through its own tool, so it is not run as a "
                         f"command. Nothing was run. Use the {owner} tool instead")
    named = str(args.get("skill") or "").strip()
    try:
        from puffpai import skills
        for name in dict.fromkeys(n for n in (named, _skill_of_folder(folder)) if n):
            if skills.is_remote(name):
                how = skills.how_it_runs(name)
                raise ValueError(f"the skill {name} is an outside service, not a program on "
                                 f"this computer, so it never runs as a command. "
                                 f"{how['why']} Nothing was run. Use its connected tool if "
                                 f"one is listed; otherwise tell the owner it needs to be "
                                 f"connected first")
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - an unreadable skill is not a remote one
        return


def question(args: dict, ctx: dict) -> dict:
    """What she is asked: the exact command and its folder."""
    command = check_command(args.get("command"))
    _check_shell(command)
    folder = folder_for(args)
    check_command(command, folder)
    check_not_remote(command, args, folder)
    own = _own_program(command)
    if own and own[0]["always_allow"]:
        # Her choice (Always allow, set from a paired device): a plain call
        # of that one program runs without asking, by its full path, no
        # shell, logged (programs.run_cli). Anything else still asks.
        return None
    skill = _skill_of_folder(folder)
    where = f"in the folder of the skill {skill}" if skill else "in the bot's work folder"
    detail = f"It runs on this computer in {shell_name()}, {where}."
    if own:
        detail = (f"It runs your own program {own[0]['name']} ({own[0]['path']}) directly, "
                  f"not through the command line, {where}.")
    _names, use = key_plan(skill, command)
    out = {"tool": "run_command", "title": "May I run this command?",
           "command": command, "folder": folder, "detail": detail}
    if use and use.get("kind") == "program":
        names = ", ".join(use["names"])
        pkg = use.get("package") or use["program"]
        ver = f" {use['version']}" if use.get("version") else ""
        detail += (f" It gives your {skill} key ({names}) to {use['program']} (from "
                   f"{pkg}{ver}, installed by BuildBot) for this one run. It runs on its "
                   f"own, not through the command line, in a new folder.")
        out["key_use"] = {"names": list(use["names"]), "program": use["program"],
                          "package": use.get("package", ""), "version": use.get("version", "")}
        out["detail"] = detail
        return out
    if use:
        from puffpai.security import keys
        if not keys._is_open(keys._vault()):  # noqa: SLF001
            # Asking first would be a yes to a command that cannot run.
            raise ValueError(keys.locked_skill_sentence(skill, use["names"]))
        hosts = " and ".join(use["hosts"])
        detail += (f" The command names {', '.join(use['names'])}: it uses your {skill} "
                   f"key from the vault for this one run, sent only to {hosts}. The request "
                   f"runs on its own, not through the command line.")
        out["key_use"] = {"names": list(use["names"]), "hosts": list(use["hosts"])}
        out["detail"] = detail
    return out


def _own_program(command: str) -> Optional[tuple[dict, list[str]]]:
    """``(row, arguments)`` when the command is one plain call of a command
    line program the owner added herself (:mod:`puffpai.programs`)."""
    try:
        from puffpai import programs
        return programs.match(command)
    except Exception:  # noqa: BLE001 - an unreadable list is no program of hers
        return None


def _skill_of_folder(folder: str) -> str:
    """The skill folder's name when *folder* is one, else ""."""
    skills_root = os.path.join(_state_dir(), SKILLS)
    if not _inside(folder, skills_root) or os.path.normcase(os.path.abspath(folder)) == \
            os.path.normcase(os.path.abspath(skills_root)):
        return ""
    rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(skills_root))
    return rel.replace("\\", "/").split("/", 1)[0]


def _declared_keys(skill: str) -> list[str]:
    """The keys *skill* may get: the ones it declares and the ones its text
    names (``puffpai.deps.key_names``), the same list its page lets her keep."""
    if not skill:
        return []
    try:
        from puffpai import deps
        return deps.key_names(skill)
    except Exception:  # noqa: BLE001 - an unreadable skill declares nothing
        return []


def _kept_keys(skill: str) -> list[str]:
    """The declared keys of *skill* that she kept in her vault (names only)."""
    declared = _declared_keys(skill)
    if not declared:
        return []
    try:
        from puffpai.security import keys
        return [n for n in keys.skill_keys_set(skill) if n in declared]
    except Exception:  # noqa: BLE001
        return []


def key_plan(skill: str, command: str = "") -> tuple[list[str], Optional[dict]]:
    """``(key names the command uses, use)`` for one command in *skill*'s
    folder. *use* is the checked key-run plan
    (``{"names", "hosts", "program", "argv"}``) when the command names a kept
    key and :func:`puffpai.security.skill_key_use.check` allows it, else
    ``([], None)``.

    The contract: a key reaches a command only through a
    checked key run. A command that does NOT name a key gets no key, even in
    the skill's own folder. So keyless commands always return ``([], None)``.
    Raises ValueError with a plain reason when a named-key command may not
    run."""
    kept = _kept_keys(skill)
    if not kept:
        return [], None
    named = skill_key_use.references(command, kept)
    if not named:
        return _program_plan(skill, command, kept)
    shown = ", ".join(named)
    try:
        use = skill_key_use.check(command, skill, kept)
        skill_key_use.trusted_program(use["program"])     # resolvable now, fail closed
    except skill_key_use.KeyUseRefused as exc:
        raise ValueError(f"this command names the key {shown}, and a skill's key may go only "
                         f"to the skill's own service, in one checked request. It was refused "
                         f"because {exc.reason}. Nothing was run") from None
    use["kind"] = "request"
    return use["names"], use


def _program_plan(skill: str, command: str, kept: list[str]) -> tuple[list[str], Optional[dict]]:
    """(ii): the skill's own program, installed and recorded by BuildBot, run
    with the skill's keys. ``([], None)`` when the command is not one plain
    call of such a program (it then runs keyless)."""
    from puffpai.security import keys, skill_programs
    if skill_programs.candidate(command, skill) is None:
        return [], None
    if not keys._is_open(keys._vault()):  # noqa: SLF001
        # The record is in the vault; without it we cannot tell, so ask.
        raise ValueError(keys.locked_skill_sentence(skill, kept))
    try:
        use = skill_programs.plan(command, skill)
    except skill_programs.ProgramRefused as exc:
        raise ValueError(f"{exc.reason}, so it does not get your {skill} key. "
                         f"Nothing was run") from None
    if use is None:
        return [], None
    use["names"] = list(kept)
    return use["names"], use


def skill_key_names(skill: str, command: str = "") -> list[str]:
    """The kept keys a checked key run in *skill* would use for *command*
    (names only). ``[]`` for a command that names no key. Raises ValueError
    when a named-key command may not run."""
    return key_plan(skill, command)[0]


def _skill_key_values(skill: str, use: dict) -> dict[str, str]:
    """The values for a checked key run's keys. Raises ValueError (vault
    locked, nothing runs) or when a key has gone (fail closed)."""
    from puffpai.security import keys
    try:
        values = keys.skill_keys(skill, use["names"])
    except keys.KeyUnavailable as exc:
        raise ValueError(exc.message) from None
    missing = [n for n in use["names"] if not values.get(n)]
    if missing:
        raise ValueError(f"the key {', '.join(missing)} is no longer in your vault, so "
                         f"nothing was run")
    return values


def skill_env(skill: str, command: str = "") -> dict[str, str]:
    """Deprecated shim: a skill's keys now reach only a checked key run (see
    :func:`key_plan`), never the general environment. Always ``{}``."""
    return {}


# -- the environment and the output ---------------------------------------------

def clean_env() -> dict[str, str]:
    """This computer's environment minus anything that looks like a secret."""
    return {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}


def command_env(bash: Optional[str] = None) -> dict[str, str]:
    """:func:`clean_env` with ``HOME`` in the work folder, and the bot's
    Python (and Git's tools, when the shell is Git's bash) first on the path."""
    env = clean_env()
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    try:
        from puffpai import deps
        tools = deps.command_paths()     # what "Install what's missing" put here
    except Exception:  # noqa: BLE001
        tools = {"path": [], "pythonpath": [], "node_path": [], "host_path": []}
    first = [shim_dir()] + tools["path"] + (_git_bin_dirs(bash) if bash else [])
    # Apps set up on this computer (integrate.run_here): their command line
    # tools are found, but LAST, so their own Python never shadows a program.
    last = list(tools.get("host_path") or [])
    env[path_key] = os.pathsep.join(first + [env.get(path_key, "")] + last)
    for var, dirs in (("PYTHONPATH", tools["pythonpath"]), ("NODE_PATH", tools["node_path"])):
        if dirs:
            env[var] = os.pathsep.join(dirs + ([env[var]] if env.get(var) else []))
    home = _workspace()
    env["HOME"] = home
    if os.name == "nt":
        env["USERPROFILE"] = home
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _known_secrets() -> list[str]:
    """The secret values this bot holds that a command could print."""
    found: list[str] = []
    api = sys.modules.get("puffpai.api_server")
    found.append(str(getattr(api, "_API_KEY", "") or ""))
    found.append(os.environ.get("PUFFPAI_API_KEY", ""))
    try:
        with open(os.path.join(_state_dir(), "api_key.txt"), encoding="utf-8") as fh:
            found.append(fh.read(4096).strip())
    except OSError:
        pass
    try:
        from puffpai.security import vault
        found.append(str(vault.read_service_key(_vault_dir() or None)[0] or ""))
    except Exception:  # noqa: BLE001
        pass
    try:
        from puffpai.security import keys
        for name in keys._tool_names():  # noqa: SLF001
            for value in (keys.tool_secret(name) or {}).values():
                found.append(str(value))
                if str(value).lower().startswith("bearer "):
                    found.append(str(value)[7:].strip())
    except Exception:  # noqa: BLE001
        pass
    return sorted({s for s in found if len(s) >= 8}, key=len, reverse=True)


def hide_secrets(text: str, extra: Any = ()) -> str:
    """*text* with every known secret, the *extra* values (a skill's keys
    given to this command, with their base64, url-encoded, hex and JSON
    forms, as :data:`HIDDEN_KEY`), and anything shaped like a token, hidden."""
    more = [str(v) for v in extra or () if len(str(v or "")) >= 4]
    if more:
        text = skill_key_use.scrub(text, more)     # the value and its encodings
    for secret in sorted(set(_known_secrets()), key=len, reverse=True):
        text = text.replace(secret, HIDDEN)
    return _JWT_RE.sub(HIDDEN, text)


#: A screen a command started on this computer: http(s) on 127.0.0.1 or
#: localhost with a port, and a plain path. Nothing else is ever offered.
_LOCAL_URL_RE = re.compile(
    r"\bhttps?://(?:127\.0\.0\.1|localhost):(\d{2,5})(/[A-Za-z0-9._~/%-]*)?(?![\w:@.])")
MAX_OPENS = 3


def local_addresses(text: str) -> list[str]:
    """The local screens (``http://127.0.0.1:<port>/...``) a command printed,
    at most :data:`MAX_OPENS`, each once. Never an outside address."""
    out: list[str] = []
    for match in _LOCAL_URL_RE.finditer(text or ""):
        port = int(match.group(1))
        if not 1 <= port <= 65535:
            continue
        scheme = "https" if match.group(0).startswith("https") else "http"
        url = f"{scheme}://127.0.0.1:{port}{match.group(2) or '/'}"
        if url not in out:
            out.append(url)
        if len(out) >= MAX_OPENS:
            break
    return out


def _remember_screen(folder: str, urls: list[str]) -> None:
    """A skill whose command printed a local screen: its page offers Open."""
    skills_root = os.path.join(_state_dir(), SKILLS)
    if not urls or not _inside(folder, skills_root):
        return
    try:
        from puffpai import things
        things.remember_screen(f"skill-{os.path.basename(folder).lower()}", urls[0])
    except Exception:  # noqa: BLE001 - the command itself is done
        pass


def made_dir(skill: str, create: bool = False) -> str:
    """``<state>/workspace/made/<skill>``: what *skill*'s commands made."""
    folder = os.path.join(_state_dir(), WORKSPACE, MADE, skill)
    if create:
        os.makedirs(folder, exist_ok=True)
    return os.path.abspath(folder)


def _files_of(folder: str) -> dict[str, tuple[int, int]]:
    """``{relative path: (size, modified ns)}`` of *folder*'s files, its
    caches and dot folders left out."""
    out: dict[str, tuple[int, int]] = {}
    for here, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _KEEP_DIRS
                   and not os.path.islink(os.path.join(here, d))]
        for name in files:
            path = os.path.join(here, name)
            if name.startswith(".") or os.path.islink(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            out[os.path.relpath(path, folder).replace(os.sep, "/")] = (
                st.st_size, st.st_mtime_ns)
    return out


def _pinned(skill: str) -> set[str]:
    """The files the install pinned for *skill* (lower case, ``/``)."""
    try:
        from puffpai.finding import install
        files = (install._read_lock().get(skill) or {}).get("files") or {}  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return set()
    return {str(rel).replace("\\", "/").lower() for rel in files}


def _free_name(path: str) -> str:
    """*path*, or ``name-2.ext`` (and so on) when it is taken."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}-{n}{ext}"):
        n += 1
    return f"{stem}-{n}{ext}"


def move_made(skill: str, folder: str, before: dict) -> list[str]:
    """Move what a command made or changed in *skill*'s folder (not a file
    the install pinned) to :func:`made_dir`. Returns the new full paths."""
    pinned = _pinned(skill)
    moved: list[str] = []
    for rel, mark in sorted(_files_of(folder).items()):
        if before.get(rel) == mark or rel.lower() in pinned:
            continue
        target = _free_name(os.path.join(made_dir(skill, create=True), *rel.split("/")))
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(os.path.join(folder, *rel.split("/")), target)
        except OSError as exc:
            logger.warning("run_command: could not move %s out of skill %s: %s",
                           rel, skill, type(exc).__name__)
            continue
        moved.append(target)
    return moved


def _tail(text: str) -> str:
    text = text or ""
    return text if len(text) <= MAX_OUTPUT else "...(start cut)\n" + text[-MAX_OUTPUT:]


def run(args: dict, ctx: dict) -> dict:
    """Run it (only reached after her yes, or for a plain call of her own
    program she set to Always allow: checked again here, never trusted)."""
    command = check_command(args.get("command"))
    own = _own_program(command)
    if not ctx.get("said_yes") and not (own and own[0]["always_allow"]):
        raise ValueError("this command was not approved")
    _check_shell(command)
    folder = folder_for(args)
    check_command(command, folder)
    check_not_remote(command, args, folder)
    if own:
        from puffpai import programs
        return programs.run_cli(own[0], own[1], folder=folder,
                                asked=bool(ctx.get("said_yes")), ctx=ctx)
    skill = _skill_of_folder(folder)
    _names, use = key_plan(skill, command)      # ValueError: refused
    if use and use.get("kind") == "program":
        return _run_skill_program(skill, use, ctx)
    if use:
        return _run_key_request(command, folder, skill, use, ctx)
    # No key is named, so no key is handed over: the command runs in the shell
    # with this computer's environment minus anything secret-shaped.
    bash = git_bash()
    env = command_env(bash)
    return _run_it(command, folder, skill, bash, env, ())


def _request_env() -> dict[str, str]:
    """A small, fixed environment for a direct web request: the system's own
    PATH, a fresh empty home (no ``.curlrc``, ``.wgetrc`` or httpie config),
    and no proxy, no TLS key log. The key is NOT here; it is in the argv."""
    from puffpai.security import host_programs
    env = dict(host_programs.run_kwargs()["env"])
    home = tempfile.mkdtemp(prefix="puffpai-keyrun-")
    env["HOME"] = home
    env["CURL_HOME"] = home
    env["XDG_CONFIG_HOME"] = home
    env["HTTPIE_CONFIG_DIR"] = home
    with open(os.path.join(home, "wgetrc"), "w", encoding="utf-8"):
        pass
    env["WGETRC"] = os.path.join(home, "wgetrc")
    if os.name == "nt":
        env["USERPROFILE"] = home
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_key_request(command: str, folder: str, skill: str, use: dict, ctx: dict) -> dict:
    """Run one checked web request directly (no shell), with the skill's key
    put into the one argument it belongs in. The vault is read here (locked ->
    ValueError, nothing runs); the value never touches a shell, the chat, the
    card or a log."""
    values = _skill_key_values(skill, use)      # ValueError: locked or gone
    argv = skill_key_use.build_argv(use, values)
    _audit_keys_used(skill, sorted(values), ctx, use)
    env = _request_env()
    home = env["HOME"]
    before = _files_of(folder) if skill else {}
    try:
        done = subprocess.run(argv, cwd=folder, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=COMMAND_SECONDS,
                              stdin=subprocess.DEVNULL, shell=False, **procflags.hidden())
        output = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
        result = {"exit_code": done.returncode, "worked": done.returncode == 0,
                  "folder": folder, "output": _finish_output(output.strip(), values)}
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        result = {"exit_code": None, "worked": False, "folder": folder,
                  "output": _finish_output(out, values),
                  "note": f"It was stopped after {COMMAND_SECONDS} seconds, so it did "
                          f"not finish."}
    except OSError as exc:
        result = {"exit_code": None, "worked": False, "folder": folder,
                  "output": "", "note": f"The request could not start ({type(exc).__name__})."}
    finally:
        values.clear()                          # kept for this one run only
        shutil.rmtree(home, ignore_errors=True)
    if skill:
        move_made(skill, folder, before)
    return result


def _run_skill_program(skill: str, use: dict, ctx: dict) -> dict:
    """(ii): run the skill's own recorded program directly (no shell), its
    hash checked again right before it starts, with a small fixed
    environment plus that skill's own keys, in a fresh folder under
    ``workspace/made/<skill>``."""
    from puffpai.security import skill_programs
    values = _skill_key_values(skill, use)      # ValueError: locked or gone
    try:
        argv = skill_programs.argv_for(use)     # verified copy, exec source = checked bytes
    except skill_programs.ProgramRefused as exc:
        values.clear()
        if use.get("run_dir"):
            shutil.rmtree(use["run_dir"], ignore_errors=True)
        raise ValueError(f"{exc.reason}, so it does not get your {skill} key. "
                         f"Nothing was run") from None
    env = _request_env()
    home = env["HOME"]
    if os.name == "nt":
        env["APPDATA"] = home
        env["LOCALAPPDATA"] = home
    env.update(values)                          # this skill's own keys, nothing else
    folder = skill_programs.run_folder(skill)
    _audit_keys_used(skill, sorted(values), ctx, use)
    try:
        done = subprocess.run(argv, cwd=folder, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=COMMAND_SECONDS,
                              stdin=subprocess.DEVNULL, shell=False, **procflags.hidden())
        output = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
        result: dict[str, Any] = {"exit_code": done.returncode,
                                  "worked": done.returncode == 0, "folder": folder,
                                  "output": _finish_output(output.strip(), values)}
        if done.returncode != 0:
            result["note"] = FAILED_NOTE.format(code=done.returncode)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        result = {"exit_code": None, "worked": False, "folder": folder,
                  "output": _finish_output(out, values),
                  "note": f"It was stopped after {COMMAND_SECONDS} seconds, so it did "
                          f"not finish."}
    except OSError as exc:
        result = {"exit_code": None, "worked": False, "folder": folder, "output": "",
                  "note": f"The program could not start ({type(exc).__name__})."}
    finally:
        values.clear()
        env.clear()
        shutil.rmtree(home, ignore_errors=True)
        if use.get("run_dir"):
            shutil.rmtree(use["run_dir"], ignore_errors=True)
    made = sorted(os.path.join(folder, n) for n in os.listdir(folder)) \
        if os.path.isdir(folder) else []
    if made:
        result["made"] = made[:20]
        result["made_note"] = ("Files it made are in the bot's work folder (listed in made); "
                               "the skill's page shows them.")
    return result


def _finish_output(text: str, key_values: Any) -> str:
    """Hide the skill's key (and its encodings) and the known secrets in the
    WHOLE text, then trim: a key cut across the trim boundary cannot leak."""
    text = skill_key_use.scrub(text or "", [v for v in (key_values or {}).values()]
                               if isinstance(key_values, dict) else (key_values or ()))
    return _tail(hide_secrets(text))


def _run_it(command: str, folder: str, skill: str, bash: Optional[str], env: dict,
            given: Any) -> dict:
    """Start the approved (keyless) command in the shell and collect its
    output, the known secrets hidden."""
    how: dict[str, Any]
    if bash:
        how = {"args": [bash, "-c", command], "shell": False}
    elif os.name != "nt" and _posix_bash():
        how = {"args": command, "shell": True, "executable": _posix_bash()}
    else:
        how = {"args": command, "shell": True}
    before = _files_of(folder) if skill else {}
    try:
        done = subprocess.run(how.pop("args"), cwd=folder, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=COMMAND_SECONDS,
                              stdin=subprocess.DEVNULL, **how, **procflags.hidden())
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        if skill:
            move_made(skill, folder, before)
        return {"exit_code": None, "worked": False, "folder": folder,
                "output": _finish_output(out, given),
                "note": f"It was stopped after {COMMAND_SECONDS} seconds, so it did not finish."}
    output = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
    result: dict[str, Any] = {"exit_code": done.returncode, "worked": done.returncode == 0,
                              "folder": folder,
                              "output": _finish_output(output.strip(), given)}
    made = move_made(skill, folder, before) if skill else []
    if made:
        result["made"] = made[:20]
        result["made_note"] = ("Files it made were moved out of the skill's folder to "
                               "the bot's work folder (listed in made); the skill's page "
                               "shows them. Use those paths from now on.")
    opens = local_addresses(result["output"])
    if opens:
        result["open"] = opens
        _remember_screen(folder, opens)
    if done.returncode != 0:
        result["note"] = FAILED_NOTE.format(code=done.returncode)
        card = missing_card(skill, result["output"], done.returncode, command)
        if card:
            result["missing"] = card
            result["note"] = MISSING_NOTE.format(code=done.returncode, line=card["line"])
    return result


#: What the model is told when a command failed for want of a program,
#: package or key: the owner was shown the install card, so no workaround.
MISSING_NOTE = ("The command did not work (exit code {code}). {line} The owner was shown an "
                "\"Install what's missing\" card with one Yes. Tell the owner plainly what is "
                "missing and that the card installs it; do not try to install it with "
                "another command.")


def missing_card(skill: str, output: str, code: Any, command: str = "") -> Optional[dict]:
    """The "Install what's missing" card for a failed command, or None: what
    its output says is missing ("command not found", ``ModuleNotFoundError``
    ...), plus, for a skill, what it needs that is not here (its packages,
    sibling skills and keys not in the vault)."""
    try:
        from puffpai import deps
        found = deps.missing_from_output(output, code, command)
        noticed = any(found.values())
        if not noticed and not skill:
            return None
        plan = deps.make_plan(skill, thing=f"skill-{skill.lower()}" if skill else "",
                              found=found)
    except Exception:  # noqa: BLE001 - a card we cannot make is no card
        return None
    if not noticed and not (plan["steps"] or plan["keys"]):
        return None
    if not (plan["steps"] or plan["keys"] or plan["manual"]):
        return None
    card = deps.public_plan(plan)
    card["line"] = deps.plan_line(plan)
    return card


def _audit_keys_used(skill: str, names: list[str], ctx: dict,
                     use: Optional[dict] = None) -> None:
    """One audit line: which skill got which keys (names, never values), and
    which device said yes. When the command names its key (*use*), one more
    line per key: the skill, the key name and the host it is sent to."""
    try:
        from puffpai.security import paired_devices
        from puffpai.security.audit_log import AuditLog
        device = str((ctx or {}).get("device_id") or "")
        who = {"device": paired_devices.label(device) if device
               else "a daily task or this computer"}
        log = AuditLog()
        log.log(action="run_command_skill_keys", user="owner", result="given",
                details=f"{skill}: {', '.join(names)}", extra=who)
        for name in (use or {}).get("names") or ():
            if use.get("kind") == "program":
                log.log(action="run_command_skill_key_program", user="owner", result="given",
                        details=f"{skill}: {name} given to {use['program']} (installed by "
                                f"BuildBot, hash checked)", extra=who)
                continue
            hosts = " and ".join(skill_key_use.host_for_log(h) for h in use["hosts"])
            log.log(action="run_command_skill_key_sent", user="owner", result="given",
                    details=f"{skill}: {name} sent only to {hosts}", extra=who)
    except Exception:  # noqa: BLE001 - the audit line must never stop the command
        pass


#: Installed command line tools named in run_command's description.
CLI_LISTED = 12


def description() -> str:
    """:data:`DESCRIPTION` plus the installed command line tools, by their
    full commands, so the brain can run one the turn after it is installed."""
    try:
        from puffpai import installed
        cli = installed.cli_commands()
    except Exception:  # noqa: BLE001 - the plain description still works
        cli = {}
    if not cli:
        return DESCRIPTION
    lines = [f"{name}: {cmd}" for name, cmd in list(cli.items())[:CLI_LISTED]]
    return (DESCRIPTION + " Installed command line tools (run them by these commands; the "
            "owner's own programs run best as one plain call, no && or |): "
            + "; ".join(lines) + ".")


def register() -> None:
    """Register run_command (again: its list of installed command line tools
    is read each time, and each chat turn registers it again)."""
    brain_tools.register(
        "run_command", description(), SCHEMA, run, needs_yes=question,
        progress=lambda a: f"Running {str(a.get('command') or '')[:80]} ...",
        when=lambda ctx: ctx.get("caller") in ("owner", "terminal"))


__all__ = ["COMMAND_SECONDS", "HIDDEN", "HIDDEN_KEY", "check_command", "check_not_remote", "clean_env", "command_env",
           "folder_for", "git_bash", "hide_secrets", "made_dir", "move_made", "question", "register", "run",
           "key_plan", "shell_name", "shim_dir", "skill_env", "skill_key_names"]
