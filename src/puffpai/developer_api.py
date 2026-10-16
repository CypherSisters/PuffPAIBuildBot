"""Read-only routes for the Developer area, and its "Use CLI" wording list.

Registered on the backend app by ``api_server`` (one line), so every route
here sits behind the same API-key check, rate limit and CORS rules as the
rest of the backend. The dashboard reaches them only through its paired
proxy (``developer`` is on its address list).

Routes::

    GET /developer/cli/<page>   the terminal commands for one page
                                (vault, brain or bridge), both shells
    GET /developer/vault        where the vault lives, whether that folder
                                and its drive are there, locked or open,
                                how many items wait in its inbox (changing
                                where it lives: puffpai.vault_place_api)
    GET /developer/brain        the model in use and the one saved for the
                                next start, where it runs and where that came
                                from, the model file if one is set, and
                                whether Home Assistant is set up (its address,
                                never its password)

Nothing here changes anything. Changes go through the pages and routes that
already own them, with the same yes they already need.

The command list
----------------
Every command below is one this build really has: ``tests/test_developer_api``
parses each one with :func:`puffpai.cli.build_parser`. A command is never
shown with an option that carries a secret (``--vault-pin``,
``--vault-password``, ``--password``, ``--value``, ``--ha-token``): those
commands ask for the secret when they run. A page action with no command yet
is said plainly in ``no_command_yet`` instead of being invented.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, jsonify

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("developer", __name__)

#: Options that carry a secret. Never in a command an owner is shown.
SECRET_OPTIONS: tuple[str, ...] = ("--vault-pin", "--vault-password",
                                   "--password", "--value", "--ha-token")

OPEN_TERMINAL = {
    "windows": "Open PowerShell (press the Windows key, type PowerShell), "
               "then paste.",
    "posix": "Open Terminal, then paste.",
}

#: page -> commands. ``argv`` is what follows ``puffpai``. ``fill`` says what
#: to put in place of a <word> before pressing Enter; ``asks`` says what the
#: command asks for when it runs (so nobody types a secret into the line).
COMMANDS: dict[str, list[dict[str, Any]]] = {
    "vault": [
        {"what": "Show where your vault folder is, and whether it is made.",
         "argv": ["vault", "status"]},
        {"what": "Show every folder this copy of your bot uses, the vault "
                 "folder too.",
         "argv": ["where"]},
        {"what": "Show where your vault lives: this computer, a drive or "
                 "folder you chose, or a network folder.",
         "argv": ["vault", "location"]},
        {"what": "Use a BuildBot vault you already have in another folder, "
                 "or say where a new vault is made.",
         "argv": ["vault", "location", "<folder>"],
         "fill": "Put the whole folder path in place of <folder>, in quotes "
                 "if it has spaces, like \"E:\\PuffPAI Vault\".",
         "asks": "that vault's PIN, and the PIN of the vault you use now"},
        {"what": "Move your vault to another folder. The copy has to open "
                 "with your PIN before anything is switched, and the old "
                 "copy is kept.",
         "argv": ["vault", "move", "<folder>"],
         "fill": "Put the whole folder path in place of <folder>, in quotes "
                 "if it has spaces. Stop your bot first (puffpai stop).",
         "asks": "your PIN"},
        {"what": "Save one item in the vault.",
         "argv": ["vault", "store", "<name>"],
         "fill": "Put a short name for the item in place of <name>.",
         "asks": "your PIN, then the item (it is hidden as you type)"},
        {"what": "Read one item from the vault.",
         "argv": ["vault", "get", "<name>"],
         "fill": "Put the item's name in place of <name>.",
         "asks": "your PIN"},
        {"what": "Forgot your PIN: open the vault with your recovery code "
                 "and pick a new PIN.",
         "argv": ["vault", "recover"],
         "asks": "your recovery code, then a new PIN twice"},
        {"what": "List your sign-up keys and whether each one works.",
         "argv": ["keys", "list"]},
    ],
    "brain": [
        {"what": "Show which model address this copy uses, and where that "
                 "address came from.",
         "argv": ["where"]},
        {"what": "Ask your bot to show it works: real answers from the model.",
         "argv": ["prove"]},
        {"what": "Show the seven helpers and how they are wired.",
         "argv": ["agents", "status"]},
        {"what": "Run setup again with another model. It asks the other "
                 "setup questions again.",
         "argv": ["setup", "--model", "<model>"],
         "fill": "Put the model's name in place of <model>, as the Brain page "
                 "lists it."},
        {"what": "After changing the model: stop your bot.",
         "argv": ["stop"]},
        {"what": "Then start it again, so it uses the new model.",
         "argv": ["start"]},
        {"what": "Connect Home Assistant: run setup again and say yes when it "
                 "asks about Home Assistant.",
         "argv": ["setup"],
         "asks": "the Home Assistant address, then its password. Type the "
                 "password only when it asks, never into the command"},
        {"what": "List the smart home devices waiting for your yes.",
         "argv": ["devices", "list"]},
        {"what": "Say yes to one device.",
         "argv": ["devices", "approve", "<device>"],
         "fill": "Put the device, as the list shows it, in place of <device>."},
    ],
    "bridge": [
        {"what": "List every outside service, with its number.",
         "argv": ["outside", "list"]},
        {"what": "Switch one outside service on.",
         "argv": ["outside", "on", "<number>"],
         "fill": "Put the service's number from the list in place of "
                 "<number>.",
         "asks": "your yes, after it shows what will leave your computer"},
        {"what": "Switch one outside service off.",
         "argv": ["outside", "off", "<number>"],
         "fill": "Put the service's number from the list in place of "
                 "<number>."},
        {"what": "Show everything that left your computer in the last day.",
         "argv": ["outside", "log", "--hours", "24"]},
        {"what": "List your sign-up keys.",
         "argv": ["keys", "list"]},
        {"what": "Show the code a phone or laptop types to use your bot's "
                 "web page.",
         "argv": ["pair"]},
        {"what": "Change the internet setting or the Bridge choice: run "
                 "setup again.",
         "argv": ["setup"]},
    ],
}

#: page -> things the page shows that have no command yet, in plain words.
NO_COMMAND_YET: dict[str, list[str]] = {
    "vault": [
        "Changing your PIN without the recovery code has no command yet.",
        "Lock and unlock are on the Vault page: a command window cannot "
        "open or lock the vault your running bot holds.",
    ],
    "brain": [
        "Downloading a new model has no command of its own yet.",
        "Pointing your bot at a model on another computer has no command "
        "of its own yet: it is a setup question.",
    ],
    "bridge": [
        "Copying a list of websites for your own firewall has no command "
        "yet.",
        "Saving the outside log to a file has no command yet.",
    ],
}

PAGES = tuple(COMMANDS)


# ---------------------------------------------------------------------------
# the commands, in each shell
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    try:
        from puffpai.config import get_config
        return str(get_config().REPO_ROOT)
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))


def box_shell() -> str:
    """``"windows"`` or ``"posix"``: the shell of the computer the bot runs on."""
    return "windows" if os.name == "nt" else "posix"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def launcher(shell: str, folder: str) -> tuple[str, bool]:
    """``(how the command starts, whether it runs from the bot's folder)``.

    The installers write a ``puffpai`` launcher into the bot's folder
    (``puffpai.cmd`` on Windows, ``puffpai`` on Mac and Linux). On the
    computer the bot runs on, the launcher is used only if it is really
    there; otherwise the plain ``puffpai`` command is shown.
    """
    if shell == "windows":
        name, start = "puffpai.cmd", ".\\puffpai.cmd"
    else:
        name, start = "puffpai", "./puffpai"
    if shell == box_shell() and not os.path.isfile(os.path.join(folder, name)):
        return "puffpai", False
    return start, True


def _render(argv: list[str], start: str) -> str:
    return " ".join([start, *argv])


#: The folder step for the other kind of computer, where this computer's path
#: would not work.
GO_TO_FOLDER_ELSEWHERE = ("First go to the folder where you installed "
                          "BuildBot on that computer.")


def cli_page(page: str, *, folder: str | None = None) -> dict[str, Any]:
    """The Use CLI panel for one page. Raises ``KeyError`` for an unknown page."""
    rows = COMMANDS[page]
    folder = folder or _repo_root()
    starts = {shell: launcher(shell, folder) for shell in ("windows", "posix")}
    commands = []
    for row in rows:
        out = {"what": row["what"],
               "windows": _render(row["argv"], starts["windows"][0]),
               "posix": _render(row["argv"], starts["posix"][0])}
        for key in ("fill", "asks"):
            if row.get(key):
                out[key] = row[key]
        commands.append(out)
    # A ``cd`` only for the shell of the computer the bot runs on: its folder
    # is a path on THIS computer ('C:\Users\...' means nothing in a Mac or
    # Linux terminal). The other shell gets a plain step instead.
    go_to = {}
    go_to_said = {}
    quote = {"windows": _ps_quote, "posix": _sh_quote}
    for shell in ("windows", "posix"):
        if not starts[shell][1]:
            continue
        if shell == box_shell():
            go_to[shell] = f"cd {quote[shell](folder)}"
        else:
            go_to_said[shell] = GO_TO_FOLDER_ELSEWHERE
    return {
        "page": page,
        "box": box_shell(),
        "folder": folder,
        "open_terminal": dict(OPEN_TERMINAL),
        "go_to_folder": go_to,
        "go_to_folder_line": "First go to your bot's folder:",
        "go_to_folder_said": go_to_said,
        "commands": commands,
        "no_command_yet": list(NO_COMMAND_YET.get(page, [])),
        "secrets_line": "No command here ever carries your PIN, a password "
                        "or a key. The commands that need one ask for it "
                        "when they run.",
    }


# ---------------------------------------------------------------------------
# the vault folder
# ---------------------------------------------------------------------------

def vault_folder() -> str:
    """The vault folder this copy uses (absolute), from the settings."""
    from puffpai.config import get_config
    cfg = get_config()
    vd = cfg.paths.vault_data_dir
    return vd if os.path.isabs(vd) else os.path.join(cfg.DATA_DIR, vd)


def _drive_there(folder: str) -> bool:
    """Is the drive (Windows) or the parent folder (Mac, Linux) there?"""
    drive, _ = os.path.splitdrive(folder)
    if drive:
        return os.path.exists(drive + os.sep)
    return os.path.isdir(os.path.dirname(folder.rstrip("/\\")) or "/")


def vault_folder_state(folder: str | None = None) -> dict[str, Any]:
    """Where the vault is and what is there. Never opens it.

    ``made`` is None when that could not be read.
    """
    folder = os.path.abspath(folder or vault_folder())
    there = os.path.isdir(folder)
    drive = there or _drive_there(folder)
    made: bool | None
    try:
        from puffpai.security.vault import Vault
        made = bool(Vault(vault_dir=folder).is_initialized) if there else False
        if made is False and drive:
            # A vault was made here before and its folder is gone (the
            # place is remembered): that is a drive not plugged in, never
            # "no vault yet".
            from puffpai.security.vault import missing_words
            if missing_words(Vault(vault_dir=folder)):
                drive = False
    except Exception as exc:
        logger.debug("vault folder check failed: %s", exc)
        made = None
    if not drive:
        line = "Your vault drive is not plugged in."
    elif made:
        line = "Your vault is made and kept in this folder."
    elif made is None:
        line = "Your bot could not read the vault folder."
    else:
        line = "No vault has been made in this folder yet."
    return {"folder": folder, "folder_there": there, "drive_there": drive,
            "made": made, "line": line}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def _no_userinfo(url: str) -> str:
    """The address with any name/password and query cut off."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@bp.route("/developer/cli/<page>", methods=["GET"])
def developer_cli(page):
    if page not in COMMANDS:
        return jsonify({"ok": False,
                        "message": "There is no Developer page by that name."}), 404
    return jsonify(cli_page(page))


@bp.route("/developer/vault", methods=["GET"])
def developer_vault():
    from puffpai.security import privacy_explainer as pe
    try:
        from puffpai import api_server
        vault = api_server._get_vault()  # noqa: SLF001
    except Exception:
        vault = None
    state = pe.vault_state(vault)
    folder = getattr(vault, "vault_dir", None) or vault_folder()
    out = vault_folder_state(folder)
    out["state"] = state
    waiting = pe.waiting_count(vault) if vault is not None else None
    out["waiting"] = waiting if state == "locked" else 0
    return jsonify(out)


@bp.route("/developer/brain", methods=["GET"])
def developer_brain():
    from puffpai import cli
    from puffpai.config import get_config
    import argparse
    cfg = get_config()
    try:
        address, source = cli._ollama_address_source(  # noqa: SLF001
            argparse.Namespace(), cfg)
    except Exception:
        address, source = cfg.ollama.url, ""
    try:
        from puffpai import api_server
        live = str(api_server.OLLAMA_URL or "")
        running = str(api_server.OLLAMA_MODEL or "")
    except Exception:
        live, running = "", ""
    saved = str(cfg.ollama.model or "")
    ha = getattr(cfg, "ha", None)
    ha_set_up = bool(ha is not None and ha.enabled and ha.token)
    gguf = str(getattr(getattr(cfg, "models", None), "llm_gguf_path", "") or "")
    return jsonify({
        # The model the running bot started with, and the one in its
        # settings now. They differ after a pick, until the bot restarts.
        "model": running or saved,
        "saved_model": saved,
        "address": _no_userinfo(live or address),
        "address_from": source or "the built-in setting",
        "model_file": gguf,
        "home_assistant": {
            "set_up": ha_set_up,
            "address": _no_userinfo(ha.url) if ha_set_up else "",
        },
        "only_ollama_line": "Your bot runs its models with Ollama on this "
                            "computer or another computer at home. Cloud "
                            "models and other model programs are not "
                            "offered.",
        "restart_line": "A new model is used after your bot restarts.",
    })


__all__ = ["COMMANDS", "NO_COMMAND_YET", "OPEN_TERMINAL", "PAGES",
           "SECRET_OPTIONS", "box_shell", "bp", "cli_page", "launcher",
           "vault_folder", "vault_folder_state"]
