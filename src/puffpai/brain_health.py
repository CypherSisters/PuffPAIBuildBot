"""Is the configured brain actually there? — one answer, used everywhere.

The wizard, the CLI chat, and the HTTP API all used to answer "is the brain
ready?" differently, and all three answered it wrong in the same direction:
they probed the Ollama *server* and called a reachable server a working bot.
A stranger who finished setup, ran what the closing screen told them to run,
and typed "hello" got ``Error: Ollama returned status 404`` delivered as the
assistant's own reply, wrapped in ``"success": true``.

The distinction this module exists to keep is:

* **unreachable** - nothing is listening at the configured URL.
* **model_missing** - Ollama is up, and the model this build is configured for
  is not among the ones it has. The bot cannot answer. Saying "connected" here
  is the lie.
* **connected** - the server is up AND the configured model is installed.

Every human-facing message it produces names the model and carries the exact
command that fixes it, because "model not found" teaches nobody anything.

Nothing here writes config, and nothing here pulls unless a caller explicitly
asks it to.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy

CONNECTED = "connected"
MODEL_MISSING = "model_missing"
UNREACHABLE = "unreachable"

DEFAULT_URL = "http://localhost:11434"

#: Where Ollama actually lives when it is not at the default. Probed in order,
#: before the wizard resorts to asking. ``OLLAMA_HOST`` is checked first because
#: a person who set it has already told us the answer.
_ALTERNATE_PORTS = ("11434", "11435")


def normalize_model(name: str) -> str:
    """Model name with an implicit ``:latest`` made explicit-insensitive.

    Ollama reports ``llama3.2:1b`` in ``/api/tags`` but accepts ``llama3.2:1b``
    and ``llama3.2:1b:latest``-ish spellings from config. Comparing raw strings
    marks an installed model missing over a suffix.
    """
    text = (name or "").strip()
    if text.endswith(":latest"):
        text = text[: -len(":latest")]
    return text


def is_installed(model: str, installed: Optional[list[str]]) -> bool:
    """True when ``model`` is one of the names Ollama reported."""
    if not model or not installed:
        return False
    target = normalize_model(model)
    return any(normalize_model(name) == target for name in installed)


def pull_command(model: str, url: Optional[str] = None) -> str:
    """The exact command a person types to fetch ``model``.

    When the build talks to a non-default Ollama, the plain command would reach
    a different server than the one this build uses, so the address is named.
    """
    base = f"ollama pull {model}"
    if url and _same_server(url, DEFAULT_URL):
        return base
    if url:
        return f"{base}    (on the Ollama at {url})"
    return base


def unreachable_remedy(url: Optional[str] = None) -> str:
    """The one action to take when nothing answers at the Ollama address.

    This is the counterpart to :func:`pull_command` and the two are mutually
    exclusive: a pull aimed at a server that is not running cannot succeed, so
    a closing screen that prints one must never print the other. The wording
    matches docs/TROUBLESHOOTING.md, which says an ``unreachable`` status means
    "start Ollama or fix the address rather than pulling anything".
    """
    where = (url or DEFAULT_URL).strip() or DEFAULT_URL
    return (f"Ollama is not reachable at {where} - install or start Ollama "
            f"(or pass --ollama-url), then re-run `puffpai setup`")


def _same_server(a: str, b: str) -> bool:
    """Loose equality for two Ollama base URLs (localhost == 127.0.0.1)."""
    def _key(raw: str) -> str:
        text = (raw or "").strip().rstrip("/")
        for prefix in ("http://", "https://"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text.replace("127.0.0.1", "localhost").replace("[::1]", "localhost")
    return _key(a) == _key(b)


#: Headroom multiplier on a catalog size. A pull is not the only thing landing
#: on that volume while it runs, and Ollama unpacks as it goes, so the honest
#: floor is the model plus a margin rather than the model exactly.
DISK_HEADROOM = 1.2


def model_store_dir(url: Optional[str] = None) -> str:
    """Where a pull to ``url`` actually lands on THIS box, or "" if elsewhere.

    Only answerable for a local Ollama. A pull to someone else's server fills
    someone else's disk, and guessing at it would produce a warning about a
    volume the owner cannot do anything about.
    """
    target = (url or "").strip() or DEFAULT_URL
    if not _same_server(target, DEFAULT_URL):
        return ""
    override = (os.environ.get("OLLAMA_MODELS") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".ollama", "models")


def disk_headroom(disk_gb: Optional[float],
                  url: Optional[str] = None) -> str:
    """A warning if this box cannot hold ``disk_gb``, or "" if it can.

    Deliberately a warning and never a refusal. The catalog size is an
    estimate, the volume the owner is looking at may not be the one we found,
    and a wizard that blocks a download over its own arithmetic is worse than
    one that lets a person who knows their disk proceed. What we owe them is
    the number before the download starts, not after it half-fills the drive.
    """
    try:
        size = float(disk_gb or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""

    store = model_store_dir(url)
    if not store:
        return ""

    # Walk up to the nearest directory that exists: ~/.ollama/models is not
    # created until the first pull, and the free space we want is the volume's.
    probe = store
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return ""
        probe = parent
    if not probe:
        return ""

    import shutil
    try:
        free_gb = shutil.disk_usage(probe).free / (1024 ** 3)
    except OSError:
        # An unreadable volume is not a reason to block a download.
        return ""

    needed = size * DISK_HEADROOM
    if free_gb >= needed:
        return ""
    return (f"Heads up: {probe} has about {free_gb:.1f} GB free, and this "
            f"download wants roughly {needed:.1f} GB "
            f"({size:g} GB plus room to unpack). It may run out partway.")


def model_missing_message(model: str, url: Optional[str] = None) -> str:
    """What to tell a person whose configured brain was never downloaded."""
    return (
        f"This build is set up to use the model '{model}', which is not "
        f"on the Ollama at {url or DEFAULT_URL}, so there is no brain to "
        f"answer with. Install it with:"
        f"\n    {pull_command(model, url)}\n"
        f"then ask again."
    )


def unreachable_message(url: str) -> str:
    """What to tell a person whose Ollama is not answering."""
    return (
        f"Nothing is answering at the Ollama address this build is set to use "
        f"({url}), so there is no brain to answer with. Start Ollama, or point "
        f"this build at the right address with:"
        f"\n    puffpai setup --ollama-url <address>"
    )


#: What a request that needed the model is told when there is no model to ask.
#:
#: Said BEFORE the remedy, because the first thing an owner needs to know is
#: which part of the box could not do the job - not a paragraph about Ollama that
#: reads, to somebody who has just typed a sentence about their plantings list,
#: like the whole build is broken. A build with no model still answers its apps,
#: its lists and its own description; this sentence says exactly which door was
#: shut, and the remedy after the colon says how to open it.
NEEDS_MODEL_SENTENCE = "That needs the language model, which is not reachable yet:"


def needs_model_message(detail: str) -> str:
    """The honest refusal for a request that could not reach the model.

    ``detail`` is one of the sentences above - it already carries the fix, so
    this adds the meaning rather than a second remedy.
    """
    return f"{NEEDS_MODEL_SENTENCE} {str(detail).strip()}"


def status_message(state: dict[str, Any]) -> str:
    """The human sentence for a :func:`probe` result. Empty when connected."""
    status = state.get("status")
    if status == MODEL_MISSING:
        return model_missing_message(str(state.get("model") or ""),
                                     state.get("url"))
    if status == UNREACHABLE:
        return unreachable_message(str(state.get("url") or ""))
    return ""


# ---------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------

def list_models(url: str, timeout: int = 3) -> Optional[list[str]]:
    """Installed model names at ``url``, or None when it is unreachable.

    An empty list means Ollama is up with nothing pulled - a different fact
    from "unreachable", and the two must never collapse into each other.
    """
    import requests

    base = (url or DEFAULT_URL).rstrip("/")
    try:
        resp = requests.get(f"{base}/api/tags", timeout=timeout, proxies=_no_proxies())  # lan-egress: Ollama /api/tags on the configured LAN url
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return [m.get("name", "") for m in models if m.get("name")]
    except Exception:
        return None


#: Sentinel for "I did not pass you a model list". It has to be distinct from
#: ``None``, because ``None`` is itself a meaningful answer here - it is what
#: :func:`list_models` returns for an unreachable server. Conflating the two
#: makes a caller who already knows Ollama is down go and ask the network
#: again, and get a different answer.
_UNSET: Any = object()


def probe(url: str, model: str, timeout: int = 3,
          installed: Any = _UNSET) -> dict[str, Any]:
    """Is this build's brain ready? Never raises.

    Pass ``installed`` to reuse a model list you already fetched instead of
    making a second round-trip - including ``None``, which says "I already
    tried, and nothing answered".

    Returns ``{status, model, url, installed, message}`` where ``status`` is
    one of :data:`CONNECTED`, :data:`MODEL_MISSING`, :data:`UNREACHABLE`.
    """
    names = (list_models(url, timeout=timeout) if installed is _UNSET
             else installed)
    if names is None:
        state = {"status": UNREACHABLE, "model": model, "url": url,
                 "installed": []}
    elif is_installed(model, names):
        state = {"status": CONNECTED, "model": model, "url": url,
                 "installed": names}
    else:
        state = {"status": MODEL_MISSING, "model": model, "url": url,
                 "installed": names}
    state["message"] = status_message(state)
    return state


#: Hosts that mean "listen on everything", which is not an address to connect to.
#: ``OLLAMA_HOST=0.0.0.0:11435`` is how a person tells Ollama to accept LAN
#: connections, and it is a strong hint about the PORT - the box answers on
#: 127.0.0.1:11435. Dialling 0.0.0.0 is a coin toss by platform, so it is
#: rewritten rather than tried.
_WILDCARD_HOSTS = ("0.0.0.0", "::", "[::]", "*", "")


def _connectable(raw: str) -> str:
    """One address, rewritten so it can actually be dialled. "" if it cannot.

    Scheme-less is accepted (Ollama's own CLI takes ``host:port``) and a wildcard
    host becomes loopback with the port kept, because the port is the part the
    owner was telling us about.
    """
    text = (raw or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    scheme, _, rest = text.partition("://")
    host, sep, port = rest.rpartition(":")
    if not sep:            # no port at all: nothing to rescue, keep as typed
        host, port = rest, ""
    if host in _WILDCARD_HOSTS:
        host = "127.0.0.1"
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def candidate_urls(default_url: Optional[str] = None) -> list[str]:
    """Addresses worth trying before asking a person, best guess first.

    ``OLLAMA_HOST`` comes first: someone who set it has already answered the
    question. Then the configured URL, then the usual loopback spellings and
    the second port Ollama gets moved to.
    """
    out: list[str] = []

    def _add(raw: Optional[str]) -> None:
        text = _connectable(raw or "")
        if not text:
            return
        if not any(_same_server(text, seen) for seen in out):
            out.append(text)

    _add(os.environ.get("OLLAMA_HOST"))
    _add(default_url)
    for port in _ALTERNATE_PORTS:
        _add(f"http://127.0.0.1:{port}")
        _add(f"http://localhost:{port}")
    return out


def survey(default_url: Optional[str] = None, timeout: int = 2,
           probe: Optional[Callable[..., Optional[list[str]]]] = None
           ) -> list[dict[str, Any]]:
    """Every candidate address and what it answered, in the order tried.

    ``[{"url": ..., "models": [...] | None}]``, where ``None`` means nothing
    answered and ``[]`` means Ollama answered with no models pulled. Keeping the
    whole survey instead of the first hit is what lets a wizard say WHY it chose
    an address, and lets it prefer the one that can actually serve a prompt.
    """
    ask = probe or list_models
    return [{"url": url, "models": ask(url, timeout=timeout)}
            for url in candidate_urls(default_url)]


def best_address(looked: list[dict[str, Any]],
                 model: Optional[str] = None) -> Optional[str]:
    """The address to use out of a :func:`survey`, or None if none answered.

    With ``model``, the address that HAS that model wins over everything: an
    Ollama that answers with other models still cannot serve this one, and a
    build pointed at it 404s every chat (a relay on 11434 holding one small
    model, while the chosen brain was only on 11435). Without one, an address
    with models beats one without: 127.0.0.1:11434 can be a relay to an EMPTY
    Ollama that answers, so "first that answers" picked one that cannot serve
    a single prompt.
    """
    if model:
        for row in looked:
            if is_installed(model, row.get("models")):
                return str(row["url"])
    for row in looked:
        if row.get("models"):
            return str(row["url"])
    for row in looked:
        if row.get("models") is not None:
            return str(row["url"])
    return None


def address_with_model(model: str, default_url: Optional[str] = None,
                       timeout: int = 2,
                       probe: Optional[Callable[..., Optional[list[str]]]] = None
                       ) -> tuple[Optional[str], list[dict[str, Any]]]:
    """``(address that has model, the survey)``; the address is None when no
    known address has it. Only the usual addresses are tried (see
    :func:`candidate_urls`), never a scan."""
    looked = survey(default_url, timeout=timeout, probe=probe)
    for row in looked:
        if is_installed(model, row.get("models")):
            return str(row["url"]), looked
    return None, looked


def survey_lines(looked: list[dict[str, Any]]) -> list[str]:
    """What was found at each address, in plain words. One line each.

    A wizard that picks an address silently is a wizard nobody can check, and
    this is the sentence an owner needs when their box has two Ollamas.
    """
    lines: list[str] = []
    for row in looked:
        url, models = str(row.get("url") or ""), row.get("models")
        if models is None:
            lines.append(f"{url}: nothing answered")
        elif not models:
            lines.append(f"{url}: answers, but has no models installed")
        elif len(models) == 1:
            lines.append(f"{url}: answers, with 1 model ({models[0]})")
        else:
            lines.append(f"{url}: answers, with {len(models)} models")
    return lines


def find_ollama(default_url: Optional[str] = None,
                timeout: int = 2) -> Optional[str]:
    """The best address out of :func:`survey`, or None when none answered.

    "Best" is not "first": an address that lists models beats one that answers
    with none. See :func:`best_address`.
    """
    return best_address(survey(default_url, timeout=timeout))


def valid_url(raw: str) -> Optional[str]:
    """Normalise a typed Ollama address, or None when it is not one.

    Accepts a bare ``host:port``; rejects anything without a host. This is a
    shape check only - whether something answers there is :func:`list_models`'
    business, and the wizard asks that separately so it can tell the owner
    which of the two went wrong.
    """
    text = (raw or "").strip()
    if not text:
        return None
    had_scheme = "://" in text
    if not had_scheme:
        text = f"http://{text}"
    if not text.startswith(("http://", "https://")):
        return None
    scheme, _, rest = text.partition("://")
    host = rest.rstrip("/")
    # A host with whitespace in it is prose, not an address. Without this the
    # scheme prefix turns any typo into a "valid" URL that then quietly fails
    # to answer, and the wizard blames the network instead of the typing.
    if not host or host.startswith(("/", ":")) or any(c.isspace() for c in host):
        return None
    # A bare word with no scheme and no port/dot (e.g. "not-a-url") is prose,
    # not an address - accepting it just means the wizard blames the network
    # for what was a typo. A typed scheme (http://host) or a host with a port
    # or a dot (localhost:11434, 192.168.1.5:11434) still passes.
    if not had_scheme and ":" not in host and "." not in host:
        return None
    return f"{scheme}://{host}"


# ---------------------------------------------------------------------------
# acquiring
# ---------------------------------------------------------------------------

def download_estimate(disk_gb: Optional[float]) -> str:
    """An honest range for how long ``disk_gb`` takes to arrive, or "".

    Two rates, both named, because a single number pretending to know the
    owner's connection is the kind of estimate that makes people distrust
    every other number on the screen.
    """
    try:
        size = float(disk_gb or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    fast = max(1, round(size * 1000 / 12.5 / 60))   # ~100 Mbit/s
    slow = max(1, round(size * 1000 / 1.9 / 60))    # ~15 Mbit/s
    unit = "minute" if fast == 1 else "minutes"
    return (f"about {size:g} GB - roughly {fast:.0f} {unit} on a fast home "
            f"connection, {slow:.0f} on a slow one")


def pull_model(model: str, url: Optional[str] = None,
               on_progress: Optional[Callable[[str], None]] = None,
               timeout: int = 60) -> dict[str, Any]:
    """Pull ``model`` into the Ollama at ``url``, reporting progress.

    Goes through Ollama's own ``/api/pull`` rather than shelling out to the
    ``ollama`` binary, because the binary talks to whatever server its own
    environment points at - which on at least one validated box was NOT the
    server this build uses. Pulling into a server the bot never reads from is
    the failure this whole row exists to prevent.

    ``on_progress`` is called with short human lines as they arrive. Returns
    ``{"ok": bool, "message": str}``; a failure says what failed, never
    "done" over an error.
    """
    import requests

    base = (url or DEFAULT_URL).rstrip("/")
    last = ""
    try:
        resp = requests.post(f"{base}/api/pull",  # lan-egress: Ollama /api/pull on the configured LAN url
                             json={"name": model, "stream": True},
                             stream=True, timeout=timeout, proxies=_no_proxies())
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except (ValueError, AttributeError):
                continue
            if event.get("error"):
                return {"ok": False,
                        "message": f"Ollama refused the pull: {event['error']}"}
            note = str(event.get("status") or "").strip()
            if note and note != last:
                last = note
                if on_progress:
                    total = event.get("total")
                    done = event.get("completed")
                    if total and done:
                        pct = 100.0 * float(done) / float(total)
                        on_progress(f"{note} ({pct:.0f}%)")
                    else:
                        on_progress(note)
    except Exception as exc:
        return {"ok": False,
                "message": (f"The download did not finish: {exc}. "
                            f"You can run it yourself with: "
                            f"{pull_command(model, base)}")}

    if is_installed(model, list_models(base) or []):
        return {"ok": True, "message": f"{model} is installed."}
    return {"ok": False,
            "message": (f"The pull reported no error, but {model} is still not "
                        f"in Ollama's model list. Try it by hand: "
                        f"{pull_command(model, base)}")}


__all__ = [
    "CONNECTED",
    "MODEL_MISSING",
    "UNREACHABLE",
    "DEFAULT_URL",
    "normalize_model",
    "is_installed",
    "pull_command",
    "unreachable_remedy",
    "model_missing_message",
    "unreachable_message",
    "NEEDS_MODEL_SENTENCE",
    "needs_model_message",
    "status_message",
    "list_models",
    "probe",
    "candidate_urls",
    "survey",
    "best_address",
    "survey_lines",
    "find_ollama",
    "valid_url",
    "download_estimate",
    "pull_model",
]
