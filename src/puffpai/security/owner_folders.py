"""Owner folders: the ONE set of rules for giving a folder to outside code.

The locked box and the browser apps both ask here before
any folder of the owner's is handed to something BuildBot did not write.

* Read roots: Pictures, Documents, Music, Videos, Desktop, Downloads (their
  real paths, including a Documents folder that Windows moved elsewhere).
* :func:`deny_roots`: the vault, its inbox, the state and data folders, the
  apps folder, the locked box's folder, the install folder, every app's data
  folder, and the fixed private and system places. :func:`check` refuses
  anything inside one of them, anything that CONTAINS one of them, anything
  outside the read roots, and junction or link tricks (the real path is
  taken before the checks and again after them, and must not move).
* Writing only ever goes into a folder :func:`write_folder` made:
  ``Documents\\<Bot> files\\<app>``. :func:`after_write_sweep` renames files
  Windows would run or open actively to ``<name>.blocked`` and marks new
  files as coming from the internet (``Zone.Identifier``, ``ZoneId=3``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import stat
import sys
from typing import Optional

from puffpai.box import wording as W

#: Files Windows runs, or opens in a way that can run code, by their ending.
#: The first line is the pinned list; the rest are more endings
#: Windows runs, mounts or opens actively, added on the stricter side.
ACTIVE_EXTS = (".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs",
               ".js", ".jse", ".wsf", ".hta", ".msi", ".lnk", ".url", ".scf",
               ".library-ms", ".docm", ".xlsm", ".pptm",
               ".vbe", ".pif", ".cpl", ".reg", ".msc", ".msp", ".mst", ".jar",
               ".chm", ".hlp", ".inf", ".settingcontent-ms", ".appref-ms",
               ".application", ".gadget", ".psm1", ".psd1", ".ps1xml", ".ws",
               ".wsc", ".wsh", ".xll", ".xlam", ".ppam", ".dotm", ".xltm", ".potm",
               ".ppsm", ".sldm", ".iso", ".img", ".vhd", ".vhdx", ".search-ms",
               ".searchconnector-ms", ".diagcab", ".appx", ".msix", ".appxbundle",
               ".msixbundle", ".appinstaller", ".website",
               # Microsoft's high-risk attachment endings (Attachment Manager
               # and Outlook's blocked list), found missing in review.
               ".ade", ".adp", ".app", ".asp", ".aspx", ".bas", ".cer", ".cnt",
               ".crt", ".csh", ".der", ".fxp", ".grp", ".hpj", ".htc", ".ins",
               ".isp", ".its", ".jnlp", ".ksh", ".mad", ".maf", ".mag", ".mam",
               ".maq", ".mar", ".mas", ".mat", ".mau", ".mav", ".maw", ".mcf",
               ".mda", ".mdb", ".mde", ".mdt", ".mdw", ".mdz", ".mht", ".mhtml",
               ".msh", ".msh1", ".msh2", ".mshxml", ".msh1xml", ".msh2xml",
               ".msu", ".ops", ".osd", ".pcd", ".pl", ".plg", ".prf", ".prg",
               ".printerexport", ".ps2", ".ps2xml", ".psc1", ".psc2", ".psdm1",
               ".pst", ".py", ".pyc", ".pyo", ".pyw", ".pyz", ".pyzw", ".sct",
               ".shb", ".shs", ".theme", ".themepack", ".deskthemepack", ".tmp",
               ".vb", ".vbp", ".vsmacros", ".vsw", ".webpnp", ".wsb", ".xbap",
               ".xnk")
#: How many entries :func:`check` looks at inside a folder for links that lead
#: somewhere private. A folder with more is REFUSED (fail closed): a link past
#: the limit could not be ruled out. Consumers must still never follow links.
LINK_SCAN_MAX = 20_000
ZONE_STREAM = ":Zone.Identifier"
ZONE_TEXT = "[ZoneTransfer]\r\nZoneId=3\r\n"
READ_ROOT_NAMES = ("Pictures", "Documents", "Music", "Videos", "Desktop", "Downloads")
_KNOWN_FOLDER_IDS = {
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
}
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


# ── paths ────────────────────────────────────────────────────────────────────

def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside_or_equal(child: str, parent: str) -> bool:
    """Both already canonical."""
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:          # different drives
        return False


def is_link(path: str) -> bool:
    """A symlink, a junction or any other reparse point. Never follows it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _known_folder_windows(guid: str) -> Optional[str]:
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("data", ctypes.c_byte * 16)]

        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(guid).bytes_le, 16)
        out = ctypes.c_wchar_p()
        # A private handle: setting argtypes on the process-wide
        # ctypes.windll.shell32 would change the function for every other
        # caller (safe_archive's own known-folder check then silently found
        # nothing, and lost the folders OneDrive moved).
        shell32 = ctypes.WinDLL("shell32")
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                       ctypes.POINTER(ctypes.c_wchar_p)]
        hr = fn(ctypes.byref(g), 0, None, ctypes.byref(out))
        try:
            return out.value if hr == 0 and out.value else None
        finally:
            ctypes.WinDLL("ole32").CoTaskMemFree(out)
    except Exception:
        return None


def known_folders() -> dict:
    """``{"Pictures": [paths], ...}``: the known folder Windows reports and the
    plain one in the profile, when they differ and both exist."""
    home = os.environ.get("USERPROFILE") if os.name == "nt" else None
    home = home or os.path.expanduser("~")
    out: dict = {}
    for name in READ_ROOT_NAMES:
        paths = []
        if os.name == "nt":
            found = _known_folder_windows(_KNOWN_FOLDER_IDS[name])
            if found:
                paths.append(found)
        paths.append(os.path.join(home, name))
        out[name] = paths
    return out


def read_roots() -> list:
    """Canonical real paths of the folders an owner may share from."""
    out = []
    for paths in known_folders().values():
        for p in paths:
            try:
                real = _canon(os.path.realpath(p, strict=True))
            except (OSError, ValueError):
                continue
            if os.path.isdir(real) and real not in out:
                out.append(real)
    return out


def documents_folder() -> Optional[str]:
    """The owner's Documents folder (real path), or None."""
    for p in known_folders().get("Documents", []):
        try:
            real = os.path.realpath(p, strict=True)
        except (OSError, ValueError):
            continue
        if os.path.isdir(real):
            return real
    return None


def apps_root() -> str:
    """Where added apps are unpacked: ``safe_archive.configured_root()`` when it
    is in this build (where the folder is set to be, never raising, so a
    wrongly placed folder can still be found, refused as a grant and removed),
    else the same rule written out."""
    sa = _optional("puffpai.security.safe_archive")
    if sa is not None and callable(getattr(sa, "configured_root", None)):
        return os.path.abspath(sa.configured_root())
    env = os.environ.get("PUFFPAI_APPS_ROOT", "").strip()
    if env:
        return os.path.abspath(env)
    return default_apps_root()


def default_apps_root() -> str:
    """The apps folder when nothing moved it: ``safe_archive``'s rule when it is in
    this build (on Windows the sibling folder PuffPAI-apps in LOCALAPPDATA,
    kept outside the state folder), else the pinned default."""
    sa = _optional("puffpai.security.safe_archive")
    if sa is not None and callable(getattr(sa, "_default_root", None)):
        return os.path.abspath(sa._default_root())
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PuffPAI", "apps")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "puffpai", "apps")


def vault_dir() -> str:
    from puffpai.config import get_config
    cfg = get_config()
    vd = cfg.paths.vault_data_dir
    return os.path.abspath(vd if os.path.isabs(vd) else os.path.join(cfg.DATA_DIR, vd))


def _fixed_private() -> list:
    """Private and system places that are never shared, whatever the config."""
    env = os.environ.get
    home = (env("USERPROFILE") if os.name == "nt" else None) or os.path.expanduser("~")
    out = [os.path.join(home, ".ssh"), os.path.join(home, ".config"),
           os.path.join(home, ".docker"), os.path.join(home, ".gnupg"),
           os.path.join(home, "PuffPAI")]
    if os.name == "nt":
        la = env("LOCALAPPDATA")
        if la:
            out += [os.path.join(la, "PuffPAI"), os.path.join(la, "Microsoft"),
                    os.path.join(la, "Packages")]
        out += [p for p in (env("APPDATA"), env("SystemRoot"), env("ProgramFiles"),
                            env("ProgramFiles(x86)"), env("ProgramData")) if p]
    else:
        out += ["/etc", "/usr", "/bin", "/sbin", "/lib", "/boot", "/root", "/var",
                "/proc", "/sys", "/dev", "/run"]
    return out


def deny_roots() -> list:
    """Every place no app or box run may ever be given (canonical real paths)."""
    from puffpai.box import driver
    from puffpai.config import get_config, get_state_dir
    raw = []
    try:
        vd = vault_dir()
        raw += [vd, vd + ".inbox"]
    except Exception:
        pass
    raw.append(get_state_dir())
    try:
        raw.append(get_config().DATA_DIR)
    except Exception:
        pass
    raw.append(apps_root())
    raw.append(driver.box_root())
    try:
        raw.append(driver.install_root())
    except Exception:
        pass
    try:
        from puffpai import app_data
        raw += [app_data._DATA_DIR, app_data._state_data_dir()]  # noqa: SLF001
    except Exception:
        pass
    raw += _fixed_private()
    out = []
    for p in raw:
        if not p:
            continue
        c = _canon(p)
        if c not in out:
            out.append(c)
    return out


# ── write folders (recorded, so "rw" can only ever mean one of them) ────────

def _write_record() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "owner_folders", "write_folders.json")


def _write_folders() -> list:
    try:
        with open(_write_record(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    items = data.get("folders") if isinstance(data, dict) else None
    return [_canon(p) for p in items or [] if isinstance(p, str) and p]


def _remember_write_folder(real: str) -> None:
    path = _write_record()
    folders = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        folders = [p for p in (data.get("folders") or []) if isinstance(p, str)]
    except (OSError, ValueError, AttributeError):
        folders = []
    if real not in folders:
        folders.append(real)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"folders": folders}, fh, indent=2)
    os.replace(tmp, path)


def _safe_name(name: str, fallback: str) -> str:
    text = _BAD_CHARS.sub(" ", str(name or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")[:60].strip(" .")
    if not text or text in (".", "..") or _RESERVED.match(text):
        return fallback
    return text


# ── the rules ────────────────────────────────────────────────────────────────

def _private_relation(real: str, denies: list) -> bool:
    return any(_inside_or_equal(real, d) or _inside_or_equal(d, real) for d in denies)


def _scan_links(folder: str, denies: list, limit: int) -> str:
    """``""`` when no link inside *folder* leads to (or holds) a private
    place; ``"private"`` when one does or its target cannot be told;
    ``"too_many"`` when the folder holds more than *limit* entries, so a link
    could not be ruled out. Never follows a link."""
    seen = 0
    stack = [folder]
    while stack:
        here = stack.pop()
        try:
            it = os.scandir(here)
        except FileNotFoundError:
            continue                         # gone since it was listed
        except OSError:
            return "private"                 # cannot look inside: cannot tell
        with it:
            for entry in it:
                seen += 1
                if seen > limit:
                    return "too_many"
                path = entry.path
                if is_link(path):
                    try:
                        target = _canon(os.path.realpath(path))
                    except (OSError, ValueError):
                        return "private"     # cannot tell where it goes: private
                    if _private_relation(target, denies):
                        return "private"
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                except OSError:
                    continue
    return ""


def links_to_private(folder: str, denies: Optional[list] = None, *,
                     limit: int = LINK_SCAN_MAX) -> bool:
    """True when a link or junction INSIDE *folder* leads to (or holds) a
    private place, or when the folder holds more than *limit* entries (a
    link past the limit cannot be ruled out: fail closed). Never follows a
    link."""
    denies = deny_roots() if denies is None else denies
    return _scan_links(folder, denies, limit) != ""


def check(path: str, mode: str) -> tuple:
    """``(real path, "")`` when *path* may be given in *mode* ("ro" or "rw"),
    else ``(None, plain reason)``. Fails closed on anything odd."""
    if mode not in ("ro", "rw"):
        return None, W.FOLDER_BAD_MODE
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None, W.FOLDER_NOT_THERE
    p = path.strip()
    if p.startswith(("\\\\", "//")):
        return None, W.FOLDER_NETWORK
    try:
        before = os.path.realpath(p, strict=True)
    except (OSError, ValueError):
        return None, W.FOLDER_NOT_THERE
    if before.startswith(("\\\\", "//")):
        return None, W.FOLDER_NETWORK
    if not os.path.isdir(before):
        return None, W.FOLDER_NOT_A_FOLDER
    real = _canon(before)
    denies = deny_roots()
    if _private_relation(real, denies):
        return None, W.FOLDER_PRIVATE
    if not any(_inside_or_equal(real, r) for r in read_roots()):
        return None, W.FOLDER_OUTSIDE
    scan = _scan_links(before, denies, LINK_SCAN_MAX)
    if scan == "too_many":
        return None, W.FOLDER_TOO_BIG
    if scan:
        return None, W.FOLDER_PRIVATE
    if mode == "rw" and not any(_inside_or_equal(real, w) for w in _write_folders()):
        return None, W.FOLDER_WRITE_ONLY_OWN
    try:
        after = os.path.realpath(before, strict=True)
    except (OSError, ValueError):
        return None, W.FOLDER_NOT_THERE
    if _canon(after) != real:
        return None, W.FOLDER_PRIVATE
    return before, ""


def write_folder(bot_name: str, app_name: str) -> str:
    """Make ``Documents\\<Bot> files\\<app>`` and return its real path.

    Raises PermissionError (plain words) when Documents is missing, when a
    part of the path is a link or junction, or when it would land somewhere
    private.
    """
    docs = documents_folder()
    if not docs:
        raise PermissionError(W.FOLDER_NOT_THERE)
    bot = _safe_name(bot_name, "BuildBot")
    app = _safe_name(app_name, "app")
    parent = os.path.join(docs, f"{bot} files")
    target = os.path.join(parent, app)
    for part in (parent, target):
        if is_link(part):
            raise PermissionError(W.FOLDER_PRIVATE)
    os.makedirs(target, exist_ok=True)
    real = os.path.realpath(target, strict=True)
    if _canon(real) != _canon(target) or not _inside_or_equal(_canon(real), _canon(docs)):
        raise PermissionError(W.FOLDER_PRIVATE)
    if _private_relation(_canon(real), deny_roots()):
        raise PermissionError(W.FOLDER_PRIVATE)
    _remember_write_folder(_canon(real))
    return real


def is_active_name(name: str) -> bool:
    low = str(name).rstrip(" .").lower()
    return any(low.endswith(ext) for ext in ACTIVE_EXTS)


def _unique_blocked(path: str) -> str:
    candidate = path + ".blocked"
    n = 2
    while os.path.lexists(candidate):
        candidate = f"{path}.{n}.blocked"
        n += 1
    return candidate


def _long(path: str) -> str:
    """The extended-length form of a Windows path (``\\\\?\\...``), so a name
    that ends in a dot or a space, which ordinary Windows calls quietly trim,
    is renamed and marked as it really is. Unchanged elsewhere."""
    if os.name != "nt" or path.startswith("\\\\"):
        return path
    return "\\\\?\\" + path


def _short(path: str) -> str:
    return path[4:] if path.startswith("\\\\?\\") else path


def _mark_zone(path: str) -> bool:
    """Mark one file as from the internet (NTFS only). True when marked now.

    A read-only file is made writable for the mark and read-only again."""
    if os.name != "nt":
        return False
    stream = path + ZONE_STREAM
    try:
        if os.path.exists(stream):
            return False
    except OSError:
        pass
    ro = False
    try:
        ro = not (os.stat(path).st_mode & stat.S_IWRITE)
        if ro:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        with open(stream, "w", encoding="ascii", newline="") as fh:
            fh.write(ZONE_TEXT)
        return True
    except OSError:
        return False
    finally:
        if ro:
            try:
                os.chmod(path, stat.S_IREAD)
            except OSError:
                pass


def _neuter(path: str) -> Optional[str]:
    """Rename an active file to ``<name>.blocked``; when that is impossible,
    delete it (an app's own output in BuildBot's folder). The new path, "" when
    deleted, None when neither worked."""
    new = _unique_blocked(path)
    try:
        os.rename(path, new)
        return new
    except OSError:
        pass
    try:
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        os.unlink(path)
        return ""
    except OSError:
        return None


def after_write_sweep(folder: str) -> list:
    """After an app wrote into its folder: rename active files to
    ``<name>.blocked`` (deleting one that cannot be renamed) and mark files as
    from the internet. Returns the new paths of the renamed files. Links are
    never followed or marked. Raises PermissionError when an active file could
    be neither renamed nor deleted, so the caller never hands it back."""
    try:
        real = os.path.realpath(folder, strict=True)
    except (OSError, ValueError):
        return []
    if not any(_inside_or_equal(_canon(real), w) for w in _write_folders()):
        raise PermissionError(W.FOLDER_WRITE_ONLY_OWN)
    renamed = []
    stuck = []
    for dirpath, dirnames, filenames in os.walk(_long(real), followlinks=False):
        dirnames[:] = [d for d in dirnames if not is_link(os.path.join(dirpath, d))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            link = is_link(path)
            if is_active_name(name):
                new = _neuter(path)
                if new is None:
                    stuck.append(_short(path))
                    continue
                if not new:
                    continue
                renamed.append(_short(new))
                path = new
            if not link:
                _mark_zone(path)
    if stuck:
        raise PermissionError(W.FOLDER_PRIVATE)
    return renamed


__all__ = ["ACTIVE_EXTS", "LINK_SCAN_MAX", "after_write_sweep", "apps_root", "check",
           "default_apps_root", "deny_roots", "links_to_private",
           "documents_folder", "is_active_name", "is_link", "known_folders",
           "read_roots", "vault_dir", "write_folder"]
