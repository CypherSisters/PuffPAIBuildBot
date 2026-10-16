"""No route logs, stores, echoes or queues a pasted key. One check for all.

The bug this fixes: ``/agents/task`` echoed a pasted key and
wrote it, word for word, to ``agent-activity.jsonl`` and ``task-queue.jsonl``.
The chat routes already caught such a message (``web_search.
looks_like_pasted_key``), but every other route that takes words from a
person -- agent tasks, routine labels and messages, announcements, add-more
and looks words, the keys page's other fields, the home town, persona edits,
app data -- had no check at all.

Now :func:`check` runs before EVERY request that can change something (POST,
PUT, PATCH, DELETE), before the route itself runs. It reads every piece of
text in the request -- the JSON body however deeply nested, form fields,
the words after ``?`` -- with the same check the chat uses. When one looks
like a key or a password the request is refused (HTTP 400, in plain words)
and the route never runs: nothing is logged, kept, echoed or queued. The one
log line names the route and the field, never what was in it -- and a field's
name only when it is safe to show (:func:`shown_name`): a key typed as a
field's name (a JSON mapping's key, a form field's or a ``?`` word's name) is
caught like any other and logged as "a field name".

What is NOT read (and why):

* the chat routes (:data:`OWN_CHECK`): they run the same check first
  themselves, swap the message for a placeholder and answer it (a chat gets a
  reply, not an error),
* fields where a secret is the point (:data:`SECRET_FIELDS`): the keys page's
  key, the vault's passwords and the value being put in the vault, the Home
  Assistant token,
* fields a program fills in, not a person (:data:`_MACHINE_FIELDS`, names
  ending ``_id``/``_ids``/``_token``/``_b64``/``_base64`` or ``Id`` as in
  ``taskId``, and ``data:`` addresses): ids, consent tickets, file and picture
  contents. These are made up of random letters on purpose and would all look
  like keys,
* folder and file paths (names ending ``_dir``/``_path``): a Windows folder
  name often has a random id in it (the knowledge folder's did),
* uploaded files: a document is the knowledge upload's to judge.

Fail closed: if the check itself breaks, the request is refused.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

#: What a refused request gets back. Plain words; never quotes what was sent.
REFUSAL = ("Part of what you sent looks like a key or a password, so I did "
           "not keep it, log it or pass it on. Please do not paste keys or "
           "passwords here. Keys go on the Sign-up keys page.")

#: Methods that can change something. GET and HEAD carry no body here.
METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Routes that run the same check themselves, first, and answer a pasted key
#: with a reply instead of an error (see api_server._swap_pasted_key).
OWN_CHECK = frozenset({
    "conversation", "chat_send", "chat_stream", "chat_save",
    "ollama_chat", "openai_chat_completions",
})

#: (route, field): the fields where a key or a password is what belongs.
SECRET_FIELDS = frozenset({
    ("keys.keys_add", "value"),              # the Sign-up keys page's key
    ("vault_store", "value"),                # what the owner puts in the vault
    ("vault_unlock", "password"), ("vault_unlock", "pin"),
    ("vault_unlock", "recovery_code"),
    ("vault_recover", "code"), ("vault_recover", "recovery_code"),
    ("vault_recover", "new_password"), ("vault_recover", "new_pin"),
    ("setup_create_vault", "password"), ("setup_create_vault", "recovery_code"),
    ("setup_set_ha", "token"), ("setup_test_ha", "token"),
    # Home Assistant's token and a tool server's password, both
    # straight into the vault (puffpai.mcp_client / security.keys).
    ("mcp.mcp_home_assistant", "token"), ("mcp.mcp_add", "password"),
    # A skill's own key, from its page's "Put the key in my vault" (kept for
    # that skill only, puffpai.things.put_key / security.keys).
    ("things.thing_key", "value"),
    # Her GitHub token, from the Developer page or the wizard, straight into
    # the vault (puffpai.github_account / security.keys).
    ("github_account.github_token", "token"),
    # Toolsmith's Anthropic key, from Settings, straight into the vault
    # (puffpai.security.toolsmith_cloud / security.keys).
    ("toolsmith_model.toolsmith_model_set", "key"),
    # Where the vault lives: switching to another vault or moving it takes
    # the vault's own PIN (and the current one), never sent in the address.
    ("vault_place.vault_location_set", "pin"),
    ("vault_place.vault_location_set", "current_pin"),
    ("vault_place.vault_move", "pin"),
})

#: Fields a program fills in. Random on purpose (ids, tickets, hashes, file
#: contents), so every one of them would look like a key.
_MACHINE_FIELDS = frozenset({
    "id", "ids", "ticket", "tickets", "session_token", "commit", "sha",
    "sha256", "hash", "checksum", "nonce", "file", "image", "audio",
    "avatar_seed", "created_at", "updated_at", "timestamp", "uuid", "guid",
    "path", "paths", "dir", "dirs", "folder",
    # A thing page sends its last few turns back with each ask: the bot's own
    # earlier replies, which can hold ids a skill printed (a spool's id).
    # Her own turns ("user") are still read.
    "assistant",
})
_MACHINE_SUFFIXES = ("_id", "_ids", "_token", "_b64", "_base64", "_hash",
                     "_sha256", "_dir", "_dirs", "_path", "_paths")
#: camelCase ids the apps use ("taskId", "eventIds", "userID").
_CAMEL_ID = re.compile(r"[a-z0-9](?:Id|Ids|ID|IDs|Uuid|UUID)$")

#: How deep a request body is read. Deeper than this is refused: nothing a
#: person fills in nests that far.
_MAX_DEPTH = 32


class _TooDeep(Exception):
    pass


def _machine(field: str) -> bool:
    name = field.lower()
    return (name in _MACHINE_FIELDS or name.endswith(_MACHINE_SUFFIXES)
            or _CAMEL_ID.search(field) is not None)


def _texts(value: Any, field: str,
           depth: int = 0) -> Iterator[tuple[str, str, bool]]:
    """``(field, text, is_name)`` for every piece of text in *value*.

    A list's items are read under the list's own field name; a field a
    program fills in is skipped with everything inside it -- but its NAME is
    still read (``is_name`` True): a name is typed by whoever sent the
    request, so a key typed as one (``{"sk-...": 1}``, ``{"q8Zr..._id": 1}``)
    is caught however it ends.
    """
    if depth > _MAX_DEPTH:
        raise _TooDeep()
    if isinstance(value, str):
        if not value.startswith("data:"):
            yield field, value, False
    elif isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            # A person may type the key of a mapping too (a list's name).
            yield name, name, True
            if not _machine(name):
                yield from _texts(item, name, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _texts(item, field, depth + 1)


#: What :func:`first_key_field` returns (and the log line says) in place of a
#: field's name when the NAME itself looked like a key or a password.
KEY_IN_NAME = "(a field name)"
#: ... and when the name is not one that can be shown safely (too long, odd
#: characters, or a personal detail in it). Never the name itself.
NAME_NOT_SHOWN = "(a field whose name is not shown)"
#: A body that is one piece of text, not a set of fields.
WHOLE_BODY = "(body)"
TOO_DEEP = "(nested too deep to read)"
UNREADABLE = "(could not be read)"

#: A field name that may be printed: a word, as a program names its fields.
_SHOWABLE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,39}")


def shown_name(name: Any) -> str:
    """A caller-supplied field name as a log line or an answer may show it.

    The name goes through the same key-safe preview every other log line uses
    (``web_search.log_preview``) and is shown only when the preview leaves it
    exactly as it was AND it is a plain word of at most 40 letters, digits,
    ``_``, ``.`` or ``-``. A name that looks like a key or a password becomes
    :data:`KEY_IN_NAME`; any other name that cannot be shown becomes
    :data:`NAME_NOT_SHOWN`. Fail closed: if the preview breaks, not shown.
    """
    try:
        from puffpai import web_search
        text = str(name)
        if web_search.looks_like_pasted_key(text):
            return KEY_IN_NAME
        if (_SHOWABLE_NAME.fullmatch(text)
                and web_search.log_preview(text, 40) == text):
            return text
    except Exception:  # never the raw name instead
        pass
    return NAME_NOT_SHOWN


def _pairs(fields: Any) -> list[tuple[str, Any]]:
    if not fields:
        return []
    try:
        return [(str(k), v) for k, v in fields.items(multi=True)]
    except TypeError:
        return [(str(k), v) for k, v in fields.items()]


def first_key_field(endpoint: Optional[str], body: Any, form: Any = None,
                    args: Any = None, path: Any = None) -> Optional[str]:
    """Which field looks like a pasted key, as it may be shown, else None.

    *body* is the parsed JSON body; *form*, *args* and *path* are the form
    fields, the words after ``?`` and the words in the address itself
    (anything with ``.items(multi=True)``, or a dict).

    The answer is already safe to print (see :func:`shown_name`): a field's
    name when it is a plain word, :data:`KEY_IN_NAME` when the name ITSELF
    looked like a key (a mapping's key, a form field's or a ``?`` word's
    name), :data:`NAME_NOT_SHOWN` otherwise. Never the text that was read.
    """
    from puffpai import web_search
    endpoint = endpoint or ""
    sources: list[tuple[str, Any]] = [("", body)]
    names: list[str] = []
    for fields in (form, args):
        pairs = _pairs(fields)
        # A form field's or a ? word's name is typed by the sender too.
        names += [name for name, _value in pairs]
        sources += [(name, value) for name, value in pairs
                    if isinstance(value, str)]
    # The address's own words are read under the route's names for them.
    sources += [(name, value) for name, value in _pairs(path)
                if isinstance(value, str)]
    for name in names:
        if web_search.looks_like_pasted_key(name):
            return KEY_IN_NAME
    for field, value in sources:
        if field and _machine(field):
            continue
        for name, text, is_name in _texts(value, field):
            if is_name:
                if web_search.looks_like_pasted_key(text):
                    return KEY_IN_NAME
                continue
            if (endpoint, name) in SECRET_FIELDS or _machine(name):
                continue
            if web_search.looks_like_pasted_key(text):
                return shown_name(name) if name else WHOLE_BODY
    return None


def _what(field: str) -> str:
    """The log line's words for *field* (a :func:`first_key_field` answer)."""
    return {
        KEY_IN_NAME: "a field name looked like a key or a password",
        NAME_NOT_SHOWN: ("a field (its name not shown) looked like a key or "
                         "a password"),
        WHOLE_BODY: "the body looked like a key or a password",
        TOO_DEEP: ("the body was nested too deep to read, so it was treated "
                   "as a key or a password"),
        UNREADABLE: ("the request could not be read, so it was treated as a "
                     "key or a password"),
    }.get(field) or (f"the '{shown_name(field)}' field looked like a key or "
                     f"a password")


def check():
    """Flask before-request hook. Refuses a request that carries a key.

    Returns None to let the request through, or the refusal response.
    """
    from flask import jsonify, request
    if request.method not in METHODS:
        return None
    endpoint = request.endpoint or ""
    if endpoint in OWN_CHECK:
        return None
    try:
        body = request.get_json(silent=True) if request.is_json else None
        form = request.form if request.form else None
        field = first_key_field(endpoint, body, form, request.args,
                                request.view_args)
    except _TooDeep:
        field = TOO_DEEP
    except Exception:  # fail closed: a request that cannot be read is refused
        logger.warning("pasted-key check failed on %s; refused", endpoint)
        field = UNREADABLE
    if field is None:
        return None
    # The route's name (the app's own, never the sender's) and the field's
    # name only when it is safe to show: never the value, never a name that
    # itself looked like a key, and not the address either (words in it may
    # be what looked like a key). request.method is one of METHODS here.
    print(f"[Security] Refused {request.method} {endpoint or 'unknown route'}: "
          f"{_what(field)} (not shown).")
    return jsonify({"success": False, "ok": False, "key_paste": True,
                    "error": REFUSAL, "message": REFUSAL}), 400


#: A terminal colour code.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: What the web server's request log shows in place of such a line.
HIDDEN_REQUEST_LINE = ("a request whose address looked like it held a key or "
                       "a password (not shown)")


#: An address with words after ``?`` in a request line.
_ADDRESS_WITH_QUERY = re.compile(r"(?P<path>/[^\s?\"']*)\?(?P<query>[^\s\"']*)")

#: Parts of a query name that mean its value may be a secret (``pin``,
#: ``current_pin``, ``pairing_code``, ``access_token``, ...).
SECRET_QUERY_NAMES = ("pin", "code", "token", "pass", "secret", "key", "auth",
                      "session", "cookie", "recovery", "sig")

#: What a hidden query reads as in the request log.
QUERY_NOT_SHOWN = "?(not shown)"


def _secret_query(path: str, query: str) -> bool:
    from urllib.parse import unquote_plus
    if any(part.lower().startswith("vault") for part in path.split("/")):
        return True                         # /vault routes: never the query
    for pair in query.split("&"):
        name = unquote_plus(pair.split("=", 1)[0]).lower()
        if any(word in name for word in SECRET_QUERY_NAMES):
            return True
    return False


def strip_secret_queries(line: str) -> str:
    """*line* with the words after ``?`` replaced by :data:`QUERY_NOT_SHOWN`
    in every address on a ``/vault`` route, or whose query names a PIN, a
    code, a token or the like. Other addresses are left as they are."""
    def swap(match: "re.Match[str]") -> str:
        if _secret_query(match.group("path"), match.group("query")):
            return match.group("path") + QUERY_NOT_SHOWN
        return match.group(0)
    return _ADDRESS_WITH_QUERY.sub(swap, line)


class KeySafeRequestLog(logging.Filter):
    """The web server's own request log ("GET /x?q=... 200") never prints an
    address that holds something that looks like a key or a password.

    The refusal above stops the route, but the server still logs the request
    line, words after ``?`` included, for every request (GETs too). Put on
    the ``werkzeug`` logger by ``api_server``.

    It also never writes the query of a ``/vault`` address, or of any
    address whose query names a PIN, a code or a token
    (:func:`strip_secret_queries`): the vault routes refuse ``?pin=``, but
    the line was logged (and ``puffpai start`` keeps that log in a file)
    before the route said no.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        cleaned = line = ""
        try:
            from urllib.parse import unquote_plus
            from puffpai import web_search
            # The server colours some lines (a 400 is bold red); the colour
            # codes ("\x1b[31m") are not words and read like a password.
            line = _ANSI.sub("", record.getMessage())
            cleaned = strip_secret_queries(line)
            hide = web_search.looks_like_pasted_key(unquote_plus(cleaned))
        except Exception:  # fail closed: a line that cannot be read is hidden
            hide = True
        if hide:
            record.msg = HIDDEN_REQUEST_LINE
            record.args = ()
        elif cleaned != line:
            record.msg = cleaned
            record.args = ()
        return True


def install_request_log_filter() -> None:
    """Put :class:`KeySafeRequestLog` on the web server's logger, once."""
    log = logging.getLogger("werkzeug")
    if not any(isinstance(f, KeySafeRequestLog) for f in log.filters):
        log.addFilter(KeySafeRequestLog())


__all__ = ["HIDDEN_REQUEST_LINE", "KEY_IN_NAME", "KeySafeRequestLog", "METHODS",
           "NAME_NOT_SHOWN", "OWN_CHECK", "QUERY_NOT_SHOWN", "REFUSAL",
           "SECRET_FIELDS", "SECRET_QUERY_NAMES", "check", "first_key_field",
           "install_request_log_filter", "shown_name", "strip_secret_queries"]
