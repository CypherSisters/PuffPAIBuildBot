"""Terminal commands: ``puffpai keys ...`` -- sign-up keys.

Kept out of ``cli.py`` so that file only gains one line that adds this parser.
Every line printed here is the terminal's own wording; the Sign-up keys page
has its own.

    puffpai keys [list]              every key this bot knows how to use
    puffpai keys add <name>          the steps, then paste the key (hidden)
    puffpai keys remove <name>       remove it (nothing else asked)
    puffpai keys test <name>         check the stored key again
    puffpai keys test <name> --public [--words "..."]
                                     try the service's shared TEST key (only
                                     services that publish one); stores nothing

A key is NEVER taken from the command line or the environment: it is typed
(hidden) or, with ``--stdin``, read from standard input. The vault PIN is
asked only because storing a key writes the vault, which only the PIN opens;
the yes to switching the service on is the same consent ticket the browser
uses, bound to this terminal.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import sys


def add_parsers(subparsers) -> None:
    sub = subparsers.add_parser(
        "keys", help="Sign-up keys: list, add <name>, remove <name>, test <name>")
    sub.add_argument("keys_action", nargs="?", default="list",
                     choices=["list", "add", "remove", "test"])
    sub.add_argument("which", nargs="?", default=None,
                     help="The key's name, as `puffpai keys` lists it")
    sub.add_argument("--stdin", action="store_true",
                     help="For 'add': read the key, then the vault PIN, from "
                          "standard input instead of asking")
    sub.add_argument("--keep-working-when-locked", action="store_true",
                     dest="keep_locked",
                     help="For 'add': keep a free key working while the vault "
                          "is locked (never for a key given with a payment card)")
    sub.add_argument("-y", "--yes", action="store_true",
                     help="For 'add': say yes without being asked (the "
                          "sentence is still printed)")
    sub.add_argument("--public", action="store_true",
                     help="For 'test': use the service's shared test key")
    sub.add_argument("--words", default=None,
                     help="For 'test --public': search for these words once")
    # Anything more on the line is caught here, so a key typed after the name
    # is refused WITHOUT being printed back (argparse would echo it).
    sub.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    sub.set_defaults(func=cmd_keys)


def _vault():
    from puffpai import cli
    from puffpai.config import get_config
    from puffpai.security.vault import Vault
    return Vault(vault_dir=cli._vault_dir(get_config()))  # noqa: SLF001


def _print_list() -> int:
    from puffpai.security import keys
    rows = keys.list_keys(vault=_vault(), surface="terminal")
    print("Sign-up keys")
    print("A key goes straight into your vault; your bot keeps only a note "
          "that says which key to use.")
    print()
    for row in rows:
        print(f"  {row['name']} ({row['id']}) - {row['state_line']}")
        print(f"      For: {row['for']}")
        print(f"      {row['cost_note']}")
        if row["uses_line"]:
            print(f"      {row['uses_line']}")
        if row["state"] == "missing":
            print(f"      To add it: puffpai keys add {row['id']}")
        else:
            print(f"      To remove it: puffpai keys remove {row['id']}")
    return 0


def _pick(which):
    from puffpai.security import keys
    text = str(which or "").strip().lower()
    for sid, row in keys.listed().items():
        if text in (sid, row["name"].lower()):
            return sid, row
    print("There is no sign-up key by that name. `puffpai keys` lists them.")
    return None, None


def _read_hidden(prompt: str, *, from_stdin: bool) -> str:
    from puffpai import cli
    if from_stdin:
        line = sys.stdin.readline()
        return line.strip() if line else ""
    return cli._read_pin(prompt)  # noqa: SLF001 - hidden when a terminal


def _add(which, *, from_stdin: bool, keep_locked: bool, yes: bool) -> int:
    from puffpai import cli
    from puffpai.security import consent_tickets, keys
    from puffpai.security.vault import AuthenticationError, VaultError
    sid, row = _pick(which)
    if sid is None:
        return 1
    how = keys.walk_through(sid, "terminal")
    print(how["line"])
    for n, step in enumerate(how["steps"], 1):
        print(f"  {n}. {step}")
    print(f"  {how['cost_note']} It takes {how['how_long']}.")
    if how.get("label"):
        print(f"  {how['label']}")
    value = _read_hidden("Paste the key (it will not show): ",
                         from_stdin=from_stdin)
    try:
        keys.check_shape(sid, value)  # nothing is sent if this fails
    except keys.KeyRefused as exc:
        print(exc.message)
        return 1
    vault = _vault()
    if not vault.is_initialized:
        from puffpai.security.vault import missing_words
        print(missing_words(vault) or (
            "A key needs the vault. Set your vault PIN first with "
            "`puffpai setup`, then add the key."))
        return 1
    pin = _read_hidden("Vault PIN (it opens the vault so the key can be "
                       "locked in it): ", from_stdin=from_stdin)
    try:
        vault.unlock(pin)
    except AuthenticationError:
        print("That PIN did not open the vault. Nothing was sent.")
        return 1
    except VaultError as exc:
        print(f"The vault could not be opened ({type(exc).__name__}). Nothing "
              f"was sent.")
        return 1
    finally:
        pin = ""
    try:
        background = bool(keep_locked) and not row["card_required"]
        if keep_locked and row["card_required"]:
            print(f"{row['name']} asks for a payment card, so its key never "
                  f"works while your vault is locked.")
        try:
            sentence = keys.confirm_sentence(sid, background=background)
        except keys.KeyRefused as exc:
            print(exc.message)
            return 1
        target = f"{sid}:locked" if background else sid
        device = consent_tickets.terminal_device_id()
        ticket = consent_tickets.mint("key_on", target, sentence, device)
        print(sentence)
        if not yes:
            if from_stdin or not cli._stdin_is_tty():  # noqa: SLF001
                print("Nothing was added: this needs a yes typed here, or "
                      "--yes.")
                return 1
            if not cli._ask_yes_no("Add it?", default=False):  # noqa: SLF001
                print("Nothing was added.")
                return 0
        ok, verdict = consent_tickets.redeem(ticket, device, "key_on", target)
        if not ok:
            print(consent_tickets.refusal_words(verdict))
            return 1
        try:
            result = keys.add_key(sid, value, vault=vault,
                                  background=background, by="owner")
        except keys.KeyRefused as exc:
            print(exc.message)
            return 1
        print(result["message"])
        for note in result.get("notes") or []:
            print(note)
        return 0
    finally:
        value = ""
        vault.lock()


def _remove(which) -> int:
    from puffpai.security import keys
    sid, _row = _pick(which)
    if sid is None:
        return 1
    try:
        result = keys.remove_key(sid, vault=_vault(), by="owner")
    except keys.KeyRefused as exc:
        print(exc.message)
        return 1
    print(result["message"])
    return 0


def _test(which, *, public: bool, words) -> int:
    from puffpai import cli, web_search
    from puffpai.security import keys
    from puffpai.security.vault import AuthenticationError, VaultError
    sid, row = _pick(which)
    if sid is None:
        return 1
    if public:
        if not row.get("public_test_key"):
            print(f"{row['name']} has no shared test key.")
            return 1
        if words:
            try:
                got = web_search.search(words, provider=sid, use_test_key=True)
            except web_search.SearchRefused as exc:
                print(exc.message)
                return 1
            if not got["ok"]:
                for note in got["notes"]:
                    print(note)
                return 1
            print(f"{row['name']} (shared test key) found "
                  f"{len(got['results'])} result(s) for: {got['words']}")
            for n, r in enumerate(got["results"], 1):
                print(f"  {n}. {r['title']} - {r['site']}")
                print(f"     {r['url']}")
            print("The shared test key is only for trying it out. For real "
                  f"use, add your own: puffpai keys add {sid}")
            return 0
        try:
            print(keys.test_public_key(sid)["message"])
        except keys.KeyRefused as exc:
            print(exc.message)
            return 1
        return 0
    vault = _vault()
    if not vault.is_initialized:
        from puffpai.security.vault import missing_words
        print(missing_words(vault)
              or "There is no vault yet, so there is no key to check.")
        return 1
    pin = cli._read_pin("Vault PIN (it opens the vault to check the key): ")  # noqa: SLF001
    try:
        vault.unlock(pin)
    except (AuthenticationError, VaultError):
        print("That PIN did not open the vault. Nothing was sent.")
        return 1
    finally:
        pin = ""
    try:
        print(keys.check_key(sid, vault=vault, by="owner")["message"])
        return 0
    except keys.KeyRefused as exc:
        print(exc.message)
        return 1
    finally:
        vault.lock()


def cmd_keys(args: argparse.Namespace) -> int:
    action = getattr(args, "keys_action", None) or "list"
    which = getattr(args, "which", None)
    if getattr(args, "extra", None):
        print("A key is never typed on the command line: nothing was sent or "
              "kept. Run the command without it and paste the key when it "
              "asks. If you typed a real key, your terminal may remember it; "
              "if so, get a new key from the service.")
        return 1
    if action == "list":
        return _print_list()
    if not which:
        print(f"Say which one: puffpai keys {action} <name>. `puffpai keys` "
              f"lists them.")
        return 1
    if action == "add":
        return _add(which, from_stdin=bool(getattr(args, "stdin", False)),
                    keep_locked=bool(getattr(args, "keep_locked", False)),
                    yes=bool(getattr(args, "yes", False)))
    if action == "remove":
        return _remove(which)
    return _test(which, public=bool(getattr(args, "public", False)),
                 words=getattr(args, "words", None))


__all__ = ["add_parsers", "cmd_keys"]
