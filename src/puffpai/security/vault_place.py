"""Where the vault lives: check a folder, start a vault there, use a vault the
owner already has, or move the vault.

The owner may already have a vault, or want vault data stored somewhere
else, such as their own server or hard drive. The vault is an ordinary folder of
locked files (:mod:`puffpai.security.vault`), so it can live anywhere the
owner can write: this computer (the default), a drive or folder she chooses
(an external drive, a drive she already locks with BitLocker or VeraCrypt, a
network folder on her own server or NAS), or a folder that already holds a
BuildBot vault.

The three answers, one question::

    Where should your vault live?
      On this computer (recommended)
      On a drive or folder I choose
      I already have a BuildBot vault

Every face (the browser wizard, the terminal wizard, the Developer page, the
``puffpai vault location|move`` commands) goes through this module, so the
checks are the same everywhere.

Checks before anything is saved (:func:`check_folder`)
------------------------------------------------------
* a whole path, an ordinary folder (no ``\\\\?\\`` or ``\\\\.\\`` device
  paths), not a bare drive: given ``E:\\`` (or a network share's top,
  ``\\\\mynas\\vault``) the owner is offered a folder ``PuffPAI Vault`` on it,
  because the vault's inbox is the folder's name plus ``.inbox`` beside it;
* the folder is there (or its parent is, and the folder is made) and BuildBot
  can write in it;
* not inside BuildBot's own install folder or BuildBot's own git folder
  (:func:`own_repos`): git could copy the locked files into its history, and
  a copy that leaves the computer is a copy anyone can try PINs against with
  no throttle. The default folder (``vault_data`` in BuildBot's data folder,
  git-ignored) is the one exception, because it is where BuildBot has always
  put it. A git folder that only happens to sit above the chosen folder (a
  whole user folder kept in git) is not BuildBot's and is not refused: that
  would refuse Documents, Desktop and everything else in it;
* not the vault's own folder or its inbox, not inside either;
* on Mac and Linux, not an empty place where a drive attaches with nothing
  attached;
* a new vault or a move only into a folder with no vault in it (a move only
  into an empty folder); "I already have one" only from a folder that holds
  ``vault.meta.json``.

A network folder gets one plain line (:data:`NETWORK_LINE`). If it is not
reachable when BuildBot starts, the vault's own missing-drive rule
(``vault_place.json``) says so and never makes a second vault.

The PIN as the yes
------------------
Moving the vault and using another vault need the PIN. It is checked with
:meth:`Vault.check_pin`, which counts against the vault's own unlock throttle;
a vault in another folder is checked through ONE handle per folder kept for
the life of the process (:func:`_checker`), so asking again with a fresh
handle is never a way around the throttle. Using another vault while one is
in use also needs the PIN of the one in use: otherwise anyone who could put
a vault of their own in a folder could make BuildBot keep the owner's things
in it.

While the remembered vault's drive is not plugged in there is no "PIN of the
one in use" to ask for, so from the browser (or any HTTP route) the only
vault BuildBot may switch to is the SAME vault in a new place (the drive came
back under another letter): the vault opened with its PIN must give the
fingerprint remembered in ``vault_place.json``
(:meth:`Vault.place_fingerprint`, made from the vault's own key, so a copied
``vault.meta.json`` cannot fake it). Another vault, or a new one, is a
choice for someone at this computer: ``puffpai vault location <folder>``,
which asks to confirm (``confirmed_at_terminal``; no route ever passes it).

A move, step by step (:func:`move_vault`)
-----------------------------------------
1. The checks above, then the PIN (the vault's own throttle).
2. The vault is locked, so nothing is written while it moves.
3. The vault folder and its inbox are copied, and every copied file is
   compared with its original (SHA-256).
4. The copy is opened with the PIN. Only if it opens is anything switched.
5. Keys set to keep working while the vault is locked are saved again for
   the new folder: their machine key is tied to the folder's path.
6. ``paths.vault_data_dir`` is written with the settings writer, the place is
   remembered, and the caller reloads the server's vault handle.
7. Anything filed into the old inbox while the copy ran is copied across.
8. The old copy is kept. The owner is told where it is, and deletes it
   herself when she is happy. Nothing here ever deletes an owner's vault.

Nothing here prints, logs or returns a PIN.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import stat
import threading
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: The folder offered on a bare drive or a network share's top.
SUBFOLDER = "PuffPAI Vault"

QUESTION = "Where should your vault live?"

#: (value, label, help) for the three answers, in the order they are offered.
CHOICES: tuple[tuple[str, str, str], ...] = (
    ("here", "On this computer (recommended)",
     "Your vault stays in BuildBot's own folder on this computer."),
    ("folder", "On a drive or folder I choose",
     "An external drive, a drive you already lock (such as BitLocker or "
     "VeraCrypt), or a network folder on your own server or NAS, such as "
     "\\\\mynas\\vault."),
    ("existing", "I already have a BuildBot vault",
     "A folder that already holds a BuildBot vault. It opens with that "
     "vault's PIN; no new vault is made."),
)
CHOICE_VALUES = tuple(c[0] for c in CHOICES)

NETWORK_LINE = ("It must be reachable whenever BuildBot starts; if it is not, "
                "BuildBot will say so and will not make a second vault.")

ENCRYPTED_DRIVE_LINE = ("Unlock that drive yourself first, then BuildBot keeps "
                        "its vault inside it.")

#: The refusal from the browser while the remembered vault is missing.
MISSING_SWITCH_LINE = ("Your vault drive is not plugged in. Plug it in, or "
                       "change vaults from this computer's command line: "
                       "puffpai vault location <folder>.")

NOT_A_VAULT_LINE = ("Password managers and programs that lock a whole drive "
                    "(BitLocker, VeraCrypt, KeePass, Bitwarden, 1Password) "
                    "cannot be used as the vault itself.")

_MAX_PATH_TEXT = 400
_BROWSE_LIMIT = 300
#: Windows ``GetDriveTypeW`` answer for a network drive.
_DRIVE_REMOTE = 4
_HIDDEN_OR_SYSTEM = 0x2 | 0x4


class PlaceRefused(Exception):
    """A folder or a change that cannot be used, with the owner's words."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        out = {"ok": False, "code": self.code, "message": self.message}
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------
# the folders this install knows
# ---------------------------------------------------------------------------

def configured_folder() -> str:
    """The vault folder in the settings now (absolute)."""
    from puffpai.config import get_config
    cfg = get_config()
    vd = cfg.paths.vault_data_dir
    return os.path.abspath(vd if os.path.isabs(vd) else os.path.join(cfg.DATA_DIR, vd))


def default_folder() -> str:
    """Where BuildBot keeps the vault when nobody chose: ``vault_data`` in its
    data folder (git-ignored)."""
    from puffpai.config import PathConfig, get_config
    return os.path.abspath(os.path.join(get_config().DATA_DIR,
                                        PathConfig().vault_data_dir))


def install_folder() -> str:
    from puffpai.config import get_config
    return os.path.abspath(get_config().REPO_ROOT)


def _key(folder: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(folder)))


def same_folder(a: str, b: str) -> bool:
    return _key(a) == _key(b)


def inside(child: str, parent: str) -> bool:
    """True when *child* is *parent* or a folder somewhere under it."""
    c, p = _key(child), _key(parent)
    if c == p:
        return True
    return c.startswith(p.rstrip("\\/") + os.sep)


def inbox_of(folder: str) -> str:
    from puffpai.security import vault_inbox
    return vault_inbox.inbox_dir(folder)


def has_vault(folder: str) -> bool:
    from puffpai.security.vault import _vault_files_present
    try:
        return _vault_files_present(folder)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# what kind of place a path is
# ---------------------------------------------------------------------------

def _drive_type(root: str) -> Optional[int]:
    """Windows' drive type for ``X:\\``, or None off Windows. Tests replace it."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]
    except Exception:
        return None


def is_network(folder: str) -> bool:
    """A network folder: ``\\\\server\\share\\...`` (or ``//server/share``), or
    a Windows drive letter mapped to one."""
    text = str(folder or "")
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    drive, _rest = os.path.splitdrive(text)
    if len(drive) == 2 and drive[1] == ":":
        return _drive_type(drive + "\\") == _DRIVE_REMOTE
    return False


def bare_root(folder: str) -> str:
    """The drive (``E:\\``), the network share's top or ``/`` when *folder*
    is one of those, else ``""``."""
    drive, rest = os.path.splitdrive(folder)
    if rest.strip("\\/"):
        return ""
    if drive:
        if len(drive) == 2 and drive[1] == ":":
            return drive + os.sep
        return drive
    return os.sep


def git_root_of(folder: str) -> Optional[str]:
    """The nearest folder at or above *folder* holding ``.git``, or None."""
    here = os.path.abspath(folder)
    for _ in range(64):
        try:
            if os.path.exists(os.path.join(here, ".git")):
                return here
        except OSError:
            return None
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent
    return None


def _worktree_main(root: str) -> Optional[str]:
    """For a git worktree (``.git`` is a file naming
    ``<main>/.git/worktrees/<name>``), the main checkout ``<main>``."""
    marker = os.path.join(root, ".git")
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(1024)
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("gitdir:"):
            continue
        gitdir = line[len("gitdir:"):].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(root, gitdir)
        gitdir = os.path.normpath(gitdir)
        parts = gitdir.replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[-3] == ".git" and parts[-2] == "worktrees":
            return os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
    return None


def own_repos() -> list[str]:
    """BuildBot's own git folders: the one holding its install folder, when
    that folder IS BuildBot's checkout (the install folder itself, or one
    holding BuildBot's ``src/puffpai``), plus a worktree's main checkout.

    A git folder higher up that is not BuildBot's (a user folder kept in
    git) is not one of these, so folders under it stay usable.
    """
    install = install_folder()
    root = git_root_of(install)
    if not root:
        return []
    if not (same_folder(root, install)
            or os.path.isdir(os.path.join(root, "src", "puffpai"))):
        return []
    out = [root]
    main = _worktree_main(root)
    if main and os.path.isdir(os.path.join(main, "src", "puffpai")):
        out.append(main)
    return out


def _unattached_mount(folder: str) -> bool:
    """Mac and Linux: a place where a drive attaches, with nothing attached.

    ``/media/<user>/<drive>``, ``/run/media/<user>/<drive>``, ``/mnt/<drive>``
    and ``/Volumes/<drive>`` are where removable drives appear. A folder there
    that is not a mount point is the empty attach point, and a vault made in
    it would be on the computer's own disk, not the drive.
    """
    if os.name == "nt":
        return False
    parts = os.path.abspath(folder).split(os.sep)
    depth = {"media": 4, "mnt": 3, "Volumes": 3}
    if len(parts) > 2 and parts[1] == "run" and parts[2] == "media":
        need = 5
    else:
        need = depth.get(parts[1] if len(parts) > 1 else "", 0)
    if not need or len(parts) < need:
        return False
    mount = os.sep.join(parts[:need]) or os.sep
    return os.path.isdir(mount) and not os.path.ismount(mount)


def _clean_text(raw: Any) -> str:
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if len(text) > _MAX_PATH_TEXT or any(not ch.isprintable() for ch in text):
        raise PlaceRefused("not_folder", "That is not a folder BuildBot can use.")
    return text


def _whole_path(raw: Any) -> str:
    """The typed path as an absolute, normal folder path, or a refusal."""
    text = _clean_text(raw)
    if not text:
        raise PlaceRefused("not_folder",
                           "Type the folder where your vault should live.")
    if text.startswith("~"):
        text = os.path.expanduser(text)
    lowered = text.replace("/", "\\")
    if lowered.startswith("\\\\?\\") or lowered.startswith("\\\\.\\"):
        raise PlaceRefused(
            "not_folder", "That kind of path is not a folder BuildBot can use. "
            "Type an ordinary folder, like E:\\PuffPAI Vault or "
            "\\\\mynas\\vault.")
    if not os.path.isabs(text):
        raise PlaceRefused(
            "not_folder", "Type the whole folder path, starting with the drive "
            "(like E:\\PuffPAI Vault) or a network folder (like "
            "\\\\mynas\\vault).")
    return os.path.normpath(os.path.abspath(text))


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _writable(folder: str) -> bool:
    probe = os.path.join(folder, f".puffpai-write-check-{secrets.token_hex(4)}")
    try:
        with open(probe, "wb") as fh:
            fh.write(b"ok")
        os.remove(probe)
        return True
    except OSError:
        try:
            os.remove(probe)
        except OSError:
            pass
        return False


def _has_files(folder: str) -> bool:
    try:
        with os.scandir(folder) as it:
            return any(True for _ in it)
    except OSError:
        return False


def check_folder(raw: Any, *, purpose: str, make_subfolder: bool = False,
                 create: bool = True) -> dict:
    """Is *raw* a folder the vault may live in, for *purpose*?

    *purpose* is ``"new"`` (a new vault will be made there), ``"existing"``
    (a BuildBot vault is already there) or ``"move"`` (the vault is copied
    there). ``make_subfolder`` is the owner's yes to the ``PuffPAI Vault``
    folder on a bare drive. ``create`` makes a missing last folder (its parent
    must be there); with ``create=False`` nothing on disk changes and the
    answer says ``will_make``.

    Returns ``{"ok": True, "folder", "network", "made", "will_make",
    "lines"}``. Raises :class:`PlaceRefused` with the owner's words.
    """
    if purpose not in ("new", "existing", "move"):
        raise ValueError(purpose)
    folder = _whole_path(raw)

    root = bare_root(folder)
    if root:
        suggestion = os.path.join(root, SUBFOLDER)
        if not make_subfolder:
            what = ("the top of a network share" if root.startswith(("\\\\", "//"))
                    else "a whole drive")
            raise PlaceRefused(
                "bare_drive",
                f"{root} is {what}. BuildBot keeps its vault in a folder on "
                f"it. Make the folder {suggestion} and keep the vault there?",
                suggestion=suggestion)
        folder = os.path.normpath(suggestion)

    network = is_network(folder)
    lines = [NETWORK_LINE] if network else []
    trusted = same_folder(folder, default_folder())
    current = configured_folder()

    if folder.lower().endswith(".inbox") and has_vault(folder[:-len(".inbox")]):
        raise PlaceRefused("inbox", "That folder is a vault's inbox, not a "
                                    "place for a vault.")
    if inside(folder, inbox_of(current)):
        raise PlaceRefused("inbox", "That folder is your vault's inbox, not a "
                                    "place for a vault.")
    if same_folder(folder, current):
        if purpose == "move":
            raise PlaceRefused("same", "Your vault already lives there.")
    elif inside(folder, current):
        raise PlaceRefused("inside_vault",
                           "That folder is inside your vault's own folder. "
                           "Pick a folder outside it.")

    if not trusted:
        if inside(folder, install_folder()):
            raise PlaceRefused(
                "in_install",
                f"That folder is inside BuildBot's own folder "
                f"({install_folder()}). Git could copy your vault files into "
                f"its history. Pick a folder outside it.")
        for repo in own_repos():
            if inside(folder, repo):
                raise PlaceRefused(
                    "in_git",
                    f"That folder is inside BuildBot's own git folder "
                    f"({repo}). Git could copy your vault files into its "
                    f"history, and from there off this computer. Pick a "
                    f"folder outside it.")
    if _unattached_mount(folder):
        raise PlaceRefused(
            "not_attached", f"Nothing is attached at {folder}. Plug the drive "
                            f"in, then try again.")

    made = will_make = False
    if os.path.lexists(folder) and not os.path.isdir(folder):
        raise PlaceRefused("not_folder", f"{folder} is a file, not a folder.")
    if not os.path.isdir(folder):
        if purpose == "existing":
            if network:
                raise PlaceRefused(
                    "unreachable", f"BuildBot cannot reach {folder} right now. "
                    f"Check that the server or NAS is on, then try again.")
            raise PlaceRefused("missing", f"There is no folder {folder}.")
        parent = os.path.dirname(folder)
        if not os.path.isdir(parent):
            if network:
                raise PlaceRefused(
                    "unreachable", f"BuildBot cannot reach {parent} right "
                    f"now. Check that the server or NAS is on, then try again.")
            raise PlaceRefused(
                "missing", f"{parent} is not there. Plug in the drive, or make "
                           f"the folder first, then try again.")
        if create:
            try:
                os.mkdir(folder)
            except OSError:
                raise PlaceRefused("not_writable",
                                   f"BuildBot cannot make {folder}.") from None
            made = True
        else:
            will_make = True

    def undo() -> None:
        if made:
            try:
                os.rmdir(folder)
            except OSError:
                pass

    if purpose == "existing":
        if not has_vault(folder):
            raise PlaceRefused(
                "no_vault", f"There is no BuildBot vault in {folder}. Pick "
                f"the folder that holds your vault's files.")
    elif not will_make:
        if has_vault(folder):
            undo()
            raise PlaceRefused(
                "has_vault", f"There is already a BuildBot vault in {folder}. "
                f"Pick \"I already have a BuildBot vault\" to use it.")
        if purpose == "move" and _has_files(folder) and not made:
            raise PlaceRefused(
                "not_empty", f"{folder} already has things in it. Pick an "
                f"empty folder, so nothing of yours is mixed in with the "
                f"vault.")
        inbox = inbox_of(folder)
        if os.path.isdir(inbox) and _has_files(inbox):
            undo()
            raise PlaceRefused(
                "inbox", f"{inbox} already has things in it. Pick another "
                f"folder.")
    if not will_make and not _writable(folder):
        undo()
        raise PlaceRefused("not_writable", f"BuildBot cannot write in {folder}.")
    return {"ok": True, "folder": folder, "network": network, "made": made,
            "will_make": will_make, "default": trusted, "lines": lines}


# ---------------------------------------------------------------------------
# the PIN as the yes, throttled
# ---------------------------------------------------------------------------

_CHECKERS: dict[str, Any] = {}
_CHECKERS_LOCK = threading.Lock()


def _checker(folder: str):
    """One vault handle per folder for the life of the process, so its unlock
    throttle holds across requests."""
    from puffpai.security.vault import Vault
    key = _key(folder)
    with _CHECKERS_LOCK:
        handle = _CHECKERS.get(key)
        if handle is None:
            handle = Vault(vault_dir=folder)
            _CHECKERS[key] = handle
        return handle


def _prove(handle: Any, pin: Any, folder: str) -> None:
    """The PIN opens the vault in *folder*, or a refusal. Never opens it."""
    from puffpai.security.vault import VaultError, VaultMissing, VaultThrottled
    if not isinstance(pin, str) or not pin:
        raise PlaceRefused("pin_needed", "Type your vault PIN.")
    try:
        ok = handle.check_pin(pin)
    except VaultThrottled as exc:
        raise PlaceRefused(
            "throttled", f"Too many wrong PINs. Try again in "
                         f"{exc.retry_after} seconds.",
            retry_after=exc.retry_after) from None
    except VaultMissing as exc:
        raise PlaceRefused("vault_missing", str(exc)) from None
    except VaultError as exc:
        raise PlaceRefused("cannot", f"That vault could not be checked: {exc}.") from None
    if not ok:
        raise PlaceRefused("wrong_pin", f"That PIN does not open the vault in "
                                        f"{folder}. Nothing was changed.")


# ---------------------------------------------------------------------------
# saving the setting
# ---------------------------------------------------------------------------

def save_folder(folder: str, *, fingerprint: Optional[str] = None) -> str:
    """Write ``paths.vault_data_dir`` with the settings writer (one setting,
    merged), and remember the place (and *fingerprint*, which vault it is)
    when a vault is there. Returns the settings file."""
    from puffpai import setup_ops
    from puffpai.security.vault import remember_place
    folder = os.path.abspath(folder)
    if same_folder(folder, default_folder()):
        value = os.path.basename(folder)   # the setting as it always was
    else:
        value = folder
    path = setup_ops.write_main_config({"paths": {"vault_data_dir": value}})
    if has_vault(folder):
        remember_place(folder, fingerprint=fingerprint)
    return path


def _keep_keys_working(handle: Any) -> Optional[bool]:
    """After a vault arrives in a new folder (open *handle*): make the keys
    set to keep working while it is locked work there too.

    Their machine key is found by the folder's path in the computer's
    password store, so in a new folder there is none until one is made. None
    when the vault had no such keys; True when they work in the new folder;
    False when they could not be made to (they still work while it is open).
    """
    if not getattr(handle, "has_service_wrap", False):
        return None
    try:
        handle._sdk_from_machine_key()  # noqa: SLF001 - the one honest test
        return True
    except Exception:
        pass
    if handle.enroll_service_wrap(make_machine_key=True):
        return True
    handle.remove_service_wrap()      # honest: no half-working locked path
    return False


def _keys_line(kept: Optional[bool]) -> list[str]:
    if kept is None:
        return []
    if kept:
        return ["Keys set to keep working while the vault is locked were saved "
                "again for the new folder, and still work."]
    return ["Keys set to keep working while the vault is locked could not be "
            "saved again for the new folder. They work while the vault is "
            "open; switch \"keep working when locked\" on again on the Sign-up "
            "keys page."]


# ---------------------------------------------------------------------------
# the three changes
# ---------------------------------------------------------------------------

def _install_vault():
    from puffpai.security.vault import Vault
    return Vault()


def location(vault: Any = None) -> dict:
    """Where the vault lives now, in words. Never opens it."""
    from puffpai import developer_api
    folder = os.path.abspath(getattr(vault, "vault_dir", None) or configured_folder())
    state = developer_api.vault_folder_state(folder)
    network = is_network(folder)
    out = dict(state)
    out.update({
        "default_folder": default_folder(),
        "is_default": same_folder(folder, default_folder()),
        "network": network,
        "network_line": NETWORK_LINE if network else "",
        "question": QUESTION,
        "choices": [{"value": v, "label": label, "help": help_}
                    for v, label, help_ in CHOICES],
        "encrypted_drive_line": ENCRYPTED_DRIVE_LINE,
        "not_a_vault_line": NOT_A_VAULT_LINE,
        "inbox": inbox_of(folder),
    })
    return out


def _missing_refusal(missing: str) -> PlaceRefused:
    from puffpai.security.vault import VAULT_DRIVE_MISSING
    if missing.startswith(VAULT_DRIVE_MISSING):
        return PlaceRefused("vault_missing", MISSING_SWITCH_LINE)
    return PlaceRefused("vault_missing", f"{missing} Or change vaults from "
                        f"this computer's command line: puffpai vault "
                        f"location <folder>.")


def set_new_place(raw: Any = None, *, make_subfolder: bool = False,
                  vault: Any = None, confirmed_at_terminal: bool = False) -> dict:
    """"On this computer" (*raw* None) or "On a drive or folder I choose":
    where the NEW vault will be made. Makes no vault.

    Refused when a vault is already in use (that is a move). When the
    install's vault is on a drive that is not plugged in it is refused too
    (that would be a second vault), unless *confirmed_at_terminal*: the
    owner said yes at this computer's command line. Then BuildBot stops
    waiting for the old vault (its record is forgotten; the vault itself is
    never touched). No HTTP route passes *confirmed_at_terminal*.
    """
    from puffpai.security.vault import forget_place, missing_words, read_place
    current = vault if vault is not None else _install_vault()
    if getattr(current, "is_initialized", False):
        raise PlaceRefused(
            "vault_exists",
            f"You already have a vault in {current.vault_dir}. To keep it "
            f"somewhere else, move it: Developer, Zone 1: Vault, or "
            f"puffpai vault move <folder>.")
    missing = missing_words(current)
    if missing and confirmed_at_terminal is not True:
        raise _missing_refusal(missing)
    if raw is None or str(raw).strip() in ("", "here"):
        folder, network, lines = default_folder(), False, []
    else:
        info = check_folder(raw, purpose="new", make_subfolder=make_subfolder)
        folder, network, lines = info["folder"], info["network"], info["lines"]
    if missing:
        old = read_place()
        forget_place()
        lines = [*lines, f"BuildBot no longer waits for the vault in {old}. "
                         f"Nothing in it was deleted."]
    save_folder(folder)
    return {"ok": True, "folder": folder, "network": network,
            "made": False,
            "lines": [f"Your vault will be made in {folder}.", *lines]}


def use_existing(raw: Any, pin: Any, *, current_pin: Any = None,
                 vault: Any = None, make_subfolder: bool = False,
                 confirmed_at_terminal: bool = False) -> dict:
    """"I already have a BuildBot vault": use the vault in *raw*.

    *pin* is that vault's PIN. When a different vault is in use now, its PIN
    (*current_pin*) is needed too. Nothing is copied and nothing is deleted:
    the vault in use before is left exactly as it is.

    While the remembered vault's drive is not plugged in, only the SAME vault
    (its fingerprint, see the module note) may be used, unless
    *confirmed_at_terminal* (the owner said yes at this computer's command
    line). No HTTP route passes *confirmed_at_terminal*.
    """
    import hmac

    from puffpai.security.vault import (Vault, VaultError, missing_words,
                                        read_place, remembered_fingerprint)
    info = check_folder(raw, purpose="existing", make_subfolder=make_subfolder)
    folder = info["folder"]
    current = vault if vault is not None else _install_vault()
    before = os.path.abspath(current.vault_dir)
    if same_folder(folder, before) and current.is_initialized:
        return {"ok": True, "folder": folder, "network": info["network"],
                "changed": False, "lines": ["BuildBot already uses that vault."]}
    missing = "" if current.is_initialized else missing_words(current)
    at_terminal = confirmed_at_terminal is True
    remembered = remembered_fingerprint() if missing else ""
    if missing and not at_terminal and not remembered:
        # No way to tell "the same vault" from "another one": fail closed.
        raise _missing_refusal(missing)
    waited_for = read_place() if missing else None
    if current.is_initialized:
        if not current_pin:
            raise PlaceRefused(
                "current_pin_needed",
                f"You use the vault in {before} now. Type its PIN too, so "
                f"nobody but you can switch BuildBot to another vault.")
        _prove(current, current_pin, before)
    _prove(_checker(folder), pin, folder)
    handle = Vault(vault_dir=folder)
    try:
        handle.unlock(pin)
    except VaultError as exc:
        raise PlaceRefused("cannot", f"The vault in {folder} could not be "
                                     f"opened: {exc}. Nothing was changed.") from None
    try:
        fingerprint = handle.place_fingerprint()
        same = bool(remembered and fingerprint
                    and hmac.compare_digest(remembered, fingerprint))
        if missing and not at_terminal and not same:
            raise _missing_refusal(missing)
        kept = _keep_keys_working(handle)
    finally:
        handle.lock()
    save_folder(folder, fingerprint=fingerprint or None)
    lines = [f"BuildBot now uses the vault in {folder}."]
    if current.is_initialized:
        lines.append(f"The vault it used before is still in {before}. Nothing "
                     f"in it was changed.")
    elif missing and same:
        lines.append(f"It is the same vault BuildBot used before, in "
                     f"{waited_for}.")
    elif missing:
        lines.append(f"BuildBot no longer waits for the vault in {waited_for}. "
                     f"Nothing in it was deleted.")
    lines += _keys_line(kept) + info["lines"]
    return {"ok": True, "folder": folder, "network": info["network"],
            "changed": True, "old_folder": before, "keys_kept": kept,
            "lines": lines}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_folder(src: str, dst: str, copied: list[str]) -> list[str]:
    """Copy every file under *src* into *dst* (made if needed); the copied
    files' paths relative to *src*. Leftover write files (``*.tmp-*``) are
    not copied. Links are not followed. Each file is added to *copied* as
    soon as it is written, so a copy that stops part way can be cleared."""
    for here, dirs, files in os.walk(src, followlinks=False):
        rel_dir = os.path.relpath(here, src)
        target_dir = dst if rel_dir == "." else os.path.join(dst, rel_dir)
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            if ".tmp-" in name:
                continue
            source = os.path.join(here, name)
            if os.path.islink(source) or not os.path.isfile(source):
                continue
            target = os.path.join(target_dir, name)
            if os.path.exists(target):
                raise PlaceRefused("copy_failed",
                                   f"{target} was already there; nothing was "
                                   f"switched.")
            # Noted before it is written: a write that fails half way leaves
            # a part file that is ours to clear.
            copied.append(os.path.normpath(os.path.join(rel_dir, name))
                          if rel_dir != "." else name)
            shutil.copy2(source, target)
    return copied


def _same_copies(src: str, dst: str, rels: list[str]) -> bool:
    try:
        return all(_sha256(os.path.join(src, r)) == _sha256(os.path.join(dst, r))
                   for r in rels)
    except OSError:
        return False


def _undo_copy(dst: str, rels: list[str], made: bool) -> None:
    """Remove only what this move copied (and the folders it made)."""
    for rel in rels:
        try:
            os.remove(os.path.join(dst, rel))
        except OSError:
            pass
    for here, _dirs, _files in sorted(os.walk(dst), key=lambda w: -len(w[0])):
        if here == dst and not made:
            continue
        try:
            os.rmdir(here)
        except OSError:
            pass


def move_vault(vault: Any, raw: Any, pin: Any, *, make_subfolder: bool = False,
               on_switched: Optional[Callable[[], None]] = None) -> dict:
    """Move the install's vault (*vault*, the handle in use) to *raw*.

    See the module note for the steps. ``on_switched`` runs right after the
    setting is written (the server reloads its vault handle there), before
    the inbox is topped up.
    """
    from puffpai.security.vault import (Vault, VaultError, forget_machine_key,
                                        missing_words)
    src = os.path.abspath(vault.vault_dir)
    missing = missing_words(vault)
    if missing:
        raise PlaceRefused("vault_missing", missing)
    if not vault.is_initialized:
        raise PlaceRefused("no_vault", "There is no vault to move yet. Set "
                                       "your vault PIN first.")
    if getattr(vault, "is_legacy", False):
        raise PlaceRefused("legacy", "This is an older vault. Open it once "
                                     "with your PIN on the Vault page, then "
                                     "move it.")
    first = check_folder(raw, purpose="move", make_subfolder=make_subfolder,
                         create=False)
    _prove(vault, pin, src)
    info = check_folder(first["folder"], purpose="move", create=True)
    dst = info["folder"]
    made = info["made"]

    was_open = bool(vault.is_unlocked)
    vault.lock()                       # nothing is written while it moves
    src_inbox, dst_inbox = inbox_of(src), inbox_of(dst)
    inbox_was_there = os.path.isdir(dst_inbox)
    copied: list[str] = []
    inbox_copied: list[str] = []
    inbox_seen: set[str] = set()
    try:
        _copy_folder(src, dst, copied)
        if os.path.isdir(src_inbox):
            _copy_folder(src_inbox, dst_inbox, inbox_copied)
            inbox_seen = set(inbox_copied)
        if not (_same_copies(src, dst, copied)
                and _same_copies(src_inbox, dst_inbox, inbox_copied)):
            raise PlaceRefused("copy_failed", "The copy did not match your "
                                              "vault, so nothing was switched.")
        handle = Vault(vault_dir=dst)
        try:
            handle.unlock(pin)
        except VaultError:
            raise PlaceRefused("copy_failed", "The copy did not open with your "
                                              "PIN, so nothing was switched.") from None
        try:
            fingerprint = handle.place_fingerprint()
            kept = _keep_keys_working(handle)
        finally:
            handle.lock()
    except PlaceRefused as exc:
        _undo_copy(dst, copied, made)
        _undo_copy(dst_inbox, inbox_copied, not inbox_was_there)
        raise PlaceRefused(exc.code, f"{exc.message} Your vault is still in "
                                     f"{src}.") from None
    except OSError as exc:
        _undo_copy(dst, copied, made)
        _undo_copy(dst_inbox, inbox_copied, not inbox_was_there)
        raise PlaceRefused("copy_failed", f"The copy could not be made "
                                          f"({type(exc).__name__}), so nothing "
                                          f"was switched. Your vault is still "
                                          f"in {src}.") from None

    save_folder(dst, fingerprint=fingerprint or None)
    if kept:
        # The old folder's machine key: its copy no longer needs to open
        # while locked (it still opens with the PIN).
        forget_machine_key(src)
    if on_switched is not None:
        try:
            on_switched()
        except Exception as exc:  # the switch is done; say it, never undo it
            logger.warning("vault handle reload after a move failed: %s",
                           type(exc).__name__)
    # Things filed into the old inbox while the copy ran: across, once.
    late: list[str] = []
    if os.path.isdir(src_inbox):
        for here, _dirs, files in os.walk(src_inbox):
            for name in files:
                rel = os.path.relpath(os.path.join(here, name), src_inbox)
                if rel in inbox_seen or ".tmp-" in name:
                    continue
                target = os.path.join(dst_inbox, rel)
                if not os.path.exists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(os.path.join(here, name), target)
                    late.append(rel)

    old = f"{src} and {src_inbox}" if os.path.isdir(src_inbox) else src
    lines = [f"Your vault now lives in {dst}.",
             "Your PIN opened the copy there before anything was switched.",
             f"The old copy is still in {old}. Delete it yourself when you "
             f"are happy."]
    lines += _keys_line(kept)
    if was_open:
        lines.append("Your vault was locked while it moved. Open it again "
                     "with your PIN.")
    lines += info["lines"]
    return {"ok": True, "folder": dst, "old_folder": src,
            "old_inbox": src_inbox if os.path.isdir(src_inbox) else "",
            "network": info["network"], "keys_kept": kept,
            "files": len(copied) + len(inbox_copied) + len(late),
            "lines": lines}


# ---------------------------------------------------------------------------
# the chooser: drives and folders, never files
# ---------------------------------------------------------------------------

def _drive_roots() -> list[str]:
    if os.name == "nt":
        try:
            import ctypes
            mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
        except Exception:
            mask = 0
        return [f"{chr(65 + i)}:\\" for i in range(26)
                if mask & (1 << i) and i >= 2]
    roots = ["/"]
    for base in ("/Volumes", "/mnt", "/media", f"/media/{os.environ.get('USER', '')}",
                 f"/run/media/{os.environ.get('USER', '')}"):
        if os.path.isdir(base) and base not in roots:
            roots.append(base)
    return roots


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$")):
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & _HIDDEN_OR_SYSTEM)


def browse(raw: Any = None) -> dict:
    """The drives (no *raw*), or the folders inside *raw*. Folder names only:
    never a file, never what is in a file."""
    home = os.path.expanduser("~")
    if raw is None or not str(raw).strip():
        drives = [{"name": d, "path": d, "network": is_network(d)}
                  for d in _drive_roots()]
        return {"ok": True, "folder": "", "parent": "", "drives": drives,
                "home": home, "folders": [], "more": False}
    folder = _whole_path(raw)
    if not os.path.isdir(folder):
        raise PlaceRefused("missing", f"BuildBot cannot open {folder}.")
    rows = []
    more = False
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False) or _hidden(entry):
                        continue
                    if (entry.name.lower().endswith(".inbox")
                            and has_vault(entry.path[:-len(".inbox")])):
                        continue        # a vault's inbox is never a choice
                except OSError:
                    continue
                if len(rows) >= _BROWSE_LIMIT:
                    more = True
                    break
                rows.append({"name": entry.name, "path": entry.path,
                             "vault": has_vault(entry.path)})
    except OSError:
        raise PlaceRefused("not_readable", f"BuildBot cannot look inside "
                                           f"{folder}.") from None
    rows.sort(key=lambda r: r["name"].lower())
    parent = os.path.dirname(folder)
    return {"ok": True, "folder": folder,
            "parent": "" if parent == folder else parent,
            "drives": [], "home": home, "folders": rows, "more": more,
            "vault": has_vault(folder), "network": is_network(folder)}


__all__ = ["CHOICES", "CHOICE_VALUES", "ENCRYPTED_DRIVE_LINE",
           "MISSING_SWITCH_LINE", "NETWORK_LINE", "NOT_A_VAULT_LINE",
           "PlaceRefused", "QUESTION", "SUBFOLDER",
           "bare_root", "browse", "check_folder", "configured_folder",
           "default_folder", "git_root_of", "is_network", "location",
           "move_vault", "own_repos", "save_folder", "set_new_place", "use_existing"]
